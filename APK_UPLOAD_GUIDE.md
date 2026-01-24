# APK Upload & Download Guide

This guide explains how to use the APK management system to upload and download Android APK files.

## Overview

The APK manager provides four main functions:
1. **Upload APKs** - Upload 4 APK files (arm64, armv7, x86_64, universal) with version management
2. **Get Download URL** - Get APK download URL based on device architecture and user email
3. **Get All Versions** - List all available APK versions (admin only)
4. **Delete Version** - Delete an APK version from storage and Firestore (admin only)

---

## 1. Upload APKs (Admin Only)

### Endpoint
```
POST /api
```

### Required Fields
- **action**: `"uploadApks"`
- **version**: Version string (e.g., "1.0.0", "1.2.3")
- **release_notes** (optional): Release notes text

### Required Files (multipart/form-data)
You must upload ALL 4 APK files with these exact names:
```
- app-arm64-v8a-release.apk
- app-armeabi-v7a-release.apk
- app-x86_64-release.apk
- app-universal-release.apk
```

### Headers
```
Authorization: Bearer <YOUR_FIREBASE_TOKEN>
Content-Type: multipart/form-data
```

### cURL Example
```bash
curl -X POST https://your-function-url \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "action=uploadApks" \
  -F "version=1.0.0" \
  -F "release_notes=Initial release with bug fixes" \
  -F "app-arm64-v8a-release.apk=@/path/to/app-arm64-v8a-release.apk" \
  -F "app-armeabi-v7a-release.apk=@/path/to/app-armeabi-v7a-release.apk" \
  -F "app-x86_64-release.apk=@/path/to/app-x86_64-release.apk" \
  -F "app-universal-release.apk=@/path/to/app-universal-release.apk"
```

### JavaScript/Node.js Example
```javascript
async function uploadApks(token, version, releaseNotes, fileInputs) {
  const formData = new FormData();

  // Add form fields
  formData.append('action', 'uploadApks');
  formData.append('version', version);
  formData.append('release_notes', releaseNotes);

  // Add files (fileInputs is an object with architecture as key and File as value)
  const fileNames = {
    'arm64': 'app-arm64-v8a-release.apk',
    'armv7': 'app-armeabi-v7a-release.apk',
    'x86_64': 'app-x86_64-release.apk',
    'universal': 'app-universal-release.apk'
  };

  for (const [arch, fileName] of Object.entries(fileNames)) {
    if (fileInputs[arch]) {
      formData.append(fileName, fileInputs[arch]);
    }
  }

  const response = await fetch('https://your-function-url', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`
    },
    body: formData
  });

  return response.json();
}

// Usage
const files = {
  'arm64': document.getElementById('arm64File').files[0],
  'armv7': document.getElementById('armv7File').files[0],
  'x86_64': document.getElementById('x86_64File').files[0],
  'universal': document.getElementById('universalFile').files[0]
};

uploadApks(token, '1.0.0', 'Initial release', files)
  .then(response => console.log(response))
  .catch(error => console.error(error));
```

### Python Example
```python
import requests

def upload_apks(token, version, release_notes, apk_files):
    """
    Upload APK files to the cloud function.

    Args:
        token: Firebase ID token
        version: Version string (e.g., "1.0.0")
        release_notes: Release notes text
        apk_files: Dict with architecture as key and file path as value
            {
                'arm64': '/path/to/app-arm64-v8a-release.apk',
                'armv7': '/path/to/app-armeabi-v7a-release.apk',
                'x86_64': '/path/to/app-x86_64-release.apk',
                'universal': '/path/to/app-universal-release.apk'
            }
    """
    url = 'https://your-function-url'

    headers = {
        'Authorization': f'Bearer {token}'
    }

    data = {
        'action': 'uploadApks',
        'version': version,
        'release_notes': release_notes
    }

    # Prepare multipart files
    file_names = {
        'arm64': 'app-arm64-v8a-release.apk',
        'armv7': 'app-armeabi-v7a-release.apk',
        'x86_64': 'app-x86_64-release.apk',
        'universal': 'app-universal-release.apk'
    }

    files = {}
    for arch, file_path in apk_files.items():
        file_name = file_names[arch]
        files[file_name] = open(file_path, 'rb')

    try:
        response = requests.post(url, headers=headers, data=data, files=files)
        return response.json()
    finally:
        for file_obj in files.values():
            file_obj.close()

