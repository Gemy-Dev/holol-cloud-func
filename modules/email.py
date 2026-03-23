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

        # Fetch email recipients from Firestore
        try:
            recipient_emails = _fetch_email_recipients(db)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

        if not recipient_emails:
            return jsonify({
                "success": False,
                "error": "No users found with receiveEmailNotifications enabled",
                "totalRecipients": 0
            }), 404

        # Build email subject and body
        subject = f"التقرير اليومي - {user_name} - {date}"
        body_text = (
            f"التقرير اليومي\n\n"
            f"الاسم: {user_name}\n"
            f"التاريخ: {date}\n"
            f"عدد المهام: {tasks_count}\n\n"
            f"يرجى الاطلاع على التقرير المرفق."
        )

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

            for recipient in recipient_emails:
                try:
                    msg = MIMEMultipart()
                    msg['From'] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>"
                    msg['To'] = recipient
                    msg['Subject'] = subject

                    msg.attach(MIMEText(body_text, 'plain', 'utf-8'))

                    # Attach PDF
                    pdf_attachment = MIMEApplication(pdf_bytes, _subtype='pdf')
                    pdf_filename = f"report_{user_name}_{date}.pdf"
                    pdf_attachment.add_header(
                        'Content-Disposition', 'attachment', filename=pdf_filename
                    )
                    msg.attach(pdf_attachment)

                    server.sendmail(EMAIL_FROM_ADDRESS, recipient, msg.as_string())
                    successful_recipients.append(recipient)
                    print(f"Daily report email sent to: {recipient}")
                except Exception as recipient_error:
                    failed_recipients.append({
                        "email": recipient,
                        "error": str(recipient_error)
                    })
                    print(f"Failed to send daily report to {recipient}: {str(recipient_error)}")

            server.quit()

            if len(successful_recipients) == 0:
                return jsonify({
                    "success": False,
                    "error": "Failed to send daily report to all recipients",
                    "failedTo": failed_recipients,
                    "totalRecipients": len(recipient_emails)
                }), 500

            response = {
                "success": True,
                "message": f"Daily report sent to {len(successful_recipients)} recipient(s)",
                "totalRecipients": len(recipient_emails),
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

