# Flutter App Update: Add Restore Backup Functionality

## Context
I have a Flutter medical advisor app with Firebase backend that already has backup functionality. The Cloud Functions backend has been updated with new restore backup endpoints. I need to update the Flutter app to include restore functionality in the backup management UI.

## Current Backend API Endpoints Available

### Existing Endpoints:
- `manualBackup` - Create manual backup
- `backupStatus` - Check backup operation status  
- `listBackups` - Get list of available backups

### NEW Restore Endpoints:
- `restoreBackup` - Restore a selected backup
- `restoreStatus` - Check restore operation progress

## API Details

### Base URL:
```
https://us-central1-medical-advisor-bd734.cloudfunctions.net/app
```

### Authentication:
- Uses Firebase Auth ID tokens
- Include in Authorization header: `Bearer <firebase_id_token>`

### New Restore Backup Endpoint:
```dart
POST /app
Content-Type: application/json
Authorization: Bearer <firebase_id_token>

{
  "action": "restoreBackup",
  "source_uri": "gs://medical-advisor-bd734-backups/2024-09-22-15-30-00/2024-09-22-15-30-00.overall_export_metadata"
}

Response:
{
  "message": "Restore operation started successfully",
  "operation_name": "projects/medical-advisor-bd734/databases/(default)/operations/operation-id",
  "source_uri": "gs://...",
  "timestamp": "2025-09-22T16:30:00.000Z"
}
```

### New Restore Status Endpoint:
```dart
POST /app
Content-Type: application/json
Authorization: Bearer <firebase_id_token>

{
  "action": "restoreStatus",
  "operation_name": "projects/medical-advisor-bd734/databases/(default)/operations/operation-id"
}

Response:
{
  "operation_name": "projects/.../operations/operation-id",
  "done": true,
  "progress": "100%",
  "state": "SUCCESSFUL"
}
```

## Requirements

### 1. Update Existing Backup List UI
- Add "Restore" button/action to each backup item in the backup list
- Show backup creation date and metadata clearly
- Implement confirmation dialog before restore

### 2. Restore Process Implementation
- Call `restoreBackup` endpoint with selected backup's `source_uri`
- Show loading indicator during restore initiation
- Handle restore operation response

### 3. Restore Progress Monitoring
- Implement periodic polling of `restoreStatus` endpoint
- Show progress indicator with completion percentage
- Display current restore state (RUNNING, SUCCESSFUL, FAILED)

### 4. User Experience Enhancements
- **Confirmation Dialog**: "Are you sure you want to restore this backup? This will overwrite current data."
- **Progress Screen**: Show restore progress with cancel option
- **Success/Error Handling**: Clear success message or detailed error info
- **Disable Actions**: Prevent multiple concurrent restores

### 5. Error Handling
- Network connectivity issues
- Authentication failures
- Invalid backup selection
- Restore operation failures
- Handle partial restores

### 6. UI/UX Considerations
- Loading states for all async operations
- Clear visual feedback for restore status
- Breadcrumb or back navigation from restore screen
- Refresh backup list after successful restore
- Show timestamp of last successful restore

## Expected Flutter Implementation Structure

```dart
// Service layer
class BackupService {
  Future<RestoreResponse> restoreBackup(String sourceUri) async { }
  Future<RestoreStatusResponse> getRestoreStatus(String operationName) async { }
}

// UI Components needed
class BackupListScreen extends StatefulWidget { }
class RestoreConfirmationDialog extends StatelessWidget { }
class RestoreProgressScreen extends StatefulWidget { }

// Data models
class RestoreResponse { }
class RestoreStatusResponse { }
```

## Technical Notes
- Backend uses Firebase Auth for authentication
- All API calls require proper error handling
- Restore operations are long-running (use polling)
- CORS is configured to allow Flutter web app connectivity
- Backend handles Arabic language permissions and validation

## Deliverables Needed
1. Updated backup list screen with restore buttons
2. Restore confirmation dialog component
3. Restore progress tracking screen
4. Updated API service methods
5. Proper error handling and user feedback
6. Loading states and UI polish

Please implement the complete restore backup functionality following Flutter best practices with proper state management, error handling, and user experience considerations.