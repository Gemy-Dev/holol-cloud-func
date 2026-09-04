"""Tests for US2: single-recipient delivery addressed by user id.

``sendNotification`` used to require the caller to supply an ``fcmToken``.
The field app has no read access to another user's document, so it could never
satisfy that and always fell back to broadcasting — which is how
"تمت مراجعة تقريرك اليومي" reached every user at once, each told their own
report had been reviewed.

Accepting ``targetUserId`` and resolving the token server-side is what makes
one-to-one delivery possible at all. ``fcmToken`` stays accepted so existing
callers keep working.

Contract: contracts/cloud-function-actions.md, "Changed: sendNotification".
"""
from unittest.mock import MagicMock, patch

from modules.notifications import handle_send_notification


class _FakeUnregisteredError(Exception):
    """Stands in for firebase_admin.messaging.UnregisteredError."""


def _call(db, payload, send_result='projects/p/messages/1', send_raises=None):
    data = {'title': 'عنوان', 'body': 'محتوى'}
    data.update(payload)

    with patch('modules.notifications.messaging') as mock_messaging:
        mock_messaging.UnregisteredError = _FakeUnregisteredError
        mock_messaging.SenderIdMismatchError = type(
            'SenderIdMismatchError', (Exception,), {}
        )
        mock_messaging.Notification = MagicMock()
        mock_messaging.Message = MagicMock()
        if send_raises is not None:
            mock_messaging.send.side_effect = send_raises
        else:
            mock_messaging.send.return_value = send_result

        response = handle_send_notification({'uid': 'caller'}, data, db)
        sent_token = None
        if mock_messaging.Message.call_args:
            sent_token = mock_messaging.Message.call_args.kwargs.get('token')

    resp_obj, status = response if isinstance(response, tuple) else (response, 200)
    return resp_obj.get_json(), status, sent_token


class TestAddressingByUserId:
    def test_token_is_resolved_from_the_target_user_document(self, db):
        db._put('users', 'rep', {'fcmToken': 'token-rep', 'isActive': True})

        body, status, sent_token = _call(db, {'targetUserId': 'rep'})

        assert status == 200
        assert body['success'] is True
        assert sent_token == 'token-rep', (
            'the recipient is addressed by id; the caller never sees the token'
        )

    def test_only_the_target_is_addressed(self, db):
        db._put('users', 'rep', {'fcmToken': 'token-rep', 'isActive': True})
        db._put('users', 'someone-else', {'fcmToken': 'token-other', 'isActive': True})

        _, _, sent_token = _call(db, {'targetUserId': 'rep'})

        assert sent_token == 'token-rep'

    def test_explicit_fcm_token_still_works(self, db):
        body, status, sent_token = _call(db, {'fcmToken': 'token-direct'})

        assert status == 200
        assert body['success'] is True
        assert sent_token == 'token-direct'

    def test_target_user_id_wins_over_a_supplied_token(self, db):
        db._put('users', 'rep', {'fcmToken': 'token-rep', 'isActive': True})

        _, _, sent_token = _call(
            db, {'targetUserId': 'rep', 'fcmToken': 'token-stale'}
        )

        assert sent_token == 'token-rep', (
            'a token resolved now beats one the caller cached earlier'
        )


class TestMissingRegistrationIsNotAnError:
    def test_target_without_a_token_reports_no_valid_registration(self, db):
        db._put('users', 'rep', {'isActive': True})

        body, status, sent_token = _call(db, {'targetUserId': 'rep'})

        assert status == 200, 'having no device is not an error condition'
        assert body['success'] is False
        assert body['reason'] == 'no_valid_registration'
        assert sent_token is None, 'nothing should be sent'

    def test_target_with_an_empty_token_reports_no_valid_registration(self, db):
        db._put('users', 'rep', {'fcmToken': '', 'isActive': True})

        body, status, _ = _call(db, {'targetUserId': 'rep'})

        assert status == 200
        assert body['reason'] == 'no_valid_registration'

    def test_unknown_target_reports_no_valid_registration(self, db):
        body, status, _ = _call(db, {'targetUserId': 'ghost'})

        assert status == 200
        assert body['reason'] == 'no_valid_registration'


class TestUnregisteredTokenIsPruned:
    def test_token_reported_unregistered_is_cleared_from_the_target(self, db):
        db._put('users', 'rep', {'fcmToken': 'token-dead', 'isActive': True})

        body, status, _ = _call(
            db,
            {'targetUserId': 'rep'},
            send_raises=_FakeUnregisteredError('not registered'),
        )

        assert status == 200
        assert body['success'] is False
        assert body['reason'] == 'token_pruned'
        assert not db._get_all('users')['rep'].get('fcmToken'), (
            'a dead registration must not survive the send that proved it dead'
        )

    def test_other_fields_on_the_target_survive_pruning(self, db):
        db._put('users', 'rep', {'fcmToken': 'token-dead', 'isActive': True,
                                 'name': 'مندوب'})

        _call(
            db,
            {'targetUserId': 'rep'},
            send_raises=_FakeUnregisteredError('gone'),
        )

        stored = db._get_all('users')['rep']
        assert stored['name'] == 'مندوب'
        assert stored['isActive'] is True

    def test_direct_token_path_reports_pruned_without_an_owner_to_clear(self, db):
        # No targetUserId means no document to clear, but the caller still
        # needs the documented outcome rather than a 400.
        body, status, _ = _call(
            db,
            {'fcmToken': 'token-dead'},
            send_raises=_FakeUnregisteredError('gone'),
        )

        assert status == 200
        assert body['reason'] == 'token_pruned'


class TestRequestValidation:
    def test_neither_target_nor_token_is_rejected(self, db):
        body, status, _ = _call(db, {})

        assert status == 400
        assert body['success'] is False
        assert body['error'] == 'targetUserId or fcmToken is required'

    def test_missing_title_is_rejected(self, db):
        body, status, _ = _call(db, {'fcmToken': 't', 'title': None})

        assert status == 400
        assert body['success'] is False

    def test_missing_body_is_rejected(self, db):
        body, status, _ = _call(db, {'fcmToken': 't', 'body': None})

        assert status == 400
        assert body['success'] is False
