"""Backup management module."""
from flask import jsonify
from google.cloud import storage  # type: ignore[attr-defined]
from googleapiclient import discovery
from google.auth import default
from datetime import datetime, timezone, timedelta
import traceback
from firebase_admin import firestore
from modules.config import BACKUP_BUCKET, COLLECTIONS_TO_BACKUP
import base64
import os
import re
import tempfile
import zipfile

# Iraq timezone (UTC+3)
IRAQ_TIMEZONE = timezone(timedelta(hours=3))

def get_iraq_time():
    """Get current time in Iraq timezone (UTC+3)"""
    return datetime.now(IRAQ_TIMEZONE)


def _safe_extract_zip(zip_file: zipfile.ZipFile, extract_dir: str):
    """Safely extract zip ensuring no path traversal."""
    base_path = os.path.abspath(extract_dir)
    for member in zip_file.namelist():
        member_path = os.path.abspath(os.path.join(base_path, member))
        if not member_path.startswith(base_path):
            raise ValueError(f"Illegal path detected in archive entry: {member}")
    zip_file.extractall(extract_dir)


def _find_export_root(extracted_dir: str):
    """Locate Firestore export root directory (contains overall_export_metadata)."""
    for root, _, files in os.walk(extracted_dir):
        for file_name in files:
            if file_name == "overall_export_metadata" or file_name.endswith(".overall_export_metadata"):
                return root
    return None


def _contains_metadata_file(entries):
    """Check if a list of file names contains a Firestore metadata file."""
    for entry in entries:
        if entry == "overall_export_metadata" or entry.endswith(".overall_export_metadata"):
            return True
    return False


def _validate_and_prepare_backup_structure(extracted_dir: str):
    """
    Validate and prepare backup structure for upload.
    Handles multiple archive formats:
    1. Files at root level (overall_export_metadata in extracted_dir)
    2. Single wrapper folder (backup_YYYYMMDD_HHMMSS/overall_export_metadata)
    3. Nested structure (any subdirectory containing overall_export_metadata)
    
    Returns the export root directory or None if invalid.
    """
    print(f"🔍 Analyzing extracted directory: {extracted_dir}")
    
    # List what's in the root
    try:
        root_contents = os.listdir(extracted_dir)
        print(f"📂 Root contains {len(root_contents)} items:")
        for item in root_contents[:10]:  # Show first 10 items
            item_path = os.path.join(extracted_dir, item)
            item_type = "dir" if os.path.isdir(item_path) else "file"
            print(f"  - {item} ({item_type})")
    except Exception as e:
        print(f"❌ Error listing directory: {e}")
        return None
    
    # Strategy 1: Check root level
    if _contains_metadata_file(root_contents):
        print(f"✓ Found Firestore export at root level: {extracted_dir}")
        return extracted_dir
    
    # Strategy 2: If root contains exactly one item and it's a directory, check inside it
    # This handles the common case of a single wrapper folder
    if len(root_contents) == 1:
        single_item = root_contents[0]
        single_item_path = os.path.join(extracted_dir, single_item)
        if os.path.isdir(single_item_path):
            print(f"  🔍 Root has single directory '{single_item}', checking inside...")
            try:
                inner_contents = os.listdir(single_item_path)
                if _contains_metadata_file(inner_contents):
                    print(f"✓ Found Firestore export in wrapper folder: {single_item_path}")
                    return single_item_path
            except Exception as e:
                print(f"❌ Error checking single directory: {e}")
    
    # Strategy 3: Search all subdirectories recursively
    print(f"⚠️  No export at root or in single wrapper, searching all subdirectories...")
    export_root = _find_export_root(extracted_dir)
    if export_root:
        print(f"✓ Found Firestore export via recursive search: {export_root}")
        return export_root
    
    print(f"❌ No valid Firestore export found in archive")
    return None


def _make_blob_public_temporarily(blob):
    """Make a blob publicly readable and return its public URL."""
    try:
        # Make the blob publicly readable
        blob.make_public()
        return blob.public_url
    except Exception as e:
        print(f"⚠️  Failed to make blob public: {str(e)}")
        raise


def handle_manual_backup(decoded_token):
    """Handle manual backup trigger"""
    try:
        credentials, project = default()
        if not project:
            return jsonify({
                "success": False,
                "error": "Unable to determine project ID",
                "timestamp": get_iraq_time().isoformat()
            }), 500
        
        firestore_service = discovery.build("firestore", "v1", credentials=credentials)
        
        # Create backup
        backup_result = create_firestore_backup_direct(firestore_service, project)
        
        return jsonify({
            "success": True,
            "message": "Manual backup completed successfully",
            "backup": backup_result,
            "timestamp": get_iraq_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Backup operation failed: {str(e)}",
            "timestamp": get_iraq_time().isoformat()
        }), 500


