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
│   └── config.py              # Configuration
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
   gcloud config set project medical-advisor-bd734
   ```

### Deploy

```bash
./deploy.sh
```

## 🎯 API Endpoints

### Health Check
```bash
curl https://us-central1-medical-advisor-bd734.cloudfunctions.net/app
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

## 📦 Deployment

The `deploy.sh` script will:
1. ✅ Enable required APIs
2. ✅ Create backup bucket
3. ✅ Deploy app function
4. ✅ Set up permissions
5. ✅ Configure schedulers

## 🔐 Security

- All endpoints (except daily notifications) require authentication
- CORS configured for allowed origins
- Firebase Auth token required in requests

## 📝 Notes

- **Entry Point**: `app` function in `app.py`
- **Runtime**: Python 3.11
- **Timeout**: 540s (9 minutes)
- **Memory**: 1GB
- **Region**: us-central1
