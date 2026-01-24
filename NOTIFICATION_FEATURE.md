# Automatic Arabic Notifications for APK Updates

## Overview

After successful APK upload, the system **automatically sends Arabic push notifications** to all Android users in your Firestore database. No additional API calls needed - it happens automatically!

---

## How It Works

### Step-by-Step Flow

```
1. Admin uploads APK files (v1.0.0)
        ↓
2. Files uploaded to Cloud Storage ✓
        ↓
3. Metadata saved to Firestore ✓
        ↓
4. System loops through all users
        ↓
5. Filters: Check if user has "android" in platforms
        ↓
6. Filters: Check if user has valid fcmToken
        ↓
7. For each matching user, send notification:
   Title:  نسخة جديدة متاحة
   Body:   يرجى تحديث التطبيق إلى النسخة 1.0.0
        ↓
8. Return statistics: "sent": 156, "errors": []
        ↓
9. Upload complete ✓
```

---

## User Requirements

For a user to receive the automatic notification, their Firestore `users` document must have:

### Required Fields

```json
{
  "uid": "user-uid-123",
  "email": "user@example.com",
  "role": "user",
  "platforms": ["android"],           // ← REQUIRED: Must contain "android"
  "fcmToken": "cXVlc...your-token..." // ← REQUIRED: Valid FCM token
}
```

### What Each Field Means

| Field | Purpose | Example |
|-------|---------|---------|
| `platforms` | List of user's app platforms | `["android", "web"]` or `["android"]` |
| `fcmToken` | Firebase Cloud Messaging token for push notifications | `"cXVlc0pYa...` |

### User Status Examples

**✅ Will receive notification:**
```json
{
  "uid": "user-123",
  "platforms": ["android", "web"],
  "fcmToken": "dXdqZWF3Zmf94jds..."
}
```

**❌ Won't receive (iOS only):**
```json
{
  "uid": "user-456",
  "platforms": ["ios"],
  "fcmToken": "dUl0Zm94ZGFzd..."
}
```

**❌ Won't receive (no FCM token):**
```json
{
  "uid": "user-789",
  "platforms": ["android"]
  // No fcmToken field
}
```

---

## Notification Content

### Message Format

**Title (Arabic):** `نسخة جديدة متاحة`
- English: "New version available"
- Display: Appears as notification title

**Body (Arabic):** `يرجى تحديث التطبيق إلى النسخة 1.0.0`
- English: "Please update the app to version 1.0.0"
- Display: Appears as notification body

### Data Included

The notification also includes metadata for your app to handle:

```json
{
  "version": "1.0.0",
  "action": "apk_update",
  "type": "app_update"
}
```

Your Android app can listen for `action: "apk_update"` and:
- Prompt user to update
- Open download page
- Show update dialog
- Log analytics event

---

## Response Format

### Upload Success Response

After uploading APKs, the response includes notification statistics:

```json
{
  "success": true,
  "message": "APKs uploaded successfully",
  "version": "1.0.0",
  "downloads": {
    "version": "1.0.0",
    "release_notes": "New features and bug fixes",
    "apks": {
      "arm64": {...},
      "armv7": {...},
      "x86_64": {...},
      "universal": {...}
    }
  },
  "notifications": {
    "sent": 156,
    "errors": [
      "Error sending to user-456: Invalid token",
      "Error sending to user-789: Unregistered token"
    ]
  }
}
```

### Response Fields Explained

