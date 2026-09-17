"""Tests for spec 2035 US4: a review notifies the representative and the other manager.

FR-015: a sales manager's review reaches the representative who carried out
the task and the system administrators.
FR-016: an administrator's review reaches the representative and the sales
managers.

Neither review was ever delivered to the second named recipient: the only
review notification told a report's author "your report was reviewed" and
reached nobody else. The field app cannot read other users' documents, so the
recipients are resolved here, by role, under the same preference and
active-account rules as every other notification.
"""
from unittest.mock import MagicMock, patch

from tests.conftest import FakeFirestore
from modules.notifications import handle_send_review_notification


class _FakeSendResponse:
    def __init__(self, success: bool, exception=None):
        self.success = success
        self.exception = exception


class _FakeMulticastResponse:
    def __init__(self, responses: list):
        self.responses = responses
        self.success_count = sum(1 for r in responses if r.success)
        self.failure_count = sum(1 for r in responses if not r.success)


def _user(role, token, preference=True, active=True):
    """A user document, reachable by default so each test varies one thing."""
    return {
        'role': role,
        'fcmToken': token,
        'receiveEmailNotifications': preference,
        'isActive': active,
    }


def _call(db, payload=None, reviewer='reviewer'):
    """Run the handler and return (body, status, tokens_actually_targeted)."""
    data = {
        'title': 'مراجعة مدير المبيعات',
        'body': 'محتوى',
        'reviewerRole': 'salesManager',
        'representativeId': 'rep',
    }
    data.update(payload or {})

    with patch('modules.notifications.messaging') as mock_messaging:
        mock_messaging.UnregisteredError = type('U', (Exception,), {})
        mock_messaging.SenderIdMismatchError = type('S', (Exception,), {})
        mock_messaging.Notification = MagicMock()
        mock_messaging.MulticastMessage = MagicMock()

        def _capture(*args, **kwargs):
            call = mock_messaging.MulticastMessage.call_args
            targeted = list(call.kwargs.get('tokens', [])) if call else []
            _capture.tokens = targeted
            return _FakeMulticastResponse(
                [_FakeSendResponse(True) for _ in targeted]
            )

        _capture.tokens = []
        mock_messaging.send_each_for_multicast.side_effect = _capture

        response = handle_send_review_notification({'uid': reviewer}, data, db)

    resp_obj, status = response if isinstance(response, tuple) else (response, 200)
    return resp_obj.get_json(), status, _capture.tokens


def _team(**overrides):
    """A representative, two admins and two sales managers."""
    users = {
        'rep': _user('salesRepresentative', 'token-rep'),
        'admin-1': _user('admin', 'token-admin-1'),
        'admin-2': _user('admin', 'token-admin-2'),
        'manager-1': _user('salesManager', 'token-manager-1'),
        'manager-2': _user('salesManager', 'token-manager-2'),
    }
    users.update(overrides)
    return _db(users)


def _db(users):
    db = FakeFirestore()
    for user_id, user in users.items():
        db._put('users', user_id, user)
    return db


class TestRecipients:
    def test_sales_manager_review_reaches_representative_and_admins(self):
        _, status, tokens = _call(_team())

        assert status == 200
        assert sorted(tokens) == ['token-admin-1', 'token-admin-2', 'token-rep']

    def test_admin_review_reaches_representative_and_sales_managers(self):
        _, _, tokens = _call(_team(), {'reviewerRole': 'admin'})

        assert sorted(tokens) == ['token-manager-1', 'token-manager-2', 'token-rep']

    def test_older_role_spellings_are_recognised(self):
        # Accounts created before the dashboard wrote enum names carry the
        # English or Arabic label instead.
        db = _db({
            'rep': _user('salesRepresentative', 'token-rep'),
            'english': _user('Admin', 'token-english'),
            'arabic': _user('مسؤول النظام', 'token-arabic'),
        })

        _, _, tokens = _call(db)

        assert sorted(tokens) == ['token-arabic', 'token-english', 'token-rep']

    def test_reviewer_is_not_notified_of_their_own_review(self):
        # An administrator may sign the sales-manager slot; they still hold the
        # admin role, and must not be told about what they just did (FR-022).
        _, _, tokens = _call(_team(), reviewer='admin-1')

        assert 'token-admin-1' not in tokens
        assert sorted(tokens) == ['token-admin-2', 'token-rep']

    def test_representative_who_reviewed_their_own_report_is_not_notified(self):
        _, _, tokens = _call(_team(), reviewer='rep')

        assert 'token-rep' not in tokens

    def test_a_person_in_both_roles_is_notified_once(self):
        db = _team(rep=_user('admin', 'token-rep'))

        _, _, tokens = _call(db)

        assert tokens.count('token-rep') == 1


class TestAudienceRules:
    def test_unreachable_representative_does_not_stop_the_managers(self):
        body, _, tokens = _call(_team(rep=_user('salesRepresentative', '')))

        assert sorted(tokens) == ['token-admin-1', 'token-admin-2']
        assert {'userId': 'rep', 'reason': 'no_valid_registration'} in body['unreachable']

    def test_preference_off_is_respected(self):
        body, _, tokens = _call(
            _team(**{'admin-2': _user('admin', 'token-admin-2', preference=False)})
        )

        assert 'token-admin-2' not in tokens
        assert {'userId': 'admin-2', 'reason': 'preference_off'} in body['unreachable']

    def test_inactive_account_is_skipped(self):
        body, _, tokens = _call(
            _team(**{'admin-1': _user('admin', 'token-admin-1', active=False)})
        )

        assert 'token-admin-1' not in tokens
        assert {'userId': 'admin-1', 'reason': 'inactive'} in body['unreachable']

    def test_missing_representative_is_reported_not_fatal(self):
        body, status, tokens = _call(_team(), {'representativeId': 'ghost'})

        assert status == 200
        assert sorted(tokens) == ['token-admin-1', 'token-admin-2']
        assert {'userId': 'ghost', 'reason': 'not_found'} in body['unreachable']

    def test_no_reachable_recipient_is_a_successful_no_op(self):
        db = _db({'rep': _user('salesRepresentative', '')})

        body, status, tokens = _call(db)

        assert status == 200
        assert body['success'] is True
        assert body['totalTokens'] == 0
        assert tokens == []


class TestValidation:
    def test_unknown_reviewer_role_is_rejected(self):
        _, status, tokens = _call(_team(), {'reviewerRole': 'salesRepresentative'})

        assert status == 400
        assert tokens == []

    def test_title_and_body_are_required(self):
        _, status, _ = _call(_team(), {'title': ''})

        assert status == 400
