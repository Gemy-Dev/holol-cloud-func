# APK Upload & Download System - Implementation Summary

## What Was Created

### 1. Core Module: `modules/apk_manager.py`
Contains 4 main functions:

#### `upload_apks(data, decoded_token, db)`
- **Purpose**: Upload 4 APK files (arm64, armv7, x86_64, universal) for a new version
- **Auth**: Admin only (verified via Firestore user role)
- **Input**: Multipart form with 4 APK files + version + optional release notes
- **Output**: Success response with all download URLs + notification statistics
- **Automatic Notifications**: After successful upload, sends Arabic notifications to all users with:
  - `"android"` in their `platforms` array
  - Valid `fcmToken` in their user document
  - Notification text: "نسخة جديدة متاحة" (New version available) + version number

#### `get_apk_download_url(data, db)`
- **Purpose**: Get APK download URL based on user email and device architecture
- **Auth**: No auth needed (email verification only)
- **Input**: User email + optional architecture + optional version
- **Output**: Direct download URL to APK file
- **Behavior**:
  - Verifies user email exists in Firestore `users` collection
  - Falls back to universal APK if specific architecture not available
  - Returns latest version if no version specified

#### `get_all_apk_versions(decoded_token, db)`
- **Purpose**: List all available APK versions with metadata
- **Auth**: Admin only
- **Output**: Array of all versions with upload dates and release notes

#### `delete_apk_version(data, decoded_token, db)`
- **Purpose**: Delete an APK version from Cloud Storage and Firestore
- **Auth**: Admin only
- **Input**: Version to delete
- **Behavior**: Deletes all 4 APK files for that version from Cloud Storage

### 2. Updated: `app.py`
Added 4 new routes to handle APK actions:
- `uploadApks` → calls upload_apks()
- `getApkDownloadUrl` → calls get_apk_download_url()
- `getAllApkVersions` → calls get_all_apk_versions()
- `deleteApkVersion` → calls delete_apk_version()

### 3. Documentation
- **APK_UPLOAD_GUIDE.md** - Complete guide with all examples (cURL, Python, JavaScript)
- **APK_QUICK_REFERENCE.md** - Quick reference for common tasks
- **APK_IMPLEMENTATION_SUMMARY.md** - This file

---

## Automatic Notification System

After successful APK upload, the system automatically:

### 1. **Notification Flow**
```
APK Upload Success
    ↓
Loop through all users in Firestore
    ↓
Check if user has "android" in platforms
    ↓
Check if user has valid fcmToken
    ↓
Send Arabic notification: "نسخة جديدة متاحة"
    ↓
Return notification statistics
```

### 2. **User Requirements**
For a user to receive the update notification, they must have:
- **platforms array** containing `"android"` (case-insensitive)
- **fcmToken** field with a valid Firebase Cloud Messaging token

Example user document:
```json
{
  "uid": "user-123",
  "email": "user@example.com",
  "role": "user",
  "platforms": ["android", "ios"],
  "fcmToken": "cXVlc...your-token..."
}
```

### 3. **Notification Content**
- **Title (Arabic)**: نسخة جديدة متاحة
  - English: "New version available"
- **Body (Arabic)**: يرجى تحديث التطبيق إلى النسخة 1.0.0
  - English: "Please update the app to version 1.0.0"
- **Data fields**:
  - `version`: The APK version
  - `action`: "apk_update"
  - `type`: "app_update"

### 4. **Response Statistics**
```json
{
  "notifications": {
    "sent": 156,
    "errors": ["Error sending to user-456: Invalid token", ...]
  }
}
```

### 5. **Error Handling**
The system handles errors gracefully:
- Invalid/expired FCM tokens → Logged but doesn't stop upload
- Users without fcmToken → Skipped silently
- Users without "android" platform → Not targeted
- Unregistered tokens → Reported in errors array

---

## How to Use

### Phase 1: Build Your APKs

Build all 4 APK variants in Android Studio:
```bash
./gradlew assembleRelease
```

