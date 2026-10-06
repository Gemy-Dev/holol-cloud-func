"""decideSpecialRequest and editSpecialRequestNote (spec 2038, US2)."""
import copy
from unittest.mock import MagicMock, patch

import pytest

from modules.special_requests import (
    handle_decide_special_request,
    handle_edit_special_request_note,
)

REP = 'rep'
MGR = 'mgr'
ADM = 'adm'


def _user(perms=('reviewDailyReport',), active=True, name='مراجع', role='salesManager', token=None):
    return {'role': role, 'isActive': active, 'permissions': list(perms), 'name': name,
            'fcmToken': token, 'receiveEmailNotifications': True}


def _seed(db, source='report', flag=True, text='10 علب', state='awaiting', **record):
    db._put('users', MGR, _user(name='مدير المبيعات'))
    db._put('users', ADM, _user(name='مسؤول النظام', role='admin'))
    db._put('users', REP, _user(perms=(), name='المندوب', role='salesRepresentative', token='rep-token'))
    db._put('clients', 'c1', {'name': 'عيادة النور'})
    if source == 'report':
        db._put('reports', 'r1', {
            'type': 'مبيعات', 'clientId': 'c1', 'assignedToId': REP, 'userName': 'المندوب',
            'createdAt': '2026-10-01T09:00:00.000', 'hasSpecialRequests': flag,
            'clientOrders': text, 'salesManagerReview': None, 'adminReview': None,
            'reportStatus': 'pending'})
        rid = 'report_r1'
        base = {'sourceType': 'report', 'reportId': 'r1', 'family': 'sales'}
    else:
        db._put('technical_support', 's1', {
            'clientId': 'c1', 'clientName': 'عيادة النور', 'reviews': {},
            'visitHistory': [{'id': 'v1', 'technicianId': REP, 'technicianName': 'المندوب',
                              'visitDate': '2026-10-01T08:00:00.000',
                              'createdAt': '2026-10-01T08:00:00.000',
                              'hasSpecialRequests': flag, 'clientOrders': text}]})
        rid = 'visit_s1_v1'
        base = {'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1', 'family': 'technical'}
    if state is not None:
        doc = {**base, 'clientId': 'c1', 'clientName': 'عيادة النور', 'representativeId': REP,
               'representativeName': 'المندوب', 'reportDate': '2026-10-01T09:00:00.000',
               'sourceCreatedAt': '2026-10-01T09:00:00.000', 'requestText': text,
               'savedAt': '2026-10-01T09:00:01.000', 'salesManagerDecision': None,
               'adminDecision': None, 'state': state, 'send': None,
               'createdAt': '2026-10-01T09:00:01.000', 'updatedAt': '2026-10-01T09:00:01.000'}
        doc.update(record)
        db._put('special_requests', rid, doc)
    return rid


def _decide(db, uid=MGR, **payload):
    data = {'sourceType': 'report', 'reportId': 'r1', 'slot': 'salesManager',
            'decision': 'approved', 'expectedText': '10 علب'}
    data.update(payload)
    sent = []
    with patch('modules.notifications.messaging') as m:
        m.UnregisteredError = type('U', (Exception,), {})
        m.SenderIdMismatchError = type('S', (Exception,), {})
        m.Notification = MagicMock()
        m.MulticastMessage.side_effect = lambda **kw: sent.append(kw.get('tokens'))

        class _R:
            success = True
            exception = None
        m.send_each_for_multicast.side_effect = lambda msg: MagicMock(
            responses=[_R()], success_count=1, failure_count=0)
        resp = handle_decide_special_request({'uid': uid}, data, db)
    body, status = resp if isinstance(resp, tuple) else (resp, 200)
    return body.get_json(), status, sent


def _doc(db, rid='report_r1'):
    return db._get_all('special_requests').get(rid)


# --- V1: who may decide ----------------------------------------------------------

def test_a_reviewer_with_the_grant_decides(db):
    _seed(db)
    body, status, _ = _decide(db)
    assert status == 200 and body['request']['salesManagerDecision']['decision'] == 'approved'
    assert _doc(db)['salesManagerDecision']['deciderId'] == MGR
    assert _doc(db)['salesManagerDecision']['deciderName'] == 'مدير المبيعات'


def test_an_inactive_account_is_refused(db):
    _seed(db)
    db._put('users', MGR, _user(active=False))
    body, status, _ = _decide(db)
    assert status == 403 and body['error'] == 'not_permitted'


def test_a_user_without_the_grant_is_refused(db):
    _seed(db)
    db._put('users', MGR, _user(perms=('viewDailyReport',)))
    assert _decide(db)[1] == 403


