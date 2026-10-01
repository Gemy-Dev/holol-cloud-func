"""send_to_roles / send_to_user (spec 2038, contracts/cloud-actions.md)."""
from unittest.mock import MagicMock, patch

from modules import notification_log
from modules.notifications import send_to_roles, send_to_user


class _Resp:
    def __init__(self, success=True):
        self.success = success
        self.exception = None


class _Multi:
    def __init__(self, n):
        self.responses = [_Resp() for _ in range(n)]
        self.success_count = n
        self.failure_count = 0


def _user(role, token='tok', pref=True, active=True):
    return {'role': role, 'fcmToken': token, 'receiveEmailNotifications': pref, 'isActive': active}


def _send(fn, db, **kw):
    sent = {}
    with patch('modules.notifications.messaging') as m:
        m.UnregisteredError = type('U', (Exception,), {})
        m.SenderIdMismatchError = type('S', (Exception,), {})
        m.Notification = MagicMock()

        def _multicast(**kwargs):
            sent.setdefault('tokens', []).extend(kwargs.get('tokens', []))
            return MagicMock()
        m.MulticastMessage.side_effect = _multicast

        def _each(message):
            return _Multi(len(sent.get('tokens', [])))
        m.send_each_for_multicast.side_effect = _each
        result = fn(db, title='T', body='B', message_data={'action': 'x'},
                    kind=kw.pop('kind', 'review_request'), source='system', **kw)
    return result, sent.get('tokens', [])


def test_roles_record_every_addressed_user_even_unreachable_but_push_only_reachable(db):
    db._put('users', 'm1', _user('salesManager', 'a'))
    db._put('users', 'm2', _user('salesManager', 'b', pref=False))
    db._put('users', 'a1', _user('admin', 'c'))
    db._put('users', 'r1', _user('salesRepresentative', 'd'))
    result, tokens = _send(send_to_roles, db, roles=['salesManager', 'admin'], actor_id=None)
    assert sorted(tokens) == ['a', 'c']
    doc = next(iter(db._get_all('notifications').values()))
    assert sorted(doc['recipientIds']) == ['a1', 'm1', 'm2']
    assert {u['userId'] for u in result['unreachable']} == {'m2'}


def test_roles_exclude_the_actor_from_record_and_push(db):
    db._put('users', 'm1', _user('salesManager', 'a'))
    db._put('users', 'a1', _user('admin', 'c'))
    _, tokens = _send(send_to_roles, db, roles=['salesManager', 'admin'], actor_id='m1')
    assert tokens == ['c']
    doc = next(iter(db._get_all('notifications').values()))
    assert doc['recipientIds'] == ['a1']


def test_roles_create_only_skips_when_the_id_is_taken(db):
    db._put('users', 'm1', _user('salesManager', 'a'))
    kw = dict(roles=['salesManager'], actor_id=None,
              notification_id='review_request-report_1', create_only=True)
    first, t1 = _send(send_to_roles, db, **kw)
    second, t2 = _send(send_to_roles, db, **kw)
    assert t1 == ['a'] and t2 == []
    assert second == {'skipped': 'already_notified'}
    assert len(db._get_all('notifications')) == 1


def test_roles_push_anyway_when_the_record_write_fails(db):
    db._put('users', 'm1', _user('salesManager', 'a'))
    with patch.object(notification_log, 'record', return_value=None):
        _, tokens = _send(send_to_roles, db, roles=['salesManager'], actor_id=None,
                          notification_id='x', create_only=True)
    assert tokens == ['a']


def test_user_is_recorded_even_when_push_is_off(db):
    db._put('users', 'u1', _user('salesManager', 'a', pref=False))
    result, tokens = _send(send_to_user, db, user_id='u1', actor_id=None, kind='review_digest')
    assert tokens == []
    doc = next(iter(db._get_all('notifications').values()))
    assert doc['recipientIds'] == ['u1'] and doc['kind'] == 'review_digest'
    assert result['unreachable'][0]['reason'] == 'preference_off'


def test_user_push_reaches_a_reachable_user(db):
    db._put('users', 'u1', _user('admin', 'a'))
    _, tokens = _send(send_to_user, db, user_id='u1', actor_id=None)
    assert tokens == ['a']


def test_user_unknown_id_is_reported_not_found_and_not_recorded(db):
    result, tokens = _send(send_to_user, db, user_id='ghost', actor_id=None)
    assert tokens == []
    assert result['unreachable'] == [{'userId': 'ghost', 'reason': 'not_found'}]
    assert db._get_all('notifications') == {}


def test_a_push_that_fails_after_the_claim_is_recorded_not_raised(db):
    db._put('users', 'm1', _user('salesManager', 'a'))
    with patch('modules.notifications._send_multicast', side_effect=RuntimeError('fcm down')):
        result = send_to_roles(db, roles=['salesManager'], title='T', body='B',
                               message_data={'action': 'x'}, kind='review_request',
                               source='system', actor_id=None,
                               notification_id='review_request-x', create_only=True)
    assert result['successCount'] == 0 and result['failureCount'] == 1
    doc = db._get_all('notifications')['review_request-x']
    assert doc['delivery'] == {'successCount': 0, 'failureCount': 1, 'reason': 'send_error'}
