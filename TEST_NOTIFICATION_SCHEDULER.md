# Test Notification Scheduler

## Overview

A test scheduler has been created to send notifications to all users **every minute** for testing purposes.

## ⚠️ Important Notes

- **This is for TESTING ONLY** - Do not leave this running in production
- Sends notifications to ALL users with FCM tokens
- Runs every single minute (60 times per hour)
- Can be expensive and annoying if left running

## What It Sends

### Notification Content
- **Title**: Random Arabic test message (اختبار الإشعارات, رسالة تجريبية, etc.)
- **Body**: "رسالة اختبارية - الوقت: HH:MM:SS"

### Notification Data (JSON)
```json
{
  "timestamp": "14:30:45",
  "action": "test_notification",
  "testId": "1234"
}
```

## Deployment

### Deploy with Test Scheduler
Run the deployment script (includes both schedulers):

```bash
./deploy.sh
```

This will create two schedulers:
1. `daily-notifications` - Every 10 minutes (production)
2. `test-notifications` - Every 1 minute (testing)

## Managing the Test Scheduler

### Check Status
```bash
gcloud scheduler jobs describe test-notifications \
  --location=us-central1 \
  --project=YOUR_PROJECT_ID
```

### Pause the Test Scheduler (RECOMMENDED after testing)
```bash
gcloud scheduler jobs pause test-notifications \
  --location=us-central1 \
  --project=YOUR_PROJECT_ID
```

### Resume the Test Scheduler
```bash
gcloud scheduler jobs resume test-notifications \
  --location=us-central1 \
  --project=YOUR_PROJECT_ID
```

### Delete the Test Scheduler (When done testing)
```bash
gcloud scheduler jobs delete test-notifications \
  --location=us-central1 \
  --project=YOUR_PROJECT_ID
```

### Trigger Manually (Without waiting)
```bash
# Get function URL
FUNCTION_URL=$(gcloud functions describe app \
  --region=us-central1 \
  --project=YOUR_PROJECT_ID \
  --format="value(serviceConfig.uri)")

# Trigger test notification
curl -X POST $FUNCTION_URL \
  -H "Content-Type: application/json" \
  -d '{"action":"test_notification_to_all"}'
```

## Monitoring

### View Recent Executions
```bash
gcloud scheduler jobs describe test-notifications \
  --location=us-central1 \
  --project=YOUR_PROJECT_ID \
  --format="table(status.lastAttemptTime, status.state)"
```

### View Logs
```bash
gcloud functions logs read app \
  --region=us-central1 \
  --project=YOUR_PROJECT_ID \
  --limit=50
```

Filter for test notifications:
```bash
gcloud functions logs read app \
  --region=us-central1 \
  --project=YOUR_PROJECT_ID \
  --limit=50 | grep "test notification"
```

## Testing Workflow

### 1. Deploy
```bash
./deploy.sh
```

### 2. Wait 1 Minute
The test notification will be sent automatically.

### 3. Check Your Phone/App
You should receive a notification with:
- Arabic test title
- Body with timestamp
- Data payload with testId

### 4. Pause When Done
```bash
gcloud scheduler jobs pause test-notifications \
  --location=us-central1 \
  --project=YOUR_PROJECT_ID
```

### 5. Delete When No Longer Needed
```bash
gcloud scheduler jobs delete test-notifications \
  --location=us-central1 \
  --project=YOUR_PROJECT_ID
```

## Cost Considerations

Running every minute means:
- **60 executions per hour**
- **1,440 executions per day**
- **43,200 executions per month**

Cloud Functions free tier includes 2 million invocations per month, but:
- FCM has its own quotas
- Users will be annoyed receiving 60+ notifications per hour
- **Always pause or delete after testing**

## Flutter Client Testing

Test that your Flutter app properly receives the notification data:

```dart
FirebaseMessaging.onMessage.listen((RemoteMessage message) {
  print('Test notification received!');
  print('Title: ${message.notification?.title}');
  print('Body: ${message.notification?.body}');
  print('Data: ${message.data}');
  
  if (message.data['action'] == 'test_notification') {
    final timestamp = message.data['timestamp'];
    final testId = message.data['testId'];
    
    // Show a snackbar or dialog
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('Test notification: $timestamp (ID: $testId)'),
      ),
    );
  }
});
```

## Troubleshooting

### No Notifications Received
1. Check if users have FCM tokens:
   ```bash
   # Check Firestore users collection
   ```

2. Check scheduler status:
   ```bash
   gcloud scheduler jobs describe test-notifications --location=us-central1
   ```

3. Check function logs:
   ```bash
   gcloud functions logs read app --region=us-central1 --limit=50
   ```

### Too Many Notifications
1. **Pause immediately**:
   ```bash
   gcloud scheduler jobs pause test-notifications --location=us-central1
   ```

2. Consider using manual trigger instead of scheduler

## Production Checklist

Before going to production:
- [ ] Pause or delete test-notifications scheduler
- [ ] Keep daily-notifications scheduler running
- [ ] Verify daily-notifications is set to every 10 minutes
- [ ] Test the production scheduler manually
- [ ] Monitor logs for any errors

## Quick Commands Reference

```bash
# Pause test scheduler
gcloud scheduler jobs pause test-notifications --location=us-central1

# Resume test scheduler
gcloud scheduler jobs resume test-notifications --location=us-central1

# Delete test scheduler
gcloud scheduler jobs delete test-notifications --location=us-central1

# Manual trigger
curl -X POST $FUNCTION_URL -H "Content-Type: application/json" \
  -d '{"action":"test_notification_to_all"}'
```


