# Medical Advisor Cloud Functions

Cloud Functions for Medical Advisor application with modular architecture.

## 📁 Project Structure

```
project/
├── app.py                      # Main entry point (modular, ~185 lines)
├── modules/                    # Business logic modules
│   ├── auth.py                # Authentication
│   ├── users.py               # User management
│   ├── products.py            # Products & clients
│   ├── tasks.py               # Task management
│   ├── backups.py             # Backup & restore
│   ├── notifications.py       # Push notifications
│   ├── email.py               # Email sending
│   ├── special_requests.py    # Client special requests: record, decisions, send
│   ├── special_request_pdf.py # The approved request's PDF
│   ├── review_reminders.py    # reportSaved and the reviewers' morning summary
│   ├── firestore_tx.py        # Transaction helper
│   └── config.py              # Configuration
├── fonts/                      # Cairo, used by the PDF
├── deploy.sh                   # Deployment script
└── requirements.txt            # Python dependencies
```

## 🚀 Quick Start

### Prerequisites

1. Install [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
2. Authenticate:
   ```bash
   gcloud auth login
   gcloud auth application-default login
   ```
3. Set project:
   ```bash
   gcloud config set project test-medical-80e1b
   ```

### Deploy

```bash
./deploy.sh
```

## 🎯 API Endpoints

### Health Check
```bash
curl https://us-central1-test-medical-80e1b.cloudfunctions.net/app
```

### User Management
- `create` - Create user
- `update` - Update user
- `delete` - Delete user

### Data Operations
- `getProducts` - Get all products
- `getPlanProducts` - Get plan products
- `getClients` - Get all clients

### Task Management
- `syncTasksForClient` - Reconcile a client's planned tasks with its current influencer doctors.
  Performs create (missing tasks) + update (priority drift) + soft-delete (removed doctors) in a
  single Firestore batch. Completed tasks are never modified or deleted.
  **Replaces** the deprecated `createTasksForNewClient` action for both client create and update.
- `createTasksForNewClient` - **Deprecated alias** for `syncTasksForClient`. Still routed to the
  same reconcile handler for back-compatibility with older app builds.
- `createPlanTasks` - Create tasks for all matching clients when a plan is first created
- `createTasksFromProduct` - Create tasks when a new product is added to an existing plan

### Backup Operations
- `manualBackup` - Trigger backup
- `backupStatus` - Check backup status
- `listBackups` - List backups
- `restoreBackup` - Restore from backup

### Notifications
- `sendNotification` - Send push notification to specific user
- `sendNotificationToAll` - Send push notification to all users
- `daily_notifications` - Daily notifications (auto-scheduled)

### Email
- `sendEmail` - Send email to all users with `receiveEmailNotifications: true`

#### Email Usage Example
```json
{
  "action": "sendEmail",
  "title": "Welcome to Medical Advisor",
  "body": "Thank you for joining our platform!"
}
```

**Note**: The email addresses are automatically fetched from the Firestore `users` collection where `receiveEmailNotifications` field is `true`. You don't need to specify recipient emails in the request.

#### Gmail Email Configuration

**Important**: Gmail requires an **App Password**, not your regular Gmail password!

**Steps to set up Gmail:**

1. **Enable 2-Step Verification** on your Google Account:
   - Go to: https://myaccount.google.com/security
   - Enable 2-Step Verification

2. **Generate App Password**:
   - Go to: https://myaccount.google.com/apppasswords
   - Select "Mail" and "Other (Custom name)"
   - Enter "Medical Advisor Cloud Function" as the name
   - Copy the generated 16-character password

3. **Set Environment Variables** in your Cloud Function:
   ```bash
   EMAIL_SMTP_HOST=smtp.gmail.com
   EMAIL_SMTP_PORT=587          # TLS (recommended) or 465 for SSL
   EMAIL_SMTP_USER=your-email@gmail.com
   EMAIL_SMTP_PASSWORD=your-16-char-app-password
   EMAIL_FROM_ADDRESS=your-email@gmail.com
   EMAIL_FROM_NAME=Medical Advisor
   ```

**Using gcloud to set environment variables:**
```bash
gcloud functions deploy app \
  --update-env-vars="EMAIL_SMTP_HOST=smtp.gmail.com,EMAIL_SMTP_PORT=587,EMAIL_SMTP_USER=your-email@gmail.com,EMAIL_SMTP_PASSWORD=your-app-password,EMAIL_FROM_ADDRESS=your-email@gmail.com,EMAIL_FROM_NAME=Medical Advisor" \
  --region=us-central1
```

**Note**: Port 587 (TLS) is recommended. Port 465 (SSL) is also supported.

### Client special requests (spec 2038)

A sales report or support visit can carry a special request from the client. The record
`special_requests/{requestId}` (`report_{reportId}` or `visit_{supportRecordId}_{visitId}`) is written only by this
function; the apps and the dashboard read it. Two reviewers each decide a slot; when both approve, the function builds a
PDF (the request with both approvals, the full report, and every attachment) and emails it once to the recipients
tagged `receiveSpecialRequests` in `email_recipients`.

Authenticated actions:
- `reportSaved` - called after every save of a report or visit. Refreshes the request record from an explicit `intent`
  (`set`, `withdraw`, `none`) and, for a new report or visit, notifies the sales managers and admins once.
- `decideSpecialRequest` - record one slot's decision (approve or reject, with an optional note). The second approval
  claims the send in the same transaction and runs it.
- `editSpecialRequestNote` - reword the note of your own decision.
- `retrySpecialRequestSend` - re-send an approved request whose email failed, partly failed, or stalled. Only addresses
  not yet delivered to are emailed.

Unauthenticated, called by Cloud Scheduler:
- `review_reminders` - each reviewer's one summary of reports waiting on them and special requests awaiting their
  decision (admins also see approved requests that were not fully sent). Scheduler job `review-reminders`, `0 5 * * *`
  UTC (08:00 Iraq time).

#### Configuration

- `REVIEW_REMINDER_SINCE` - the go-live moment of the summary (an ISO date like `2026-10-05T08:00:00.000`, Iraq time).
  Reports and visits created earlier are never reminded about. Unset, it is far in the future and the summary stays
  silent. Set it in `deploy.sh` (or the environment): every deploy replaces all env vars, so a value left out resets.
- New Python dependencies: `fpdf2` and `uharfbuzz` (Arabic shaping), `Pillow` (photos), `pypdf` (attached PDFs).
  The Cairo fonts in `fonts/` are part of the deployed source.
- Attachment links are fetched only from Firebase Storage over https.

#### Tests

```bash
.venv/bin/python -m pytest -q
```

## 📦 Deployment

The `deploy.sh` script will:
1. ✅ Enable required APIs
2. ✅ Create backup bucket
3. ✅ Deploy app function
4. ✅ Set up permissions
5. ✅ Configure schedulers

## 🔐 Security

- All endpoints (except the scheduled `daily_notifications`, `notify_today_tasks`, `notify_tomorrow_tasks` and `review_reminders`) require authentication
- CORS configured for allowed origins
- Firebase Auth token required in requests

## 📝 Notes

- **Entry Point**: `app` function in `app.py`
- **Runtime**: Python 3.11
- **Timeout**: 540s (9 minutes)
- **Memory**: 1GB
- **Region**: us-central1
