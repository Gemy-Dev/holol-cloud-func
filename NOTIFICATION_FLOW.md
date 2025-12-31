# Notification System Flow

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Google Cloud Scheduler                    │
├──────────────────────────────┬──────────────────────────────┤
│  Production Scheduler        │  Test Scheduler              │
│  Name: daily-notifications   │  Name: test-notifications    │
│  Schedule: */10 * * * *      │  Schedule: * * * * *         │
│  (Every 10 minutes)          │  (Every 1 minute)            │
└──────────────┬───────────────┴──────────────┬───────────────┘
               │                              │
               │ POST /app                    │ POST /app
               │ {"action":                   │ {"action":
               │  "daily_notifications"}      │  "test_notification_to_all"}
               │                              │
               ▼                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Cloud Function: app                        │
│                                                              │
│  ┌──────────────────────────┐  ┌──────────────────────────┐│
│  │ handle_daily_            │  │ handle_test_             ││
│  │ notifications()          │  │ notification_to_all()    ││
│  │                          │  │                          ││
│  │ 1. Get today's date      │  │ 1. Get current time      ││
│  │ 2. Query users           │  │ 2. Query all users       ││
│  │ 3. For each user:        │  │ 3. Collect FCM tokens    ││
│  │    - Get tasks due today │  │ 4. Send multicast        ││
│  │    - Collect task IDs    │  │    notification          ││
│  │    - Send notification   │  │                          ││
│  └──────────┬───────────────┘  └──────────┬───────────────┘│
│             │                              │                │
└─────────────┼──────────────────────────────┼────────────────┘
              │                              │
              │ Firebase Cloud Messaging     │
              ▼                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  Firebase Cloud Messaging                    │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
               ▼                               ▼
┌──────────────────────────┐    ┌──────────────────────────┐
│  User 1 (Sales Rep)      │    │  User 2 (Sales Rep)      │
│  ┌────────────────────┐  │    │  ┌────────────────────┐  │
│  │ 📱 Flutter App     │  │    │  │ 📱 Flutter App     │  │
│  │                    │  │    │  │                    │  │
│  │ Receives:          │  │    │  │ Receives:          │  │
│  │ - Notification     │  │    │  │ - Notification     │  │
│  │ - Data payload     │  │    │  │ - Data payload     │  │
│  └────────────────────┘  │    │  └────────────────────┘  │
└──────────────────────────┘    └──────────────────────────┘
```

## Data Flow

### Production Flow (Every 10 Minutes)

```
Cloud Scheduler (10 min)
    │
    ├─► Trigger: {"action": "daily_notifications"}
    │
    ▼
Cloud Function
    │
    ├─► Query Firestore: Get all users
    │
    ├─► For each user:
    │   │
    │   ├─► Query tasks where assignedToId == user.id
    │   │
    │   ├─► Filter tasks where targetDate == today
    │   │
    │   ├─► If tasks found:
    │   │   │
    │   │   ├─► Collect task IDs and titles
    │   │   │
    │   │   ├─► Build notification:
    │   │   │   - Title: "تذكير بالمهام"
    │   │   │   - Body: Task title or count
    │   │   │   - Data: {taskCount, taskIds, date, action}
    │   │   │
    │   │   └─► Send via FCM
    │   │
    │   └─► Next user
    │
    └─► Return: {success, count, date}
```

### Test Flow (Every 1 Minute)

```
Cloud Scheduler (1 min)
    │
    ├─► Trigger: {"action": "test_notification_to_all"}
    │
    ▼
Cloud Function
    │
    ├─► Get current timestamp
    │
    ├─► Generate random test message
    │
    ├─► Query Firestore: Get all users
    │
    ├─► Collect all FCM tokens
    │
    ├─► Build multicast notification:
    │   - Title: Random Arabic test message
    │   - Body: "رسالة اختبارية - الوقت: HH:MM:SS"
    │   - Data: {timestamp, action, testId}
    │
    ├─► Send multicast via FCM
    │
    └─► Return: {success, successCount, failureCount}