def test_the_technical_family_needs_the_technical_grant(db):
    _seed(db, source='visit')
    body, status, _ = _decide(db, sourceType='visit', supportRecordId='s1', visitId='v1', reportId=None)
    assert status == 403  # the manager only holds reviewDailyReport
    db._put('users', MGR, _user(perms=('reviewTechnicalReport',)))
    assert _decide(db, sourceType='visit', supportRecordId='s1', visitId='v1', reportId=None)[1] == 200


def test_the_legacy_arabic_grant_label_is_accepted(db):
    _seed(db)
    db._put('users', MGR, _user(perms=('مراجعة تقرير يومي',)))
    assert _decide(db)[1] == 200


def test_a_report_corrected_from_sales_to_technical_is_checked_against_the_technical_grant(db):
    _seed(db)
    db._get_all('reports')['r1']['type'] = 'دعم فني'
    body, status, _ = _decide(db)
    assert status == 403 and body['error'] == 'not_permitted'
    db._put('users', MGR, _user(perms=('reviewTechnicalReport',)))
    assert _decide(db)[1] == 200
    assert _doc(db)['family'] == 'technical'


# --- V2 / V3: slots --------------------------------------------------------------

def test_a_taken_slot_is_409_slot_taken(db):
    _seed(db)
    _decide(db)
    db._put('users', 'mgr2', _user())
    body, status, _ = _decide(db, uid='mgr2')
    assert status == 409 and body['error'] == 'slot_taken'


def test_the_same_person_cannot_decide_both_slots(db):
    _seed(db)
    _decide(db)
    body, status, _ = _decide(db, slot='admin')
    assert status == 409 and body['error'] == 'same_person'
    assert _doc(db)['adminDecision'] is None


def test_a_different_person_decides_the_second_slot(db):
    _seed(db)
    db._put('users', ADM, _user(name='مسؤول النظام', role='admin'))
    _decide(db)
    body, status, _ = _decide(db, uid=ADM, slot='admin')
    assert status == 200
    assert _doc(db)['state'] == 'approved'


# --- V4: the source ----------------------------------------------------------------

def test_a_deleted_report_is_404(db):
    _seed(db)
    db._get_all('reports')['r1']['reviewState'] = 'deleted'
    assert _decide(db)[1] == 404


def test_a_visit_of_a_deleted_support_record_is_404(db):
    _seed(db, source='visit')
    db._put('users', MGR, _user(perms=('reviewTechnicalReport',)))
    db._get_all('technical_support')['s1']['reviewState'] = 'deleted'
    assert _decide(db, sourceType='visit', supportRecordId='s1', visitId='v1', reportId=None)[1] == 404


def test_an_unflagged_source_with_no_record_is_not_a_special_request(db):
    _seed(db, flag=False, state=None)
    body, status, _ = _decide(db)
    assert status == 404 and body['error'] == 'not_a_special_request'


def test_a_missing_record_on_a_flagged_source_is_created_in_the_same_transaction(db):
    _seed(db, state=None)
    body, status, _ = _decide(db)
    assert status == 200
    assert _doc(db)['requestText'] == '10 علب' and _doc(db)['salesManagerDecision'] is not None


def test_a_withdrawn_request_is_409_withdrawn(db):
    _seed(db, state='withdrawn')
    body, status, _ = _decide(db)
    assert status == 409 and body['error'] == 'withdrawn'


def test_a_missing_source_is_404(db):
    _seed(db)
    assert _decide(db, reportId='ghost')[1] == 404


# --- V5: the text -----------------------------------------------------------------

def test_a_changed_text_is_409_text_changed(db):
    _seed(db)
    body, status, _ = _decide(db, expectedText='نص آخر')
    assert status == 409 and body['error'] == 'text_changed'
    assert _doc(db)['salesManagerDecision'] is None


def test_the_text_is_compared_after_trimming(db):
    _seed(db)
    assert _decide(db, expectedText='  10 علب  ')[1] == 200


def test_the_text_frozen_at_the_first_decision_is_what_the_second_must_acknowledge(db):
    _seed(db)
    _decide(db)
    # The source text moves on after the first decision; the record does not.
    db._get_all('reports')['r1']['clientOrders'] = 'نص جديد'
    db._put('users', ADM, _user(name='مسؤول النظام', role='admin'))
    assert _decide(db, uid=ADM, slot='admin', expectedText='نص جديد')[1] == 409
    assert _decide(db, uid=ADM, slot='admin', expectedText='10 علب')[1] == 200


# --- V6: the note -----------------------------------------------------------------

def test_a_note_is_trimmed_and_stored(db):
    _seed(db)
    _decide(db, note='  موافق  ')
    assert _doc(db)['salesManagerDecision']['note'] == 'موافق'


