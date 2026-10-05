"""Tests for spec 2037: every notification sent is stored in `notifications`.

The field app, the dashboard and Cloud Scheduler all send through this
function, so it is the one writer of the collection. Each record names the
sender (from the verified token, never the payload) and either the addressed
users or ``"all"``. The field app lists the records and shows today's count on
its icon — there is no read state, so the badge is "how many today", computed
here with the same visibility rule the app applies.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from modules import notification_log
from modules.config import IRAQ_TIMEZONE
from modules.dates import to_iso
from modules.notifications import (
    handle_daily_notifications,
    handle_send_notification,
    handle_send_notification_to_all,
    handle_send_review_notification,
)
from modules.apk_manager import _send_notifications_to_android_users
from tests.conftest import is_contract_iso


class _FakeSendResponse:
    def __init__(self, success: bool, exception=None):
        self.success = success
        self.exception = exception


class _FakeMulticastResponse:
    def __init__(self, responses: list):
        self.responses = responses
        self.success_count = sum(1 for r in responses if r.success)
        self.failure_count = sum(1 for r in responses if not r.success)


def _user(token='token', preference=True, active=True, **extra):
    return {
        'fcmToken': token,
        'receiveEmailNotifications': preference,
        'isActive': active,
        **extra,
    }


def _patched_messaging():
    """A messaging stub whose multicast sends all succeed."""
    mock_messaging = MagicMock()
    mock_messaging.UnregisteredError = type('U', (Exception,), {})
    mock_messaging.SenderIdMismatchError = type('S', (Exception,), {})

    def _multicast(message):
        tokens = mock_messaging.MulticastMessage.call_args.kwargs['tokens']
        return _FakeMulticastResponse([_FakeSendResponse(True) for _ in tokens])

    mock_messaging.send_each_for_multicast.side_effect = _multicast
    mock_messaging.send.return_value = 'projects/p/messages/1'
    return mock_messaging


def _json(response):
    resp_obj, status = response if isinstance(response, tuple) else (response, 200)
    return resp_obj.get_json(), status


def _records(db):
    return list(db._get_all('notifications').values())


def _iso(days_ago=0, hour=10):
    moment = datetime.now(IRAQ_TIMEZONE) - timedelta(days=days_ago)
    return to_iso(moment.replace(hour=hour, minute=0, second=0, microsecond=0))


class TestBroadcastIsRecorded:
    def _send(self, db, payload=None, uid='sender'):
        data = {'title': 'إعلان', 'body': 'العميل: مستشفى', 'userId': uid,
                'notificationAction': {'action': 'info'}}
        data.update(payload or {})
        with patch('modules.notifications.messaging', _patched_messaging()) as m:
            response = handle_send_notification_to_all({'uid': uid}, data, db)
        return _json(response), m

    def test_one_record_addressed_to_all(self, db):
        db._put('users', 'sender', {'name': 'أحمد'})
        db._put('users', 'rep', _user('token-rep'))

        (body, status), _ = self._send(db)

        assert status == 200
        [record] = _records(db)
        assert record['recipientIds'] == ['all']
        assert record['audience'] == 'all'
        assert record['kind'] == 'broadcast'
        assert record['senderId'] == 'sender'
        assert record['senderName'] == 'أحمد'
        assert record['source'] == 'app'
        assert record['title'] == 'إعلان'
        assert record['data'] == {'action': 'info'}
        assert is_contract_iso(record['createdAt'])
        assert record['delivery'] == {'successCount': 1, 'failureCount': 0}
        assert 'isRead' not in record, 'there is no read state'

    def test_the_push_carries_the_record_id(self, db):
        db._put('users', 'rep', _user('token-rep'))

        _, m = self._send(db)

        [record] = _records(db)
        sent_data = m.MulticastMessage.call_args.kwargs['data']
        assert sent_data['notificationId'] == record['id']
        assert sent_data['action'] == 'info'

    def test_sender_comes_from_the_token_not_the_payload(self, db):
        db._put('users', 'rep', _user('token-rep'))
        db._put('users', 'victim', _user('token-victim'))

        _, m = self._send(db, {'senderId': 'victim'}, uid='sender')

        [record] = _records(db)
        assert record['senderId'] == 'sender'
        assert set(m.MulticastMessage.call_args.kwargs['tokens']) == {
            'token-rep', 'token-victim'
        }, 'a payload senderId must not exclude someone else'

    def test_recorded_even_when_nobody_can_receive_the_push(self, db):
        db._put('users', 'opted-out', _user('token', preference=False))

        (body, status), _ = self._send(db)

        assert status == 200
        [record] = _records(db)
        assert record['recipientIds'] == ['all'], (
            'the preference turns off the phone alert, not the list'
        )
        assert record['delivery'] == {'successCount': 0, 'failureCount': 0}

    def test_dashboard_requests_are_told_apart(self, db):
        db._put('users', 'rep', _user('token-rep'))
        data = {'title': 't', 'body': 'b'}  # the dashboard sends no userId
        with patch('modules.notifications.messaging', _patched_messaging()):
            handle_send_notification_to_all({'uid': 'manager'}, data, db)

        [record] = _records(db)
        assert record['source'] == 'dashboard'

    def test_explicit_source_wins(self, db):
        self._send(db, {'source': 'dashboard'})

        [record] = _records(db)
        assert record['source'] == 'dashboard'


class TestSingleTargetIsRecorded:
    def _send(self, db, payload):
        data = {'title': 'تحديث حالة طلب', 'body': 'تمت الموافقة', **payload}
        with patch('modules.notifications.messaging', _patched_messaging()) as m:
            response = handle_send_notification({'uid': 'manager'}, data, db)
        return _json(response), m

    def test_addressed_by_id(self, db):
        db._put('users', 'rep', _user('token-rep'))

        (body, _), m = self._send(db, {'targetUserId': 'rep'})

        assert body['success'] is True
        [record] = _records(db)
        assert record['recipientIds'] == ['rep']
        assert record['audience'] == 'users'
        assert record['kind'] == 'direct'
        assert record['senderId'] == 'manager'
        assert m.Message.call_args.kwargs['data']['notificationId'] == record['id']

    def test_addressed_by_token_resolves_the_owner(self, db):
        db._put('users', 'rep', _user('token-rep'))

        self._send(db, {'fcmToken': 'token-rep'})

        [record] = _records(db)
        assert record['recipientIds'] == ['rep'], (
            'the dashboard still addresses some sends by raw token'
        )

    def test_recorded_when_the_recipient_has_no_device(self, db):
        db._put('users', 'rep', _user(token=None))

        (body, status), m = self._send(db, {'targetUserId': 'rep'})

        assert status == 200
        assert body['reason'] == 'no_valid_registration'
        m.send.assert_not_called()
        [record] = _records(db)
        assert record['recipientIds'] == ['rep']
        assert record['delivery']['reason'] == 'no_valid_registration'


class TestReviewIsRecorded:
    def test_every_named_recipient_except_the_reviewer(self, db):
        db._put('users', 'rep', _user('token-rep', role='salesRepresentative'))
        db._put('users', 'admin-1', _user('token-a1', role='admin'))
        db._put('users', 'admin-off', _user('token-a2', preference=False, role='admin'))
        db._put('users', 'reviewer', _user('token-r', role='salesManager'))
        data = {'title': 'مراجعة مدير المبيعات', 'body': 'b', 'reviewerRole': 'salesManager',
                'representativeId': 'rep',
                'notificationAction': {'action': 'open_daily_report', 'reportId': 'r1'}}

        with patch('modules.notifications.messaging', _patched_messaging()):
            handle_send_review_notification({'uid': 'reviewer'}, data, db)

        [record] = _records(db)
        assert set(record['recipientIds']) == {'rep', 'admin-1', 'admin-off'}, (
            'an unreachable phone is exactly when the list matters'
        )
        assert record['kind'] == 'review'
        assert record['senderId'] == 'reviewer'
        assert record['data'] == {'action': 'open_daily_report', 'reportId': 'r1'}


class TestDailyReminder:
    def _run(self, db):
        with patch('modules.notifications.messaging', _patched_messaging()) as m:
            response = handle_daily_notifications(db, days_offset=0)
        return _json(response), m

    def _task(self, db, task_id, assignee, **extra):
        today = datetime.now(IRAQ_TIMEZONE).replace(hour=9, minute=0, second=0, microsecond=0)
        db._put('tasks', task_id, {'title': task_id, 'assignedToId': assignee,
                                   'targetDate': to_iso(today), 'status': 'pending', **extra})

    def test_each_user_is_reminded_of_their_own_tasks_only(self, db):
        db._put('users', 'rep-1', _user('token-1', role='salesRepresentative'))
        db._put('users', 'rep-2', _user('token-2', role='salesRepresentative'))
        self._task(db, 'visit-a', 'rep-1')
        self._task(db, 'visit-b', 'rep-1')
        self._task(db, 'visit-c', 'rep-2')
        self._task(db, 'gone', 'rep-2', reviewState='deleted')

        (body, _), m = self._run(db)

        assert body['count'] == 2
        by_user = {r['recipientIds'][0]: r for r in _records(db)}
        assert set(by_user) == {'rep-1', 'rep-2'}
        assert set(by_user['rep-1']['data']['taskIds'].split(',')) == {'visit-a', 'visit-b'}
        assert by_user['rep-2']['data']['taskIds'] == 'visit-c', (
            'a deleted task is not a task to be reminded of'
        )
        assert by_user['rep-1']['source'] == 'system'
        assert by_user['rep-1']['senderId'] is None

    def test_a_second_run_does_not_remind_twice(self, db):
        db._put('users', 'rep-1', _user('token-1', role='salesRepresentative'))
        self._task(db, 'visit-a', 'rep-1')

        self._run(db)
        (body, _), m = self._run(db)

        assert body['count'] == 0
        m.send.assert_not_called()
        assert len(_records(db)) == 1

    def test_user_without_a_device_still_gets_the_record(self, db):
        db._put('users', 'rep-1', _user(token=None, role='salesRepresentative'))
        self._task(db, 'visit-a', 'rep-1')

        (body, _), _ = self._run(db)

        assert body['count'] == 0
        [record] = _records(db)
        assert record['delivery']['reason'] == 'no_valid_registration'


class TestApkUpdate:
    def test_recorded_for_every_android_user(self, db):
        db._put('users', 'droid', _user('token-d', platforms=['Android']))
        db._put('users', 'droid-offline', _user(None, platforms=['android']))
        db._put('users', 'iphone', _user('token-i', platforms=['ios']))

        with patch('modules.apk_manager.messaging') as m:
            sent, errors = _send_notifications_to_android_users('1.7.0', db, sender_id='admin')

        assert (sent, errors) == (1, [])
        [record] = _records(db)
        assert set(record['recipientIds']) == {'droid', 'droid-offline'}
        assert record['kind'] == 'apk_update'
        assert m.Message.call_args.kwargs['data']['notificationId'] == record['id']


class TestTodaysCount:
    def _seed(self, db, doc_id, recipients, sender='someone', days_ago=0, important=True):
        db._put('notifications', doc_id, {
            'id': doc_id, 'recipientIds': recipients, 'senderId': sender,
            'createdAt': _iso(days_ago), 'important': important,
        })

    def test_counts_what_the_user_sees_today(self, db):
        self._seed(db, 'mine', ['rep'])
        self._seed(db, 'everyone', ['all'])
        self._seed(db, 'own-broadcast', ['all'], sender='rep')
        self._seed(db, 'someone-else', ['other'])
        self._seed(db, 'yesterday', ['all'], days_ago=1)

        assert notification_log.todays_counts(db, ['rep']) == {'rep': 1}

    def test_only_important_ones_count(self, db):
        self._seed(db, 'completed', ['rep'])
        self._seed(db, 'date-changed', ['all'], important=False)

        assert notification_log.todays_counts(db, ['rep']) == {'rep': 1}

    def test_records_from_before_the_flag_are_classified_on_read(self, db):
        db._put('notifications', 'legacy-review', {
            'recipientIds': ['rep'], 'kind': 'review', 'createdAt': _iso(),
        })
        db._put('notifications', 'legacy-date', {
            'recipientIds': ['all'], 'kind': 'broadcast', 'source': 'app',
            'title': 'تغيير تاريخ مهمة', 'createdAt': _iso(),
        })

        assert notification_log.todays_counts(db, ['rep']) == {'rep': 1}

    def test_the_badge_is_sent_with_the_push(self, db):
        self._seed(db, 'earlier', ['rep'])
        db._put('users', 'rep', _user('token-rep', role='salesManager'))
        db._put('users', 'manager', _user('actor', role='salesRepresentative'))
        db._put('tasks', 't', {'status': 'completed'})
        data = {'title': 'إنجاز مهمة', 'body': 'b',
                'notificationAction': {'action': 'open_task', 'event': 'task_completed', 'taskId': 't'}}

        with patch('modules.notifications.messaging', _patched_messaging()) as m:
            handle_send_notification_to_all({'uid': 'manager'}, data, db)

        kwargs = m.MulticastMessage.call_args.kwargs
        assert kwargs['apns'].payload.aps.badge == 2, 'the earlier one and this one'
        assert kwargs['android'].notification.notification_count == 2

    def test_a_push_only_notification_leaves_the_count_alone(self, db):
        self._seed(db, 'earlier', ['rep'])
        db._put('users', 'rep', _user('token-rep'))
        data = {'title': 'تغيير تاريخ مهمة', 'body': 'b'}

        with patch('modules.notifications.messaging', _patched_messaging()) as m:
            handle_send_notification_to_all({'uid': 'manager'}, data, db)

        assert m.MulticastMessage.call_args.kwargs['apns'].payload.aps.badge == 1

    def test_recipients_with_different_counts_get_their_own_badge(self, db):
        self._seed(db, 'only-for-a', ['rep-a'])
        db._put('users', 'rep-a', _user('token-a', role='salesManager'))
        db._put('users', 'rep-b', _user('token-b', role='admin'))
        db._put('users', 'manager', _user('actor', role='salesRepresentative'))
        db._put('tasks', 't', {})
        data = {'title': 't', 'body': 'b', 'notificationAction': {'event': 'activity_added', 'taskId': 't'}}

        with patch('modules.notifications.messaging', _patched_messaging()) as m:
            handle_send_notification_to_all({'uid': 'manager'}, data, db)

        badges = {
            tuple(call.kwargs['tokens']): call.kwargs['apns'].payload.aps.badge
            for call in m.MulticastMessage.call_args_list
        }
        assert badges == {('token-a',): 2, ('token-b',): 1}


class TestImportance:
    """Spec 2039: only the events someone has to act on are listed."""

    def _record(self, db, *, title='t', kind='broadcast', data=None, source='app'):
        notification_id = notification_log.record(
            db, title=title, body='b', data=data or {}, kind=kind, source=source,
            sender_id=None, recipient_ids=['all'])
        return db._get_all('notifications')[notification_id]

    def test_a_named_important_event_is_listed(self, db):
        record = self._record(db, data={'action': 'open_task', 'event': 'task_completed'})

        assert record['event'] == 'task_completed'
        assert record['important'] is True

    def test_a_named_push_only_event_is_not(self, db):
        record = self._record(db, data={'event': 'deal_updated'})

        assert record['event'] == 'deal_updated'
        assert record['important'] is False

    def test_client_and_task_date_events_are_listed(self, db):
        for event in ('client_added', 'client_updated', 'task_date_set',
                      'task_date_changed', 'task_date_reset'):
            assert self._record(db, data={'event': event})['important'] is True

    def test_every_review_is_listed(self, db):
        assert self._record(db, kind='review', source='dashboard')['important'] is True

    def test_older_app_builds_are_recognised_by_title(self, db):
        for title in ('إنجاز مهمة', 'إضافة نشاط', 'إضافة سجل دعم فني جديد',
                      '🔔 Add: Visit Technical Support', '🔔 Add: Main Opportunity',
                      'تمت مراجعة التقرير'):
            assert self._record(db, title=title)['important'] is True, title

    def test_unnamed_sends_are_push_only(self, db):
        for title in ('تغيير تاريخ مهمة', 'حذف مهمة', 'تذكير بالمهام'):
            assert self._record(db, title=title)['important'] is False, title

    def test_a_dashboard_title_alone_does_not_make_it_important(self, db):
        # An admin's free-text message titled like an event is still a message.
        assert self._record(db, title='إنجاز مهمة', kind='direct',
                            source='dashboard')['important'] is False


class TestCreateOnly:
    """Spec 2038 (research R16): once-only ids are claimed with create()."""

    def _record(self, db, **kw):
        return notification_log.record(
            db, title='t', body='b', data={}, kind='review_request',
            source='system', sender_id=None, recipient_ids=['u1'],
            notification_id='review_request-report_1', **kw)

    def test_first_create_only_record_is_written(self, db):
        assert self._record(db, create_only=True) == 'review_request-report_1'
        assert 'review_request-report_1' in db._get_all('notifications')

    def test_second_create_only_record_reports_already_recorded(self, db):
        self._record(db, create_only=True)
        assert self._record(db, create_only=True) is notification_log.ALREADY_RECORDED
        assert len(db._get_all('notifications')) == 1

    def test_a_failed_write_is_none_not_already_recorded(self):
        broken = MagicMock()
        broken.collection.side_effect = RuntimeError('boom')
        assert self._record(broken, create_only=True) is None

    def test_default_record_still_overwrites(self, db):
        self._record(db)
        self._record(db)
        assert len(db._get_all('notifications')) == 1
