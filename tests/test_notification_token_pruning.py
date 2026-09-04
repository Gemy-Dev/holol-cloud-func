"""Tests for US1: invalid device registrations are pruned by the fan-out.

The multicast fan-out used to log per-token failures and discard them, so a
token that FCM had already reported as unregistered was retried on every
send, forever. Once push is the only delivery channel those dead registrations
drag the delivery success rate down and hide the fact that a real user has
stopped receiving anything.
"""
from unittest.mock import MagicMock, patch

from tests.conftest import FakeFirestore
from modules.notifications import handle_send_notification_to_all


class _FakeSendResponse:
    """One entry of a multicast response."""

    def __init__(self, success: bool, exception=None):
        self.success = success
        self.exception = exception


class _FakeMulticastResponse:
    def __init__(self, responses: list):
        self.responses = responses
        self.success_count = sum(1 for r in responses if r.success)
        self.failure_count = sum(1 for r in responses if not r.success)


class _FakeUnregisteredError(Exception):
    """Stands in for firebase_admin.messaging.UnregisteredError."""


def _seed_users(db: FakeFirestore, users: dict):
    for uid, data in users.items():
        db._put('users', uid, data)


def _call(db, outcomes: list, data=None):
    """Run the fan-out with a stubbed messaging layer.

    ``outcomes`` is one boolean per token, in the order the handler collects
    them: True delivered, False reported unregistered.
    """
    payload = {
        'title': 'عنوان',
        'body': 'محتوى',
        'senderId': 'sender',
    }
    payload.update(data or {})

    with patch('modules.notifications.messaging') as mock_messaging:
        mock_messaging.UnregisteredError = _FakeUnregisteredError
        mock_messaging.MulticastMessage = MagicMock()
        mock_messaging.Notification = MagicMock()
        mock_messaging.send_each_for_multicast.return_value = _FakeMulticastResponse(
            [
                _FakeSendResponse(True)
                if ok
                else _FakeSendResponse(False, _FakeUnregisteredError('not registered'))
                for ok in outcomes
            ]
        )
        response = handle_send_notification_to_all({'uid': 'sender'}, payload, db)

    if isinstance(response, tuple):
        resp_obj, status = response
    else:
        resp_obj, status = response, 200
    return resp_obj.get_json(), status


class TestInvalidTokensArePruned:
    def test_unregistered_token_is_cleared_from_its_user(self, db):
        _seed_users(
            db,
            {
                'alive': {'fcmToken': 'token-alive', 'receiveEmailNotifications': True, 'isActive': True},
                'dead': {'fcmToken': 'token-dead', 'receiveEmailNotifications': True, 'isActive': True},
            },
        )

        body, status = _call(db, outcomes=[True, False])

        assert status == 200
        stored = db._get_all('users')
        assert stored['alive']['fcmToken'] == 'token-alive'
        assert not stored['dead'].get('fcmToken'), (
            'a registration FCM reports as unregistered must not survive the send'
        )

    def test_pruned_count_is_reported(self, db):
        _seed_users(
            db,
            {
                'a': {'fcmToken': 'token-a', 'receiveEmailNotifications': True, 'isActive': True},
                'b': {'fcmToken': 'token-b', 'receiveEmailNotifications': True, 'isActive': True},
            },
        )

        body, _ = _call(db, outcomes=[False, False])

        assert body['prunedTokens'] == 2, 'pruning must be observable, not a silent side effect'

    def test_nothing_is_pruned_when_every_send_succeeds(self, db):
        _seed_users(
            db,
            {
                'a': {'fcmToken': 'token-a', 'receiveEmailNotifications': True, 'isActive': True},
                'b': {'fcmToken': 'token-b', 'receiveEmailNotifications': True, 'isActive': True},
            },
        )

        body, _ = _call(db, outcomes=[True, True])

        assert body['prunedTokens'] == 0
        assert body['successCount'] == 2
        stored = db._get_all('users')
        assert stored['a']['fcmToken'] == 'token-a'
        assert stored['b']['fcmToken'] == 'token-b'

    def test_a_non_registration_failure_leaves_the_token_alone(self, db):
        """A transient send error is not evidence the device is gone."""
        _seed_users(
            db,
            {'a': {'fcmToken': 'token-a', 'receiveEmailNotifications': True, 'isActive': True}},
        )

        with patch('modules.notifications.messaging') as mock_messaging:
            mock_messaging.UnregisteredError = _FakeUnregisteredError
            mock_messaging.MulticastMessage = MagicMock()
            mock_messaging.Notification = MagicMock()
            mock_messaging.send_each_for_multicast.return_value = _FakeMulticastResponse(
                [_FakeSendResponse(False, RuntimeError('temporarily unavailable'))]
            )
            response = handle_send_notification_to_all(
                {'uid': 'sender'},
                {'title': 'عنوان', 'body': 'محتوى', 'senderId': 'sender'},
                db,
            )

        resp_obj = response[0] if isinstance(response, tuple) else response
        body = resp_obj.get_json()

        assert body['prunedTokens'] == 0
        assert db._get_all('users')['a']['fcmToken'] == 'token-a'