def test_a_blank_note_is_stored_as_none(db):
    _seed(db)
    _decide(db, note='   ')
    assert _doc(db)['salesManagerDecision']['note'] is None


def test_a_note_over_1000_characters_is_invalid(db):
    _seed(db)
    body, status, _ = _decide(db, note='ا' * 1001)
    assert status == 400 and body['error'] == 'invalid_payload'


@pytest.mark.parametrize('bad', [{'slot': 'owner'}, {'decision': 'maybe'}, {'sourceType': 'nope'},
                                 {'expectedText': None}, {'note': 5}])
def test_bad_payloads_are_invalid(db, bad):
    _seed(db)
    body, status, _ = _decide(db, **bad)
    assert status == 400 and body['error'] == 'invalid_payload'


# --- state --------------------------------------------------------------------------

def test_approve_then_approve_is_approved(db):
    _seed(db)
    _decide(db)
    _decide(db, uid=ADM, slot='admin')
    assert _doc(db)['state'] == 'approved'


def test_approve_then_reject_is_rejected(db):
    _seed(db)
    _decide(db)
    _decide(db, uid=ADM, slot='admin', decision='rejected', note='غير متاح')
    assert _doc(db)['state'] == 'rejected'


def test_a_decision_after_rejected_is_already_decided(db):
    _seed(db)
    _decide(db, decision='rejected')
    body, status, _ = _decide(db, uid=ADM, slot='admin')
    assert status == 409 and body['error'] == 'already_decided'


def test_one_approval_leaves_it_awaiting(db):
    _seed(db)
    _decide(db)
    assert _doc(db)['state'] == 'awaiting'


# --- FR-011 / FR-030 -------------------------------------------------------------------

def test_a_decision_leaves_the_report_and_the_support_record_untouched(db):
    _seed(db)
    before_report = copy.deepcopy(db._get_all('reports'))
    _decide(db)
    assert db._get_all('reports') == before_report

    _seed(db, source='visit', state=None)
    db._put('users', MGR, _user(perms=('reviewTechnicalReport',)))
    before_support = copy.deepcopy(db._get_all('technical_support'))
    _decide(db, sourceType='visit', supportRecordId='s1', visitId='v1', reportId=None)
    assert db._get_all('technical_support') == before_support


def test_a_stripped_old_shape_visit_with_an_awaiting_record_can_still_be_decided(db):
    _seed(db, source='visit')
    db._put('users', MGR, _user(perms=('reviewTechnicalReport',)))
    entry = db._get_all('technical_support')['s1']['visitHistory'][0]
    entry.pop('hasSpecialRequests'); entry.pop('clientOrders')  # an old build rewrote the history
    body, status, _ = _decide(db, sourceType='visit', supportRecordId='s1', visitId='v1',
                              reportId=None)
    assert status == 200
    assert _doc(db, 'visit_s1_v1')['salesManagerDecision'] is not None


# --- rejection notice --------------------------------------------------------------------

def test_a_rejection_keeps_the_decision_without_an_unlisted_role_notice(db):
    _seed(db)
    _decide(db, decision='rejected', note='غير متاح')
    assert _doc(db)['state'] == 'rejected'
    assert _doc(db)['salesManagerDecision']['note'] == 'غير متاح'
    assert not any(n['kind'] == 'special_request' for n in db._get_all('notifications').values())


def test_a_visit_rejection_keeps_the_decision_without_an_extra_push(db):
    _seed(db, source='visit')
    db._put('users', MGR, _user(perms=('reviewTechnicalReport',)))
    _decide(db, sourceType='visit', supportRecordId='s1', visitId='v1', reportId=None, decision='rejected')
    assert _doc(db, 'visit_s1_v1')['state'] == 'rejected'
    assert not any(n['kind'] == 'special_request' for n in db._get_all('notifications').values())


def test_an_unreachable_rep_does_not_receive_an_unlisted_notice(db):
    _seed(db)
    db._get_all('users')[REP]['fcmToken'] = None
    _decide(db, decision='rejected')
    assert _doc(db)['state'] == 'rejected'
    assert not any(n['kind'] == 'special_request' for n in db._get_all('notifications').values())


def test_an_approval_sends_no_rejection_notice(db):
    _seed(db)
    _decide(db)
    assert not any(n['kind'] == 'special_request' for n in db._get_all('notifications').values())


def test_a_notification_failure_never_fails_the_decision(db):
    _seed(db)
    with patch('modules.special_requests.notifications.send_to_user', side_effect=RuntimeError('boom')):
        body, status, _ = _decide(db, decision='rejected')
    assert status == 200 and _doc(db)['state'] == 'rejected'


# --- editSpecialRequestNote ------------------------------------------------------------------

