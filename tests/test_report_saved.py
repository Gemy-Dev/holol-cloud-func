"""reportSaved: refresh the request record, and (US4) announce new reports."""
import pytest

from modules.review_reminders import handle_report_saved

REP = 'rep'


def _report(**kw):
    base = {
        'type': 'مبيعات', 'clientId': 'c1', 'assignedToId': REP, 'userName': 'المندوب',
        'createdAt': '2026-10-01T09:00:00.000', 'hasSpecialRequests': True,
        'clientOrders': '10 علب',
    }
    base.update(kw)
    return base


def _seed_report(db, rid='r1', **kw):
    db._put('reports', rid, _report(**kw))
    db._put('clients', 'c1', {'name': 'عيادة النور'})


def _seed_visit(db, sid='s1', vid='v1', **visit):
    entry = {'status': 'completed', 'id': vid, 'technicianId': 'tech', 'technicianName': 'الفني',
             'visitDate': '2026-10-01T08:00:00.000', 'createdAt': '2026-10-01T08:00:00.000',
             'hasSpecialRequests': True, 'clientOrders': 'طلب'}
    entry.update(visit)
    db._put('technical_support', sid,
            {'clientId': 'c1', 'clientName': 'عيادة النور', 'visitHistory': [entry]})
    return entry


def _call(db, payload, uid=REP):
    data = {'event': 'created', 'intent': 'set', 'sourceType': 'report', 'reportId': 'r1',
            'expectedFlag': True, 'expectedText': '10 علب'}
    data.update(payload)
    resp = handle_report_saved({'uid': uid}, data, db)
    body, status = (resp if isinstance(resp, tuple) else (resp, 200))
    return body.get_json(), status


def _doc(db, rid):
    return db._get_all('special_requests').get(rid)


# --- refresh table over the action ---------------------------------------------

def test_set_on_a_report_creates_the_record(db):
    _seed_report(db)
    body, status = _call(db, {})
    assert status == 200 and body['request']['state'] == 'awaiting'
    assert _doc(db, 'report_r1')['requestText'] == '10 علب'


def test_set_on_a_visit_creates_the_record(db):
    _seed_visit(db)
    body, status = _call(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1',
                              'reportId': None, 'expectedText': 'طلب'}, uid='tech')
    assert status == 200
    assert _doc(db, 'visit_s1_v1')['state'] == 'awaiting'


def test_updated_set_refreshes_the_text(db):
    _seed_report(db)
    _call(db, {})
    db._put('reports', 'r1', _report(clientOrders='20 علبة'))
    _call(db, {'event': 'updated', 'expectedText': '20 علبة'})
    assert _doc(db, 'report_r1')['requestText'] == '20 علبة'


def test_withdraw_then_set_restores(db):
    _seed_report(db)
    _call(db, {})
    db._put('reports', 'r1', _report(hasSpecialRequests=False))
    _call(db, {'event': 'updated', 'intent': 'withdraw', 'expectedFlag': False, 'expectedText': '10 علب'})
    assert _doc(db, 'report_r1')['state'] == 'withdrawn'
    db._put('reports', 'r1', _report(clientOrders='جديد'))
    _call(db, {'event': 'updated', 'expectedText': 'جديد'})
    assert _doc(db, 'report_r1')['state'] == 'awaiting'


def test_a_decided_record_is_untouched(db):
    _seed_report(db)
    _call(db, {})
    db._get_all('special_requests')['report_r1']['adminDecision'] = {'decision': 'approved', 'deciderId': 'x'}
    before = dict(_doc(db, 'report_r1'))
    db._put('reports', 'r1', _report(clientOrders='متأخر'))
    _call(db, {'event': 'updated', 'expectedText': 'متأخر'})
    assert _doc(db, 'report_r1') == before


def test_invalid_intent_writes_nothing(db):
    _seed_report(db, hasSpecialRequests=False)
    body, status = _call(db, {'expectedFlag': False, 'expectedText': '10 علب'})
    assert status == 400 and body['error'] == 'invalid_intent'
    assert _doc(db, 'report_r1') is None


