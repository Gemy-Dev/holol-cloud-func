"""Email module for sending emails."""
import smtplib
import re
import base64
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from flask import jsonify
from modules.config import (
    EMAIL_SMTP_HOST,
    EMAIL_SMTP_PORT,
    EMAIL_SMTP_USER,
    EMAIL_SMTP_PASSWORD,
    EMAIL_FROM_ADDRESS,
    EMAIL_FROM_NAME,
    SMTP_TIMEOUT_S,
)


def _validate_email(email):
    """Validate email address format.
    
    Args:
        email: Email address string
        
    Returns:
        True if valid, False otherwise
    """
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def _normalize_emails(emails):
    """Normalize email input to a list.
    
    Args:
        emails: Can be a single email string or a list of emails
        
    Returns:
        List of email strings
        
    Raises:
        ValueError: If emails are invalid or empty
    """
    if not emails:
        raise ValueError("Email addresses are required")
    
    # Convert single email to list
    if isinstance(emails, str):
        emails = [emails]
    elif not isinstance(emails, list):
        raise ValueError("Emails must be a string or a list of strings")
    
    # Validate and normalize each email
    normalized_emails = []
    for email in emails:
        if not isinstance(email, str):
            raise ValueError(f"Invalid email type: {type(email)}")
        
        email = email.strip()
        if not email:
            continue
        
        if not _validate_email(email):
            raise ValueError(f"Invalid email format: {email}")
        
        normalized_emails.append(email)
    
    if not normalized_emails:
        raise ValueError("No valid email addresses provided")
    
    return normalized_emails


def _check_email_config():
    """Check if email configuration is valid.
    
    Returns:
        tuple: (is_valid, error_message)
    """
    if not EMAIL_SMTP_HOST:
        return False, "EMAIL_SMTP_HOST is not configured"
    
    if not EMAIL_SMTP_USER:
        return False, "EMAIL_SMTP_USER is not configured"
    
    if not EMAIL_SMTP_PASSWORD:
        return False, "EMAIL_SMTP_PASSWORD is not configured"
    
    if not EMAIL_FROM_ADDRESS:
        return False, "EMAIL_FROM_ADDRESS is not configured"
    
    return True, None


def _fetch_email_recipients(db):
    """Fetch email addresses from users collection where receiveEmailNotifications is true.

    Args:
        db: Firestore database instance

    Returns:
        List of email addresses (strings)

    Raises:
        Exception: If query fails
    """
    try:
        recipient_emails = []

        # Query users where receiveEmailNotifications is true
        users_query = (
            db.collection("users")
            .where("receiveEmailNotifications", "==", True)
            .stream()
        )

        for user_doc in users_query:
            user_data = user_doc.to_dict()
            email = user_data.get("email")

            # Validate and add email
            if email and isinstance(email, str):
                email = email.strip()
                if email and _validate_email(email):
                    recipient_emails.append(email)

        return recipient_emails

    except Exception as e:
        raise Exception(f"Failed to fetch email recipients from users collection: {str(e)}")


def _fetch_recipients_by_permission(db, permission):
    """Fetch recipients from email_recipients collection filtered by permission.

    Args:
        db: Firestore database instance
        permission: Permission string to filter by (e.g. 'receiveDailyReport', 'receiveOrders')

    Returns:
        List of dicts with 'email' and 'name' keys

    Raises:
        Exception: If query fails
    """
    try:
        recipients = []

        # Query email_recipients where isActive is true and permissions contains the permission
        query = (
            db.collection("email_recipients")
            .where("isActive", "==", True)
            .where("permissions", "array_contains", permission)
            .stream()
        )

        for doc in query:
            data = doc.to_dict()
            email = data.get("email", "")
            name = data.get("name", "")

            if email and isinstance(email, str):
                email = email.strip()
                if email and _validate_email(email):
                    recipients.append({"email": email, "name": name})

        return recipients

    except Exception as e:
        raise Exception(f"Failed to fetch email recipients: {str(e)}")