def _edit(db, uid=MGR, **payload):
    data = {'requestId': 'report_r1', 'slot': 'salesManager', 'note': 'ملاحظة جديدة'}
    data.update(payload)
    resp = handle_edit_special_request_note({'uid': uid}, data, db)
    body, status = resp if isinstance(resp, tuple) else (resp, 200)
    return body.get_json(), status


def test_the_decider_rewords_their_own_note(db):
    _seed(db)
    _decide(db, note='قديمة')
    body, status = _edit(db)
    assert status == 200
    assert _doc(db)['salesManagerDecision']['note'] == 'ملاحظة جديدة'


def test_only_the_note_changes(db):
    _seed(db)
    _decide(db, note='قديمة')
    before = copy.deepcopy(_doc(db)['salesManagerDecision'])
    _edit(db)
    after = _doc(db)['salesManagerDecision']
    for key in ('decision', 'deciderId', 'deciderName', 'decidedAt'):
        assert after[key] == before[key]


def test_another_reviewer_cannot_edit_the_note(db):
    _seed(db)
    _decide(db, note='قديمة')
    db._put('users', 'mgr2', _user())
    body, status = _edit(db, uid='mgr2')
    assert status == 403 and body['error'] == 'not_owner'


def test_an_empty_note_clears_it(db):
    _seed(db)
    _decide(db, note='قديمة')
    _edit(db, note='')
    assert _doc(db)['salesManagerDecision']['note'] is None


def test_a_note_over_1000_characters_is_invalid_on_edit(db):
    _seed(db)
    _decide(db)
    body, status = _edit(db, note='ا' * 1001)
    assert status == 400 and body['error'] == 'invalid_payload'


def test_editing_an_undecided_slot_is_not_the_owners(db):
    _seed(db)
    body, status = _edit(db)
    assert status == 403 and body['error'] == 'not_owner'


def test_editing_a_missing_record_is_404(db):
    _seed(db, state=None)
    body, status = _edit(db)
    assert status == 404


def test_the_decider_must_still_hold_the_grant_to_edit(db):
    _seed(db)
    _decide(db)
    db._put('users', MGR, _user(perms=()))
    body, status = _edit(db)
    assert status == 403 and body['error'] == 'not_permitted'


# --- Sending to operations (US3) --------------------------------------------------------------

import smtplib  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

from modules import special_request_pdf  # noqa: E402
from modules.config import IRAQ_TIMEZONE  # noqa: E402
from modules.dates import to_iso  # noqa: E402
from modules.special_requests import (  # noqa: E402
    _finish,
    handle_retry_special_request_send,
    run_send,
)

_APPROVED = {'decision': 'approved', 'deciderId': MGR, 'deciderName': 'مدير المبيعات',
             'decidedAt': '2026-10-01T10:00:00.000', 'note': None}


def _recipient(db, doc_id, email, *, active=True, permissions=('receiveSpecialRequests',), name=''):
    db._put('email_recipients', doc_id, {'email': email, 'name': name or doc_id, 'isActive': active,
                                         'permissions': list(permissions)})


def _ago(minutes):
    return to_iso(datetime.now(IRAQ_TIMEZONE) - timedelta(minutes=minutes))


class _Mail:
    """What the SMTP and PDF steps were asked, and what they answer."""

    def __init__(self):
        self.calls = []
        self.builds = []
        self.admin_notices = []
        self.fail_with = None
        self.undelivered = ()
        self.pdf_error = None


@pytest.fixture
def mail():
    state = _Mail()

    def send_pdf_email(recipients, subject, body, pdf, filename):
        state.calls.append({'recipients': [r['email'] for r in recipients], 'subject': subject,
                            'body': body, 'pdf': pdf, 'filename': filename})
        if state.fail_with:
            raise state.fail_with
        sent = [r['email'] for r in recipients if r['email'] not in state.undelivered]
        failed = [{'email': a, 'error': 'refused'} for a in state.undelivered
                  if a in [r['email'] for r in recipients]]
        return {'sent': sent, 'failed': failed}

    def build(record, source, attachments):
        state.builds.append((record, source, attachments))
        if state.pdf_error:
            raise state.pdf_error
        return b'%PDF-fake'

    def send_to_roles(db, **kwargs):
        state.admin_notices.append(kwargs)
        return {}

    with patch('modules.special_requests.email_module.send_pdf_email', side_effect=send_pdf_email), \
            patch('modules.special_requests.special_request_pdf.build_within_limit', side_effect=build), \
            patch('modules.special_requests.special_request_pdf.fetch_attachments', return_value=[]), \
            patch('modules.special_requests.notifications.send_to_roles', side_effect=send_to_roles):
        yield state


def _awaiting_second(db, **kwargs):
    return _seed(db, salesManagerDecision=_APPROVED, **kwargs)


