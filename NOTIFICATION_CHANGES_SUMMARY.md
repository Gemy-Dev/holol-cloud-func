# Notification System Changes Summary

## Overview
Created a comprehensive notification system with both production and testing schedulers.

## 🎯 What Was Created

### 1. Production Scheduler (Every 10 Minutes)
- **Name**: `daily-notifications`
- **Schedule**: Every 10 minutes (`*/10 * * * *`)
- **Purpose**: Send task reminders to users with tasks due today
- **Action**: `daily_notifications`

### 2. Test Scheduler (Every 1 Minute)
- **Name**: `test-notifications`
- **Schedule**: Every 1 minute (`* * * * *`)
- **Purpose**: Testing notification delivery
- **Action**: `test_notification_to_all`
- **⚠️ FOR TESTING ONLY** - Should be paused or deleted after testing

## 📝 Files Modified

### 1. `modules/notifications.py`
**Changes:**
- Added `import random` for test notifications
- Enhanced `handle_daily_notifications()`:
  - Collects all tasks for each user before sending
  - Sends one notification per user (not one per task)
  - Includes JSON data: `taskCount`, `taskIds`, `date`, `action`
  - Smart body: shows task title if 1 task, count if multiple
  - Fixed field name: `assignedToId` instead of `assignedTo`
- Added new function `handle_test_notification_to_all()`:
  - Sends random Arabic test messages
  - Includes timestamp and testId
  - Sends to all users with FCM tokens

### 2. `app.py`
**Changes:**
- Added import for `handle_test_notification_to_all`
- Added route for `test_notification_to_all` action (no auth required)

### 3. `deploy.sh`
**Changes:**
- Updated production scheduler to every 10 minutes
- Added test scheduler setup (every 1 minute)
- Updated descriptions and comments

### 4. New Files Created
- `NOTIFICATION_SCHEDULE_UPDATE.md` - Production scheduler documentation
- `TEST_NOTIFICATION_SCHEDULER.md` - Test scheduler documentation
- `manage-test-scheduler.sh` - Script to manage test scheduler
- `NOTIFICATION_CHANGES_SUMMARY.md` - This file

## 📊 Notification Data Structure

### Production Notifications (Task Reminders)
```json
{
  "notification": {
    "title": "تذكير بالمهام",
    "body": "عندك اليوم 3 مهام" // or "عندك اليوم مهمة: [Task Title]"
  },
  "data": {
    "taskCount": "3",
    "taskIds": "task123,task456,task789",
    "date": "2025-12-30",
    "action": "daily_tasks"
  }
}
```

### Test Notifications
```json
{
  "notification": {
    "title": "اختبار الإشعارات",
    "body": "رسالة اختبارية - الوقت: 14:30:45"
  },
  "data": {
    "timestamp": "14:30:45",
    "action": "test_notification",
    "testId": "1234"
  }
}
```

## 🚀 Deployment

### Full Deployment (Recommended)
```bash
./deploy.sh
```

This will:
1. Deploy the cloud function
2. Create/update production scheduler (every 10 minutes)
3. Create/update test scheduler (every 1 minute)

### Update Schedulers Only
```bash
# Production scheduler
gcloud scheduler jobs update http daily-notifications \
  --schedule="*/10 * * * *" \
  --location=us-central1

# Test scheduler
gcloud scheduler jobs update http test-notifications \
  --schedule="* * * * *" \
  --location=us-central1
```

## 🧪 Testing

### Using the Management Script
```bash
# Show status
./manage-test-scheduler.sh status

# Trigger test notification immediately
./manage-test-scheduler.sh trigger

# Pause test scheduler (IMPORTANT after testing)
./manage-test-scheduler.sh pause

# Resume test scheduler
./manage-test-scheduler.sh resume

# Delete test scheduler
./manage-test-scheduler.sh delete

# View logs
./manage-test-scheduler.sh logs
```

### Manual Testing
```bash
# Get function URL
FUNCTION_URL=$(gcloud functions describe app \
  --region=us-central1 \
  --format="value(serviceConfig.uri)")

# Test production notification
curl -X POST $FUNCTION_URL \
  -H "Content-Type: application/json" \
  -d '{"action":"daily_notifications"}'

# Test notification to all
curl -X POST $FUNCTION_URL \
  -H "Content-Type: application/json" \
  -d '{"action":"test_notification_to_all"}'
```

