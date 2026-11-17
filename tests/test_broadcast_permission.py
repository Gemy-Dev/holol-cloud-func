"""SEC-10: a free-form broadcast to every user is the sendNotification grant's.

Anyone else's broadcast reaches the managers only, so no account can push
arbitrary text to everyone, and a deal's value or a support problem the
field app announces no longer reaches every representative.
"""
from unittest.mock import MagicMock, patch

from modules.notifications import handle_send_notification_to_all


class _Response:
    def __init__(self, n):
        self.responses = [MagicMock(success=True) for _ in range(n)]
        self.success_count = n
        self.failure_count = 0


def _user(role, token, permissions=()):
    return {
        'role': role,
        'permissions': list(permissions),
        'fcmToken': token,
        'receiveEmailNotifications': True,
        'isActive': True,
    }


def _seed(db, sender_permissions=(), sender_role='salesRepresentative'):
    db._put('users', 'sender', _user(sender_role, 't-sender', sender_permissions))
    db._put('users', 'admin', _user('admin', 't-admin'))
    db._put('users', 'manager', _user('مدير المبيعات', 't-manager'))
    db._put('users', 'rep', _user('salesRepresentative', 't-rep'))


def _targeted(db):
    """Broadcast from 'sender'; return the tokens the push went to."""
    with patch('modules.notifications.messaging') as messaging:
        messaging.UnregisteredError = type('U', (Exception,), {})
        targeted = []

        def send(message):
            tokens = messaging.MulticastMessage.call_args.kwargs.get('tokens', [])
            targeted.extend(tokens)
            return _Response(len(tokens))

        messaging.send_each_for_multicast.side_effect = send
        handle_send_notification_to_all(
            {'uid': 'sender'},
            {'title': 'تم إنشاء صفقة', 'body': 'القيمة 5000', 'source': 'app'},
            db,
        )
    return set(targeted)


def test_without_the_grant_only_managers_receive_it(db):
    _seed(db)
    assert _targeted(db) == {'t-admin', 't-manager'}


def test_with_the_grant_everyone_else_receives_it(db):
    _seed(db, sender_permissions=['sendNotification'])
    assert _targeted(db) == {'t-admin', 't-manager', 't-rep'}


def test_an_admin_sender_reaches_everyone(db):
    _seed(db, sender_role='admin')
    assert _targeted(db) == {'t-admin', 't-manager', 't-rep'}


def test_the_record_names_only_the_managers_it_went_to(db):
    _seed(db)
    _targeted(db)
    records = list(db._get_all('notifications').values())
    assert len(records) == 1
    assert sorted(records[0]['recipientIds']) == ['admin', 'manager']
