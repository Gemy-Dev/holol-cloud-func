"""Server-side audience policy for business events, shared by both clients."""
import uuid

from flask import jsonify
from modules import notifications as n, notification_log

ROLE_SPELLINGS = {
    **n._ROLE_SPELLINGS,
    'salesRepresentative': ['salesRepresentative', 'Sales Representative', 'sales representative',
                            'sales_representative', 'مندوب المبيعات', 'المندوب'],
    'technicalSupport': ['technicalSupport', 'Technical Support', 'technical support',
                         'technical_support', 'support', 'technician', 'دعم فني', 'مسؤول الدعم الفني'],
}
CLIENT_EVENTS = {'client_added', 'client_updated'}
TASK_DATE_EVENTS = {'task_date_set', 'task_date_changed', 'task_date_reset'}
EVENTS = {'task_completed', 'activity_added', 'opportunity_added',
          'support_record_added', 'support_visit_added', 'support_activity_assigned',
          *CLIENT_EVENTS, *TASK_DATE_EVENTS}


def role_of(user):
    value = str(user.get('role') or '').strip().casefold()
    return next((role for role, spellings in ROLE_SPELLINGS.items()
                 if value in {s.casefold() for s in spellings}), None)


def users_for_roles(db, roles):
    candidates = {}
    for role in roles:
        for snapshot in db.collection('users').where('role', 'in', ROLE_SPELLINGS[role]).stream():
            user = snapshot.to_dict() or {}
            if user.get('isActive') is True:
                candidates[snapshot.id] = user
    return candidates


def audience_roles(event, actor_role):
    # Adding or editing a client reaches every admin, sales manager and
    # representative, whoever made the change.
    if event in CLIENT_EVENTS:
        return ('admin', 'salesManager', 'salesRepresentative')
    # An activity from any side, and a task's date being set, moved or reset,
    # reach the admins and sales managers; the task's own representative is
    # added from the record in deliver_event.
    if event == 'activity_added' or event in TASK_DATE_EVENTS:
        return ('salesManager', 'admin')
    if event == 'task_completed' and actor_role == 'salesRepresentative':
        return ('salesManager', 'admin')
    if event in ('task_completed', 'support_visit_added', 'support_record_added') and actor_role == 'technicalSupport':
        return ('salesManager', 'admin')
    if event == 'opportunity_added':
        return {
            'salesRepresentative': ('salesManager', 'admin'),
            'salesManager': ('admin', 'salesRepresentative'),
            'admin': ('salesManager', 'salesRepresentative'),
        }.get(actor_role, ())
    return ()


def _read(db, collection, record_id):
    if not isinstance(record_id, str) or not record_id or '/' in record_id:
        return None
    snapshot = db.collection(collection).document(record_id).get()
    return (snapshot.to_dict() or {}) if snapshot.exists else None


def _live(record):
    return record is not None and record.get('reviewState') != 'deleted'