def test_withdraw_by_a_non_author_is_refused(db):
    _seed_report(db, hasSpecialRequests=False)
    body, status = _call(db, {'intent': 'withdraw', 'expectedFlag': False}, uid='manager')
    assert status == 403 and body['error'] == 'not_author'


# --- errors ----------------------------------------------------------------------

def test_a_missing_source_is_404_source_not_found(db):
    body, status = _call(db, {'reportId': 'ghost'})
    assert status == 404 and body['error'] == 'source_not_found'


def test_a_missing_visit_is_404(db):
    _seed_visit(db)
    _, status = _call(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'ghost'})
    assert status == 404


@pytest.mark.parametrize('bad', [
    {'event': 'nope'}, {'intent': 'nope'}, {'sourceType': 'nope'}, {'reportId': ''},
    {'expectedFlag': None}, {'expectedFlag': 'yes'},
])
def test_bad_payloads_are_invalid_payload(db, bad):
    _seed_report(db)
    body, status = _call(db, bad)
    assert status == 400 and body['error'] == 'invalid_payload'


def test_missing_expected_text_key_is_invalid_payload(db):
    _seed_report(db)
    resp = handle_report_saved({'uid': REP}, {
        'event': 'created', 'intent': 'set', 'sourceType': 'report', 'reportId': 'r1',
        'expectedFlag': True}, db)
    body, status = resp
    assert status == 400


def test_a_visit_needs_both_ids(db):
    _seed_visit(db)
    body, status = _call(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': ''})
    assert status == 400


# --- report copies ------------------------------------------------------------------

def test_report_copy_with_none_is_a_no_op_200(db):
    _seed_report(db, 'r9', hasSpecialRequests=True)
    _seed_visit(db, vid='r9', hasSpecialRequests=False, clientOrders=None)
    body, status = _call(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'r9',
                              'intent': 'none', 'expectedFlag': False, 'expectedText': None})
    assert status == 200 and body['reason'] == 'report_copy' and body['request'] is None
    assert _doc(db, 'visit_s1_r9') is None


@pytest.mark.parametrize('intent', ['set', 'withdraw'])
def test_report_copy_with_set_or_withdraw_is_409_and_writes_nothing(db, intent):
    _seed_report(db, 'r9')
    _seed_visit(db, vid='r9')
    body, status = _call(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'r9',
                              'intent': intent, 'expectedFlag': intent == 'set'}, uid='tech')
    assert status == 409 and body['error'] == 'report_copy'
    assert _doc(db, 'visit_s1_r9') is None


# --- the source fingerprint --------------------------------------------------------

def test_a_source_that_does_not_match_the_fingerprint_is_409_source_not_synced(db):
    _seed_report(db, clientOrders='نص قديم')
    body, status = _call(db, {'expectedText': 'نص جديد'})
    assert status == 409 and body['error'] == 'source_not_synced'
    assert _doc(db, 'report_r1') is None


def test_the_same_event_succeeds_once_the_source_matches(db):
    _seed_report(db, clientOrders='نص قديم')
    assert _call(db, {'expectedText': 'نص جديد'})[1] == 409
    db._put('reports', 'r1', _report(clientOrders='نص جديد'))
    assert _call(db, {'expectedText': 'نص جديد'})[1] == 200


def test_a_flag_mismatch_is_not_synced(db):
    _seed_report(db, hasSpecialRequests=False)
    body, status = _call(db, {'intent': 'set', 'expectedFlag': True})
    assert status == 409 and body['error'] == 'source_not_synced'


def test_a_stripped_visit_uses_the_authors_expected_values(db):
    entry = _seed_visit(db)
    entry.pop('hasSpecialRequests'); entry.pop('clientOrders')  # an old build rewrote the history
    body, status = _call(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1',
                              'expectedText': 'طلب'}, uid='tech')
    assert status == 200
    assert _doc(db, 'visit_s1_v1')['requestText'] == 'طلب'


def test_an_authors_withdraw_on_a_stripped_visit_succeeds(db):
    entry = _seed_visit(db)
    _call(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1',
               'expectedText': 'طلب'}, uid='tech')
    entry.pop('hasSpecialRequests'); entry.pop('clientOrders')
    body, status = _call(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1',
                              'event': 'updated', 'intent': 'withdraw', 'expectedFlag': False,
                              'expectedText': None}, uid='tech')
    assert status == 200
    assert _doc(db, 'visit_s1_v1')['state'] == 'withdrawn'


