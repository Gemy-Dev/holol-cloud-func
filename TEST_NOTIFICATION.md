# Testing FCM Notifications

## Method 1: Test via Flutter App (Recommended)

Use your existing Flutter code:

```dart
await notificationDataSource.sendNotificationToUser(
  fcmToken: "cFsRMBcq4LTZv16bnryeyo:APA91bHmBc2NmsPGtet76zaaL26udELS66iw7UgN95uqRWpI15Xf4vIMNLIcC0Cbmei_uY_blWl6-cqWBX3Sq9sGIh-oY_BBg1zOJYy0f507U9fRt-QuUUw",
  title: "Test Notification",
  body: "This is a test from Cloud Function",
  action: NotificationAction.someAction, // Use your enum
);
```

## Method 2: Test via cURL

1. **Get your Firebase Auth token** (from Flutter app or Firebase Console)

2. **Send the request:**

```bash
curl -X POST https://us-central1-medical-advisor-bd734.cloudfunctions.net/app \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_FIREBASE_ID_TOKEN" \
  -d '{
    "action": "sendNotification",
    "fcmToken": "cFsRMBcq4LTZv16bnryeyo:APA91bHmBc2NmsPGtet76zaaL26udELS66iw7UgN95uqRWpI15Xf4vIMNLIcC0Cbmei_uY_blWl6-cqWBX3Sq9sGIh-oY_BBg1zOJYy0f507U9fRt-QuUUw",
    "title": "Test Notification",
    "body": "This is a test notification",
    "notificationAction": {
      "type": "test",
      "data": "test_data"
    } \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_FIREBASE_ID_TOKEN" \
  -d '{
    "action": "sendNotification",
    "fcmToken": "cFsRMBcq4LTZv16bnryeyo:APA91bHmBc2NmsPGtet76zaaL26udELS66iw7UgN95uqRWpI15Xf4vIMNLIcC0Cbmei_uY_blWl6-cqWBX3Sq9sGIh-oY_BBg1zOJYy0f507U9fRt-QuUUw",
    "title": "Test Notification",
    "body": "This is a test notification",
    "notificationAction": {
      "type": "test",
      "data": "test_data"
    }
  }'
```

## Method 3: Test via Postman/HTTP Client

**URL:** `POST https://YOUR_REGION-YOUR_PROJECT.cloudfunctions.net/app`

**Headers:**
```
Content-Type: application/json
Authorization: Bearer YOUR_FIREBASE_ID_TOKEN
```

**Body:**
```json
{
  "action": "sendNotification",
  "fcmToken": "cFsRMBcq4LTZv16bnryeyo:APA91bHmBc2NmsPGtet76zaaL26udELS66iw7UgN95uqRWpI15Xf4vIMNLIcC0Cbmei_uY_blWl6-cqWBX3Sq9sGIh-oY_BBg1zOJYy0f507U9fRt-QuUUw",
  "title": "Test Notification",
  "body": "This is a test notification",
  "notificationAction": {
    "type": "test",
    "data": "test_data"
  }
}
```

## Expected Response

**Success (200):**
```json
{
  "success": true,
  "message": "Notification sent successfully",
  "messageId": "projects/.../messages/..."
}
```

**Error (400/500):**
```json
{
  "success": false,
  "error": "Error message here"
}
```

## Common Issues

1. **Invalid FCM Token**: Token may be expired or device uninstalled app
2. **Missing Auth Token**: Make sure you include the Firebase ID token in Authorization header
3. **Wrong Function URL**: Check your Cloud Function deployment URL

## Testing Broadcast Notifications

To test sending to all users:

```json
{
  "action": "sendNotificationToAll",
  "title": "Announcement",
  "body": "Important update for all users",
  "notificationAction": {
    "type": "announcement"
  }
}
```

