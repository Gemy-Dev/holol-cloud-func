"""Backup management module."""
from flask import jsonify
from google.cloud import storage
from googleapiclient import discovery
from google.auth import default
from datetime import datetime
import traceback
from modules.config import BACKUP_BUCKET, COLLECTIONS_TO_BACKUP


def handle_manual_backup(decoded_token):
    """Handle manual backup trigger"""
    try:
        credentials, project = default()
        firestore_service = discovery.build("firestore", "v1", credentials=credentials)
        
        # Create backup
        backup_result = create_firestore_backup_direct(firestore_service, project)
        
        return jsonify({
            "success": True,
            "message": "Manual backup completed successfully",
            "backup": backup_result,
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Backup operation failed: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }), 500


def create_firestore_backup_direct(firestore_service, project: str):
    """Create a Firestore backup with timestamp"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = f"gs://{BACKUP_BUCKET}/firestore-backups/{timestamp}"
        
        name = f"projects/{project}/databases/(default)"
        
        # Start the export
        request = firestore_service.projects().databases().exportDocuments(
            name=name,
            body={
                "outputUriPrefix": backup_path,
                "collectionIds": COLLECTIONS_TO_BACKUP
            }
        )
        response = request.execute()
        
        print(f"📦 Firestore export started: {response.get('name', 'Unknown')}")
        
        return {
            "operation_name": response.get('name'),
            "backup_path": backup_path,
            "timestamp": timestamp,
            "collections": COLLECTIONS_TO_BACKUP,
            "status": "started"
        }
        
    except Exception as e:
        print(f"❌ Failed to create Firestore backup: {str(e)}")
        raise


def handle_backup_status(decoded_token):
    """Handle backup status request"""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(BACKUP_BUCKET)
        prefix = "firestore-backups/"
        
        # Get all backup folders
        blobs = bucket.list_blobs(prefix=prefix)
        backup_folders = set()
        
        for blob in blobs:
            parts = blob.name[len(prefix):].split('/')
            if len(parts) > 0 and parts[0]:
                backup_folders.add(parts[0])
        
        # Sort folders by timestamp (newest first)
        sorted_folders = sorted(backup_folders, reverse=True)
        
        backups = []
        for folder in sorted_folders[:5]:  # Show last 5 backups
            folder_blobs = list(bucket.list_blobs(prefix=f"{prefix}{folder}/"))
            
            if folder_blobs:
                total_size = sum(blob.size for blob in folder_blobs if blob.size)
                backups.append({
                    "timestamp": folder,
                    "date": datetime.strptime(folder, '%Y%m%d_%H%M%S').isoformat(),
                    "file_count": len(folder_blobs),
                    "size_mb": round(total_size / (1024 * 1024), 2)
                })
        
        return jsonify({
            "success": True,
            "total_backups": len(sorted_folders),
            "retention_days": 30,
            "recent_backups": backups,
            "bucket": BACKUP_BUCKET,
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to get backup status: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }), 500


def handle_list_backups(decoded_token):
    """Handle list backups request"""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(BACKUP_BUCKET)
        prefix = "firestore-backups/"
        
        # Get all backup folders
        blobs = bucket.list_blobs(prefix=prefix)
        backup_data = {}
        
        for blob in blobs:
            path_parts = blob.name.split('/')
            if len(path_parts) >= 2:
                folder_name = path_parts[1]
                
                if folder_name not in backup_data:
                    backup_data[folder_name] = {
                        "files": [],
                        "total_size": 0,
                        "timestamp": folder_name
                    }
                
                backup_data[folder_name]["files"].append({
                    "name": blob.name,
                    "size": blob.size or 0,
                    "created": blob.time_created.isoformat() if blob.time_created else None
                })
                backup_data[folder_name]["total_size"] += blob.size or 0
        
        # Convert to list and sort by timestamp
        backups_list = []
        for timestamp, data in backup_data.items():
            try:
                backup_date = datetime.strptime(timestamp, '%Y%m%d_%H%M%S')
                backups_list.append({
                    "timestamp": timestamp,
                    "date": backup_date.isoformat(),
                    "file_count": len(data["files"]),
                    "total_size_mb": round(data["total_size"] / (1024 * 1024), 2),
                    "files": data["files"]
                })
            except ValueError:
                continue
        
        # Sort by date (newest first)
        backups_list.sort(key=lambda x: x["date"], reverse=True)
        
        return jsonify({
            "success": True,
            "total_backups": len(backups_list),
            "backups": backups_list,
            "bucket": BACKUP_BUCKET,
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Failed to list backups: {str(e)}",
            "success": False,
            "timestamp": datetime.now().isoformat()
        }), 500


def get_restore_status_direct(firestore_service, operation_name: str):
    """Get the status of a restore operation"""
    try:
        print(f"🔍 Debug: get_restore_status_direct called with:")
        print(f"  operation_name: {operation_name}")
        print(f"  firestore_service type: {type(firestore_service)}")
        
        full_operation_name = operation_name
        
        if not operation_name.startswith('projects/'):
            if len(operation_name) > 20 and operation_name.replace('_', '').replace('-', '').isalnum():
                credentials, project = default()
                full_operation_name = f"projects/{project}/databases/(default)/operations/{operation_name}"
            else:
                print(f"⚠️ Custom operation name detected: {operation_name}")
                return {
                    "operation_name": operation_name,
                    "done": False,
                    "metadata": {},
                    "status": "custom_operation",
                    "error": "Cannot check status for custom operation names"
                }
        
        print(f"🔍 Debug: Using full operation name: {full_operation_name}")
        
        request = firestore_service.projects().databases().operations().get(name=full_operation_name)
        response = request.execute()
        
        print(f"🔍 Debug: Firestore API response:")
        print(f"  response keys: {list(response.keys())}")
        print(f"  done: {response.get('done', False)}")
        
        operation_status = {
            "operation_name": operation_name,
            "done": response.get("done", False),
            "metadata": response.get("metadata", {}),
            "status": "completed" if response.get("done") else "in_progress"
        }
        
        if response.get("error"):
            operation_status["error"] = response.get("error")
            operation_status["status"] = "failed"
        
        print(f"🔍 Debug: Final operation status:")
        print(f"  {operation_status}")
        
        return operation_status
        
    except Exception as e:
        print(f"❌ Failed to get restore status: {str(e)}")
        return {
            "operation_name": operation_name,
            "done": False,
            "metadata": {},
            "status": "error",
            "error": f"Cannot check status: {str(e)}"
        }


def handle_restore_backup(decoded_token, data):
    """Handle restore backup request"""
    try:
        backup_timestamp = data.get("backup_timestamp") or data.get("timestamp") or data.get("backupTimestamp")
        
        print(f"🔍 Debug: handle_restore_backup called with:")
        print(f"  data keys: {list(data.keys())}")
        print(f"  backup_timestamp from data: {backup_timestamp}")
        
        if not backup_timestamp:
            return jsonify({
                "error": f"Backup timestamp is required. Received data: {data}",
                "success": False,
                "timestamp": datetime.now().isoformat()
            }), 400
        
        credentials, project = default()
        firestore_service = discovery.build("firestore", "v1", credentials=credentials)
        
        try:
            restore_result = restore_firestore_backup_direct(firestore_service, project, backup_timestamp)
            
            operation_name = restore_result.get("operation_name", "")
            if not operation_name or not operation_name.startswith("projects/"):
                print(f"⚠️ Warning: No valid operation name returned. Result: {restore_result}")
                return jsonify({
                    "success": True,
                    "message": f"Restore operation started for backup: {backup_timestamp}",
                    "restore_operation": restore_result,
                    "note": "Restore started but operation tracking may not be available",
                    "timestamp": datetime.now().isoformat()
                })
            
            return jsonify({
                "success": True,
                "message": f"Restore operation started for backup: {backup_timestamp}",
                "restore_operation": restore_result,
                "timestamp": datetime.now().isoformat()
            })
            
        except ValueError as ve:
            print(f"❌ Backup validation error: {str(ve)}")
            return jsonify({
                "error": f"Backup validation failed: {str(ve)}",
                "success": False,
                "timestamp": datetime.now().isoformat()
            }), 404
            
        except Exception as restore_error:
            print(f"❌ Restore operation error: {str(restore_error)}")
            print(f"❌ Error type: {type(restore_error)}")
            return jsonify({
                "error": f"Failed to start restore: {str(restore_error)}",
                "success": False,
                "timestamp": datetime.now().isoformat()
            }), 500
        
    except Exception as e:
        print(f"❌ General error in handle_restore_backup: {str(e)}")
        return jsonify({
            "error": f"Failed to restore backup: {str(e)}",
            "success": False,
            "timestamp": datetime.now().isoformat()
        }), 500


def restore_firestore_backup_direct(firestore_service, project: str, backup_timestamp: str):
    """Restore a Firestore backup from the specified timestamp"""
    try:
        backup_path = f"gs://{BACKUP_BUCKET}/firestore-backups/{backup_timestamp}"
        
        print(f"🔍 Debug: restore_firestore_backup_direct called with:")
        print(f"  project: {project}")
        print(f"  backup_timestamp: {backup_timestamp}")
        print(f"  backup_path: {backup_path}")
        
        # Verify backup exists
        storage_client = storage.Client()
        bucket = storage_client.bucket(BACKUP_BUCKET)
        backup_prefix = f"firestore-backups/{backup_timestamp}/"
        
        backup_blobs = list(bucket.list_blobs(prefix=backup_prefix))
        if not backup_blobs:
            print(f"❌ No backup files found at: {backup_prefix}")
            print(f"🔍 Checking for backups in bucket...")
            all_backup_folders = set()
            for blob in bucket.list_blobs(prefix="firestore-backups/"):
                path_parts = blob.name.split('/')
                if len(path_parts) >= 2 and path_parts[1]:
                    all_backup_folders.add(path_parts[1])
            
            print(f"🔍 Available backup folders: {list(all_backup_folders)}")
            raise ValueError(f"Backup not found: {backup_timestamp}. Available backups: {list(all_backup_folders)}")
        
        print(f"📥 Found backup with {len(backup_blobs)} files")
        
        name = f"projects/{project}/databases/(default)"
        
        print(f"🔍 Debug: Starting importDocuments with:")
        print(f"  name: {name}")
        print(f"  inputUriPrefix: {backup_path}")
        print(f"  collectionIds: {COLLECTIONS_TO_BACKUP}")
        
        request = firestore_service.projects().databases().importDocuments(
            name=name,
            body={
                "inputUriPrefix": backup_path,
                "collectionIds": COLLECTIONS_TO_BACKUP
            }
        )
        
        print(f"🔍 Debug: About to execute importDocuments request...")
        response = request.execute()
        print(f"🔍 Debug: importDocuments response received:")
        print(f"  response type: {type(response)}")
        print(f"  response: {response}")
        
        actual_operation_name = response.get('name', '')
        print(f"📥 Firestore restore started with operation: {actual_operation_name}")
        
        if not actual_operation_name:
            print(f"❌ Warning: No operation name in response!")
            fallback_name = f"restore_{datetime.now().isoformat()}"
            print(f"⚠️ Using fallback operation name: {fallback_name}")
            return {
                "operation_name": fallback_name,
                "backup_path": backup_path,
                "backup_timestamp": backup_timestamp,
                "collections": COLLECTIONS_TO_BACKUP,
                "status": "started_without_tracking",
                "files_count": len(backup_blobs),
                "warning": "Operation started but cannot be tracked"
            }
        
        operation_id = actual_operation_name
        if '/' in actual_operation_name:
            operation_id = actual_operation_name.split('/')[-1]
        
        print(f"📥 Operation ID: {operation_id}")
        
        return {
            "operation_name": actual_operation_name,
            "operation_id": operation_id,
            "backup_path": backup_path,
            "backup_timestamp": backup_timestamp,
            "collections": COLLECTIONS_TO_BACKUP,
            "status": "started",
            "files_count": len(backup_blobs)
        }
        
    except Exception as e:
        print(f"❌ Failed to restore Firestore backup: {str(e)}")
        print(f"❌ Exception type: {type(e)}")
        print(f"❌ Exception details: {e.args}")
        raise


def handle_restore_status(decoded_token, data):
    """Handle restore status request"""
    try:
        operation_name = data.get("operation_name") or data.get("operationName")
        
        print(f"🔍 Debug: handle_restore_status called with:")
        print(f"  data keys: {list(data.keys())}")
        print(f"  operation_name from data: {operation_name}")
        
        if not operation_name:
            return jsonify({
                "error": f"Operation name is required. Received data: {data}",
                "success": False,
                "timestamp": datetime.now().isoformat()
            }), 400
        
        credentials, project = default()
        firestore_service = discovery.build("firestore", "v1", credentials=credentials)
        
        print(f"🔍 Debug: Firestore service built successfully")
        print(f"  Service type: {type(firestore_service)}")
        
        try:
            status_result = get_restore_status_direct(firestore_service, operation_name)
            
            return jsonify({
                "success": True,
                "operation_status": status_result,
                "timestamp": datetime.now().isoformat()
            })
            
        except Exception as status_error:
            print(f"❌ Status check error: {str(status_error)}")
            return jsonify({
                "error": f"Failed to get restore status: {str(status_error)}",
                "success": False,
                "timestamp": datetime.now().isoformat()
            }), 500
        
    except Exception as e:
        print(f"❌ Handle restore status error: {str(e)}")
        return jsonify({
            "error": f"Failed to check restore status: {str(e)}",
            "success": False,
            "timestamp": datetime.now().isoformat()
        }), 500