# Usage
apk_files = {
    'arm64': '/home/user/app-arm64-v8a-release.apk',
    'armv7': '/home/user/app-armeabi-v7a-release.apk',
    'x86_64': '/home/user/app-x86_64-release.apk',
    'universal': '/home/user/app-universal-release.apk'
}

result = upload_apks(token, '1.0.0', 'Initial release', apk_files)
print(result)
```

### Response Example
```json
{
  "success": true,
  "message": "APKs uploaded successfully",
  "version": "1.0.0",
  "downloads": {
    "version": "1.0.0",
    "release_notes": "Initial release with bug fixes",
    "uploaded_at": "2024-01-21T10:30:45.123456",
    "uploaded_by": "user-uid-123",
    "apks": {
      "arm64": {
        "name": "arm64-v8a",
        "filename": "app-arm64-v8a-release.apk",
        "url": "https://storage.googleapis.com/...",
        "size": 45678900
      },
      "armv7": {
        "name": "armeabi-v7a",
        "filename": "app-armeabi-v7a-release.apk",
        "url": "https://storage.googleapis.com/...",
        "size": 42345600
      },
      "x86_64": {
        "name": "x86_64",
        "filename": "app-x86_64-release.apk",
        "url": "https://storage.googleapis.com/...",
        "size": 48765400
      },
      "universal": {
        "name": "universal",
        "filename": "app-universal-release.apk",
        "url": "https://storage.googleapis.com/...",
        "size": 52000000
      }
    }
  },
  "notifications": {
    "sent": 156,
    "errors": []
  }
}
```

### Automatic Arabic Notifications

After successful upload, the system automatically:
1. **Loops through all users** in your Firestore `users` collection
2. **Checks platform list** - Only sends to users with `"android"` in their `platforms` field
3. **Sends notification in Arabic**:
   - Title: "نسخة جديدة متاحة" (New version available)
   - Body: "يرجى تحديث التطبيق إلى النسخة X.X.X" (Please update the app to version X.X.X)
4. **Returns notification stats** - Shows how many users received the notification

**User Requirements for Notifications:**
- Must have `"android"` in `platforms` array (case-insensitive)
- Must have `fcmToken` field with valid Firebase Cloud Messaging token

---

## 1.5 Automatic Arabic Notifications (After Upload)

### How It Works

When APKs are uploaded successfully, the system **automatically** sends push notifications to all Android users:

1. **Loop through all users** in Firestore `users` collection
2. **Check platforms**: Only target users with `"android"` in their `platforms` array
3. **Verify FCM token**: Skip users without a valid `fcmToken`
4. **Send Arabic notification** via Firebase Cloud Messaging:
   - **Title**: نسخة جديدة متاحة *(New version available)*
   - **Body**: يرجى تحديث التطبيق إلى النسخة X.X.X *(Please update the app to version X.X.X)*
5. **Return statistics** showing how many notifications were sent

### Required User Fields

For users to receive notifications, their Firestore `users` document must have:

```json
{
  "uid": "user-uid-123",
  "email": "user@example.com",
  "role": "user",
  "platforms": ["android"],           // REQUIRED: Must contain "android"
  "fcmToken": "cXVlc...token..."      // REQUIRED: Valid FCM token
}
```

### Response Statistics

The upload response includes notification statistics:

```json
{
  "success": true,
  "message": "APKs uploaded successfully",
  "version": "1.0.0",
  "notifications": {
    "sent": 156,
    "errors": [
      "Error sending to user-456: Invalid token",
      "Error sending to user-789: Unregistered token"
    ]
  }
}
```

### Example: Expected User Documents

**User 1 - Will receive notification:**
```json
{
  "uid": "user-123",
  "email": "ahmed@example.com",
  "role": "user",
  "platforms": ["android", "web"],
  "fcmToken": "dXdqZWF3Zmf94jds..."
}
```

**User 2 - Will NOT receive notification (iOS only):**
```json
{
  "uid": "user-456",
  "email": "sara@example.com",
  "role": "user",
  "platforms": ["ios"],
  "fcmToken": "dUl0Zm94ZGFzd..."
}
```

**User 3 - Will NOT receive notification (no FCM token):**
```json
{
  "uid": "user-789",
  "email": "fatima@example.com",
  "role": "user",
  "platforms": ["android"]
  // Note: No fcmToken field
}
```

### Notification Behavior

- **Automatic**: No additional API calls needed - happens after upload
- **Non-blocking**: Notification errors don't fail the upload
- **Logged**: All errors are tracked and returned in response
- **Arabic**: All text is in Arabic for localization
- **Metadata**: Notification includes version and action type for app to handle

### Testing Notifications

1. **Setup test user** in Firestore with `"android"` in platforms and valid FCM token
2. **Upload APKs** using any of the methods above
3. **Check response** for notification statistics
4. **Check server logs** for detailed notification sent/error messages
5. **Device should receive** Arabic notification about new version

### Troubleshooting Notifications

**Q: Notification not received**
A: Check that user document has:
- ✅ `"android"` in `platforms` array (exact match, case-insensitive)
- ✅ Valid `fcmToken` field with active token

**Q: Notification sent but app doesn't respond**
A: App must handle notification data with action `"apk_update"` in its notification handler

**Q: Some users didn't get notification**
A: Check errors array in response - tokens may be expired or unregistered

---

## 2. Get APK Download URL (No Auth Required)

This endpoint checks user email and device architecture, then returns the appropriate download URL.

### Endpoint
```
POST /api
```

### Request Fields
```json
{
  "action": "getApkDownloadUrl",
  "email": "user@example.com",
  "architecture": "arm64",  // Optional: arm64, armv7, x86_64, universal (default)
  "version": "1.0.0"        // Optional: defaults to latest version
}
```

### Headers
```
Content-Type: application/json
```

### cURL Example
```bash
# Get universal APK for latest version
curl -X POST https://your-function-url \
  -H "Content-Type: application/json" \
  -d '{
    "action": "getApkDownloadUrl",
    "email": "user@example.com"
  }'

