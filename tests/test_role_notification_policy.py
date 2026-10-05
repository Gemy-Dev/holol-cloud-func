from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from modules.business_notifications import deliver_event
from modules.role_reminders import (
    task_reminders, review_reminders, opportunity_reminders, missed_task_reminders,
    support_reminders, weekly_reminders, handle_role_reminders, instant,
)
from modules.config import IRAQ_TIMEZONE

NOW = datetime(2026, 10, 3, 8, tzinfo=IRAQ_TIMEZONE)  # Saturday
USERS = {
    'mgr': {'role': 'salesManager', 'isActive': True, 'receiveEmailNotifications': True},
    'adm': {'role': 'admin', 'isActive': True, 'receiveEmailNotifications': True},
    'rep': {'role': 'salesRepresentative', 'isActive': True, 'receiveEmailNotifications': True},
    'tech': {'role': 'technicalSupport', 'isActive': True, 'receiveEmailNotifications': True},
}


def seed_users(db):
    for uid, user in USERS.items():
        db._put('users', uid, user)


@pytest.mark.parametrize('event,actor,expected', [
    ('task_completed', 'rep', {'mgr', 'adm'}),
    ('task_completed', 'tech', {'mgr', 'adm'}),
    ('activity_added', 'rep', {'mgr', 'adm'}),
    ('activity_added', 'mgr', set()),
    ('opportunity_added', 'rep', {'mgr', 'adm'}),
    ('opportunity_added', 'mgr', {'adm', 'rep'}),
    ('opportunity_added', 'adm', {'mgr', 'rep'}),
    ('support_record_added', 'tech', {'mgr', 'adm'}),
    ('support_record_added', 'rep', set()),
    ('support_visit_added', 'tech', {'mgr', 'adm'}),
])
def test_business_audience_and_history_follow_the_role_matrix(db, event, actor, expected):
    seed_users(db)
    if event == 'opportunity_added':
        db._put('main_opportunities', 'o', {})
        route = {'mainOpportunityId': 'o'}
    elif event.startswith('support_'):
        db._put('technical_support', 's', {'visitHistory': [{'id': 'v', 'status': 'completed'}]})
        route = {'supportRecordId': 's', 'visitId': 'v'}
    else:
        db._put('tasks', 't', {'status': 'completed', 'taskType': 'appointment'})
        route = {'taskId': 't'}
    result = deliver_event(db, actor_id=actor, event=event, route=route,
                           title='حدث', body='تفاصيل', source='app')
    records = list(db._get_all('notifications').values())
    assert {uid for record in records for uid in record['recipientIds']} == expected
    if expected:
        assert result['totalTokens'] == 0  # no token still gets in-app history
        again = deliver_event(db, actor_id=actor, event=event, route=route,
                              title='حدث', body='تفاصيل', source='app')
        assert again['skipped'] == 'already_notified'


def test_assigned_support_activity_only_reaches_its_technician_and_reviewers(db):
    seed_users(db)
    db._put('users', 'another-tech', USERS['tech'])
    db._put('tasks', 't', {'assignedToId': 'tech', 'taskType': 'appointment'})
    deliver_event(db, actor_id='rep', event='activity_added', route={'taskId': 't'},
                  title='نشاط', body='تفاصيل', source='app')
    records = list(db._get_all('notifications').values())
    assert {uid for record in records for uid in record['recipientIds']} == {'mgr', 'adm', 'tech'}
    assigned, = [r for r in records if r['event'] == 'support_activity_assigned']
    assert assigned['recipientIds'] == ['tech']
    assert assigned['title'] == 'نشاط دعم فني مطلوب تنفيذه'


def test_linked_opportunity_action_is_an_activity_not_a_new_opportunity(db):
    seed_users(db)
    db._put('tasks', 't', {'mainOpportunityId': 'o'})
    result = deliver_event(db, actor_id='adm', event='opportunity_added',
                           route={'taskId': 't'}, title='نشاط', body='تفاصيل', source='dashboard')
    assert result['skipped'] == 'no_audience'
    assert db._get_all('notifications') == {}


def test_missing_or_deleted_record_never_announces(db):
    seed_users(db)
    for record in (None, {'reviewState': 'deleted'}):
        if record is not None:
            db._put('tasks', 't', record)
        assert deliver_event(db, actor_id='rep', event='activity_added', route={'taskId': 't'},
                             title='نشاط', body='تفاصيل', source='app') is None


def test_daily_tasks_exclude_other_roles_and_finished_deleted_tasks():
    tasks = {str(i): {'targetDate': '2026-10-03T00:00:00.000', 'assignedToId': uid,
                     'status': status, 'reviewState': review}
             for i, (uid, status, review) in enumerate([
                 ('rep', 'pending', 'approved'), ('rep', 'completed', 'approved'),
                 ('rep', 'canceled', 'approved'), ('rep', 'pending', 'deleted'),
                 ('tech', 'pending', 'approved'), ('mgr', 'pending', 'approved')])}
    notices = list(task_reminders(USERS, tasks, NOW, 0))
    assert len(notices) == 1 and notices[0].route['taskIds'] == '0'


