"""A review is emailed to the representative and the other reviewer role.

An administrator's review reaches the representative and every sales manager;
a sales manager's reaches the representative and every administrator. The
email is built from the stored record, so it carries the note and the solution
on record whichever app made the call.
"""
from email import message_from_string
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import FakeFirestore
from modules import email as email_module
from modules import review_emails
from modules.notifications import handle_send_review_notification


def _user(role, email, *, preference=True, active=True, name=''):
    return {
        'role': role,
        'email': email,
        'name': name or email.split('@')[0],
        'fcmToken': f'token-{email}',
        'receiveEmailNotifications': preference,
        'isActive': active,
    }


def _review(reviewer_id, name, at, note=None):
    return {'reviewerId': reviewer_id, 'reviewerName': name, 'reviewedAt': at, 'note': note}


def _db(**user_overrides):
    users = {
        'rep': _user('salesRepresentative', 'rep@x.com', name='علي'),
        'tech': _user('technicalSupport', 'tech@x.com', name='فني'),
        'admin-1': _user('admin', 'admin1@x.com'),
        'admin-2': _user('admin', 'admin2@x.com'),
        'manager-1': _user('salesManager', 'manager1@x.com'),
        'manager-2': _user('salesManager', 'manager2@x.com'),
    }
    users.update(user_overrides)
    db = FakeFirestore()
    for user_id, user in users.items():
        if user is not None:
            db._put('users', user_id, user)
    db._put('clients', 'client-1', {'name': 'مستشفى النور'})
    return db


def _put_report(db, report_id='report-1', **fields):
    report = {
        'type': 'call',
        'assignedToId': 'rep',
        'userName': 'علي',
        'clientId': 'client-1',
        'createdAt': '2026-10-24T16:53:37.857',
        'problems': 'الجهاز لا يعمل',
        'hasProblems': True,
        'suggestSolutions': 'استبدال القطعة',
        'solved': True,
        'solutionDetails': 'تم الاستبدال',
        'solvedByName': 'همام',
    }
    report.update(fields)
    db._put('reports', report_id, report)


def _put_visits(db, reviews, record_id='support-1', resolutions=None):
    db._put('technical_support', record_id, {
        'clientName': 'مستشفى الشفاء',
        'technicianId': 'tech',
        'technicianName': 'فني',
        'visitHistory': [
            {'id': 'visit-1', 'technicianId': 'tech', 'technicianName': 'فني',
             'visitDate': '2026-10-21T11:11:51.405', 'visitType': 'maintenance',
             'completionReport': {'hasProblem': True, 'problemDescription': 'عطل في الشاشة',
                                  'suggestedSolution': 'تبديل الشاشة'}},
            {'id': 'visit-2', 'technicianId': 'tech', 'technicianName': 'فني',
             'visitDate': '2026-10-22T09:00:00.000', 'visitType': 'periodicVisit',
             'completionReport': {'hasProblem': False}},
        ],
        'reviews': reviews,
        'resolutions': resolutions or {},
    })


@pytest.fixture
def smtp():
    with patch.object(email_module.smtplib, 'SMTP') as cls, \
            patch.object(email_module, 'EMAIL_SMTP_PASSWORD', 'app-password'):
        server = MagicMock()
        cls.return_value = server
        yield server


def _sent(server):
    """(to, decoded plain-text body, subject) for each email sent."""
    out = []
    for call in server.sendmail.call_args_list:
        message = message_from_string(call.args[2])
        text = message.get_payload(0).get_payload(decode=True).decode('utf-8')
        from email.header import decode_header, make_header
        subject = str(make_header(decode_header(message['Subject'])))
        out.append((call.args[1], text, subject))
    return out


def _recipients(server):
    return sorted(to for to, _, _ in _sent(server))