def deliver_event(db, *, actor_id, event, route, title, body, source):
    """Resolve role and assignee from Firestore; never accept recipient roles from clients.

    Returns None when the named event is not yet synced. Notification identities
    are tied to records so offline retries and reportSaved cannot double announce.
    """
    actor = _read(db, 'users', actor_id) or {}
    actor_role = role_of(actor)
    if actor.get('isActive') is not True:
        return {'skipped': 'inactive_actor'}
    route = dict(route)
    task = None
    if route.get('taskId'):
        task = _read(db, 'tasks', route['taskId'])
        if not _live(task):
            return None
        if event == 'task_completed' and task.get('status') != 'completed':
            return {'skipped': 'not_completed'}
        # OpportunityModel is also the storage type of an activity linked to a
        # main opportunity. Adding such an action is an activity, not a new lead.
        if event == 'opportunity_added' and task.get('mainOpportunityId'):
            event = 'activity_added'
        identity = route['taskId']
        # A date can be set, moved and reset again on the same task, so each
        # change is its own notice rather than one claim per task.
        if event in TASK_DATE_EVENTS:
            identity = route['taskId'] + '-' + uuid.uuid4().hex
    elif route.get('clientId') and event in CLIENT_EVENTS:
        if not _live(_read(db, 'clients', route['clientId'])):
            return None
        # One announcement per new client, shared by the immediate send and the
        # offline replay; every edit is announced on its own.
        identity = route['clientId']
        if event == 'client_updated':
            identity += '-' + uuid.uuid4().hex
    elif route.get('reportId'):
        if not _live(_read(db, 'reports', route['reportId'])):
            return None
        identity = 'report-' + route['reportId']
    elif route.get('mainOpportunityId'):
        if not _live(_read(db, 'main_opportunities', route['mainOpportunityId'])):
            return None
        identity = route['mainOpportunityId']
    elif route.get('supportRecordId'):
        parent = _read(db, 'technical_support', route['supportRecordId'])
        if not _live(parent):
            return None
        identity = route['supportRecordId']
        if event == 'support_visit_added':
            visit_id = route.get('visitId')
            if _read(db, 'reports', visit_id) is not None:
                return {'skipped': 'report_copy'}
            from modules.special_requests import visit_entries
            history = visit_entries(parent.get('visitHistory'))
            visit = next((v for v in history if isinstance(v, dict) and v.get('id') == visit_id), None)
            if not visit or visit.get('status') != 'completed':
                return {'skipped': 'not_completed'}
            identity += '-' + visit_id
    else:
        return {'skipped': 'missing_record'}

    candidates = users_for_roles(db, audience_roles(event, actor_role))
    # The representative the activity or task belongs to hears about it too.
    if task and (event in TASK_DATE_EVENTS or event == 'activity_added'):
        owner_id = task.get('assignedToId')
        owner = _read(db, 'users', owner_id) or {}
        if (owner.get('isActive') is True
                and (event in TASK_DATE_EVENTS or role_of(owner) == 'salesRepresentative')):
            candidates[owner_id] = owner
    technician = None
    # A newly assigned support activity has its own wording and claim, so
    # technicians see the instruction and managers see the rep's addition.
    if (task and event in ('activity_added', 'support_activity_assigned')
            and task.get('status') not in ('completed', 'canceled', 'cancelled')):
        assignee = task.get('assignedToId')
        user = _read(db, 'users', assignee) or {}
        if role_of(user) == 'technicalSupport' and user.get('isActive') is True:
            technician = assignee
    titles = {
        'task_completed': 'إنجاز مهمة دعم فني' if actor_role == 'technicalSupport' else 'المندوب أنجز مهمة',
        'activity_added': {
            'salesRepresentative': 'المندوب أضاف نشاطاً',
            'salesManager': 'مدير المبيعات أضاف نشاطاً',
            'admin': 'مسؤول النظام أضاف نشاطاً',
        }.get(actor_role, 'إضافة نشاط'),
        'client_added': 'إضافة عميل جديد',
        'client_updated': 'تعديل معلومات عميل',
        'task_date_set': 'تحديد تاريخ لمهمة',
        'task_date_changed': 'تغيير تاريخ مهمة',
        'task_date_reset': 'إعادة تعيين تاريخ مهمة',
        'opportunity_added': 'إضافة فرصة',
        'support_record_added': 'إضافة سجل دعم فني',
        'support_visit_added': 'إنجاز مهمة دعم فني',
    }
    route['event'] = event
    result = {'skipped': 'no_audience'}
    if candidates:
        result = n._deliver_to_candidates(
            db, candidates=candidates, unreachable=[], title=titles.get(event, title), body=body,
            message_data=route, kind='business_event', source=source, actor_id=actor_id,
            notification_id=f'{event}-{identity}', create_only=True,
        )
    if technician:
        assigned = n.send_to_user(
            db, user_id=technician, title='نشاط دعم فني مطلوب تنفيذه',
            body='أُضيف نشاط دعم فني مطلوب منك تنفيذه\n' + body,
            message_data={**route, 'event': 'support_activity_assigned'},
            kind='business_event', source=source, actor_id=actor_id,
            notification_id=f'support_activity_assigned-{identity}-{technician}', create_only=True,
        )
        if result.get('skipped'):
            result = assigned
        elif not assigned.get('skipped'):
            for key in ('successCount', 'failureCount', 'totalTokens', 'prunedTokens'):
                result[key] = result.get(key, 0) + assigned.get(key, 0)
            result.setdefault('unreachable', []).extend(assigned.get('unreachable', []))
    return result


def handle_business_notification(decoded_token, data, db):
    event = data.get('event')
    if event not in EVENTS:
        return jsonify({'success': False, 'error': 'invalid_event'}), 400
    route = data.get('notificationAction')
    if not isinstance(route, dict) or not data.get('title') or not data.get('body'):
        return jsonify({'success': False, 'error': 'invalid_payload'}), 400
    result = deliver_event(db, actor_id=decoded_token.get('uid'), event=event,
                           route=route, title=data['title'], body=data['body'],
                           source=notification_log.request_source(data))
    if result is None:
        return jsonify({'success': False, 'reason': 'source_not_synced'}), 409
    return jsonify({'success': True, **result}), 200
