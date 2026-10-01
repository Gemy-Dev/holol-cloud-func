"""send_pdf_email: one SMTP connection, per-recipient results, UTF-8 filenames."""
from email import message_from_string
from email.header import decode_header
from unittest.mock import MagicMock, patch

import pytest

from modules import email as email_module


@pytest.fixture
def smtp():
    with patch.object(email_module.smtplib, 'SMTP') as cls:
        server = MagicMock()
        cls.return_value = server
        yield server


def _sent_messages(server):
    return [message_from_string(call.args[2]) for call in server.sendmail.call_args_list]


def test_sends_one_message_per_recipient_over_one_connection(smtp):
    result = email_module.send_pdf_email(
        [{'email': 'a@x.com', 'name': 'أحمد'}, {'email': 'b@x.com', 'name': ''}],
        'subject', 'body', b'%PDF-1', 'report.pdf')
    assert result == {'sent': ['a@x.com', 'b@x.com'], 'failed': []}
    assert smtp.login.call_count == 1
    smtp.quit.assert_called_once()


def test_greeting_only_when_a_name_is_known(smtp):
    email_module.send_pdf_email(
        [{'email': 'a@x.com', 'name': 'أحمد'}, {'email': 'b@x.com', 'name': ''}],
        's', 'body', b'%PDF', 'r.pdf')
    first, second = _sent_messages(smtp)
    assert 'أحمد' in first.get_payload(0).get_payload(decode=True).decode('utf-8')
    assert second.get_payload(0).get_payload(decode=True).decode('utf-8') == 'body'


def test_non_ascii_filename_is_rfc2231_encoded(smtp):
    email_module.send_pdf_email([{'email': 'a@x.com', 'name': ''}], 's', 'b', b'%PDF',
                                'طلب خاص - عميل - 2026-09-30.pdf')
    attachment = _sent_messages(smtp)[0].get_payload(1)
    assert attachment.get_filename() == 'طلب خاص - عميل - 2026-09-30.pdf'


def test_a_failing_recipient_does_not_stop_the_others(smtp):
    def sendmail(sender, to, msg):
        if to == 'bad@x.com':
            raise RuntimeError('rejected')
    smtp.sendmail.side_effect = sendmail
    result = email_module.send_pdf_email(
        [{'email': 'bad@x.com', 'name': ''}, {'email': 'ok@x.com', 'name': ''}],
        's', 'b', b'%PDF', 'r.pdf')
    assert result['sent'] == ['ok@x.com']
    assert result['failed'] == [{'email': 'bad@x.com', 'error': 'rejected'}]


def test_quit_runs_even_when_login_fails(smtp):
    smtp.login.side_effect = email_module.smtplib.SMTPAuthenticationError(535, b'no')
    with pytest.raises(email_module.smtplib.SMTPAuthenticationError):
        email_module.send_pdf_email([{'email': 'a@x.com', 'name': ''}], 's', 'b', b'%PDF', 'r.pdf')
    smtp.quit.assert_called_once()


def test_the_smtp_connection_has_a_timeout(smtp):
    with patch.object(email_module.smtplib, 'SMTP') as cls:
        cls.return_value = MagicMock()
        email_module.send_pdf_email([{'email': 'a@x.com'}], 's', 'b', b'%PDF', 'f.pdf')
    assert cls.call_args.kwargs.get('timeout') == email_module.SMTP_TIMEOUT_S