def _fetch_recipient_names(db):
    """Return a mapping of lowercased email -> recipient name.

    Used to personalise emails when the caller supplies an explicit list of
    recipient addresses (the in-app recipients picker) rather than relying on a
    permission-based mailing list. Best effort: failures yield an empty map so
    the email is still sent (just without a personalised greeting).

    Args:
        db: Firestore database instance

    Returns:
        dict: {email_lowercased: name}
    """
    names = {}
    try:
        for doc in db.collection("email_recipients").stream():
            data = doc.to_dict()
            email = data.get("email", "")
            name = data.get("name", "")
            if email and isinstance(email, str):
                names[email.strip().lower()] = name
    except Exception as e:
        print(f"Could not fetch recipient names for personalisation: {str(e)}")
    return names


def send_email(title, body, db):
    """Send email with title and body to users with receiveEmailNotifications enabled.
    
    Fetches email addresses from Firestore users collection where 
    receiveEmailNotifications field is true.
    Uses SMTP configuration from environment variables.
    
    Args:
        title: Email subject/title
        body: Email body content (plain text)
        db: Firestore database instance
        
    Returns:
        JSON response with success status and details
    """
    try:
        # Validate inputs
        if not title or not isinstance(title, str):
            return jsonify({
                "success": False,
                "error": "Title is required and must be a string"
            }), 400
        
        if not body or not isinstance(body, str):
            return jsonify({
                "success": False,
                "error": "Body is required and must be a string"
            }), 400
        
        # Validate email configuration
        config_valid, config_error = _check_email_config()
        if not config_valid:
            return jsonify({
                "success": False,
                "error": f"Email configuration error: {config_error}"
            }), 500
        
        # Fetch email recipients from Firestore users collection
        try:
            recipient_emails = _fetch_email_recipients(db)
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500
        
        # Check if any recipients found
        if not recipient_emails:
            return jsonify({
                "success": False,
                "error": "No users found with receiveEmailNotifications enabled",
                "totalRecipients": 0
            }), 404
        
        # Create message
        msg = MIMEMultipart()
        msg['From'] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>"
        msg['To'] = ", ".join(recipient_emails)
        msg['Subject'] = title
        
        # Add body to email
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # Send email via SMTP
        # Gmail: Port 587 uses TLS (starttls), Port 465 uses SSL (SMTP_SSL)
        try:
            if EMAIL_SMTP_PORT == 465:
                # Use SSL for port 465 (Gmail)
                server = smtplib.SMTP_SSL(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT)
            else:
                # Use TLS for port 587 (Gmail) or other ports
                server = smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT)
                server.starttls()  # Enable TLS encryption
            
            server.login(EMAIL_SMTP_USER, EMAIL_SMTP_PASSWORD)
            
            # Send email to all recipients
            failed_recipients = []
            successful_recipients = []
            
            for recipient in recipient_emails:
                try:
                    # Create individual message for each recipient
                    individual_msg = MIMEMultipart()
                    individual_msg['From'] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>"
                    individual_msg['To'] = recipient
                    individual_msg['Subject'] = title
                    individual_msg.attach(MIMEText(body, 'plain', 'utf-8'))
                    
                    server.sendmail(EMAIL_FROM_ADDRESS, recipient, individual_msg.as_string())
                    successful_recipients.append(recipient)
                    print(f"✅ Email sent successfully to: {recipient}")
                except Exception as recipient_error:
                    failed_recipients.append({
                        "email": recipient,
                        "error": str(recipient_error)
                    })
                    print(f"❌ Failed to send email to {recipient}: {str(recipient_error)}")
            
            # Close server connection
            server.quit()
            
            # Prepare response
            response = {
                "success": True,
                "message": f"Email sent to {len(successful_recipients)} recipient(s)",
                "totalRecipients": len(recipient_emails),
                "successfulRecipients": len(successful_recipients),
                "failedRecipients": len(failed_recipients),
            }
            
            if successful_recipients:
                response["sentTo"] = successful_recipients
            
            if failed_recipients:
                response["failedTo"] = failed_recipients
                response["partialSuccess"] = True
            
            # If all failed, return error
            if len(successful_recipients) == 0:
                return jsonify({
                    "success": False,
                    "error": "Failed to send email to all recipients",
                    "failedTo": failed_recipients,
                    "totalRecipients": len(recipient_emails)
                }), 500
            
            # If some failed, return 207 (multi-status)
            if failed_recipients:
                return jsonify(response), 207
            
            return jsonify(response), 200
                
        except smtplib.SMTPAuthenticationError:
            return jsonify({
                "success": False,
                "error": "SMTP authentication failed. Please check your email credentials."
            }), 500
        
        except smtplib.SMTPConnectError:
            return jsonify({
                "success": False,
                "error": f"Failed to connect to SMTP server {EMAIL_SMTP_HOST}:{EMAIL_SMTP_PORT}"
            }), 500
        
        except smtplib.SMTPException as smtp_error:
            return jsonify({
                "success": False,
                "error": f"SMTP error: {str(smtp_error)}"
            }), 500
        
    except Exception as e:
        error_msg = f"Error sending email: {str(e)}"
        print(f"❌ {error_msg}")
        print(traceback.format_exc())
        return jsonify({
            "success": False,
            "error": error_msg
        }), 500