def test_a_non_author_on_a_stripped_visit_is_not_synced(db):
    entry = _seed_visit(db)
    entry.pop('hasSpecialRequests'); entry.pop('clientOrders')
    body, status = _call(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1',
                              'expectedText': 'طلب'}, uid='manager')
    assert status == 409 and body['error'] == 'source_not_synced'


def test_none_on_a_stripped_visit_leaves_an_awaiting_record_intact(db):
    entry = _seed_visit(db)
    _call(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1',
               'expectedText': 'طلب'}, uid='tech')
    entry.pop('hasSpecialRequests'); entry.pop('clientOrders')
    _, status = _call(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1',
                           'event': 'updated', 'intent': 'none', 'expectedFlag': False,
                           'expectedText': None}, uid='tech')
    assert status == 200
    doc = _doc(db, 'visit_s1_v1')
    assert doc['state'] == 'awaiting' and doc['requestText'] == 'طلب'


# --- announce (US4) -------------------------------------------------------------------------

from unittest.mock import MagicMock, patch  # noqa: E402


def _reviewers(db):
    def user(role, token='tok', **extra):
        return {'role': role, 'isActive': True, 'fcmToken': token, 'name': role,
                'receiveEmailNotifications': True, **extra}

    db._put('users', 'mgr', user('salesManager', 'mgr-token'))
    db._put('users', 'mgr_off', user('مدير المبيعات', None))  # no device: still recorded
    db._put('users', 'adm', user('admin', 'adm-token'))
    db._put('users', REP, user('salesRepresentative', 'rep-token'))
    db._put('users', 'tech', user('technician', 'tech-token'))


def _announce(db, payload=None, uid=REP):
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
        body, status = _call(db, payload or {}, uid=uid)
    return body, status, pushed


def _notification(db, rid='report_r1'):
    if rid.startswith('visit_'):
        return db._get_all('notifications').get('support_visit_added-' + rid[6:].replace('_', '-'))
    return db._get_all('notifications').get('task_completed-report-' + rid[7:])


def test_a_new_report_notifies_the_sales_managers_and_admins_once(db):
    _reviewers(db)
    _seed_report(db)
    body, status, pushed = _announce(db)
    assert status == 200 and body['notified'] is True
    notice = _notification(db)
    assert notice['kind'] == 'business_event'
    assert notice['title'] == 'المندوب أنجز مهمة'
    assert sorted(notice['recipientIds']) == ['adm', 'mgr', 'mgr_off']
    # Pushed to the two reachable reviewers only; the unreachable one is on record.
    assert sorted(pushed[0]['tokens']) == ['adm-token', 'mgr-token']


def test_the_audience_never_includes_the_rep_or_other_roles(db):
    _reviewers(db)
    _seed_report(db)
    _announce(db)
    recipients = _notification(db)['recipientIds']
    assert REP not in recipients and 'tech' not in recipients


def test_the_actor_is_not_told_about_their_own_save(db):
    _reviewers(db)
    _seed_report(db)
    _announce(db, uid='adm')
    assert _notification(db) is None  # an admin's report is not a rep completion


def test_a_second_created_is_already_notified_and_pushes_nothing(db):
    _reviewers(db)
    _seed_report(db)
    _announce(db)
    body, status, pushed = _announce(db)
    assert status == 200 and body['notified'] is False and body['reason'] == 'already_notified'
    assert pushed == []
    assert len([k for k in db._get_all('notifications') if k.startswith('task_completed-report-')]) == 1


def test_an_updated_event_never_announces(db):
    _reviewers(db)
    _seed_report(db)
    body, _, pushed = _announce(db, {'event': 'updated'})
    assert body['notified'] is False and body['reason'] == 'updated_event'
    assert _notification(db) is None and pushed == []


def test_a_report_copy_never_announces(db):
    _reviewers(db)
    _seed_report(db, rid='v1')
    _seed_visit(db)
    body, _, pushed = _announce(db, {
        'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1', 'intent': 'none',
        'expectedFlag': True, 'expectedText': 'طلب'}, uid='tech')
    assert body['reason'] == 'report_copy' and body['notified'] is False
    assert _notification(db, 'visit_s1_v1') is None and pushed == []


