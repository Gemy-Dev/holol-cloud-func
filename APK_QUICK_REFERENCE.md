# APK Manager - Quick Reference

## Upload All 4 APKs (Admin Only)

### cURL
```bash
curl -X POST https://your-function-url \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "action=uploadApks" \
  -F "version=1.0.0" \
  -F "release_notes=Your release notes here" \
  -F "app-arm64-v8a-release.apk=@/path/to/app-arm64-v8a-release.apk" \
  -F "app-armeabi-v7a-release.apk=@/path/to/app-armeabi-v7a-release.apk" \
  -F "app-x86_64-release.apk=@/path/to/app-x86_64-release.apk" \
  -F "app-universal-release.apk=@/path/to/app-universal-release.apk"
```

### Python
```python
import requests

token = "YOUR_FIREBASE_TOKEN"
version = "1.0.0"
release_notes = "Your release notes"

files = {
    'app-arm64-v8a-release.apk': open('/path/to/app-arm64-v8a-release.apk', 'rb'),
    'app-armeabi-v7a-release.apk': open('/path/to/app-armeabi-v7a-release.apk', 'rb'),
    'app-x86_64-release.apk': open('/path/to/app-x86_64-release.apk', 'rb'),
    'app-universal-release.apk': open('/path/to/app-universal-release.apk', 'rb'),
}

data = {
    'action': 'uploadApks',
    'version': version,
    'release_notes': release_notes
}

response = requests.post(
    'https://your-function-url',
    headers={'Authorization': f'Bearer {token}'},
    data=data,
    files=files
)

print(response.json())
```

### JavaScript
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
    headers: {
      'Authorization': `Bearer ${token}`
    },
    body: formData
  });

  return response.json();
}

// Usage:
const files = {
  arm64: document.getElementById('arm64Input').files[0],
  armv7: document.getElementById('armv7Input').files[0],
  x86_64: document.getElementById('x86_64Input').files[0],
  universal: document.getElementById('universalInput').files[0],
};

uploadAPKs(token, '1.0.0', 'Bug fixes and improvements', files)
  .then(result => console.log(result));
```

---

## Download APK by User Email (No Auth Needed)

### Get by Email Only (Latest Version, Auto-Detect Architecture)
```bash
curl -X POST https://your-function-url \
  -H "Content-Type: application/json" \
  -d '{
    "action": "getApkDownloadUrl",
    "email": "user@example.com"
  }'
```

### Get Specific Architecture for Latest Version
```bash
curl -X POST https://your-function-url \
  -H "Content-Type: application/json" \
  -d '{
    "action": "getApkDownloadUrl",
    "email": "user@example.com",
    "architecture": "arm64"
  }'
```

### Get Specific Version and Architecture
```bash
curl -X POST https://your-function-url \
  -H "Content-Type: application/json" \
  -d '{
    "action": "getApkDownloadUrl",
    "email": "user@example.com",
    "architecture": "armv7",
    "version": "1.0.0"
  }'
```

### Python
```python
import requests

def get_download_url(email, architecture='universal', version=None):
    payload = {
        'action': 'getApkDownloadUrl',
        'email': email
    }
    if architecture:
        payload['architecture'] = architecture
    if version:
        payload['version'] = version

    response = requests.post('https://your-function-url', json=payload)
    return response.json()

# Usage
result = get_download_url('user@example.com', 'arm64')
if result.get('success'):
    print(f"Download: {result['download_url']}")
```

### JavaScript
```javascript
async function getDownloadUrl(email, architecture = 'universal', version = null) {
  const payload = {
    action: 'getApkDownloadUrl',
    email: email,
    architecture: architecture
  };

  if (version) payload.version = version;

  const response = await fetch('https://your-function-url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  return response.json();
}

// Usage
getDownloadUrl('user@example.com', 'arm64')
  .then(data => {
    if (data.success) {
      window.location.href = data.download_url; // Download APK
    }
  });
```

---

## Admin Functions

### List All Versions (Admin Only)
```bash
curl -X POST https://your-function-url \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "getAllApkVersions"}'
```

### Delete a Version (Admin Only)
```bash
curl -X POST https://your-function-url \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "deleteApkVersion", "version": "1.0.0"}'
```

---

## Architecture Reference

| Architecture | File Name | Device Types |
|---|---|---|
| **arm64** | app-arm64-v8a-release.apk | Modern 64-bit phones |
| **armv7** | app-armeabi-v7a-release.apk | Older 32-bit phones |
| **x86_64** | app-x86_64-release.apk | Tablets & Emulators |
| **universal** | app-universal-release.apk | All devices (fallback) |

---

## Key Points

✅ **Upload**: Only admins can upload, all 4 files required
✅ **Download**: Email verification only, no auth token needed
✅ **Auto-Replace**: New version of same file replaces old one
✅ **Architecture Detection**: Defaults to universal if not available
✅ **Firestore Record**: Each upload creates a document with all URLs
✅ **Cloud Storage**: Files stored in `downloads/{version}/{filename}`
✅ **Automatic Notifications**: Sends Arabic messages to all Android users after upload
✅ **Notification Tracking**: Response includes how many notifications were sent

## Automatic Arabic Notifications

When you upload APKs:
1. System loops through all users
2. Checks if user has `"android"` in `platforms` array
3. Sends Arabic notification (if user has `fcmToken`):
   - **Title**: نسخة جديدة متاحة (New version available)
   - **Body**: يرجى تحديث التطبيق إلى النسخة X.X.X (Please update the app to version X.X.X)

Response includes notification stats:
```json
{
  "notifications": {
    "sent": 156,
    "errors": []
  }
}
```

---

## Implementation Summary

```
modules/apk_manager.py    ← All APK functions
app.py                     ← Routes added to app.py
APK_UPLOAD_GUIDE.md       ← Full documentation
APK_QUICK_REFERENCE.md    ← This file
```

See `APK_UPLOAD_GUIDE.md` for complete documentation with examples.