def create_firestore_backup_direct(firestore_service, project: str):
    """Create a Firestore backup with timestamp"""
    try:
        timestamp = get_iraq_time().strftime('%Y%m%d_%H%M%S')
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
            "timestamp": get_iraq_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to get backup status: {str(e)}",
            "timestamp": get_iraq_time().isoformat()
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
            "timestamp": get_iraq_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Failed to list backups: {str(e)}",
            "success": False,
            "timestamp": get_iraq_time().isoformat()
        }), 500


def handle_download_backup_archive(decoded_token, data):
    """Generate (or reuse) a zipped backup archive and return base64 content."""
    try:
        backup_timestamp = (
            data.get("backup_timestamp")
            or data.get("timestamp")
            or data.get("backupTimestamp")
        )
        force_rebuild = data.get("forceRebuild", False)
        
        if not backup_timestamp:
            return jsonify({
                "success": False,
                "error": "backup_timestamp is required"
            }), 400
        
        storage_client = storage.Client()
        bucket = storage_client.bucket(BACKUP_BUCKET)
        prefix = f"firestore-backups/{backup_timestamp}/"
        blobs = list(bucket.list_blobs(prefix=prefix))
        
        if not blobs:
            return jsonify({
                "success": False,
                "error": f"No backup found for timestamp {backup_timestamp}"
            }), 404
        
        archive_blob_name = f"firestore-backups-archives/{backup_timestamp}.zip"
        archive_blob = bucket.get_blob(archive_blob_name)
        
        # If archive exists and no rebuild requested, download it
        if archive_blob and not force_rebuild:
            archive_bytes = archive_blob.download_as_bytes()
            return jsonify({
                "success": True,
                "message": "Archive already exists. Returning cached archive.",
                "fileContent": base64.b64encode(archive_bytes).decode('utf-8'),
                "fileName": f"backup_{backup_timestamp}.zip",
                "sizeBytes": len(archive_bytes)
            })
        
        # Build new archive
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = os.path.join(tmp_dir, "export")
            os.makedirs(data_dir, exist_ok=True)
            
            print(f"📥 Downloading {len(blobs)} files from backup {backup_timestamp}")
            for blob in blobs:
                rel_path = blob.name[len(prefix):]
                if not rel_path:
                    continue
                local_path = os.path.join(data_dir, rel_path)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                blob.download_to_filename(local_path)
            
            archive_path = os.path.join(tmp_dir, f"{backup_timestamp}.zip")
            print(f"📦 Creating ZIP archive at root level (no wrapper folder)")
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive_file:
                for root, _, files in os.walk(data_dir):
                    for file_name in files:
                        file_path = os.path.join(root, file_name)
                        # Store files at root of ZIP (no wrapper folder)
                        arcname = os.path.relpath(file_path, data_dir)
                        archive_file.write(file_path, arcname)
                        print(f"  Added to ZIP: {arcname}")
            
            # Upload to cache for future requests
            archive_blob = bucket.blob(archive_blob_name)
            archive_blob.upload_from_filename(
                archive_path,
                content_type="application/zip"
            )
            
            # Read the archive file and return as base64
            with open(archive_path, "rb") as f:
                archive_bytes = f.read()
        
        return jsonify({
            "success": True,
            "message": "Backup archive generated successfully",
            "fileContent": base64.b64encode(archive_bytes).decode('utf-8'),
            "fileName": f"backup_{backup_timestamp}.zip",
            "sizeBytes": len(archive_bytes)
        })
    except Exception as e:
        error_msg = f"Failed to download backup archive: {str(e)}"
        print(error_msg)
        return jsonify({
            "success": False,
            "error": error_msg
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
                "timestamp": get_iraq_time().isoformat()
            }), 400
        
        credentials, project = default()
        if not project:
            return jsonify({
                "success": False,
                "error": "Unable to determine project ID",
                "timestamp": get_iraq_time().isoformat()
            }), 500
        
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
                    "timestamp": get_iraq_time().isoformat()
                })
            
            return jsonify({
                "success": True,
                "message": f"Restore operation started for backup: {backup_timestamp}",
                "restore_operation": restore_result,
                "timestamp": get_iraq_time().isoformat()
            })
            
        except ValueError as ve:
            print(f"❌ Backup validation error: {str(ve)}")
            return jsonify({
                "error": f"Backup validation failed: {str(ve)}",
                "success": False,
                "timestamp": get_iraq_time().isoformat()
            }), 404
            
        except Exception as restore_error:
            print(f"❌ Restore operation error: {str(restore_error)}")
            print(f"❌ Error type: {type(restore_error)}")
            return jsonify({
                "error": f"Failed to start restore: {str(restore_error)}",
                "success": False,
                "timestamp": get_iraq_time().isoformat()
            }), 500
        
    except Exception as e:
        print(f"❌ General error in handle_restore_backup: {str(e)}")
        return jsonify({
            "error": f"Failed to restore backup: {str(e)}",
            "success": False,
            "timestamp": get_iraq_time().isoformat()
        }), 500