def _approve_second(db, source='report'):
    payload = {'slot': 'admin'}
    if source == 'visit':
        # A visit is the technical family: it needs the technical review grant.
        for uid, name, role in ((MGR, 'مدير المبيعات', 'salesManager'), (ADM, 'مسؤول النظام', 'admin')):
            db._put('users', uid, _user(perms=('reviewTechnicalReport',), name=name, role=role))
        payload.update({'sourceType': 'visit', 'supportRecordId': 's1', 'visitId': 'v1'})
    return _decide(db, uid=ADM, **payload)


def _sending(db, rid='report_r1', **send):
    return _seed(db, state='approved', salesManagerDecision=_APPROVED,
                 adminDecision={**_APPROVED, 'deciderId': ADM},
                 send={'state': 'sending', 'attemptId': 'a1', 'claimedAt': _ago(1), 'attempts': 1, **send})


def _retry(db, uid=MGR, rid='report_r1'):
    resp = handle_retry_special_request_send({'uid': uid}, {'requestId': rid}, db)
    body, status = resp if isinstance(resp, tuple) else (resp, 200)
    return body.get_json(), status


def test_the_second_approval_claims_the_send_and_sends_once(db, mail):
    _awaiting_second(db)
    _recipient(db, 'e1', 'ops@x.com')
    body, status, _ = _approve_second(db)
    assert status == 200 and body['send'] == {'state': 'sent', 'recipientCount': 1}
    send = _doc(db)['send']
    assert send['state'] == 'sent' and send['attempts'] == 1 and send['deliveredTo'] == ['ops@x.com']
    assert send['sentAt'] and body['request']['send']['state'] == 'sent'
    assert len(mail.calls) == 1


def test_the_first_approval_alone_sends_nothing(db, mail):
    _seed(db)
    _recipient(db, 'e1', 'ops@x.com')
    body, _, _ = _decide(db)
    assert 'send' not in body and _doc(db)['send'] is None and mail.calls == []


@pytest.mark.parametrize('first,second', [(MGR, ADM), (ADM, MGR)])
def test_two_approvals_in_either_order_send_exactly_once(db, mail, first, second):
    _seed(db)
    _recipient(db, 'e1', 'ops@x.com')
    slots = {MGR: 'salesManager', ADM: 'admin'}
    _decide(db, uid=first, slot=slots[first])
    _decide(db, uid=second, slot=slots[second])
    # A late third decision cannot start another send.
    _, status, _ = _decide(db, uid=first, slot=slots[first])
    assert status == 409
    assert len(mail.calls) == 1 and _doc(db)['send']['attempts'] == 1


def test_a_rejected_request_is_never_sent(db, mail):
    _awaiting_second(db)
    _recipient(db, 'e1', 'ops@x.com')
    _decide(db, uid=ADM, slot='admin', decision='rejected')
    assert _doc(db)['state'] == 'rejected' and mail.calls == [] and _doc(db)['send'] is None


def test_a_withdrawn_request_is_never_sent(db, mail):
    _seed(db, state='withdrawn')
    _recipient(db, 'e1', 'ops@x.com')
    _, status, _ = _decide(db)
    assert status == 409 and mail.calls == []


def test_the_email_carries_the_arabic_subject_filename_and_no_internal_id(db, mail):
    _awaiting_second(db)
    _recipient(db, 'e1', 'ops@x.com')
    _approve_second(db)
    call = mail.calls[0]
    assert call['subject'] == 'طلب خاص من العميل - عيادة النور - المندوب - 2026/10/01'
    assert call['filename'] == 'طلب خاص - عيادة النور - 2026-10-01.pdf'
    assert 'r1' not in call['filename'] and 'report_' not in call['filename']
    for line in ('العميل: عيادة النور', 'المندوب: المندوب', 'مرفق ملف الطلب والتقرير الكامل'):
        assert line in call['body']
    assert call['pdf'] == b'%PDF-fake'


def test_the_filename_drops_characters_a_mail_client_rejects(db, mail):
    _awaiting_second(db, clientName='عيادة/النور: "الجديدة"')
    _recipient(db, 'e1', 'ops@x.com')
    _approve_second(db)
    assert mail.calls[0]['filename'] == 'طلب خاص - عيادةالنور الجديدة - 2026-10-01.pdf'


def test_the_pdf_prints_the_decided_text_even_when_the_source_changed_later(db, mail):
    _awaiting_second(db)
    db._put('reports', 'r1', {**db._get_all('reports')['r1'], 'clientOrders': 'نص عدّله المندوب لاحقا'})
    _recipient(db, 'e1', 'ops@x.com')
    _approve_second(db)
    record = mail.builds[0][0]
    assert record['requestText'] == '10 علب'


