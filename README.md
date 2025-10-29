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
- `createPlanTasks` - Create tasks from plan

### Backup Operations
- `manualBackup` - Trigger backup
- `backupStatus` - Check backup status
- `listBackups` - List backups
- `restoreBackup` - Restore from backup

### Scheduled
- `daily_notifications` - Daily notifications (auto-scheduled)

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
