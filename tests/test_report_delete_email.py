"""Deleting a report is emailed to the other manager role and to the rep.

A sales manager's deletion reaches every administrator; an administrator's
reaches every sales manager. Either way the representative whose work the
report recorded is told, and whoever did the deleting never emails themselves.
"""
from unittest.mock import patch

from tests.conftest import FakeFirestore
from modules import review_emails


def _user(role, email, *, preference=True, active=True, name=''):
    return {
        'role': role,
        'email': email,
        'name': name or email.split('@')[0],
        'receiveEmailNotifications': preference,
        'isActive': active,
    }


def _db(**overrides):
    users = {
        'rep': _user('salesRepresentative', 'rep@x.com', name='علي'),
        'admin-1': _user('admin', 'admin1@x.com'),
        'admin-2': _user('admin', 'admin2@x.com'),
        'manager-1': _user('salesManager', 'manager1@x.com'),
    }
    users.update(overrides)
    db = FakeFirestore()
    for user_id, user in users.items():
        db._put('users', user_id, user)
    return db


_REPORT = {
    'reportId': 'r-1',
    'typeLabel': 'تقرير زيارة',
    'repName': 'علي',
    'client': 'مستشفى السلام',
    'day': '2026-10-26',
    'taskReset': True,
}


def _send(db, actor_id, *, report=None, owner='rep'):
    with patch.object(review_emails.mail, '_check_email_config',
                      return_value=(True, None)), \
            patch.object(review_emails.mail, 'send_text_email') as sender:
        sender.side_effect = lambda recipients, *a, **k: {
            'sent': [r['email'] for r in recipients],
            'failed': [],
        }
        result = review_emails.send_report_deleted_email(
            db,
            actor_id=actor_id,
            report=report if report is not None else _REPORT,
            task_owner_id=owner,
        )
    return result, sender


def test_a_sales_managers_deletion_reaches_the_admins_and_the_rep():
    result, _ = _send(_db(), 'manager-1')

    assert sorted(result['sent']) == ['admin1@x.com', 'admin2@x.com', 'rep@x.com']


def test_an_admins_deletion_reaches_the_sales_managers_and_the_rep():
    result, _ = _send(_db(), 'admin-1')

    assert sorted(result['sent']) == ['manager1@x.com', 'rep@x.com']


# Nobody needs an email telling them what they just did.
def test_the_deleter_is_never_emailed():
    result, _ = _send(_db(), 'admin-1')

    assert 'admin1@x.com' not in result['sent']


# A rep removing their own report is still reported to the administrators, who
# own the system whoever acted.
def test_a_reps_own_deletion_reaches_the_admins():
    result, _ = _send(_db(), 'rep')

    assert sorted(result['sent']) == ['admin1@x.com', 'admin2@x.com']


def test_a_recipient_who_turned_emails_off_is_skipped():
    db = _db(**{'admin-2': _user('admin', 'admin2@x.com', preference=False)})

    result, _ = _send(db, 'manager-1')

    assert 'admin2@x.com' not in result['sent']
    assert {'userId': 'admin-2', 'reason': 'preference_off'} in result['skipped']


def test_an_inactive_recipient_is_skipped():
    db = _db(**{'admin-2': _user('admin', 'admin2@x.com', active=False)})

    result, _ = _send(db, 'manager-1')

    assert 'admin2@x.com' not in result['sent']


def test_the_notice_names_the_report_the_actor_and_the_task_reset():
    db = _db()

    _, sender = _send(db, 'manager-1')

    subject, body = sender.call_args.args[1], sender.call_args.args[2]
    assert 'حذف تقرير' in subject
    assert 'تقرير زيارة' in subject
    assert 'مستشفى السلام' in body
    assert 'مدير المبيعات' in body
    assert 'قيد الانجاز' in body


# The report is the only record of the visit, so a second call for the same
# deletion must not email the same people again.
def test_the_same_deletion_is_not_emailed_twice():
    db = _db()

    _send(db, 'manager-1')
    again, sender = _send(db, 'manager-1')

    assert again == {'skippedReason': 'already_sent'}
    sender.assert_not_called()


def test_a_notice_without_a_report_id_sends_nothing():
    result, sender = _send(_db(), 'manager-1', report={'typeLabel': 'تقرير'})

    assert result == {'skippedReason': 'report_id_missing'}
    sender.assert_not_called()


# The rep is read by id rather than by role: a report may belong to someone
# who has since changed role, and they are still the one who filed it.
def test_a_missing_task_owner_does_not_stop_the_managers_being_told():
    result, _ = _send(_db(), 'manager-1', owner='no-such-user')

    assert sorted(result['sent']) == ['admin1@x.com', 'admin2@x.com']
    assert {'userId': 'no-such-user', 'reason': 'not_found'} in result['skipped']