# --- outcomes ---------------------------------------------------------------------------------

def test_every_recipient_delivered_is_sent(db, mail):
    _awaiting_second(db)
    _recipient(db, 'e1', 'a@x.com')
    _recipient(db, 'e2', 'b@x.com')
    _approve_second(db)
    send = _doc(db)['send']
    assert send['state'] == 'sent' and sorted(send['deliveredTo']) == ['a@x.com', 'b@x.com']
    assert send['recipientCount'] == 2 and send['failedRecipients'] == []
    assert mail.admin_notices == []


def test_some_recipients_failing_is_partial_and_the_admins_are_told(db, mail):
    mail.undelivered = ('b@x.com',)
    _awaiting_second(db)
    _recipient(db, 'e1', 'a@x.com')
    _recipient(db, 'e2', 'b@x.com')
    body, _, _ = _approve_second(db)
    send = _doc(db)['send']
    assert send['state'] == 'partial' and send['deliveredTo'] == ['a@x.com']
    assert send['failedRecipients'] == ['b@x.com'] and body['send']['state'] == 'partial'
    assert len(mail.admin_notices) == 1
    notice = mail.admin_notices[0]
    assert notice['roles'] == ['admin'] and notice['kind'] == 'special_request'
    assert notice['title'] == 'تعذّر إرسال طلب خاص إلى العمليات'
    assert 'العميل: عيادة النور' in notice['body'] and 'المندوب: المندوب' in notice['body']
    assert 'b@x.com' in notice['body']


def test_no_recipients_fails_and_the_admins_are_told(db, mail):
    _awaiting_second(db)
    _approve_second(db)
    send = _doc(db)['send']
    assert send['state'] == 'failed' and send['failureReason'] == 'no_recipients'
    assert len(mail.admin_notices) == 1 and 'لا يوجد مستلمون' in mail.admin_notices[0]['body']


def test_inactive_and_other_category_recipients_do_not_count(db, mail):
    _awaiting_second(db)
    _recipient(db, 'e1', 'off@x.com', active=False)
    _recipient(db, 'e2', 'orders@x.com', permissions=('receiveOrders',))
    _approve_second(db)
    assert _doc(db)['send']['failureReason'] == 'no_recipients' and mail.calls == []


def test_an_oversize_pdf_fails_as_too_large(db, mail):
    mail.pdf_error = special_request_pdf.PdfTooLarge(30_000_000)
    _awaiting_second(db)
    _recipient(db, 'e1', 'a@x.com')
    _approve_second(db)
    send = _doc(db)['send']
    assert send['state'] == 'failed' and send['failureReason'] == 'too_large'
    assert mail.calls == [] and 'حجم الملف أكبر من المسموح' in mail.admin_notices[0]['body']


def test_an_smtp_failure_before_anything_is_sent_fails_as_smtp(db, mail):
    mail.fail_with = smtplib.SMTPAuthenticationError(535, b'bad login')
    _awaiting_second(db)
    _recipient(db, 'e1', 'a@x.com')
    _approve_second(db)
    send = _doc(db)['send']
    assert send['state'] == 'failed' and send['failureReason'] == 'smtp'
    assert send['failedRecipients'] == ['a@x.com'] and send['deliveredTo'] == []


def test_all_recipients_refused_fails_as_smtp(db, mail):
    mail.undelivered = ('a@x.com',)
    _awaiting_second(db)
    _recipient(db, 'e1', 'a@x.com')
    _approve_second(db)
    send = _doc(db)['send']
    assert send['state'] == 'failed' and send['failureReason'] == 'smtp'


def test_a_source_deleted_after_approval_fails_as_source_missing(db, mail):
    _sending(db)
    _recipient(db, 'e1', 'a@x.com')
    db._put('reports', 'r1', {**db._get_all('reports')['r1'], 'reviewState': 'deleted'})
    run_send(db, 'report_r1', 'a1')
    send = _doc(db)['send']
    assert send['state'] == 'failed' and send['failureReason'] == 'source_missing'
    assert mail.calls == [] and 'التقرير محذوف' in mail.admin_notices[0]['body']


def test_a_visit_whose_support_record_was_deleted_fails_as_source_missing(db, mail):
    _seed(db, source='visit', state='approved', salesManagerDecision=_APPROVED,
          adminDecision={**_APPROVED, 'deciderId': ADM},
          send={'state': 'sending', 'attemptId': 'a1', 'claimedAt': _ago(1), 'attempts': 1})
    _recipient(db, 'e1', 'a@x.com')
    db._put('technical_support', 's1', {**db._get_all('technical_support')['s1'], 'reviewState': 'deleted'})
    run_send(db, 'visit_s1_v1', 'a1')
    assert _doc(db, 'visit_s1_v1')['send']['failureReason'] == 'source_missing'