This creates:
- `app-arm64-v8a-release.apk` (64-bit phones)
- `app-armeabi-v7a-release.apk` (32-bit phones)
- `app-x86_64-release.apk` (tablets/emulators)
- `app-universal-release.apk` (all devices)

### Phase 2: Upload All 4 Files

You have 3 options to upload:

#### Option A: Using cURL (Quick)
```bash
curl -X POST https://your-function-url \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "action=uploadApks" \
  -F "version=1.0.0" \
  -F "release_notes=Initial release" \
  -F "app-arm64-v8a-release.apk=@./app-arm64-v8a-release.apk" \
  -F "app-armeabi-v7a-release.apk=@./app-armeabi-v7a-release.apk" \
  -F "app-x86_64-release.apk=@./app-x86_64-release.apk" \
  -F "app-universal-release.apk=@./app-universal-release.apk"
```

#### Option B: Using Python Script
```python
import requests

token = "YOUR_FIREBASE_TOKEN"
version = "1.0.0"

files = {
    'app-arm64-v8a-release.apk': open('app-arm64-v8a-release.apk', 'rb'),
    'app-armeabi-v7a-release.apk': open('app-armeabi-v7a-release.apk', 'rb'),
    'app-x86_64-release.apk': open('app-x86_64-release.apk', 'rb'),
    'app-universal-release.apk': open('app-universal-release.apk', 'rb'),
}

data = {
    'action': 'uploadApks',
    'version': version,
    'release_notes': 'Your release notes here'
}

response = requests.post(
    'https://your-function-url',
    headers={'Authorization': f'Bearer {token}'},
    data=data,
    files=files
)

print(response.json())
```

#### Option C: Using JavaScript (Web Upload)
```javascript
async function uploadAPKs(token, version, releaseNotes, files) {
  const formData = new FormData();
  formData.append('action', 'uploadApks');
  formData.append('version', version);
  formData.append('release_notes', releaseNotes);

  formData.append('app-arm64-v8a-release.apk', files.arm64);
  formData.append('app-armeabi-v7a-release.apk', files.armv7);
  formData.append('app-x86_64-release.apk', files.x86_64);
  formData.append('app-universal-release.apk', files.universal);

  const response = await fetch('https://your-function-url', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` },
    body: formData
  });

  return response.json();
}
```

**Important**: You must upload **ALL 4 FILES** at once. The system will not accept partial uploads.

### Phase 3: Share Download Link with Users

Give users (or your app) just this information:
- **Your function URL**
- **Their email address**

Users call:
```bash
curl -X POST https://your-function-url \
  -H "Content-Type: application/json" \
  -d '{
    "action": "getApkDownloadUrl",
    "email": "user@example.com"
  }'
```

Response:
```json
{
  "success": true,
  "download_url": "https://storage.googleapis.com/...",
  "version": "1.0.0",
  "filename": "app-arm64-v8a-release.apk"
}
```

Users can then download directly from the URL.

### Phase 4: Update to New Version

When you have a new version (e.g., 1.1.0):
1. Build new APKs with version 1.1.0
2. Upload all 4 files using same steps as Phase 2, but with version "1.1.0"
3. Old version "1.0.0" stays available in cloud storage
4. Users automatically get the latest version if they don't specify a version

---

## Architecture Fallback Logic

```
User requests → Check requested architecture
               ↓
            Available? ──→ Return it
               ↓ No
            Return universal APK
```

Example:
- User has **arm64** device → Gets `app-arm64-v8a-release.apk`
- User has **armv7** device → Gets `app-armeabi-v7a-release.apk`
- User has **x86_64** tablet → Gets `app-x86_64-release.apk`
- User has unknown architecture → Gets `app-universal-release.apk` (works everywhere)

---

## Firestore Structure Created

Every time you upload, a new document is created in the `downloads` collection:

```
downloads/
  └── 1.0.0/
      ├── version: "1.0.0"
      ├── release_notes: "Your notes"
      ├── uploaded_at: <timestamp>
      ├── uploaded_by: "user-uid-123"
      └── apks:
          ├── arm64:
          │   ├── name: "arm64-v8a"
          │   ├── filename: "app-arm64-v8a-release.apk"
          │   ├── url: "https://storage.googleapis.com/..."
          │   └── size: 45678900
          ├── armv7: {...}
          ├── x86_64: {...}
          └── universal: {...}
