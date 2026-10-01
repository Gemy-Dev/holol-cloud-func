"""Configuration constants and settings."""
import os
from datetime import timedelta, timezone

# The business runs on Iraq time (UTC+3, no DST). Every "which day is
# this?" decision must be made in this zone: task target dates are
# written from local midnight, so comparing them in UTC lands them on the
# previous day.
IRAQ_TIMEZONE = timezone(timedelta(hours=3))

BACKUP_BUCKET = "test-medical-80e1b-firestore-backups"
COLLECTIONS_TO_BACKUP = [
    "users", "products","deals", "clients", "tasks","daily_reports", "plans", "technical_support","main_opportunities",
    "departments", "specialties", "procedures", "companies",
    "notifications", "reports", "analytics","manufacturers","opportunities","email_recipients"
]

# Email configuration (Gmail)
# Gmail SMTP Settings:
# - Host: smtp.gmail.com
# - Port 587: TLS (recommended)
# - Port 465: SSL (alternative)
# 
# IMPORTANT: For Gmail, you need an App Password, not your regular password!
# To generate an App Password:
# 1. Enable 2-Step Verification on your Google Account
# 2. Go to: https://myaccount.google.com/apppasswords
# 3. Generate an app-specific password
# 4. Use that password in EMAIL_SMTP_PASSWORD
EMAIL_SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "587"))  # 587 for TLS, 465 for SSL
EMAIL_SMTP_USER = os.getenv("EMAIL_SMTP_USER", "zaid.h.dev@gmail.com")  # Your Gmail address
EMAIL_SMTP_PASSWORD = os.getenv("EMAIL_SMTP_PASSWORD")  # Gmail App Password - must be set via environment variable
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", "zaid.h.dev@gmail.com")  # Usually same as EMAIL_SMTP_USER
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "holol-tibbiya")



# Special client requests (spec 2038).
SPECIAL_REQUEST_PERMISSION = "receiveSpecialRequests"
# Gmail refuses messages over 25 MB, measured after base64 (about 4/3 of the
# file plus line breaks): 17 MB of PDF leaves room for the encoding and the body.
SPECIAL_REQUEST_MAX_PDF_BYTES = 17_000_000
SPECIAL_REQUEST_IMAGE_PASSES = ((1600, 80), (1024, 60))  # (long edge px, JPEG quality)
SPECIAL_REQUEST_STALE_CLAIM_MINUTES = 15
ATTACHMENT_FETCH_TIMEOUT_S = 30
# All of one PDF's downloads together; later attachments past it get their
# placeholder page, so the send still finishes inside the function's 540 s.
ATTACHMENT_FETCH_BUDGET_S = 180
SMTP_TIMEOUT_S = 60
FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
# Reports created before this instant are not counted by the daily review
# summary. Disabled until deploy.sh sets it to the go-live time.
REVIEW_REMINDER_SINCE = os.getenv("REVIEW_REMINDER_SINCE", "2099-01-01T00:00:00.000")