def test_an_exception_inside_send_still_records_the_decision(db, mail):
    mail.pdf_error = RuntimeError('font missing')
    _awaiting_second(db)
    _recipient(db, 'e1', 'a@x.com')
    body, status, _ = _approve_second(db)
    assert status == 200 and body['success'] is True
    doc = _doc(db)
    assert doc['adminDecision']['decision'] == 'approved' and doc['state'] == 'approved'
    assert doc['send']['state'] == 'failed' and doc['send']['failureReason'] == 'internal'
    assert body['send']['failureReason'] == 'internal'


# --- recipients: invalid, duplicates, retries -------------------------------------------------

def test_a_malformed_address_is_excluded_recorded_and_named_to_the_admins(db, mail):
    _awaiting_second(db)
    _recipient(db, 'e1', 'ops@x.com')
    _recipient(db, 'e2', 'ops@@x')
    _approve_second(db)
    send = _doc(db)['send']
    assert mail.calls[0]['recipients'] == ['ops@x.com']
    assert send['invalidRecipients'] == ['ops@@x']
    # Every valid recipient has it, so this is `sent`, not `partial`.
    assert send['state'] == 'sent'
    assert len(mail.admin_notices) == 1 and 'ops@@x' in mail.admin_notices[0]['body']


def test_duplicate_addresses_are_emailed_once(db, mail):
    _awaiting_second(db)
    _recipient(db, 'e1', 'ops@x.com')
    _recipient(db, 'e2', 'OPS@x.com')
    _approve_second(db)
    assert len(mail.calls[0]['recipients']) == 1


def test_a_retry_after_a_partial_delivery_never_emails_a_delivered_address_again(db, mail):
    _sending(db, state='partial', deliveredTo=['a@x.com'], failedRecipients=['b@x.com'])
    db._put('special_requests', 'report_r1',
            {**_doc(db), 'send': {**_doc(db)['send'], 'state': 'partial'}})
    _recipient(db, 'e1', 'a@x.com')
    _recipient(db, 'e2', 'b@x.com')
    body, status = _retry(db)
    assert status == 200 and body['send']['state'] == 'sent'
    assert mail.calls[0]['recipients'] == ['b@x.com']
    send = _doc(db)['send']
    assert sorted(send['deliveredTo']) == ['a@x.com', 'b@x.com'] and send['attempts'] == 2


def test_a_partial_followed_by_a_failing_retry_stays_partial(db, mail):
    mail.undelivered = ('b@x.com',)
    _sending(db, deliveredTo=['a@x.com'])
    db._put('special_requests', 'report_r1', {**_doc(db), 'send': {**_doc(db)['send'], 'state': 'partial'}})
    _recipient(db, 'e1', 'a@x.com')
    _recipient(db, 'e2', 'b@x.com')
    body, _ = _retry(db)
    assert body['send']['state'] == 'partial'
    assert _doc(db)['send']['deliveredTo'] == ['a@x.com']


def test_an_early_exit_after_a_partial_delivery_stays_partial(db, mail):
    _sending(db, deliveredTo=['a@x.com'])
    db._put('special_requests', 'report_r1', {**_doc(db), 'send': {**_doc(db)['send'], 'state': 'partial'}})
    # No recipient is active any more.
    _recipient(db, 'e1', 'a@x.com', active=False)
    body, _ = _retry(db)
    send = _doc(db)['send']
    assert send['state'] == 'partial' and send['failureReason'] == 'no_recipients'
    assert body['send']['state'] == 'partial'


def test_a_deactivated_failing_address_is_dropped_and_a_new_one_included(db, mail):
    _sending(db, deliveredTo=['a@x.com'])
    db._put('special_requests', 'report_r1', {**_doc(db), 'send': {**_doc(db)['send'], 'state': 'partial'}})
    _recipient(db, 'e1', 'a@x.com')
    _recipient(db, 'e2', 'b@x.com', active=False)  # was failing, now off
    _recipient(db, 'e3', 'c@x.com')                # newly added
    body, _ = _retry(db)
    assert mail.calls[0]['recipients'] == ['c@x.com']
    assert body['send']['state'] == 'sent'


def test_a_retry_with_everyone_already_delivered_just_closes_as_sent(db, mail):
    _sending(db, deliveredTo=['a@x.com'])
    db._put('special_requests', 'report_r1', {**_doc(db), 'send': {**_doc(db)['send'], 'state': 'partial'}})
    _recipient(db, 'e1', 'a@x.com')
    body, _ = _retry(db)
    assert mail.calls == [] and body['send']['state'] == 'sent'