def handle_upload_backup_archive(decoded_token, data):
    """Upload a zipped backup archive from client device and optionally trigger restore."""
    try:
        file_name = data.get("fileName")
        file_content = data.get("fileContent")
        backup_timestamp = (
            data.get("backup_timestamp")
            or data.get("timestamp")
            or data.get("backupTimestamp")
        )
        restore_after_upload = data.get("restoreAfterUpload", False)
        
        if not file_name or not file_content:
            return jsonify({
                "success": False,
                "error": "fileName and fileContent are required"
            }), 400
        
        try:
            archive_bytes = base64.b64decode(file_content)
        except Exception as decode_error:
            return jsonify({
                "success": False,
                "error": f"fileContent must be a valid base64-encoded string: {str(decode_error)}"
            }), 400
        
        storage_client = storage.Client()
        bucket = storage_client.bucket(BACKUP_BUCKET)
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = os.path.join(tmp_dir, file_name)
            with open(archive_path, "wb") as archive_file:
                archive_file.write(archive_bytes)
            
            extract_dir = os.path.join(tmp_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            
            print(f"📦 Extracting uploaded archive: {file_name}")
            print(f"   Archive size: {len(archive_bytes)} bytes")
            
            # First, list what's in the ZIP
            with zipfile.ZipFile(archive_path, "r") as zip_file:
                zip_contents = zip_file.namelist()
                print(f"   ZIP contains {len(zip_contents)} files")
                print(f"   First 10 files in ZIP:")
                for i, name in enumerate(zip_contents[:10]):
                    print(f"     {i+1}. {name}")
                
                # Extract
                _safe_extract_zip(zip_file, extract_dir)
            
            print(f"🔍 Validating backup structure...")
            export_root = _validate_and_prepare_backup_structure(extract_dir)
            if not export_root:
                # List what we found to help debug
                found_files = []
                for root, dirs, files in os.walk(extract_dir):
                    for f in files[:5]:  # Show first 5 files
                        rel_path = os.path.relpath(os.path.join(root, f), extract_dir)
                        found_files.append(rel_path)
                
                return jsonify({
                    "success": False,
                    "error": "Uploaded archive does not look like a Firestore export (missing overall_export_metadata)",
                    "debug": {
                        "extracted_files_sample": found_files,
                        "hint": "The ZIP should contain Firestore export files including 'overall_export_metadata' (or '<timestamp>.overall_export_metadata')"
                    }
                }), 400
            
            if not backup_timestamp:
                rel_path = os.path.relpath(export_root, extract_dir)
                match = re.search(r"\d{8}_\d{6}", rel_path.replace(os.sep, "/"))
                if match:
                    backup_timestamp = match.group(0)
            
            if not backup_timestamp:
                return jsonify({
                    "success": False,
                    "error": "Cannot determine backup timestamp. Provide backup_timestamp explicitly."
                }), 400
            
            upload_prefix = f"firestore-backups/{backup_timestamp}/"
            # Clean existing files for that timestamp
            existing_blobs = list(bucket.list_blobs(prefix=upload_prefix))
            for blob in existing_blobs:
                blob.delete()
            
            uploaded_files = 0
            total_bytes = 0
            for root, _, files in os.walk(export_root):
                for file_name_in_export in files:
                    file_path = os.path.join(root, file_name_in_export)
                    rel_path = os.path.relpath(file_path, export_root).replace("\\", "/")
                    blob_name = f"{upload_prefix}{rel_path}"
                    blob = bucket.blob(blob_name)
                    blob.upload_from_filename(file_path)
                    uploaded_files += 1
                    total_bytes += os.path.getsize(file_path)
        
        response = {
            "success": True,
            "message": "Backup uploaded successfully",
            "backupTimestamp": backup_timestamp,
            "uploadedFiles": uploaded_files,
            "totalBytes": total_bytes
        }
        
        if restore_after_upload:
            try:
                credentials, project = default()
                if not project:
                    response["restoreError"] = "Unable to determine project ID"
                    response["restoreOperation"] = None
                else:
                    firestore_service = discovery.build("firestore", "v1", credentials=credentials)
                    restore_result = restore_firestore_backup_direct(
                        firestore_service,
                        project,
                        backup_timestamp
                    )
                    response["restoreOperation"] = restore_result
            except Exception as restore_error:
                print(f"Restore error after upload: {str(restore_error)}")
                response["restoreError"] = str(restore_error)
                response["restoreOperation"] = None
        
        return jsonify(response)
        
    except Exception as e:
        error_msg = f"Failed to upload backup archive: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return jsonify({
            "success": False,
            "error": error_msg
        }), 500