@pytest.mark.parametrize('age,count', [(timedelta(hours=23, minutes=59), 0), (timedelta(hours=24), 1)])
def test_review_waits_full_24_hours_and_respects_each_slot(age, count):
    report = {'createdAt': (NOW-age).isoformat(), 'salesManagerReview': {'reviewerId': 'mgr'}}
    notices = list(review_reminders(USERS, {}, {'r': report}, {}, NOW))
    assert len(notices) == count
    if count:
        assert notices[0].user_id == 'adm'


def test_review_uses_completion_instead_of_creation_or_later_edit():
    task = {'status': 'completed', 'completionReport': {'completedAt': (NOW-timedelta(hours=2)).isoformat()}}
    report = {'taskId': 't', 'createdAt': (NOW-timedelta(days=7)).isoformat(), 'updatedAt': NOW.isoformat()}
    assert list(review_reminders(USERS, {'t': task}, {'r': report}, {}, NOW)) == []


def test_copied_support_visit_does_not_double_count_task_report():
    report = {'createdAt': (NOW-timedelta(days=1)).isoformat()}
    support = {'s': {'visitHistory': [{'id': 'r', 'status': 'completed', 'createdAt': report['createdAt']}]}}
    assert len(list(review_reminders(USERS, {}, {'r': report}, support, NOW))) == 2


def test_support_review_uses_parent_review_slots_and_map_shaped_history():
    parent = {'visitHistory': {'v': {'status': 'completed', 'createdAt': (NOW-timedelta(days=1)).isoformat()}},
              'reviews': {'v__admin': {'reviewerId': 'adm'}}}
    notices = list(review_reminders(USERS, {}, {}, {'s': parent}, NOW))
    assert len(notices) == 1 and notices[0].user_id == 'mgr'


def test_opportunity_repeats_every_72_hours_and_stops_only_after_live_execution():
    opportunity = {'createdAt': (NOW-timedelta(days=3)).isoformat(), 'salesRepresentativeIds': ['rep']}
    notice, = opportunity_reminders(USERS, {}, {'o': opportunity}, NOW)
    assert list(opportunity_reminders(USERS, {}, {'o': opportunity}, NOW-timedelta(seconds=1))) == []
    same, = opportunity_reminders(USERS, {}, {'o': opportunity}, NOW+timedelta(days=2))
    next_notice, = opportunity_reminders(USERS, {}, {'o': opportunity}, NOW+timedelta(days=3))
    assert notice.notification_id == same.notification_id
    assert next_notice.notification_id != notice.notification_id
    for status, review, expected in [('pending', 'approved', 1), ('completed', 'deleted', 1), ('completed', 'approved', 0)]:
        tasks = {'t': {'mainOpportunityId': 'o', 'status': status, 'reviewState': review}}
        assert len(list(opportunity_reminders(USERS, tasks, {'o': opportunity}, NOW))) == expected


def test_evening_and_next_morning_missed_task_share_claim_and_redating_cancels():
    task = {'targetDate': '2026-10-02T00:00:00.000', 'assignedToId': 'rep', 'status': 'pending'}
    evening, = missed_task_reminders(USERS, {'t': task}, NOW-timedelta(hours=12))
    morning, = missed_task_reminders(USERS, {'t': task}, NOW)
    assert evening.notification_id == morning.notification_id
    task['targetDate'] = '2026-10-04T00:00:00.000'
    assert list(missed_task_reminders(USERS, {'t': task}, NOW)) == []


def test_support_due_and_overdue_include_hospital_and_stop_after_visit():
    record = {'nextVisitDate': '2026-10-10T00:00:00.000', 'technicianId': 'tech', 'clientName': 'الكندي'}
    upcoming, = support_reminders(USERS, {'s': record}, NOW)
    assert upcoming.kind == 'support_due' and 'الكندي' in upcoming.body
    assert list(support_reminders(USERS, {'s': record}, NOW+timedelta(days=7))) == []
    overdue, = support_reminders(USERS, {'s': record}, NOW+timedelta(days=8))
    assert overdue.body == 'عندك زيارة متأخرة للمستشفى الكندي'
    record['lastVisitDate'] = '2026-10-10T12:00:00.000'
    assert list(support_reminders(USERS, {'s': record}, NOW+timedelta(days=8))) == []
    record.pop('lastVisitDate')
    record['status'] = 'closed'
    assert list(support_reminders(USERS, {'s': record}, NOW)) == []


