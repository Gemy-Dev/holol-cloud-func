"""Tests for US2: a notification reaches exactly the configured audience.

The fan-out used to stream the whole ``users`` collection and send to every
document that happened to carry an ``fcmToken``. Nothing consulted the
notification preference and nothing consulted ``isActive``, so a user who had
switched delivery off, and a user whose account had been deactivated, both
kept receiving every business event in the system.

The audience is defined in data-model.md §2:

    receiveEmailNotifications == true
    AND isActive == true
    AND fcmToken is present and non-empty
    AND document id != senderId

The first two are a Firestore composite query; the last two are applied in
application code.
"""
from unittest.mock import MagicMock, patch

from tests.conftest import FakeFirestore
from modules.notifications import handle_send_notification_to_all


class _FakeSendResponse:
    def __init__(self, success: bool, exception=None):
        self.success = success
        self.exception = exception


class _FakeMulticastResponse:
    def __init__(self, responses: list):
        self.responses = responses
        self.success_count = sum(1 for r in responses if r.success)
        self.failure_count = sum(1 for r in responses if not r.success)


def _recipient(token='token', preference=True, active=True):
    """A user document, healthy by default so each test varies one thing."""
    return {
        'fcmToken': token,
        'receiveEmailNotifications': preference,
        'isActive': active,
    }


def _call(db, payload=None):
    """Run the fan-out and return (body, status, tokens_actually_targeted)."""
    data = {'title': 'عنوان', 'body': 'محتوى', 'senderId': 'sender'}
    data.update(payload or {})

    with patch('modules.notifications.messaging') as mock_messaging:
        mock_messaging.UnregisteredError = type('U', (Exception,), {})
        mock_messaging.Notification = MagicMock()
        mock_messaging.MulticastMessage = MagicMock()

        def _capture(*args, **kwargs):
            # The handler builds MulticastMessage(tokens=[...]); recover the
            # list it actually chose so the assertions test the audience
            # rather than the send count.
            call = mock_messaging.MulticastMessage.call_args
            targeted = list(call.kwargs.get('tokens', [])) if call else []
            _capture.tokens = targeted
            return _FakeMulticastResponse(
                [_FakeSendResponse(True) for _ in targeted]
            )

        _capture.tokens = []
        mock_messaging.send_each_for_multicast.side_effect = _capture

        response = handle_send_notification_to_all({'uid': 'sender'}, data, db)

    resp_obj, status = response if isinstance(response, tuple) else (response, 200)
    return resp_obj.get_json(), status, _capture.tokens


def _seed(db: FakeFirestore, users: dict):
    for uid, doc in users.items():
        db._put('users', uid, doc)


class TestPreferenceGatesDelivery:
    def test_user_with_preference_off_is_excluded(self, db):
        _seed(db, {
            'wants': _recipient('token-wants'),
            'declined': _recipient('token-declined', preference=False),
        })

        body, status, tokens = _call(db)

        assert status == 200
        assert tokens == ['token-wants'], (
            'a user who switched notifications off must not be sent one'
        )
        assert body['successCount'] == 1

    def test_missing_preference_is_treated_as_no_consent(self, db):
        # data-model.md §1: "A missing preference is not consent."
        _seed(db, {
            'wants': _recipient('token-wants'),
            'never-configured': {'fcmToken': 'token-unset', 'isActive': True},
        })

        _, _, tokens = _call(db)

        assert tokens == ['token-wants']


class TestInactiveAccountsAreExcluded:
    def test_deactivated_account_is_excluded(self, db):
        _seed(db, {
            'current': _recipient('token-current'),
            'former': _recipient('token-former', active=False),
        })

        _, _, tokens = _call(db)

        assert tokens == ['token-current'], (
            'a deactivated account keeps its token but must stop receiving'
        )

    def test_missing_isActive_is_excluded(self, db):
        _seed(db, {
            'current': _recipient('token-current'),
            'unknown': {'fcmToken': 'token-unknown', 'receiveEmailNotifications': True},
        })

        _, _, tokens = _call(db)

        assert tokens == ['token-current']


class TestSenderIsExcluded:
    def test_sender_never_receives_their_own_action(self, db):
        _seed(db, {
            'sender': _recipient('token-sender'),
            'other': _recipient('token-other'),
        })

        _, _, tokens = _call(db)

        assert tokens == ['token-other']

    def test_sender_falls_back_to_the_decoded_token_uid(self, db):
        # No senderId in the payload — the caller's uid must still be honoured.
        _seed(db, {
            'sender': _recipient('token-sender'),
            'other': _recipient('token-other'),
        })

        data = {'title': 't', 'body': 'b'}
        with patch('modules.notifications.messaging') as mock_messaging:
            mock_messaging.UnregisteredError = type('U', (Exception,), {})
            mock_messaging.Notification = MagicMock()
            mock_messaging.MulticastMessage = MagicMock()
            mock_messaging.send_each_for_multicast.return_value = (
                _FakeMulticastResponse([_FakeSendResponse(True)])
            )
            handle_send_notification_to_all({'uid': 'sender'}, data, db)
            targeted = mock_messaging.MulticastMessage.call_args.kwargs['tokens']

        assert targeted == ['token-other']