# Get arm64 APK for specific version
curl -X POST https://your-function-url \
  -H "Content-Type: application/json" \
  -d '{
    "action": "getApkDownloadUrl",
    "email": "user@example.com",
    "architecture": "arm64",
    "version": "1.0.0"
  }'
```

### JavaScript Example
```javascript
async function getApkDownloadUrl(email, architecture = 'universal', version = null) {
  const payload = {
    action: 'getApkDownloadUrl',
    email: email
  };

  if (architecture) {
    payload.architecture = architecture;
  }

  if (version) {
    payload.version = version;
  }

  const response = await fetch('https://your-function-url', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });

  return response.json();
}

// Usage - Get universal APK
getApkDownloadUrl('user@example.com')
  .then(data => {
    console.log('Download URL:', data.download_url);
    // Use the URL to download the APK
  });

// Usage - Get arm64 APK for specific version
getApkDownloadUrl('user@example.com', 'arm64', '1.0.0')
  .then(data => console.log(data));
```

### Python Example
```python
import requests

def get_apk_download_url(email, architecture='universal', version=None):
    """
    Get APK download URL based on device architecture.

    Args:
        email: User email to verify
        architecture: Device architecture (arm64, armv7, x86_64, universal)
        version: Specific APK version (defaults to latest)

    Returns:
        Dict with download URL and metadata
    """
    url = 'https://your-function-url'

    payload = {
        'action': 'getApkDownloadUrl',
        'email': email
    }

    if architecture:
        payload['architecture'] = architecture

    if version:
        payload['version'] = version

    response = requests.post(url, json=payload)
    return response.json()

# Usage
result = get_apk_download_url('user@example.com', 'arm64')
if result.get('success'):
    print(f"Download URL: {result['download_url']}")
    print(f"Version: {result['version']}")
    print(f"File size: {result['size']} bytes")
else:
    print(f"Error: {result['error']}")
```

### Response Example
```json
{
  "success": true,
  "email": "user@example.com",
  "version": "1.0.0",
  "architecture": "arm64",
  "download_url": "https://storage.googleapis.com/your-bucket/downloads/1.0.0/app-arm64-v8a-release.apk",
  "filename": "app-arm64-v8a-release.apk",
  "size": 45678900
}
```

### Error Responses
```json
// User not found
{
  "error": "User not found"
}