def test_weekly_schedule_and_kpis_are_at_eight_on_the_requested_days():
    saturday = list(weekly_reminders(USERS, NOW))
    assert {(n.user_id, n.kind) for n in saturday} == {
        ('mgr', 'weekly_schedule'), ('adm', 'weekly_schedule'), ('mgr', 'kpi_review'), ('adm', 'kpi_review')}
    assert [(n.user_id, n.kind) for n in weekly_reminders(USERS, NOW-timedelta(days=1))] == [('mgr', 'weekly_schedule')]
    assert list(weekly_reminders(USERS, NOW.replace(hour=9))) == []
    assert list(weekly_reminders(USERS, NOW+timedelta(days=1))) == []


def test_utc_timestamp_near_midnight_keeps_iraq_business_day():
    assert instant(datetime(2026, 10, 2, 21, tzinfo=timezone.utc)).date() == NOW.date()


def test_repeated_scheduler_run_and_legacy_daily_route_do_not_double_send(db):
    seed_users(db)
    db._put('tasks', 't', {'targetDate': '2026-10-03T00:00:00.000', 'assignedToId': 'rep', 'status': 'pending'})
    first, _ = handle_role_reminders(db, now=NOW)
    count = len(db._get_all('notifications'))
    second, _ = handle_role_reminders(db, now=NOW)
    legacy, _ = handle_role_reminders(db, now=NOW, daily_offset=0)
    assert first.get_json()['notified'] == 5
    assert second.get_json()['notified'] == legacy.get_json()['notified'] == 0
    assert len(db._get_all('notifications')) == count


def test_inactive_users_and_disabled_push_are_respected(db):
    seed_users(db)
    db._put('users', 'mgr', {**USERS['mgr'], 'receiveEmailNotifications': False, 'fcmToken': 'off'})
    db._put('users', 'adm', {**USERS['adm'], 'isActive': False, 'fcmToken': 'inactive'})
    with patch('modules.notifications.messaging') as messaging:
        handle_role_reminders(db, now=NOW)
        messaging.send_each_for_multicast.assert_not_called()
    assert {uid for r in db._get_all('notifications').values() for uid in r['recipientIds']} == {'mgr'}

@pytest.mark.parametrize('action', [
    'open_client', 'open_deals', 'open_plans', 'open_material_transfers',
])
def test_unlisted_operational_notices_do_not_escape_via_legacy_senders(db, action):
    from modules.notifications import handle_send_notification, handle_send_notification_to_all
    seed_users(db)
    data = {'title': 'عملية', 'body': 'تفاصيل', 'targetUserId': 'rep',
            'notificationAction': {'action': action}}
    with patch('modules.notifications.messaging') as messaging:
        for handler in (handle_send_notification, handle_send_notification_to_all):
            response, status = handler({'uid': 'adm'}, data, db)
            assert status == 200 and response.get_json()['skipped'] == 'event_disabled'
        messaging.send.assert_not_called()
        messaging.send_each_for_multicast.assert_not_called()
    assert db._get_all('notifications') == {}


def test_special_request_workflow_does_not_emit_an_extra_role_notice(db):
    from modules.notifications import send_to_user
    seed_users(db)
    result = send_to_user(db, user_id='rep', title='طلب خاص', body='تفاصيل',
                          message_data={'action': 'open_daily_report'},
                          kind='special_request', source='system', actor_id='adm')
    assert result['skipped'] == 'event_disabled'
    assert db._get_all('notifications') == {}


def test_a_support_visit_copied_from_a_task_report_does_not_announce_twice(db):
    seed_users(db)
    db._put('reports', 'r', {'taskId': 't'})
    db._put('technical_support', 's', {'visitHistory': [{'id': 'r', 'status': 'completed'}]})
    result = deliver_event(db, actor_id='tech', event='support_visit_added',
                           route={'supportRecordId': 's', 'visitId': 'r'},
                           title='زيارة', body='تفاصيل', source='app')
    assert result['skipped'] == 'report_copy'
    assert db._get_all('notifications') == {}


def test_review_audience_uses_actual_actor_role_and_includes_the_task_technician(db):
    from modules.notifications import handle_send_review_notification
    seed_users(db)
    # An admin signed the sales-manager slot: their actual role still decides
    # who receives the notice, and the technician who did the work is included.
    response, status = handle_send_review_notification({'uid': 'adm'}, {
        'reviewerRole': 'salesManager', 'representativeId': 'tech',
        'title': 'مراجعة', 'body': 'تفاصيل',
        'notificationAction': {'action': 'open_support_record', 'supportRecordId': 's'},
    }, db)
    assert status == 200
    record, = db._get_all('notifications').values()
    assert set(record['recipientIds']) == {'mgr', 'tech'}
    assert record['title'] == 'مسؤول النظام عمل مراجعة'