def send_pdf_email(recipients, subject, body_text, pdf_bytes, filename):
    """Email ``pdf_bytes`` to each recipient over one SMTP connection.

    Args:
        recipients: list of ``{"email", "name"}``. A known name adds an Arabic
            greeting; hand-picked recipients without one get the bare body.
        filename: attachment name. Sent RFC 2231 UTF-8 encoded, so Arabic names
            survive every mail client.

    Returns:
        ``{"sent": [emails], "failed": [{"email", "error"}]}``. One recipient
        failing never stops the others.

    Raises:
        smtplib.SMTPException: connecting or logging in failed, so nothing was
            sent. Callers map it to their own error response.
    """
    # A timeout, so a hung mail server fails this send (recorded, retryable)
    # instead of holding the request open until the function itself times out.
    if EMAIL_SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, timeout=SMTP_TIMEOUT_S)
    else:
        server = smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, timeout=SMTP_TIMEOUT_S)
        server.starttls()

    sent = []
    failed = []
    try:
        server.login(EMAIL_SMTP_USER, EMAIL_SMTP_PASSWORD)
        for recipient in recipients:
            recipient_email = recipient.get("email", "")
            try:
                recipient_name = recipient.get("name") or ""
                msg = MIMEMultipart()
                msg['From'] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>"
                msg['To'] = recipient_email
                msg['Subject'] = subject
                personalized = f"مرحباً {recipient_name},\n\n{body_text}" if recipient_name else body_text
                msg.attach(MIMEText(personalized, 'plain', 'utf-8'))
                attachment = MIMEApplication(pdf_bytes, _subtype='pdf')
                attachment.add_header(
                    'Content-Disposition', 'attachment', filename=('utf-8', '', filename)
                )
                msg.attach(attachment)
                server.sendmail(EMAIL_FROM_ADDRESS, recipient_email, msg.as_string())
                sent.append(recipient_email)
                print(f"PDF email sent to: {recipient_email}")
            except Exception as recipient_error:
                failed.append({"email": recipient_email, "error": str(recipient_error)})
                print(f"Failed to send PDF email to {recipient_email}: {recipient_error}")
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return {"sent": sent, "failed": failed}