| Field | Meaning |
|-------|---------|
| `notifications.sent` | Number of notifications successfully sent |
| `notifications.errors` | Array of errors (doesn't fail upload) |

---

## Implementation Details

### Function: `_send_notifications_to_android_users()`

```python
def _send_notifications_to_android_users(version, db):
    """
    Sends Arabic notifications to users with Android platform.

    1. Queries all users from Firestore
    2. Checks if user has "android" in platforms list
    3. Verifies user has valid fcmToken
    4. Sends Arabic notification via FCM
    5. Returns count and any errors
    """
```

### How Notifications Are Sent

1. **Query** all users from `users` collection
2. **Filter** by platform:
   - Check if `"android"` in user's `platforms` array
   - Case-insensitive matching
3. **Verify** FCM token:
   - Skip if `fcmToken` field is missing
   - Skip if token is empty
4. **Send** via Firebase Cloud Messaging:
   - Title in Arabic
   - Body with version number
   - Metadata for app handling
5. **Log** all results:
   - Success: "✅ APK update notification sent to user-123"
   - Error: "❌ Error sending to user-456: Invalid token"

### Error Handling

**The system is resilient:**
- ❌ Invalid token → Logged, upload continues
- ❌ Unregistered token → Logged, upload continues
- ❌ User without fcmToken → Skipped silently
- ❌ User without android platform → Skipped silently
- ❌ Network error → Logged, upload continues

**Notification errors do NOT fail the APK upload!**

---

## Testing

### Test Setup

1. **Create test user in Firestore:**
   ```json
   {
     "uid": "test-user-123",
     "email": "test@example.com",
     "role": "user",
     "platforms": ["android"],
     "fcmToken": "your-valid-fcm-token-here"
   }
   ```

2. **Get valid FCM token:**
   - From your Android app after Firebase initialization
   - Use: `FirebaseMessaging.getInstance().getToken()`

3. **Upload APKs:**
   ```bash
   curl -X POST https://your-function-url \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -F "action=uploadApks" \
     -F "version=1.0.0" \
     -F "app-arm64-v8a-release.apk=@./app-arm64-v8a-release.apk" \
     ... other files ...
   ```

4. **Check response for notifications:**
   ```json
   "notifications": {
     "sent": 1,
     "errors": []
   }
   ```

5. **Verify on device:**
   - Device should receive push notification
   - Notification text in Arabic
   - Title: "نسخة جديدة متاحة"
   - Body: "يرجى تحديث التطبيق إلى النسخة 1.0.0"

6. **Check server logs:**
   - Look for: "✅ APK update notification sent to test-user-123"

### Test Scenarios

**Scenario 1: Successful notification**
- User has "android" in platforms
- User has valid fcmToken
- Result: ✅ Notification sent

**Scenario 2: Missing platform**
- User only has "ios" in platforms
- Result: ⏭️ Skipped (no notification)

**Scenario 3: Missing FCM token**
- User has "android" but no fcmToken
- Result: ⏭️ Skipped (no notification)

**Scenario 4: Invalid token**
- User has "android" and fcmToken
- Token is expired/revoked
- Result: ❌ Error logged, upload succeeds

---

## Troubleshooting

### Q: Notifications not being sent
**A:** Check:
- ✅ User document has `"android"` in `platforms` array
- ✅ User document has `fcmToken` field
- ✅ FCM token is valid and active
- ✅ User collection exists and is readable

### Q: Wrong number of notifications sent
**A:**
- Count = users with both "android" platform AND valid fcmToken
- Some users may not have both fields
- Check Firestore documents to verify

### Q: Notifications are in English, not Arabic
**A:** This should not happen. Check:
- ✅ Module is using Arabic strings
- ✅ Database has been redeployed
- ✅ Check server logs for errors

### Q: Some users got notification, others didn't
**A:** Likely causes:
- Some users missing `platforms` field
- Some users missing `fcmToken` field
- Some users have expired tokens
- Check `notifications.errors` array in response

### Q: Upload succeeded but can't find notification stats
**A:**
- Look at `notifications` field in response
- It's always included in successful upload
- May be empty if no users matched filters

---

## Best Practices

### 1. Keep FCM Tokens Fresh
- Refresh FCM tokens periodically in your app
- Handle token refresh events
- Remove old/invalid tokens

### 2. Test Before Production
- Upload test APK first
- Verify notifications reach devices
- Check message appears correctly

### 3. Monitor Notification Errors
- Check `notifications.errors` array
- Address common errors:
  - Invalid tokens → User may have uninstalled app
  - Unregistered tokens → Token expired

### 4. Update User Documents
- Ensure all users have `platforms` array
- Ensure Android users have `fcmToken`
- Remove tokens when user uninstalls app

### 5. Handle Notifications in App
- Listen for `action: "apk_update"`
- Show update prompt to user
- Track notification reception for analytics

---

## Implementation Architecture

### Code Location

**Main function:**
```
modules/apk_manager.py:_send_notifications_to_android_users()
```

**Called by:**
```
modules/apk_manager.py:upload_apks()
```

**Routes:**
```
app.py: action="uploadApks" → calls upload_apks()
```

### Database Collections Used

**Reads from:**
- `users` collection
  - Checks: `platforms` array, `fcmToken` field

**Writes to:**
- `downloads` collection (metadata with URLs)

**Sends via:**
- Firebase Cloud Messaging (FCM)
  - External service, not Firestore

---

## Arabic Message Reference

### Arabic Text Used

| English | Arabic | Unicode |
|---------|--------|---------|
| New version available | نسخة جديدة متاحة | AR |
| Please update the app to version | يرجى تحديث التطبيق إلى النسخة | AR |

### Message Examples

- Version 1.0.0: "يرجى تحديث التطبيق إلى النسخة 1.0.0"
- Version 2.1.5: "يرجى تحديث التطبيق إلى النسخة 2.1.5"
- Version 10.0.0: "يرجى تحديث التطبيق إلى النسخة 10.0.0"

---

## Complete Example

### 1. Prepare User Document
```json
{
  "uid": "user-123",
  "email": "ahmed@example.com",
  "role": "user",
  "platforms": ["android", "web"],
  "fcmToken": "dXdqZWF3Zmf94jds_valid_token_here"
}
```

### 2. Upload APKs
```bash
curl -X POST https://your-function-url \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -F "action=uploadApks" \
  -F "version=2.0.0" \
  -F "release_notes=Major update with new features" \
  -F "app-arm64-v8a-release.apk=@./app-arm64-v8a-release.apk" \
  -F "app-armeabi-v7a-release.apk=@./app-armeabi-v7a-release.apk" \
  -F "app-x86_64-release.apk=@./app-x86_64-release.apk" \
  -F "app-universal-release.apk=@./app-universal-release.apk"
```

### 3. Server Response
```json
{
  "success": true,
  "message": "APKs uploaded successfully",
  "version": "2.0.0",
  "notifications": {
    "sent": 1,
    "errors": []
  }
}
```

### 4. User Receives Notification
```
Device notification:
┌─────────────────────────────────┐
│ نسخة جديدة متاحة                   │
│                                 │
│ يرجى تحديث التطبيق إلى النسخة 2.0.0 │
└─────────────────────────────────┘
```

### 5. App Handles Notification
```java
// In your Android app's FirebaseMessagingService
@Override
public void onMessageReceived(RemoteMessage remoteMessage) {
  String action = remoteMessage.getData().get("action");
  String version = remoteMessage.getData().get("version");

  if ("apk_update".equals(action)) {
    // Show update dialog
    showUpdateDialog(version);
  }
}
```

---

## Security Considerations

- ✅ Only admins can upload APKs
- ✅ Notifications sent to users in database only
- ✅ FCM tokens are secure, Firebase handles delivery
- ✅ Errors don't expose sensitive information
- ✅ Arabic text is standard Unicode

---

## Summary

The automatic notification system:
1. **Triggers automatically** after APK upload
2. **Targets only Android users** with valid tokens
3. **Sends in Arabic** for localization
4. **Non-blocking** - errors don't fail upload
5. **Tracked** - statistics returned in response
6. **Resilient** - handles errors gracefully

See `APK_UPLOAD_GUIDE.md` for complete upload documentation.
