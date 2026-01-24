"""Configuration constants and settings."""
import os

BACKUP_BUCKET = "medical-advisor-bd734-backups"
COLLECTIONS_TO_BACKUP = [
    "users", "products","deals", "clients", "tasks", "plans","main_opportunities", 
    "departments", "specialties", "procedures", "companies",
    "notifications", "reports", "analytics","manufacturers","opportunities"
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
EMAIL_SMTP_PASSWORD = os.getenv("EMAIL_SMTP_PASSWORD", "cacivoxvpwlyoarq")  # Gmail App Password (not regular password)
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", "zaid.h.dev@gmail.com")  # Usually same as EMAIL_SMTP_USER
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "holol-tibbiya")