def send_daily_report(data, db):
    """Send daily report PDF via email to users with receiveEmailNotifications enabled.

    Args:
        data: Dictionary containing reportId, userId, userName, date, pdfBase64, tasksCount
        db: Firestore database instance

    Returns:
        JSON response with success status and details
    """
    try:
        # Validate required fields
        report_id = data.get("reportId")
        user_id = data.get("userId")
        user_name = data.get("userName", "")
        date = data.get("date", "")
        pdf_base64 = data.get("pdfBase64")
        tasks_count = data.get("tasksCount", 0)

        if not report_id:
            return jsonify({"success": False, "error": "reportId is required"}), 400

        if not user_id:
            return jsonify({"success": False, "error": "userId is required"}), 400

        if not pdf_base64:
            return jsonify({"success": False, "error": "pdfBase64 is required"}), 400

        # Validate email configuration
        config_valid, config_error = _check_email_config()
        if not config_valid:
            return jsonify({
                "success": False,
                "error": f"Email configuration error: {config_error}"
            }), 500

        # Decode the base64 PDF
        try:
            pdf_bytes = base64.b64decode(pdf_base64)
        except Exception:
            return jsonify({"success": False, "error": "Invalid pdfBase64 data"}), 400

        # Optional per-report customisation. When these fields are absent the
        # email is byte-for-byte identical to the original daily report, so
        # existing callers are unaffected.
        report_permission = data.get("permission") or "receiveDailyReport"

        # When the caller supplies an explicit list of recipient emails (the
        # in-app recipients picker), honour that selection instead of the
        # permission-based mailing list. Falls back to the permission list when
        # no emails are provided, so existing callers keep working unchanged.
        explicit_emails = data.get("emails")
        if explicit_emails:
            if not isinstance(explicit_emails, list):
                return jsonify({
                    "success": False,
                    "error": "emails must be a list of email addresses"
                }), 400

            # Validate and de-duplicate the requested addresses.
            seen = set()
            cleaned_emails = []
            invalid_emails = []
            for raw in explicit_emails:
                if not isinstance(raw, str):
                    invalid_emails.append(str(raw))
                    continue
                email = raw.strip()
                if not email:
                    continue
                if not _validate_email(email):
                    invalid_emails.append(email)
                    continue
                key = email.lower()
                if key in seen:
                    continue
                seen.add(key)
                cleaned_emails.append(email)

            if invalid_emails:
                return jsonify({
                    "success": False,
                    "error": f"Invalid email addresses: {', '.join(invalid_emails)}"
                }), 400

            # Best-effort name lookup so the greeting stays personalised.
            names_by_email = _fetch_recipient_names(db)
            recipients = [
                {"email": email, "name": names_by_email.get(email.lower(), "")}
                for email in cleaned_emails
            ]
        else:
            # Fetch recipients with the requested permission from email_recipients
            try:
                recipients = _fetch_recipients_by_permission(db, report_permission)
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        if not recipients:
            return jsonify({
                "success": False,
                "error": "No active recipients found with receiveDailyReport permission",
                "totalRecipients": 0
            }), 404

        # Build email subject and body (overridable per report type)
        subject = data.get("subject") or f"التقرير اليومي - {user_name} - {date}"
        body_text = data.get("bodyText") or (
            f"التقرير اليومي\n\n"
            f"الاسم: {user_name}\n"
            f"التاريخ: {date}\n"
            f"عدد المهام: {tasks_count}\n\n"
            f"يرجى الاطلاع على التقرير المرفق."
        )
        filename_prefix = data.get("filenamePrefix") or "report"

        # Send email via SMTP
        try:
            pdf_filename = f"{filename_prefix}_{user_name}_{date}.pdf"
            outcome = send_pdf_email(recipients, subject, body_text, pdf_bytes, pdf_filename)
            successful_recipients = outcome["sent"]
            failed_recipients = outcome["failed"]

            if len(successful_recipients) == 0:
                return jsonify({
                    "success": False,
                    "error": "Failed to send daily report to all recipients",
                    "failedTo": failed_recipients,
                    "totalRecipients": len(recipients)
                }), 500

            response = {
                "success": True,
                "message": f"Daily report sent to {len(successful_recipients)} recipient(s)",
                "totalRecipients": len(recipients),
                "successfulRecipients": len(successful_recipients),
                "failedRecipients": len(failed_recipients),
            }

            if failed_recipients:
                response["failedTo"] = failed_recipients
                return jsonify(response), 207

            return jsonify(response), 200

        except smtplib.SMTPAuthenticationError:
            return jsonify({
                "success": False,
                "error": "SMTP authentication failed. Please check your email credentials."
            }), 500

        except smtplib.SMTPException as smtp_error:
            return jsonify({
                "success": False,
                "error": f"SMTP error: {str(smtp_error)}"
            }), 500

    except Exception as e:
        error_msg = f"Error sending daily report: {str(e)}"
        print(f"{error_msg}")
        print(traceback.format_exc())
        return jsonify({"success": False, "error": error_msg}), 500