def _call(db, reviewer, route, reviewer_role='admin', push_error=None):
    data = {
        'title': 'مراجعة',
        'body': 'محتوى',
        'reviewerRole': reviewer_role,
        'representativeId': 'rep',
        'notificationAction': route,
    }
    with patch('modules.notifications.messaging') as messaging:
        messaging.UnregisteredError = type('U', (Exception,), {})
        messaging.SenderIdMismatchError = type('S', (Exception,), {})
        if push_error:
            messaging.send_each_for_multicast.side_effect = push_error
        else:
            response = MagicMock()
            response.responses = []
            response.success_count = 0
            response.failure_count = 0
            messaging.send_each_for_multicast.return_value = response
        result = handle_send_review_notification({'uid': reviewer}, data, db)
    body, status = result
    return body.get_json(), status


_REPORT = {'action': 'open_daily_report', 'reportId': 'report-1', 'event': 'review'}


class TestSalesReportAudience:
    def test_admin_review_reaches_representative_and_sales_managers(self, smtp):
        db = _db()
        _put_report(db, adminReview=_review('admin-1', 'زيد', '2026-10-25T10:04:46.095'))

        body, status = _call(db, 'admin-1', _REPORT)

        assert status == 200
        assert _recipients(smtp) == ['manager1@x.com', 'manager2@x.com', 'rep@x.com']
        assert sorted(body['email']['sent']) == ['manager1@x.com', 'manager2@x.com', 'rep@x.com']

    def test_sales_manager_review_reaches_representative_and_admins(self, smtp):
        db = _db()
        _put_report(db, salesManagerReview=_review('manager-1', 'همام', '2026-10-24T20:43:58.100'))

        _call(db, 'manager-1', _REPORT, reviewer_role='salesManager')

        assert _recipients(smtp) == ['admin1@x.com', 'admin2@x.com', 'rep@x.com']

    def test_audience_follows_who_reviewed_not_which_slot(self, smtp):
        # An administrator may sign the sales-manager slot; the sales managers
        # are still the ones told.
        db = _db()
        _put_report(db, salesManagerReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000'))

        _call(db, 'admin-1', _REPORT, reviewer_role='salesManager')

        assert _recipients(smtp) == ['manager1@x.com', 'manager2@x.com', 'rep@x.com']

    def test_reviewer_of_their_own_report_is_not_emailed(self, smtp):
        db = _db(rep=_user('admin', 'rep@x.com'))
        _put_report(db, adminReview=_review('rep', 'زيد', '2026-10-25T10:00:00.000'))

        _call(db, 'rep', _REPORT)

        assert _recipients(smtp) == ['manager1@x.com', 'manager2@x.com']

    def test_technical_report_reaches_the_technician(self, smtp):
        db = _db()
        _put_report(db, type='technical_support', assignedToId='tech', userName='فني',
                    adminReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000'))

        _call(db, 'admin-1', _REPORT)

        assert _recipients(smtp) == ['manager1@x.com', 'manager2@x.com', 'tech@x.com']
        _, text, subject = _sent(smtp)[0]
        assert 'تقرير دعم فني' in subject
        assert 'الفني: فني' in text


class TestContent:
    def test_email_carries_the_note_problem_and_solution_on_record(self, smtp):
        db = _db()
        _put_report(db, adminReview=_review('admin-1', 'زيد', '2026-10-25T10:04:46.095',
                                            note='يرجى المتابعة غداً'))

        _call(db, 'admin-1', _REPORT)

        _, text, subject = _sent(smtp)[0]
        assert subject == 'مراجعة مسؤول النظام لتقرير مبيعات - علي - 2026-10-24'
        for line in ('المندوب: علي', 'العميل: مستشفى النور', 'النشاط: مكالمة هاتفية',
                     'تاريخ التقرير: 2026-10-24', 'المراجِع: زيد (مسؤول النظام)',
                     'تاريخ المراجعة: 2026-10-25 10:04', 'ملاحظة المراجعة: يرجى المتابعة غداً',
                     'المشكلة: الجهاز لا يعمل', 'الحل المقترح: استبدال القطعة',
                     'الحل: تم الاستبدال (سجّله همام)',
                     'حالة المراجعات: مدير المبيعات: بانتظار المراجعة | مسؤول النظام: تمت'):
            assert line in text

    def test_recipient_is_greeted_by_name(self, smtp):
        db = _db()
        _put_report(db, adminReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000'))

        _call(db, 'admin-1', _REPORT)

        texts = {to: text for to, text, _ in _sent(smtp)}
        assert texts['rep@x.com'].startswith('مرحباً علي،')

    def test_open_problem_says_no_solution_yet(self, smtp):
        db = _db()
        _put_report(db, solved=False, solutionDetails=None,
                    adminReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000'))

        _call(db, 'admin-1', _REPORT)

        _, text, _ = _sent(smtp)[0]
        assert 'الحل: لم يُسجَّل حل بعد' in text
        assert 'ملاحظة المراجعة: لا توجد ملاحظة' in text

    def test_html_part_is_right_to_left_and_escaped(self, smtp):
        db = _db()
        _put_report(db, adminReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000',
                                            note='<b>x</b>'))

        _call(db, 'admin-1', _REPORT)

        message = message_from_string(smtp.sendmail.call_args_list[0].args[2])
        html = message.get_payload(1).get_payload(decode=True).decode('utf-8')
        assert 'dir="rtl"' in html
        assert '&lt;b&gt;x&lt;/b&gt;' in html


class TestSupportVisits:
    def test_visit_review_reaches_the_technician_with_its_resolution(self, smtp):
        db = _db()
        _put_visits(db, {'visit-1__salesManager': _review('manager-1', 'همام', '2026-10-22T18:31:50.759',
                                                          note='ممتاز')},
                    resolutions={'visit-1': {'solutionDetails': 'تم تبديل الشاشة',
                                             'resolvedByName': 'زيد'}})

        _call(db, 'manager-1', {'action': 'open_support_record', 'supportRecordId': 'support-1',
                                'visitId': 'visit-1', 'event': 'review'},
              reviewer_role='salesManager')

        assert _recipients(smtp) == ['admin1@x.com', 'admin2@x.com', 'tech@x.com']
        _, text, subject = _sent(smtp)[0]
        assert subject == 'مراجعة مدير المبيعات لزيارة دعم فني - فني - 2026-10-21'
        for line in ('العميل: مستشفى الشفاء', 'نوع الزيارة: صيانة', 'ملاحظة المراجعة: ممتاز',
                     'المشكلة: عطل في الشاشة', 'الحل المقترح: تبديل الشاشة',
                     'الحل: تم تبديل الشاشة (سجّله زيد)'):
            assert line in text

    def test_visit_is_found_without_its_id(self, smtp):
        # Older dashboard builds do not send visitId: the visit is the one whose
        # newest sign-off is the caller's.
        db = _db()
        _put_visits(db, {
            'visit-1__admin': _review('admin-1', 'زيد', '2026-10-21T21:21:36.515'),
            'visit-2__admin': _review('admin-1', 'زيد', '2026-10-23T08:00:00.000'),
        })

        _call(db, 'admin-1', {'action': 'open_support_record', 'supportRecordId': 'support-1'})

        _, text, _ = _sent(smtp)[0]
        assert 'نوع الزيارة: زيارة دورية' in text


class TestRecipientRules:
    def test_preference_off_inactive_and_missing_email_are_skipped_and_reported(self, smtp):
        db = _db(**{
            'manager-1': _user('salesManager', 'manager1@x.com', preference=False),
            'manager-2': _user('salesManager', 'manager2@x.com', active=False),
            'manager-3': _user('salesManager', 'not-an-email'),
        })
        _put_report(db, adminReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000'))

        body, _ = _call(db, 'admin-1', _REPORT)

        assert _recipients(smtp) == ['rep@x.com']
        skipped = body['email']['skipped']
        assert {'userId': 'manager-1', 'reason': 'preference_off'} in skipped
        assert {'userId': 'manager-2', 'reason': 'inactive'} in skipped
        assert {'userId': 'manager-3', 'reason': 'no_email'} in skipped

    def test_shared_mailbox_gets_one_email(self, smtp):
        db = _db(**{'manager-2': _user('salesManager', 'MANAGER1@x.com')})
        _put_report(db, adminReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000'))

        _call(db, 'admin-1', _REPORT)

        assert [to.lower() for to in _recipients(smtp)].count('manager1@x.com') == 1


class TestReliability:
    def test_repeated_call_for_one_sign_off_emails_once(self, smtp):
        db = _db()
        _put_report(db, adminReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000'))

        _call(db, 'admin-1', _REPORT)
        body, _ = _call(db, 'admin-1', _REPORT)

        assert smtp.sendmail.call_count == 3
        assert body['email'] == {'skippedReason': 'already_sent'}

    def test_each_slot_is_emailed_on_its_own(self, smtp):
        db = _db()
        _put_report(db, salesManagerReview=_review('manager-1', 'همام', '2026-10-24T20:00:00.000'))
        _call(db, 'manager-1', _REPORT, reviewer_role='salesManager')
        _put_report(db, salesManagerReview=_review('manager-1', 'همام', '2026-10-24T20:00:00.000'),
                    adminReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000'))

        _call(db, 'admin-1', _REPORT)

        assert smtp.sendmail.call_count == 6

    def test_smtp_login_failure_is_logged_and_retried_next_time(self, smtp):
        import smtplib
        db = _db()
        _put_report(db, adminReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000'))
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b'bad credentials')

        body, status = _call(db, 'admin-1', _REPORT)

        assert status == 200
        assert body['email']['sent'] == []
        assert len(body['email']['failed']) == 3
        log = db._store['review_emails']['report__report-1__admin']
        assert log['status'] == 'failed'

        smtp.login.side_effect = None
        _call(db, 'admin-1', _REPORT)
        assert smtp.sendmail.call_count == 3
        assert db._store['review_emails']['report__report-1__admin']['status'] == 'sent'

    def test_push_failure_does_not_stop_the_email(self, smtp):
        db = _db()
        _put_report(db, adminReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000'))

        body, status = _call(db, 'admin-1', _REPORT, push_error=RuntimeError('fcm down'))

        assert status == 200
        assert len(body['email']['sent']) == 3

    def test_unsynced_review_sends_nothing_and_leaves_no_log(self, smtp):
        db = _db()
        _put_report(db)  # neither slot signed yet

        body, _ = _call(db, 'admin-1', _REPORT)

        assert smtp.sendmail.call_count == 0
        assert body['email'] == {'skippedReason': 'not_signed'}
        assert 'review_emails' not in db._store

    def test_missing_smtp_password_is_reported(self):
        db = _db()
        _put_report(db, adminReview=_review('admin-1', 'زيد', '2026-10-25T10:00:00.000'))
        with patch.object(email_module, 'EMAIL_SMTP_PASSWORD', None):
            body, status = _call(db, 'admin-1', _REPORT)

        assert status == 200
        assert body['email']['error'] == 'email_not_configured'

    def test_unknown_record_is_skipped(self, smtp):
        body, _ = _call(_db(), 'admin-1', _REPORT)

        assert body['email'] == {'skippedReason': 'record_not_found'}
        assert smtp.sendmail.call_count == 0


def test_never_raises():
    db = MagicMock()
    db.collection.side_effect = RuntimeError('firestore down')

    result = review_emails.send_review_email(
        db, actor_id='admin-1', actor_role='admin', slot_hint='admin', route=_REPORT)

    assert result == {'error': 'firestore down'}