// Invalid architecture (falls back to universal)
{
  "success": true,
  "architecture": "universal",
  "download_url": "..."
}
```

---

## 3. Get All APK Versions (Admin Only)

List all available APK versions with metadata.

### Endpoint
```
POST /api
```

### Request Fields
```json
{
  "action": "getAllApkVersions"
}
```

### Headers
```
Authorization: Bearer <YOUR_FIREBASE_TOKEN>
Content-Type: application/json
```

### cURL Example
```bash
curl -X POST https://your-function-url \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "getAllApkVersions"}'
```

### Response Example
```json
{
  "success": true,
  "versions": [
    {
      "version": "1.2.0",
      "uploaded_at": "2024-01-21T15:30:00",
      "uploaded_by": "user-uid-123",
      "release_notes": "Latest features",
      "apks_count": 4
    },
    {
      "version": "1.1.0",
      "uploaded_at": "2024-01-15T10:20:00",
      "uploaded_by": "user-uid-123",
      "release_notes": "Bug fixes",
      "apks_count": 4
    }
  ],
  "total": 2
}
```

---

## 4. Delete APK Version (Admin Only)

Delete a specific APK version from storage and Firestore.

### Endpoint
```
POST /api
```

### Request Fields
```json
{
  "action": "deleteApkVersion",
  "version": "1.0.0"
}
```

### Headers
```
Authorization: Bearer <YOUR_FIREBASE_TOKEN>
Content-Type: application/json
```

### cURL Example
```bash
curl -X POST https://your-function-url \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "deleteApkVersion", "version": "1.0.0"}'
```

### Response Example
```json
{
  "success": true,
  "message": "Version 1.0.0 deleted successfully"
}
```

---

## Complete Upload & Download Workflow

### Step 1: Build All APK Variants
In Android Studio or using Gradle:
```bash
./gradlew assembleRelease
```

This creates:
- `app/release/outputs/apk/arm64/app-arm64-v8a-release.apk`
- `app/release/outputs/apk/armv7/app-armeabi-v7a-release.apk`
- `app/release/outputs/apk/x86_64/app-x86_64-release.apk`
- `app/release/outputs/universal/app-universal-release.apk`

### Step 2: Upload All 4 APKs
Use the cURL, JavaScript, or Python example above with all 4 files.

### Step 3: Users Download APK by Email
Users only need to provide their email and optionally their device architecture:
```json
{
  "action": "getApkDownloadUrl",
  "email": "user@example.com",
  "architecture": "arm64"
}
```

The system automatically:
- ✅ Verifies the user exists in Firestore
- ✅ Detects device architecture (or defaults to universal)
- ✅ Returns the appropriate download URL
- ✅ Falls back to universal APK if specific architecture isn't available

### Step 4: Manage Versions
- **List versions**: Use `getAllApkVersions` (admin only)
- **Delete old versions**: Use `deleteApkVersion` (admin only)
- **Update new version**: Simply upload new APKs with a new version number (old ones are replaced)

---

## File Structure in Cloud Storage

```
gs://your-bucket/downloads/
├── 1.0.0/
│   ├── app-arm64-v8a-release.apk
│   ├── app-armeabi-v7a-release.apk
│   ├── app-x86_64-release.apk
│   └── app-universal-release.apk
├── 1.1.0/
│   ├── app-arm64-v8a-release.apk
│   ├── app-armeabi-v7a-release.apk
│   ├── app-x86_64-release.apk
│   └── app-universal-release.apk
└── 1.2.0/
    ├── app-arm64-v8a-release.apk
    ├── app-armeabi-v7a-release.apk
    ├── app-x86_64-release.apk
    └── app-universal-release.apk
```

---

## Firestore Structure

```
downloads/
├── 1.0.0/
│   ├── version: "1.0.0"
│   ├── release_notes: "Initial release"
│   ├── uploaded_at: timestamp
│   ├── uploaded_by: "user-uid"
│   └── apks:
│       ├── arm64:
│       │   ├── name: "arm64-v8a"
│       │   ├── filename: "app-arm64-v8a-release.apk"
│       │   ├── url: "https://storage.googleapis.com/..."
│       │   └── size: 45678900
│       ├── armv7: {...}
│       ├── x86_64: {...}
│       └── universal: {...}
```

---

## Security Notes

1. **Upload** - Admin only (verified via Firestore user role)
2. **Download** - Email verification only (no auth token required)
3. **List versions** - Admin only
4. **Delete versions** - Admin only
5. All files are publicly accessible via Cloud Storage URLs
6. User email must exist in `users` collection to download

---

## Troubleshooting

### Upload fails with "Missing file"
- Ensure all 4 APK files are included with exact filenames
- Check file names match exactly: `app-arm64-v8a-release.apk`, etc.

### Download returns "User not found"
- Verify user email exists in Firestore `users` collection
- Check email spelling matches exactly (case-sensitive in some cases)

### User gets universal APK instead of specific architecture
- This is by design - fallback happens if requested architecture not available
- Universal APK works on all devices

### Files not deleting when new version uploaded
- Old version isn't automatically deleted
- Use `deleteApkVersion` action to manually remove old versions