def test_a_deleted_report_never_announces(db):
    _reviewers(db)
    _seed_report(db, reviewState='deleted')
    body, status, pushed = _announce(db)
    assert status == 200 and body['reason'] == 'deleted' and body['notified'] is False
    assert _notification(db) is None and pushed == []


def test_a_visit_of_a_soft_deleted_support_record_never_announces(db):
    _reviewers(db)
    _seed_visit(db)
    parent = db._get_all('technical_support')['s1']
    db._put('technical_support', 's1', {**parent, 'reviewState': 'deleted'})
    body, status, _ = _announce(db, {
        'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1',
        'expectedFlag': True, 'expectedText': 'طلب'}, uid='tech')
    assert status == 200 and body['reason'] == 'deleted'
    assert _notification(db, 'visit_s1_v1') is None


def test_an_offline_synced_visit_announces_from_visit_history(db):
    _reviewers(db)
    _seed_visit(db)  # only the parent's array exists; no support_visits document
    body, status, _ = _announce(db, {
        'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1',
        'expectedFlag': True, 'expectedText': 'طلب'}, uid='tech')
    assert status == 200 and body['notified'] is True
    notice = _notification(db, 'visit_s1_v1')
    assert 'النوع: زيارة دعم فني' in notice['body'] and 'الفني' in notice['body']


def test_the_body_names_the_rep_the_client_the_type_and_the_special_request(db):
    _reviewers(db)
    _seed_report(db)
    _announce(db)
    lines = _notification(db)['body'].split('\n')
    assert lines == ['المندوب: المندوب', 'العميل: عيادة النور', 'النوع: تقرير مبيعات',
                     'يتضمن طلبات خاصة من العميل']


def test_a_report_without_a_special_request_has_no_such_line(db):
    _reviewers(db)
    _seed_report(db, hasSpecialRequests=False, clientOrders=None)
    _announce(db, {'intent': 'none', 'expectedFlag': False, 'expectedText': None})
    assert 'يتضمن طلبات خاصة' not in _notification(db)['body']


def test_a_withdrawn_request_has_no_such_line(db):
    _reviewers(db)
    _seed_report(db, hasSpecialRequests=False, clientOrders=None)
    db._put('special_requests', 'report_r1', {'state': 'withdrawn', 'sourceType': 'report',
                                             'reportId': 'r1', 'family': 'sales',
                                             'requestText': 'x', 'salesManagerDecision': None,
                                             'adminDecision': None, 'send': None})
    _announce(db, {'intent': 'none', 'expectedFlag': False, 'expectedText': None})
    assert 'يتضمن طلبات خاصة' not in _notification(db)['body']


def test_a_technical_task_report_is_labelled_a_support_report(db):
    _reviewers(db)
    _seed_report(db, type='technical_support')
    _announce(db)
    assert 'النوع: تقرير دعم فني' in _notification(db)['body']


def test_tapping_the_notice_opens_the_source(db):
    _reviewers(db)
    _seed_report(db)
    _seed_visit(db, vid='v9')
    _announce(db)
    _announce(db, {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v9',
                   'expectedFlag': True, 'expectedText': 'طلب'}, uid='tech')
    assert _notification(db)['data'] == {'action': 'open_daily_report', 'reportId': 'r1', 'event': 'task_completed'}
    assert _notification(db, 'visit_s1_v9')['data'] == {
        'action': 'open_support_record', 'supportRecordId': 's1',
        'visitId': 'v9', 'event': 'support_visit_added'}


def test_an_announcement_failure_is_a_retryable_error_not_a_lost_notice(db):
    _reviewers(db)
    _seed_report(db)
    with patch('modules.business_notifications.deliver_event', side_effect=RuntimeError('down')):
        body, status = _call(db, {})
    assert status == 500
    # The record refresh still happened, and a retry announces.
    assert _doc(db, 'report_r1') is not None
    body, status, _ = _announce(db)
    assert status == 200 and body['notified'] is True