## ⚠️ Important Notes

### Production Scheduler
- ✅ Safe to leave running
- Runs every 10 minutes
- Only sends to users with tasks due today
- Includes task count and IDs in data payload

### Test Scheduler
- ⚠️ **PAUSE OR DELETE AFTER TESTING**
- Runs every single minute (60 times per hour)
- Sends to ALL users regardless of tasks
- Can be annoying and expensive if left running

### Cost Considerations
- Production: ~144 executions per day (every 10 minutes)
- Test: ~1,440 executions per day (every minute)
- Always pause/delete test scheduler when not actively testing

## 📱 Flutter Integration

### Handle Production Notifications
```dart
FirebaseMessaging.onMessage.listen((RemoteMessage message) {
  if (message.data['action'] == 'daily_tasks') {
    final taskCount = int.parse(message.data['taskCount'] ?? '0');
    final taskIds = message.data['taskIds']?.split(',') ?? [];
    final date = message.data['date'];
    
    // Navigate to tasks screen
    Navigator.pushNamed(
      context,
      '/tasks',
      arguments: {'taskIds': taskIds, 'date': date},
    );
  }
});
```

### Handle Test Notifications
```dart
FirebaseMessaging.onMessage.listen((RemoteMessage message) {
  if (message.data['action'] == 'test_notification') {
    final timestamp = message.data['timestamp'];
    final testId = message.data['testId'];
    
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Test: $timestamp (ID: $testId)')),
    );
  }
});
```

## 🔍 Monitoring

### Check Scheduler Status
```bash
# Production scheduler
gcloud scheduler jobs describe daily-notifications --location=us-central1

# Test scheduler
gcloud scheduler jobs describe test-notifications --location=us-central1
```

### View Logs
```bash
# All logs
gcloud functions logs read app --region=us-central1 --limit=50

# Production notifications only
gcloud functions logs read app --region=us-central1 --limit=50 | grep "task notifications"

# Test notifications only
gcloud functions logs read app --region=us-central1 --limit=50 | grep "test notification"
```

### View Recent Executions
```bash
gcloud scheduler jobs describe daily-notifications \
  --location=us-central1 \
  --format="table(status.lastAttemptTime, status.state)"
```

## ✅ Pre-Production Checklist

Before going live:
- [ ] Deploy the function: `./deploy.sh`
- [ ] Test production scheduler manually
- [ ] Test with real FCM tokens
- [ ] Verify task data is correct
- [ ] **Pause test scheduler**: `./manage-test-scheduler.sh pause`
- [ ] Or **delete test scheduler**: `./manage-test-scheduler.sh delete`
- [ ] Monitor logs for errors
- [ ] Verify production scheduler is running every 10 minutes

## 🆘 Troubleshooting

### No Notifications Received
1. Check if users have FCM tokens in Firestore
2. Check scheduler status: `./manage-test-scheduler.sh status`
3. Check function logs: `./manage-test-scheduler.sh logs`
4. Trigger manually: `./manage-test-scheduler.sh trigger`

### Too Many Notifications
1. **Immediately pause**: `./manage-test-scheduler.sh pause`
2. Check which scheduler is running
3. Delete test scheduler if not needed

### Scheduler Not Running
1. Check if scheduler exists: `gcloud scheduler jobs list --location=us-central1`
2. Check scheduler state (should be ENABLED, not PAUSED)
3. Redeploy: `./deploy.sh`

## 📚 Documentation Files

- `NOTIFICATION_SCHEDULE_UPDATE.md` - Production scheduler details
- `TEST_NOTIFICATION_SCHEDULER.md` - Test scheduler details and management
- `NOTIFICATION_CHANGES_SUMMARY.md` - This file (overview of all changes)
- `manage-test-scheduler.sh` - Management script for test scheduler

## 🎓 Key Improvements

1. **Consolidated Notifications**: One notification per user instead of multiple
2. **Rich Data Payload**: Task count and IDs included for Flutter app
3. **Smart Messaging**: Shows task title or count based on number of tasks
4. **Testing Infrastructure**: Separate test scheduler for easy testing
5. **Management Tools**: Script to easily manage test scheduler
6. **Comprehensive Documentation**: Multiple docs covering all aspects