# --- retry claim rules ------------------------------------------------------------------------

def test_a_failed_send_can_be_retried(db, mail):
    _sending(db, state='failed', failureReason='no_recipients')
    db._put('special_requests', 'report_r1', {**_doc(db), 'send': {**_doc(db)['send'], 'state': 'failed'}})
    _recipient(db, 'e1', 'a@x.com')
    body, status = _retry(db)
    assert status == 200 and body['send']['state'] == 'sent'
    assert _doc(db)['send']['attempts'] == 2 and _doc(db)['send']['attemptId'] != 'a1'


def test_a_stalled_sending_claim_can_be_retried(db, mail):
    _sending(db, claimedAt=_ago(20))
    _recipient(db, 'e1', 'a@x.com')
    body, status = _retry(db)
    assert status == 200 and body['send']['state'] == 'sent'


@pytest.mark.parametrize('send,state', [
    ({'state': 'sending', 'claimedAt': None}, 'approved'),  # fresh claim (set below)
    ({'state': 'sent', 'deliveredTo': ['a@x.com']}, 'approved'),
    (None, 'awaiting'),
    (None, 'rejected'),
])
def test_anything_else_is_not_retryable(db, mail, send, state):
    _seed(db, state=state, salesManagerDecision=_APPROVED, send=(
        {**send, 'attemptId': 'a1', 'attempts': 1, 'claimedAt': _ago(1)} if send else None))
    _recipient(db, 'e1', 'a@x.com')
    body, status = _retry(db)
    assert status == 409 and body['error'] == 'not_retryable' and mail.calls == []
    assert 'send' in body


def test_a_retry_needs_the_review_grant(db, mail):
    _sending(db, state='failed')
    _, status = _retry(db, uid=REP)
    assert status == 403


def test_retrying_an_unknown_request_is_404(db, mail):
    _seed(db, state=None)
    _, status = _retry(db, rid='report_nope')
    assert status == 404


# --- stale attempts ---------------------------------------------------------------------------

def test_a_stale_attempt_cannot_overwrite_a_newer_one(db, mail):
    _sending(db, attemptId='newer')
    _recipient(db, 'e1', 'a@x.com')
    assert run_send(db, 'report_r1', 'older') is None
    assert _finish(db, 'report_r1', 'older', all_delivered=True) is None
    send = _doc(db)['send']
    assert send['attemptId'] == 'newer' and send['state'] == 'sending' and mail.calls == []


# --- notices carry where to go -----------------------------------------------------------------

def test_a_report_failure_notice_opens_the_report(db, mail):
    _awaiting_second(db)
    _approve_second(db)
    assert mail.admin_notices[0]['message_data'] == {'action': 'open_daily_report', 'reportId': 'r1'}


def test_a_visit_failure_notice_opens_the_support_record(db, mail):
    _seed(db, source='visit', salesManagerDecision=_APPROVED)
    _approve_second(db, source='visit')
    assert mail.admin_notices[0]['message_data'] == {
        'action': 'open_support_record', 'supportRecordId': 's1'}


def test_a_visit_is_sent_with_its_parents_signoffs_and_resolution(db, mail):
    _seed(db, source='visit', salesManagerDecision=_APPROVED)
    parent = db._get_all('technical_support')['s1']
    db._put('technical_support', 's1', {
        **parent,
        'reviews': {'v1__salesManager': {'reviewerName': 'هالة'}},
        'resolutions': {'v1': {'solutionDetails': 'تم الاستبدال'}}})
    _recipient(db, 'e1', 'a@x.com')
    _approve_second(db, source='visit')
    _, source, _ = mail.builds[0]
    assert source['reviews']['salesManager'] == {'reviewerName': 'هالة'}
    assert source['reviews']['admin'] is None
    assert source['resolution'] == {'solutionDetails': 'تم الاستبدال'}
    assert _doc(db, 'visit_s1_v1')['send']['state'] == 'sent'


def test_a_failed_outcome_write_still_keeps_who_was_delivered(db, mail):
    _awaiting_second(db)
    _recipient(db, 'e1', 'a@x.com')
    _recipient(db, 'e2', 'b@x.com')
    real_finish = __import__('modules.special_requests', fromlist=['_finish'])._finish
    calls = {'n': 0}

    def flaky_finish(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('deadline exceeded')
        return real_finish(*args, **kwargs)

    with patch('modules.special_requests._finish', side_effect=flaky_finish):
        _approve_second(db)
    send = _doc(db)['send']
    assert sorted(send['deliveredTo']) == ['a@x.com', 'b@x.com']
    # A retry then has nobody left to email.
    mail.calls.clear()
    _retry(db)
    assert mail.calls == [] and _doc(db)['send']['state'] == 'sent'
