"""Role reminders evaluated from synced records on a 15-minute Iraq-time tick."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from flask import jsonify
from modules import notifications as n
from modules.business_notifications import role_of, users_for_roles
from modules.config import IRAQ_TIMEZONE
from modules.dates import as_iso


@dataclass(frozen=True)
class Reminder:
    user_id: str
    kind: str
    identity: str
    title: str
    body: str
    route: dict

    @property
    def notification_id(self):
        # Fixed ids atomically claimed by the existing notification writer.
        key = f'{self.kind}|{self.identity}|{self.user_id}'
        return 'role_reminder-' + sha256(key.encode()).hexdigest()


def instant(value):
    iso = as_iso(value)
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).replace(tzinfo=IRAQ_TIMEZONE)
    except (ValueError, TypeError):
        return None


def live(record):
    return record.get('reviewState') != 'deleted'


def unfinished(task):
    return live(task) and task.get('status') in ('pending', 'reset', 'قيد الانجاز', 'إعادة تعيين')


def snapshots(db, collection):
    return {doc.id: doc.to_dict() or {} for doc in db.collection(collection).stream()}


def task_reminders(users, tasks, now, days_offset):
    target = now.date() + timedelta(days=days_offset)
    grouped = {}
    for tid, task in tasks.items():
        due = instant(task.get('targetDate'))
        uid = task.get('assignedToId')
        if (unfinished(task) and due and due.date() == target
                and role_of(users.get(uid, {})) == 'salesRepresentative'):
            grouped.setdefault(uid, []).append((tid, task))
    for uid, entries in grouped.items():
        when = 'اليوم' if days_offset == 0 else 'الغد'
        yield Reminder(uid, 'daily_tasks', f'{target}-{days_offset}',
                       f'تذكير بمهام {when}', f'عندك {len(entries)} مهام {when}',
                       {'action': 'daily_tasks', 'taskIds': ','.join(tid for tid, _ in entries),
                        'taskCount': str(len(entries)), 'date': target.isoformat()})


def review_reminders(users, tasks, reports, support, now):
    sources = []
    report_tasks = {r.get('taskId') for r in reports.values() if r.get('taskId')}
    for rid, report in reports.items():
        if not live(report):
            continue
        tid = report.get('taskId')
        task = tasks.get(tid, {})
        if tid:
            report_tasks.add(tid)
            if not task or not live(task) or task.get('status') != 'completed':
                continue
        completed = (task.get('completionReport') or {}).get('completedAt')
        stamp = instant(completed or report.get('createdAt'))
        sources.append((f'report-{rid}', report, stamp,
                        {'action': 'open_daily_report', 'reportId': rid}))
    # Legacy completions without a separately filed report still need review.
    for tid, task in tasks.items():
        if tid in report_tasks or not live(task) or task.get('status') != 'completed':
            continue
        stamp = instant((task.get('completionReport') or {}).get('completedAt'))
        sources.append((f'task-{tid}', task, stamp, {'action': 'open_task', 'taskId': tid}))
    for sid, parent in support.items():
        if not live(parent):
            continue
        from modules.special_requests import visit_entries
        history = visit_entries(parent.get('visitHistory'))
        for visit in history:
            if not isinstance(visit, dict) or not live(visit) or visit.get('status') != 'completed':
                continue
            vid = visit.get('id')
            # Copies of task reports are reviewed through the report once.
            if not vid or vid in reports:
                continue
            reviews = parent.get('reviews') or {}
            source = {role + 'Review': reviews.get(f'{vid}__{role}')
                      for role in ('salesManager', 'admin')}
            stamp = instant((visit.get('completionReport') or {}).get('completedAt')
                            or visit.get('createdAt'))
            sources.append((f'visit-{sid}-{vid}', source, stamp,
                            {'action': 'open_support_record', 'supportRecordId': sid}))
    for identity, source, stamp, route in sources:
        if stamp is None or now - stamp < timedelta(hours=24):
            continue
        for uid, user in users.items():
            role = role_of(user)
            if role in ('salesManager', 'admin') and not source.get(role + 'Review'):
                yield Reminder(uid, 'pending_review', identity, 'مهام بحاجة إلى مراجعة',
                               'عندك مهام بحاجة إلى مراجعة', route)


def opportunity_reminders(users, tasks, opportunities, now):
    executed = {task.get('mainOpportunityId') for task in tasks.values()
                if live(task) and task.get('status') == 'completed'}
    for oid, opportunity in opportunities.items():
        created = instant(opportunity.get('createdAt'))
        if (not live(opportunity) or opportunity.get('status') in ('completed', 'canceled', 'cancelled')
                or oid in executed or created is None):
            continue
        period = int((now - created).total_seconds() // (3 * 86400))
        if period < 1:
            continue
        recipients = opportunity.get('salesRepresentativeIds') or []
        if not isinstance(recipients, list):
            recipients = []
        recipients = set(recipients)
        for field in ('assignedTo', 'salesRepresentativeId'):
            if opportunity.get(field):
                recipients.add(opportunity[field])
        for uid in recipients:
            if role_of(users.get(uid, {})) == 'salesRepresentative':
                yield Reminder(uid, 'opportunity_action', f'{oid}-{created.isoformat()}-{period}',
                               'فرص بحاجة إلى اتخاذ إجراء',
                               f"فرص بحاجة إلى اتخاذ إجراء: {opportunity.get('name') or 'فرصة'}",
                               {'action': 'open_main_opportunity', 'mainOpportunityId': oid})


def missed_task_reminders(users, tasks, now):
    # Once at the end of the due day, or at 08:00 the next morning if the
    # evening tick was missed. Re-dating changes the identity and cancels it.
    due_day = now.date() if now.hour == 20 else now.date() - timedelta(days=1)
    for tid, task in tasks.items():
        due = instant(task.get('targetDate'))
        uid = task.get('assignedToId')
        if (unfinished(task) and due and due.date() == due_day
                and role_of(users.get(uid, {})) == 'salesRepresentative'):
            yield Reminder(uid, 'missed_task', f'{tid}-{due_day}',
                           'مهام بحاجة إلى إعادة جدولة', 'مهام لم تُنفذ بحاجة إلى إعادة جدولة',
                           {'action': 'open_task', 'taskId': tid})


def support_reminders(users, records, now):
    for sid, record in records.items():
        due = instant(record.get('nextVisitDate'))
        uid = record.get('technicianId')
        if (not live(record) or record.get('status') == 'closed' or due is None
                or role_of(users.get(uid, {})) != 'technicalSupport'):
            continue
        last_visit = instant(record.get('lastVisitDate'))
        if last_visit and last_visit.date() >= due.date():
            continue
        days = (due.date() - now.date()).days
        hospital = record.get('clientName') or 'غير محدد'
        if days == 7:
            yield Reminder(uid, 'support_due', f'{sid}-{due.date()}', 'زيارة دورية قريبة',
                           f'زيارة دورية قريبة للمستشفى {hospital} بعد أسبوع',
                           {'action': 'open_support_record', 'supportRecordId': sid})
        elif days < 0:
            yield Reminder(uid, 'support_overdue', f'{sid}-{due.date()}', 'زيارة متأخرة',
                           f'عندك زيارة متأخرة للمستشفى {hospital}',
                           {'action': 'open_support_record', 'supportRecordId': sid})


def weekly_reminders(users, now):
    if now.hour != 8:
        return
    for uid, user in users.items():
        role = role_of(user)
        if ((role == 'salesManager' and now.weekday() in (4, 5))
                or (role == 'admin' and now.weekday() == 5)):
            title = 'تذكير بالجدولة الأسبوعية' if role == 'salesManager' else 'تذكير بمراجعة الجدولة الأسبوعية'
            yield Reminder(uid, 'weekly_schedule', str(now.date()), title, title,
                           {'action': 'open_plans'})
        if role in ('salesManager', 'admin') and now.weekday() == 5:
            yield Reminder(uid, 'kpi_review', str(now.date()), 'تذكير بمراجعة مؤشرات الأداء',
                           'تذكير بمراجعة مؤشرات الأداء', {'action': 'open_kpi'})


def send_reminders(db, reminders):
    sent = skipped = failed = pushed = 0
    for reminder in reminders:
        try:
            result = n.send_to_user(
                db, user_id=reminder.user_id, title=reminder.title, body=reminder.body,
                message_data={**reminder.route, 'event': reminder.kind}, kind=reminder.kind,
                source='system', actor_id=None, notification_id=reminder.notification_id,
                create_only=True,
            )
            if result.get('skipped'):
                skipped += 1
            else:
                sent += 1
                pushed += result.get('successCount', 0)
        except Exception as error:
            print(f'Reminder {reminder.kind} failed: {error}')
            failed += 1
    return {'notified': sent, 'skipped': skipped, 'failed': failed, 'pushCount': pushed}


def handle_role_reminders(db, now=None, daily_offset=None):
    now = (now or datetime.now(IRAQ_TIMEZONE)).astimezone(IRAQ_TIMEZONE)
    users = users_for_roles(db, ('salesManager', 'admin', 'salesRepresentative', 'technicalSupport'))
    tasks = snapshots(db, 'tasks')
    if daily_offset is not None:
        reminders = list(task_reminders(users, tasks, now, daily_offset))
    else:
        reports = snapshots(db, 'reports')
        support = snapshots(db, 'technical_support')
        reminders = list(review_reminders(users, tasks, reports, support, now))
        reminders.extend(opportunity_reminders(users, tasks, snapshots(db, 'main_opportunities'), now))
        reminders.extend(weekly_reminders(users, now))
        if now.hour in (8, 20):
            reminders.extend(task_reminders(users, tasks, now, 0 if now.hour == 8 else 1))
            reminders.extend(missed_task_reminders(users, tasks, now))
        if now.hour == 8:
            reminders.extend(support_reminders(users, support, now))
    result = send_reminders(db, reminders)
    return jsonify({'success': result['failed'] == 0, **result}), 200
