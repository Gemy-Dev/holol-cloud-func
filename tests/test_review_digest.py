"""The morning summary of pending reviews (spec 2038, US5)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from modules import config
from modules.config import IRAQ_TIMEZONE
from modules.dates import to_iso
from modules.review_reminders import handle_review_reminders, reports_phrase

SINCE = '2026-09-01T00:00:00.000'
NEW = '2026-10-01T09:00:00.000'
OLD = '2026-08-15T09:00:00.000'
SIGNED = {'reviewerId': 'x', 'reviewerName': 'x', 'reviewedAt': NEW}


@pytest.fixture(autouse=True)
def _cutoff(monkeypatch):
    monkeypatch.setattr(config, 'REVIEW_REMINDER_SINCE', SINCE)


def _users(db):
    def user(role, token, **extra):
        return {'role': role, 'isActive': True, 'fcmToken': token, 'name': role,
                'receiveEmailNotifications': True, **extra}

    db._put('users', 'mgr', user('salesManager', 'mgr-token'))
    db._put('users', 'adm', user('admin', 'adm-token'))
    db._put('users', 'rep', user('salesRepresentative', 'rep-token'))


def _report(db, rid, created=NEW, **kw):
    db._put('reports', rid, {'type': 'مبيعات', 'createdAt': created, 'clientId': 'c1',
                             'userName': 'المندوب', 'salesManagerReview': None,
                             'adminReview': None, **kw})


def _visit_record(db, sid='s1', visits=(('v1', NEW),), reviews=None, **parent):
    db._put('technical_support', sid, {
        'clientName': 'عيادة النور', 'reviews': reviews or {},
        'visitHistory': [{'id': vid, 'createdAt': created} for vid, created in visits], **parent})


def _request(db, rid, state='awaiting', send=None, **kw):
    db._put('special_requests', rid, {
        'state': state, 'send': send, 'salesManagerDecision': None, 'adminDecision': None,
        'clientName': 'عيادة النور', 'representativeName': 'المندوب', **kw})


def _run(db):
    pushed = []
    with patch('modules.notifications.messaging') as m:
        m.UnregisteredError = type('U', (Exception,), {})
        m.SenderIdMismatchError = type('S', (Exception,), {})
        m.Notification = MagicMock()
        m.MulticastMessage.side_effect = lambda **kw: pushed.append(kw)

        class _R:
            success = True
            exception = None

        m.send_each_for_multicast.side_effect = lambda msg: MagicMock(
            responses=[_R()] * len(pushed[-1]['tokens']),
            success_count=len(pushed[-1]['tokens']), failure_count=0)
        resp = handle_review_reminders(db)
    body, status = resp if isinstance(resp, tuple) else (resp, 200)
    return body.get_json(), status, pushed


def _digests(db):
    return {doc['recipientIds'][0]: doc for key, doc in db._get_all('notifications').items()
            if key.startswith('review_digest-')}


def _lines(db, uid):
    return _digests(db)[uid]['body'].split('\n')


# --- counts ---------------------------------------------------------------------------------

def test_the_counts_are_per_slot_and_cover_reports_and_visits(db):
    _users(db)
    _report(db, 'r1')
    _report(db, 'r2', salesManagerReview=SIGNED)            # manager done, admin pending
    _visit_record(db, visits=(('v1', NEW),), reviews={'v1__admin': SIGNED})  # admin done
    handle = _run(db)
    assert handle[1] == 200
    # manager: r1, v1 pending (r2 signed); admin: r1, r2 pending (v1 signed)
    assert _lines(db, 'mgr')[0] == 'لديك تقريران بحاجة إلى مراجعة، يرجى زيارة الموقع لمراجعة المهمة'
    assert _lines(db, 'adm')[0] == 'لديك تقريران بحاجة إلى مراجعة، يرجى زيارة الموقع لمراجعة المهمة'


def test_a_single_report_uses_the_singular_form(db):
    _users(db)
    _report(db, 'r1', adminReview=SIGNED)
    _run(db)
    assert _lines(db, 'mgr')[0].startswith('لديك تقرير واحد بحاجة إلى مراجعة')
    assert 'adm' not in _digests(db)


def test_deleted_reports_visits_of_deleted_records_and_copies_are_excluded(db):
    _users(db)
    _report(db, 'live')
    _report(db, 'gone', reviewState='deleted')
    _visit_record(db, 's1', visits=(('v1', NEW),), reviewState='deleted')
    # v2 is a copy of the task report 'live': the report counts, not the copy.
    _visit_record(db, 's2', visits=(('live', NEW), ('v2', NEW)))
    _run(db)
    # live report + v2 visit; not 'gone', not s1's visit, not the copy 'live'.
    assert _lines(db, 'mgr')[0].startswith('لديك تقريران')


# --- the cut-off ----------------------------------------------------------------------------

def test_older_reports_and_visits_are_not_counted(db):
    _users(db)
    _report(db, 'old', created=OLD)
    _report(db, 'new')
    _visit_record(db, visits=(('v-old', OLD), ('v-new', NEW)))
    _run(db)
    assert _lines(db, 'mgr')[0].startswith('لديك تقريران')


def test_a_legacy_timestamp_created_at_is_compared_correctly(db):
    _users(db)
    _report(db, 'ts-new', created=datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    _report(db, 'ts-old', created=datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc))
    body, status, _ = _run(db)
    assert status == 200
    assert _lines(db, 'mgr')[0].startswith('لديك تقرير واحد')


def test_server_created_at_is_ignored(db):
    _users(db)
    # Filed long ago; only the server stamp is recent.
    _report(db, 'r1', created=OLD, serverCreatedAt=datetime(2026, 10, 1, tzinfo=timezone.utc))
    _run(db)
    assert _digests(db) == {}


def test_a_report_with_no_readable_date_is_not_counted(db):
    _users(db)
    _report(db, 'r1', created=None)
    _run(db)
    assert _digests(db) == {}


# --- special requests (M) -------------------------------------------------------------------

def test_m_counts_awaiting_requests_even_on_a_report_both_slots_signed(db):
    _users(db)
    _report(db, 'r1', salesManagerReview=SIGNED, adminReview=SIGNED)
    _request(db, 'report_r1')
    _run(db)
    assert _lines(db, 'mgr') == ['1 طلبات خاصة بانتظار قرارك']
    assert _lines(db, 'adm') == ['1 طلبات خاصة بانتظار قرارك']


def test_a_reviewer_with_no_pending_reviews_but_a_request_gets_no_first_line(db):
    _users(db)
    _report(db, 'old', created=OLD, salesManagerReview=SIGNED, adminReview=SIGNED)
    _request(db, 'report_old')
    _run(db)
    lines = _lines(db, 'mgr')
    assert lines == ['1 طلبات خاصة بانتظار قرارك']


def test_m_counts_only_requests_whose_slot_for_the_role_is_open(db):
    _users(db)
    _report(db, 'a', salesManagerReview=SIGNED, adminReview=SIGNED)
    _report(db, 'b', salesManagerReview=SIGNED, adminReview=SIGNED)
    _request(db, 'report_a', salesManagerDecision={'decision': 'approved', 'deciderId': 'someone'})
    _request(db, 'report_b')
    _run(db)
    assert _lines(db, 'mgr') == ['1 طلبات خاصة بانتظار قرارك']          # b only
    assert _lines(db, 'adm') == ['2 طلبات خاصة بانتظار قرارك']          # a and b


def test_m_excludes_requests_whose_other_slot_this_reviewer_decided(db):
    _users(db)
    _report(db, 'a', salesManagerReview=SIGNED, adminReview=SIGNED)
    _request(db, 'report_a', salesManagerDecision={'decision': 'approved', 'deciderId': 'adm'})
    _run(db)
    # The admin took the manager slot, so the same-person rule bars them from
    # the admin slot; nothing else is waiting on them.
    assert 'adm' not in _digests(db)
    assert 'mgr' not in _digests(db)  # the manager slot is taken; the admin slot is not theirs


def test_requests_of_deleted_sources_are_not_counted(db):
    _users(db)
    _report(db, 'gone', reviewState='deleted')
    _visit_record(db, 's1', visits=(('v1', NEW),), reviewState='deleted')
    _request(db, 'report_gone')
    _request(db, 'visit_s1_v1')
    _request(db, 'report_missing')
    _run(db)
    assert _digests(db) == {}


def test_a_visit_request_is_counted_when_its_visit_is_live(db):
    _users(db)
    _visit_record(db, visits=(('v1', OLD),), reviews={'v1__salesManager': SIGNED, 'v1__admin': SIGNED})
    _request(db, 'visit_s1_v1')
    _run(db)
    assert _lines(db, 'mgr') == ['1 طلبات خاصة بانتظار قرارك']


def test_rejected_and_withdrawn_requests_are_not_waiting(db):
    _users(db)
    _report(db, 'a', salesManagerReview=SIGNED, adminReview=SIGNED)
    _report(db, 'b', salesManagerReview=SIGNED, adminReview=SIGNED)
    _request(db, 'report_a', state='rejected')
    _request(db, 'report_b', state='withdrawn')
    _run(db)
    assert _digests(db) == {}


# --- unsent approvals (K) -------------------------------------------------------------------

def _approved(db, rid, send):
    _report(db, rid.replace('report_', ''), salesManagerReview=SIGNED, adminReview=SIGNED)
    _request(db, rid, state='approved', send=send)


def _ago(minutes):
    return to_iso(datetime.now(IRAQ_TIMEZONE) - timedelta(minutes=minutes))


def test_k_counts_failed_partial_and_stalled_for_admins_only(db):
    _users(db)
    _approved(db, 'report_a', {'state': 'failed', 'claimedAt': _ago(60)})
    _approved(db, 'report_b', {'state': 'partial', 'claimedAt': _ago(60)})
    _approved(db, 'report_c', {'state': 'sending', 'claimedAt': _ago(30)})   # stalled
    _approved(db, 'report_d', {'state': 'sending', 'claimedAt': _ago(2)})    # in flight
    _approved(db, 'report_e', {'state': 'sent', 'claimedAt': _ago(60)})
    _run(db)
    assert _lines(db, 'adm') == ['3 طلبات خاصة موافق عليها لم تُرسل بالكامل']
    assert 'mgr' not in _digests(db)


def test_an_admin_with_no_pending_reviews_but_unsent_requests_still_gets_a_summary(db):
    _users(db)
    _approved(db, 'report_a', {'state': 'failed', 'claimedAt': _ago(60)})
    body, status, pushed = _run(db)
    assert body['notified'] == 1
    assert pushed[0]['tokens'] == ['adm-token']


def test_unsent_requests_of_deleted_sources_are_not_counted(db):
    _users(db)
    _report(db, 'a', reviewState='deleted')
    _request(db, 'report_a', state='approved', send={'state': 'failed', 'claimedAt': _ago(60)})
    _run(db)
    assert 'adm' not in _digests(db)


# --- wording --------------------------------------------------------------------------------

@pytest.mark.parametrize('count,expected', [
    (1, 'تقرير واحد'), (2, 'تقريران'), (3, '3 تقارير'), (10, '10 تقارير'),
    (11, '11 تقريرًا'), (25, '25 تقريرًا'),
])
def test_arabic_number_agreement(count, expected):
    assert reports_phrase(count) == expected


def test_the_summary_names_no_client_or_rep_and_opens_the_pending_list(db):
    _users(db)
    _report(db, 'r1')
    _request(db, 'report_r1')
    _run(db)
    digest = _digests(db)['mgr']
    assert digest['kind'] == 'review_digest'
    assert digest['title'] == 'تقارير بحاجة إلى مراجعة'
    assert digest['data'] == {'action': 'open_pending_reviews'}
    assert 'عيادة النور' not in digest['body'] and 'المندوب' not in digest['body']


# --- who gets one, and how often ------------------------------------------------------------

def test_nothing_pending_sends_nothing(db):
    _users(db)
    body, status, pushed = _run(db)
    assert status == 200 and body['notified'] == 0 and pushed == []
    assert _digests(db) == {}


def test_only_active_sales_managers_and_admins_are_reviewers(db):
    _users(db)
    db._put('users', 'off', {'role': 'admin', 'isActive': False, 'fcmToken': 'off-token'})
    _report(db, 'r1')
    body, _, pushed = _run(db)
    assert body['reviewers'] == 2
    assert sorted(_digests(db)) == ['adm', 'mgr']
    assert sorted(pushed[0]['tokens'] + (pushed[1]['tokens'] if len(pushed) > 1 else [])) == [
        'adm-token', 'mgr-token']


def test_a_second_run_the_same_day_sends_nothing(db):
    _users(db)
    _report(db, 'r1')
    _run(db)
    body, _, pushed = _run(db)
    assert body['notified'] == 0 and body['skipped']['already'] == 2 and pushed == []
    assert len(_digests(db)) == 2


def test_the_summary_is_recorded_for_an_unreachable_reviewer_but_not_pushed(db):
    _users(db)
    db._put('users', 'mgr', {'role': 'salesManager', 'isActive': True, 'fcmToken': None, 'name': 'm'})
    _report(db, 'r1')
    body, _, pushed = _run(db)
    assert 'mgr' in _digests(db)
    assert body['skipped']['unreachable'] == 1
    assert all('mgr-token' not in call['tokens'] for call in pushed)


def test_the_response_reports_the_date_and_counts(db):
    _users(db)
    _report(db, 'r1')
    body, status, _ = _run(db)
    today = datetime.now(IRAQ_TIMEZONE).strftime('%Y-%m-%d')
    assert status == 200 and body['success'] is True and body['date'] == today
    assert body['reviewers'] == 2 and body['notified'] == 2
    assert f'review_digest-{today}-mgr' in db._get_all('notifications')


def test_one_reviewer_failing_does_not_stop_the_others(db):
    _users(db)
    _report(db, 'r1')
    real = __import__('modules.review_reminders', fromlist=['_send_digest'])._send_digest

    def flaky(db_, uid, *args):
        if uid == 'mgr':
            raise RuntimeError('boom')
        return real(db_, uid, *args)

    with patch('modules.review_reminders._send_digest', side_effect=flaky):
        body, status, _ = _run(db)
    assert status == 200 and body['failed'] == 1
    assert 'adm' in _digests(db)