def notify_new_deal(data, db):
    """Send email notification for a new deal/order to recipients with receiveOrders permission.

    Args:
        data: Dictionary containing deal details (clientId, productId, amount, price, preparationDate, remarks, status)
        db: Firestore database instance

    Returns:
        JSON response with success status and details
    """
    try:
        # Extract deal data
        deal_id = data.get("dealId", "")
        client_id = data.get("clientId", "")
        products_ids = data.get("productId", [])
        amount = data.get("amount", 0)
        price = data.get("price", 0)
        preparation_date = data.get("preparationDate", "")
        remarks = data.get("remarks", "")
        status = data.get("status", "")

        if not client_id:
            return jsonify({"success": False, "error": "clientId is required"}), 400

        # Validate email configuration
        config_valid, config_error = _check_email_config()
        if not config_valid:
            return jsonify({
                "success": False,
                "error": f"Email configuration error: {config_error}"
            }), 500

        # Fetch client name
        client_name = client_id
        try:
            client_doc = db.collection("clients").document(client_id).get()
            if client_doc.exists:
                client_data = client_doc.to_dict()
                client_name = client_data.get("name", client_id)
        except Exception:
            pass

        # Fetch product names
        product_names = []
        for pid in (products_ids if isinstance(products_ids, list) else [products_ids]):
            try:
                prod_doc = db.collection("products").document(pid).get()
                if prod_doc.exists:
                    prod_data = prod_doc.to_dict()
                    product_names.append(prod_data.get("name", pid))
                else:
                    product_names.append(pid)
            except Exception:
                product_names.append(pid)

        # Fetch recipients with receiveOrders permission
        try:
            recipients = _fetch_recipients_by_permission(db, "receiveOrders")
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

        if not recipients:
            return jsonify({
                "success": False,
                "error": "No active recipients found with receiveOrders permission",
                "totalRecipients": 0
            }), 404

        # Calculate total price
        total_price = amount * price

        # Build email subject and body
        subject = f"طلب جديد - {client_name}"
        body_text = (
            f"تم إضافة طلب جديد\n\n"
            f"العميل: {client_name}\n"
            f"المنتجات: {', '.join(product_names)}\n"
            f"الكمية: {amount}\n"
            f"السعر: {price}\n"
            f"الإجمالي: {total_price}\n"
            f"تاريخ التحضير: {preparation_date}\n"
            f"الحالة: {status}\n"
        )
        if remarks:
            body_text += f"ملاحظات: {remarks}\n"

        # Send email via SMTP
        try:
            if EMAIL_SMTP_PORT == 465:
                server = smtplib.SMTP_SSL(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT)
            else:
                server = smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT)
                server.starttls()

            server.login(EMAIL_SMTP_USER, EMAIL_SMTP_PASSWORD)

            failed_recipients = []
            successful_recipients = []

            for recipient in recipients:
                try:
                    recipient_email = recipient["email"]
                    recipient_name = recipient["name"]

                    msg = MIMEMultipart()
                    msg['From'] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>"
                    msg['To'] = recipient_email
                    msg['Subject'] = subject

                    personalized_body = f"مرحباً {recipient_name},\n\n{body_text}"
                    msg.attach(MIMEText(personalized_body, 'plain', 'utf-8'))

                    server.sendmail(EMAIL_FROM_ADDRESS, recipient_email, msg.as_string())
                    successful_recipients.append(recipient_email)
                    print(f"New deal notification sent to: {recipient_name} <{recipient_email}>")
                except Exception as recipient_error:
                    failed_recipients.append({
                        "email": recipient.get("email", ""),
                        "error": str(recipient_error)
                    })
                    print(f"Failed to send deal notification to {recipient.get('email', '')}: {str(recipient_error)}")

            server.quit()

            if len(successful_recipients) == 0:
                return jsonify({
                    "success": False,
                    "error": "Failed to send deal notification to all recipients",
                    "failedTo": failed_recipients,
                    "totalRecipients": len(recipients)
                }), 500

            response = {
                "success": True,
                "message": f"Deal notification sent to {len(successful_recipients)} recipient(s)",
                "totalRecipients": len(recipients),
                "successfulRecipients": len(successful_recipients),
                "failedRecipients": len(failed_recipients),
                "dealId": deal_id,
            }

            if failed_recipients:
                response["failedTo"] = failed_recipients
                return jsonify(response), 207

            return jsonify(response), 200

        except smtplib.SMTPAuthenticationError:
            return jsonify({
                "success": False,
                "error": "SMTP authentication failed. Please check your email credentials."
            }), 500

        except smtplib.SMTPException as smtp_error:
            return jsonify({
                "success": False,
                "error": f"SMTP error: {str(smtp_error)}"
            }), 500

    except Exception as e:
        error_msg = f"Error sending deal notification: {str(e)}"
        print(f"{error_msg}")
        print(traceback.format_exc())
        return jsonify({"success": False, "error": error_msg}), 500