```

## Notification Payload Structure

### Production Notification

```json
{
  "notification": {
    "title": "تذكير بالمهام",
    "body": "عندك اليوم 3 مهام"
  },
  "data": {
    "taskCount": "3",
    "taskIds": "abc123,def456,ghi789",
    "date": "2025-12-30",
    "action": "daily_tasks"
  },
  "token": "user_fcm_token_here"
}
```

### Test Notification

```json
{
  "notification": {
    "title": "اختبار الإشعارات",
    "body": "رسالة اختبارية - الوقت: 14:30:45"
  },
  "data": {
    "timestamp": "14:30:45",
    "action": "test_notification",
    "testId": "7432"
  },
  "tokens": ["token1", "token2", "token3", "..."]
}
```

## Flutter App Integration

### Notification Handler

```dart
class NotificationService {
  void initialize() {
    // Handle foreground notifications
    FirebaseMessaging.onMessage.listen(_handleMessage);
    
    // Handle background notifications
    FirebaseMessaging.onBackgroundMessage(_handleBackgroundMessage);
    
    // Handle notification tap
    FirebaseMessaging.onMessageOpenedApp.listen(_handleNotificationTap);
  }
  
  void _handleMessage(RemoteMessage message) {
    final action = message.data['action'];
    
    switch (action) {
      case 'daily_tasks':
        _handleDailyTasks(message);
        break;
      case 'test_notification':
        _handleTestNotification(message);
        break;
    }
  }
  
  void _handleDailyTasks(RemoteMessage message) {
    final taskCount = int.parse(message.data['taskCount'] ?? '0');
    final taskIds = message.data['taskIds']?.split(',') ?? [];
    final date = message.data['date'];
    
    // Show local notification
    showLocalNotification(
      title: message.notification?.title ?? '',
      body: message.notification?.body ?? '',
    );
    
    // Navigate to tasks screen
    Get.toNamed('/tasks', arguments: {
      'taskIds': taskIds,
      'date': date,
    });
  }
  
  void _handleTestNotification(RemoteMessage message) {
    final timestamp = message.data['timestamp'];
    final testId = message.data['testId'];
    
    print('Test notification received: $timestamp (ID: $testId)');
    
    // Show snackbar
    Get.snackbar(
      'Test Notification',
      'Time: $timestamp, ID: $testId',
      duration: Duration(seconds: 3),
    );
  }
}
```

## Firestore Data Structure

### Users Collection

```
users/
  └─ {userId}/
      ├─ email: "user@example.com"
      ├─ fcmToken: "fcm_token_string"
      ├─ name: "Sales Rep Name"
      └─ role: "salesRepresentative"
```

### Tasks Collection

```
tasks/
  └─ {taskId}/
      ├─ title: "زيارة عميل ABC"
      ├─ assignedToId: "userId"
      ├─ targetDate: "2025-12-30"
      ├─ status: "pending"
      └─ clientId: "clientId"
```

## Execution Timeline

### Production (10-Minute Intervals)

```
00:00 ─► Notification sent (if tasks due today)
00:10 ─► Notification sent (if tasks due today)
00:20 ─► Notification sent (if tasks due today)
00:30 ─► Notification sent (if tasks due today)
...
23:50 ─► Notification sent (if tasks due today)
```

### Test (1-Minute Intervals)

```
00:00 ─► Test notification sent to ALL users
00:01 ─► Test notification sent to ALL users
00:02 ─► Test notification sent to ALL users
00:03 ─► Test notification sent to ALL users
...
⚠️ PAUSE OR DELETE AFTER TESTING!
```

## Error Handling

```
Cloud Function
    │
    ├─► Try to send notification
    │   │
    │   ├─► Success
    │   │   └─► Log: "✅ Sent to {userId}"
    │   │
    │   └─► Error
    │       ├─► UnregisteredError
    │       │   └─► Log: "❌ Invalid FCM token"
    │       │
    │       └─► Other Error
    │           └─► Log: "❌ Error: {message}"
    │
    └─► Continue to next user
```

## Monitoring Points

1. **Cloud Scheduler**: Check execution history
2. **Cloud Function Logs**: View success/error messages
3. **FCM Console**: Monitor delivery rates
4. **Flutter App**: Track notification receipts
5. **Firestore**: Verify user FCM tokens exist

## Cost Breakdown

### Production Scheduler
- Executions: 144 per day (every 10 minutes)
- Monthly: ~4,320 executions
- Cost: Within free tier

### Test Scheduler
- Executions: 1,440 per day (every minute)
- Monthly: ~43,200 executions
- Cost: Still within free tier, but **annoying for users**
- **⚠️ Always pause/delete after testing**

## Security

- Production scheduler: No authentication required (internal trigger)
- Test scheduler: No authentication required (internal trigger)
- Both endpoints are triggered by Cloud Scheduler only
- User-facing notification endpoints require authentication