class TestRegistrationIsRequired:
    def test_empty_token_is_excluded(self, db):
        _seed(db, {
            'registered': _recipient('token-registered'),
            'blank': _recipient(''),
        })

        _, _, tokens = _call(db)

        assert tokens == ['token-registered']

    def test_null_token_is_excluded(self, db):
        _seed(db, {
            'registered': _recipient('token-registered'),
            'none': _recipient(None),
        })

        _, _, tokens = _call(db)

        assert tokens == ['token-registered']

    def test_opted_in_without_a_token_is_not_a_failure(self, db):
        # data-model.md §1: they are simply not in the audience. SC-009
        # measures success against registered devices, not everyone opted in.
        _seed(db, {
            'registered': _recipient('token-registered'),
            'no-device': _recipient(None),
        })

        body, status, _ = _call(db)

        assert status == 200
        assert body['successCount'] == 1
        assert body['failureCount'] == 0


class TestEmptyAudience:
    def test_empty_audience_returns_200_not_404(self, db):
        # Contract change: FR-023 requires the record to save regardless, and
        # a 404 reads as a failure to the caller.
        _seed(db, {'declined': _recipient('token-declined', preference=False)})

        body, status, _ = _call(db)

        assert status == 200, 'an empty audience is a valid outcome, not an error'
        assert body['success'] is True
        assert body['successCount'] == 0
        assert body['totalTokens'] == 0

    def test_no_users_at_all_returns_200(self, db):
        body, status, _ = _call(db)

        assert status == 200
        assert body['successCount'] == 0

    def test_no_send_is_attempted_for_an_empty_audience(self, db):
        _seed(db, {'declined': _recipient('token-declined', preference=False)})

        with patch('modules.notifications.messaging') as mock_messaging:
            mock_messaging.UnregisteredError = type('U', (Exception,), {})
            mock_messaging.Notification = MagicMock()
            mock_messaging.MulticastMessage = MagicMock()
            handle_send_notification_to_all(
                {'uid': 'sender'},
                {'title': 't', 'body': 'b', 'senderId': 'sender'},
                db,
            )

            mock_messaging.send_each_for_multicast.assert_not_called()


class TestTheQueryIsFilteredNotAFullScan:
    """The scan is a cost and latency problem, not only a correctness one.

    Asserting on the query shape stops a future edit from restoring the full
    scan and filtering in Python, which would still pass every audience test
    above while re-introducing the regression.

    These assert on the calls made rather than raising from inside the handler:
    ``handle_send_notification_to_all`` catches ``Exception`` broadly, so an
    assertion raised in a stub is swallowed into a 500 and the test passes
    while proving nothing.
    """

    def test_the_audience_is_selected_with_where_not_a_full_stream(self, db):
        # Derived from the live object, not imported: pytest loads conftest.py
        # as top-level ``conftest`` while ``from tests.conftest import ...``
        # builds a second module with different class objects, and patching
        # those would not touch the instances the fixture hands out.
        collection = db.collection('users')
        _FakeCollection = type(collection)
        _FakeQuery = type(collection.where('x', '==', 'y'))

        _seed(db, {'a': _recipient('token-a')})
        where_calls = []

        # Chaining moves off the collection after the first predicate, so both
        # classes have to be recorded to see the whole filter.
        def _recorder(cls):
            real = cls.where

            def _record(self, field, op, value):
                where_calls.append((field, op, value))
                return real(self, field, op, value)

            return _record

        with patch.object(_FakeCollection, 'where', _recorder(_FakeCollection)):
            with patch.object(_FakeQuery, 'where', _recorder(_FakeQuery)):
                with patch.object(
                    _FakeCollection, 'stream', autospec=True
                ) as full_scan:
                    body, status, _ = _call(db)

        assert status == 200, f'handler errored instead of querying: {body}'
        assert not full_scan.called, (
            'the fan-out must not stream the whole users collection'
        )
        assert ('receiveEmailNotifications', '==', True) in where_calls
        assert ('isActive', '==', True) in where_calls


class TestRequestValidation:
    def test_missing_title_is_rejected(self, db):
        body, status, _ = _call(db, {'title': None})

        assert status == 400
        assert body['success'] is False

    def test_missing_body_is_rejected(self, db):
        body, status, _ = _call(db, {'body': None})

        assert status == 400
        assert body['success'] is False