```

---

## Cloud Storage Structure Created

```
gs://your-bucket/
  └── downloads/
      └── 1.0.0/
          ├── app-arm64-v8a-release.apk
          ├── app-armeabi-v7a-release.apk
          ├── app-x86_64-release.apk
          └── app-universal-release.apk
```

---

## Security Features

✅ **Upload Protection**: Only admins can upload (checked via Firestore `users.role`)
✅ **Download Verification**: Users must exist in `users` collection
✅ **No Token Needed for Download**: Simple email verification only
✅ **Public URLs**: Files are publicly accessible (if you want private, can be configured)
✅ **Version Control**: Each version is separate, can delete old versions

---

## Common Tasks

### Task: Upload Version 1.0.0
```bash
# Option 1: cURL
curl -X POST https://your-function-url \
  -H "Authorization: Bearer TOKEN" \
  -F "action=uploadApks" \
  -F "version=1.0.0" \
  -F "release_notes=Initial release" \
  -F "app-arm64-v8a-release.apk=@./app-arm64-v8a-release.apk" \
  -F "app-armeabi-v7a-release.apk=@./app-armeabi-v7a-release.apk" \
  -F "app-x86_64-release.apk=@./app-x86_64-release.apk" \
  -F "app-universal-release.apk=@./app-universal-release.apk"
```

### Task: Get Download URL for User
```bash
curl -X POST https://your-function-url \
  -H "Content-Type: application/json" \
  -d '{"action": "getApkDownloadUrl", "email": "user@example.com"}'
```

### Task: Get Specific Architecture
```bash
curl -X POST https://your-function-url \
  -H "Content-Type: application/json" \
  -d '{
    "action": "getApkDownloadUrl",
    "email": "user@example.com",
    "architecture": "arm64"
  }'
```

### Task: View All Versions (Admin)
```bash
curl -X POST https://your-function-url \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "getAllApkVersions"}'
```

### Task: Delete Old Version (Admin)
```bash
curl -X POST https://your-function-url \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "deleteApkVersion", "version": "1.0.0"}'
```

---

## Requirements

- ✅ Firebase Admin SDK (already in project)
- ✅ Cloud Storage bucket (Firebase default)
- ✅ Firestore database (already in project)
- ✅ User collection with email field
- ✅ Admin user must have `role: "admin"` in Firestore

---

## Troubleshooting

**Q: Upload fails with "Missing file"**
A: Make sure all 4 files are included with exact names:
- app-arm64-v8a-release.apk
- app-armeabi-v7a-release.apk
- app-x86_64-release.apk
- app-universal-release.apk

**Q: Download returns "User not found"**
A: Email must exist in your Firestore `users` collection

**Q: User gets universal APK instead of requested architecture**
A: This is by design - it's a fallback. Universal APK works on all devices.

**Q: Where are the files stored?**
A:
- Cloud Storage: `gs://your-bucket/downloads/{version}/{filename}`
- Firestore: `downloads/{version}` document with metadata

**Q: Can I update a version in place?**
A: No, each version is separate. To "update" a version, delete old one and upload new one with same version number.

---

## Next Steps

1. **Test with cURL**: Try uploading one version to verify everything works
2. **Create Upload UI**: Build admin interface for uploading APKs
3. **Create Download UI**: Build user interface for downloading APKs
4. **Integrate in App**: Call `getApkDownloadUrl` from your Android app

---

## Files Summary

| File | Purpose |
|------|---------|
| `modules/apk_manager.py` | Core APK management functions |
| `app.py` | Updated with APK routes |
| `APK_UPLOAD_GUIDE.md` | Complete detailed guide |
| `APK_QUICK_REFERENCE.md` | Quick command reference |
| `APK_IMPLEMENTATION_SUMMARY.md` | This file |

See `APK_UPLOAD_GUIDE.md` for complete documentation with examples for Python, JavaScript, and cURL.