def send_support_visit_report(data, db):
    """Send support visit report PDF via email to a provided list of emails.

    Args:
        data: Dictionary containing visitId, supportRecordId, userName, date, visitType, pdfBase64, emails
        db: Firestore database instance

    Returns:
        JSON response with success status and details
    """
    try:
        visit_id = data.get("visitId")
        support_record_id = data.get("supportRecordId", "")
        user_name = data.get("userName", "")
        date = data.get("date", "")
        visit_type = data.get("visitType", "")
        pdf_base64 = data.get("pdfBase64")
        emails = data.get("emails")

        if not visit_id:
            return jsonify({"success": False, "error": "visitId is required"}), 400

        if not pdf_base64:
            return jsonify({"success": False, "error": "pdfBase64 is required"}), 400

        if not emails or not isinstance(emails, list) or len(emails) == 0:
            return jsonify({"success": False, "error": "emails list is required"}), 400

        # Validate all emails
        invalid_emails = [e for e in emails if not _validate_email(e)]
        if invalid_emails:
            return jsonify({
                "success": False,
                "error": f"Invalid email addresses: {', '.join(invalid_emails)}"
            }), 400

        # Validate email configuration
        config_valid, config_error = _check_email_config()
        if not config_valid:
            return jsonify({
                "success": False,
                "error": f"Email configuration error: {config_error}"
            }), 500

        # Decode the base64 PDF
        try:
            pdf_bytes = base64.b64decode(pdf_base64)
        except Exception:
            return jsonify({"success": False, "error": "Invalid pdfBase64 data"}), 400

        # Build email subject and body
        subject = f"تقرير زيارة دعم - {user_name} - {date}"
        body_text = (
            f"تقرير زيارة دعم\n\n"
            f"الاسم: {user_name}\n"
            f"التاريخ: {date}\n"
            f"نوع الزيارة: {visit_type}\n\n"
            f"يرجى الاطلاع على التقرير المرفق."
        )

        # Send email via SMTP
        try:
            pdf_filename = f"support_visit_{user_name}_{date}.pdf"
            outcome = send_pdf_email(
                [{"email": address, "name": ""} for address in emails],
                subject, body_text, pdf_bytes, pdf_filename,
            )
            successful_recipients = outcome["sent"]
            failed_recipients = outcome["failed"]

            if len(successful_recipients) == 0:
                return jsonify({
                    "success": False,
                    "error": "Failed to send support visit report to all recipients",
                    "failedTo": failed_recipients,
                    "totalRecipients": len(emails)
                }), 500

            response = {
                "success": True,
                "message": f"Support visit report sent to {len(successful_recipients)} recipient(s)",
                "totalRecipients": len(emails),
                "successfulRecipients": len(successful_recipients),
                "failedRecipients": len(failed_recipients),
                "visitId": visit_id,
            }

            if failed_recipients:
                response["failedTo"] = failed_recipients
                return jsonify(response), 207

            return jsonify(response), 200

        except smtplib.SMTPAuthenticationError:
            return jsonify({
                "success": False,
                "error": "SMTP authentication failed. Please check your email credentials."
            }), 500

        except smtplib.SMTPException as smtp_error:
            return jsonify({
                "success": False,
                "error": f"SMTP error: {str(smtp_error)}"
            }), 500

    except Exception as e:
        error_msg = f"Error sending support visit report: {str(e)}"
        print(f"{error_msg}")
        print(traceback.format_exc())
        return jsonify({"success": False, "error": error_msg}), 500