def delete_all_collections_data(collections: list):
    """Delete all documents from the specified collections before restore"""
    try:
        db = firestore.client()
        deleted_counts = {}
        
        print(f"🗑️  Starting deletion of existing data from {len(collections)} collections...")
        
        for collection_name in collections:
            try:
                collection_ref = db.collection(collection_name)
                deleted_count = 0
                
                # Get all documents in batches
                while True:
                    docs = collection_ref.limit(500).stream()
                    batch = db.batch()
                    batch_count = 0
                    
                    for doc in docs:
                        batch.delete(doc.reference)
                        batch_count += 1
                    
                    if batch_count == 0:
                        break
                    
                    batch.commit()
                    deleted_count += batch_count
                    print(f"  ✓ Deleted {batch_count} documents from '{collection_name}' (total: {deleted_count})")
                
                deleted_counts[collection_name] = deleted_count
                print(f"✅ Completed deletion of '{collection_name}': {deleted_count} documents")
                
            except Exception as e:
                print(f"⚠️  Warning: Failed to delete collection '{collection_name}': {str(e)}")
                deleted_counts[collection_name] = 0
        
        total_deleted = sum(deleted_counts.values())
        print(f"🗑️  Total documents deleted: {total_deleted} across {len(collections)} collections")
        
        return deleted_counts
        
    except Exception as e:
        print(f"❌ Error deleting collections: {str(e)}")
        raise


def restore_firestore_backup_direct(firestore_service, project: str, backup_timestamp: str):
    """Restore a Firestore backup from the specified timestamp - replaces ALL existing data"""
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
        
        # Delete all existing data from collections before restoring
        print(f"🗑️  Deleting all existing data from collections to ensure complete replacement...")
        deleted_counts = {}
        try:
            deleted_counts = delete_all_collections_data(COLLECTIONS_TO_BACKUP)
            print(f"✅ Successfully deleted existing data. Deleted counts: {deleted_counts}")
        except Exception as delete_error:
            print(f"⚠️  Warning: Failed to delete existing data: {str(delete_error)}")
            print(f"⚠️  Proceeding with restore anyway - existing documents will be overwritten")
        
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
            fallback_name = f"restore_{get_iraq_time().isoformat()}"
            print(f"⚠️ Using fallback operation name: {fallback_name}")
            return {
                "operation_name": fallback_name,
                "backup_path": backup_path,
                "backup_timestamp": backup_timestamp,
                "collections": COLLECTIONS_TO_BACKUP,
                "status": "started_without_tracking",
                "files_count": len(backup_blobs),
                "deleted_documents": deleted_counts,
                "replacement_mode": "full_replacement",
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
            "files_count": len(backup_blobs),
            "deleted_documents": deleted_counts,
            "replacement_mode": "full_replacement"
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
                "timestamp": get_iraq_time().isoformat()
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
                "timestamp": get_iraq_time().isoformat()
            })
            
        except Exception as status_error:
            print(f"❌ Status check error: {str(status_error)}")
            return jsonify({
                "error": f"Failed to get restore status: {str(status_error)}",
                "success": False,
                "timestamp": get_iraq_time().isoformat()
            }), 500
        
    except Exception as e:
        print(f"❌ Handle restore status error: {str(e)}")
        return jsonify({
            "error": f"Failed to check restore status: {str(e)}",
            "success": False,
            "timestamp": get_iraq_time().isoformat()
        }), 500

