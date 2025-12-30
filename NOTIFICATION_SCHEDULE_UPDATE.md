# Notification Schedule Update

## Changes Made

### 1. **Notification Frequency**
- **Before**: Notifications ran once daily at 5:00 AM UTC
- **After**: Notifications run every 10 minutes

### 2. **Enhanced Notification Data**
The notification now includes structured JSON data:

```json
{
  "taskCount": "3",
  "taskIds": "task123,task456,task789",
  "date": "2025-12-30",
  "action": "daily_tasks"
}
```

### 3. **Smart Notification Body**
- **Single task**: "عندك اليوم مهمة: [Task Title]"
- **Multiple tasks**: "عندك اليوم 3 مهام"

### 4. **One Notification Per User**
Instead of sending multiple notifications (one per task), users now receive a single consolidated notification with all task information.

## Files Modified

1. **`modules/notifications.py`**
   - Updated `handle_daily_notifications()` to collect all tasks first
   - Added JSON data payload with task count and IDs
   - Improved notification body logic

2. **`deploy.sh`**
   - Changed schedule from `"0 5 * * *"` to `"*/10 * * * *"`
   - Updated comments and descriptions

## How to Deploy

### Option 1: Full Deployment
Run the deployment script to update everything:

```bash
./deploy.sh
```

### Option 2: Update Scheduler Only
If you've already deployed the function and only want to update the schedule:

```bash
# Set your project variables
PROJECT_ID="your-project-id"
REGION="us-central1"

# Get the function URL
FUNCTION_URL=$(gcloud functions describe app --region=$REGION --project=$PROJECT_ID --format="value(serviceConfig.uri)")

# Update the scheduler
gcloud scheduler jobs update http daily-notifications \
    --schedule="*/10 * * * *" \
    --uri="$FUNCTION_URL" \
    --http-method=POST \
    --message-body='{"action":"daily_notifications"}' \
    --time-zone="UTC" \
    --location=$REGION \
    --project=$PROJECT_ID
```

### Option 3: Test Manually
Test the notification function without waiting:

```bash
# Get your function URL
FUNCTION_URL=$(gcloud functions describe app --region=$REGION --project=$PROJECT_ID --format="value(serviceConfig.uri)")

# Trigger manually
curl -X POST $FUNCTION_URL \
  -H "Content-Type: application/json" \
  -d '{"action":"daily_notifications"}'
```

## Flutter Client Integration

The Flutter app should handle the notification data like this:

```dart
// In your Firebase messaging handler
FirebaseMessaging.onMessage.listen((RemoteMessage message) {
  if (message.data['action'] == 'daily_tasks') {
    final taskCount = int.parse(message.data['taskCount'] ?? '0');
    final taskIds = message.data['taskIds']?.split(',') ?? [];
    final date = message.data['date'];
    
    // Navigate to tasks screen or show task list
    Navigator.pushNamed(
      context,
      '/tasks',
      arguments: {
        'taskIds': taskIds,
        'date': date,
      },
    );
  }
});
```

## Verification

After deployment, verify the scheduler is running:

```bash
# Check scheduler status
gcloud scheduler jobs describe daily-notifications \
  --location=$REGION \
  --project=$PROJECT_ID

# View recent executions
gcloud scheduler jobs describe daily-notifications \
  --location=$REGION \
  --project=$PROJECT_ID \
  --format="table(status.lastAttemptTime, status.state)"
```

## Notes

- The function still checks for tasks with `targetDate` matching today's date
- Notifications are only sent to users with valid FCM tokens
- The scheduler name remains "daily-notifications" for backward compatibility
- The function runs every 10 minutes but only notifies about today's tasks

