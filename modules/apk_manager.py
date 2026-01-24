"""APK Manager module for handling Android app uploads and downloads."""
import firebase_admin
from firebase_admin import storage, firestore, auth, messaging
from flask import jsonify, request
from datetime import datetime
import os


def _send_notifications_to_android_users(version, db):
    """
    Send Arabic notifications to all users with Android platform.

    Args:
        version: APK version that was uploaded
        db: Firestore database instance

    Returns:
        Tuple of (notification_count, errors)
    """
    notification_count = 0
    errors = []

    try:
        # Get all users
        users_query = db.collection("users").stream()

        for user_doc in users_query:
            try:
                user_data = user_doc.to_dict()

                # Check if user has Android platform
                platforms = user_data.get("platforms", [])
                if not isinstance(platforms, list):
                    continue

                # Check if "android" is in platforms list
                if "android" not in [p.lower() for p in platforms]:
                    continue

                # Get FCM token
                fcm_token = user_data.get("fcmToken")
                if not fcm_token:
                    continue

                # Prepare Arabic message
                message = messaging.Message(
                    token=fcm_token,
                    notification=messaging.Notification(
                        title="نسخة جديدة متاحة",  # "New version available"
                        body=f"يرجى تحديث التطبيق إلى النسخة {version}"  # "Please update the app to version X.X.X"
                    ),
                    data={
                        "version": version,
                        "action": "apk_update",
                        "type": "app_update"
                    }
                )

                # Send notification
                try:
                    response = messaging.send(message)
                    notification_count += 1
                    print(f"✅ APK update notification sent to {user_doc.id} (Android): {response}")
                except Exception as send_error:
                    error_msg = f"Error sending to {user_doc.id}: {str(send_error)}"
                    errors.append(error_msg)
                    print(f"❌ {error_msg}")

            except Exception as user_error:
                error_msg = f"Error processing user {user_doc.id}: {str(user_error)}"
                errors.append(error_msg)
                print(f"❌ {error_msg}")

    except Exception as query_error:
        error_msg = f"Error querying users: {str(query_error)}"
        errors.append(error_msg)
        print(f"❌ {error_msg}")

    return notification_count, errors


def upload_apks(data, decoded_token, db):
    """
    Upload multiple APK files to Cloud Storage and create Firestore records.
    Sends Arabic notifications to all Android users after successful upload.

    Args:
        data: Dict containing:
            - apk_files: List of file objects from request
            - version: Version string for the APKs (e.g., "1.0.0")
            - release_notes: Optional release notes
        decoded_token: Decoded Firebase Auth token (for authorization)
        db: Firestore database instance

    Returns:
        JSON response with upload status, download URLs, and notification stats
    """
    try:
        # Check if user is admin
        uid = decoded_token.get("uid") or decoded_token.get("user_id")
        if not uid:
            return jsonify({"error": "Invalid token"}), 403

        # Verify admin role
        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists:
            return jsonify({"error": "User not found"}), 404

        user_data = user_doc.to_dict()
        if user_data.get("role") != "admin":
            return jsonify({"error": "Only admins can upload APKs"}), 403

        # Get request files
        files = request.files
        version = data.get("version")
        release_notes = data.get("release_notes", "")

        if not version:
            return jsonify({"error": "Version is required"}), 400

        if not files:
            return jsonify({"error": "No files provided"}), 400

        # Expected APK files
        expected_apks = {
            "arm64": "app-arm64-v8a-release.apk",
            "armv7": "app-armeabi-v7a-release.apk",
            "x86_64": "app-x86_64-release.apk",
            "universal": "app-universal-release.apk"
        }

        bucket = storage.bucket()
        uploaded_files = {}
        download_urls = {}

        # Upload each APK file
        for arch_type, filename in expected_apks.items():
            if filename not in files:
                return jsonify({
                    "error": f"Missing file: {filename}"
                }), 400

            file = files[filename]

            # Create storage path: downloads/{version}/{filename}
            storage_path = f"downloads/{version}/{filename}"

            # Delete old file if it exists (replace logic)
            blob = bucket.blob(storage_path)
            try:
                blob.delete()
            except:
                pass  # File might not exist

            # Upload new file
            blob = bucket.blob(storage_path)
            blob.upload_from_string(
                file.read(),
                content_type="application/vnd.android.package-archive"
            )

            # Make file public and get URL
            blob.make_public()
            download_url = blob.public_url

            uploaded_files[arch_type] = {
                "filename": filename,
                "path": storage_path,
                "size": blob.size
            }
            download_urls[arch_type] = download_url

        # Create Firestore document
        timestamp = datetime.now()
        downloads_doc = {
            "version": version,
            "release_notes": release_notes,
            "uploaded_at": timestamp,
            "uploaded_by": uid,
            "apks": {
                "arm64": {
                    "name": "arm64-v8a",
                    "filename": expected_apks["arm64"],
                    "url": download_urls["arm64"],
                    "size": uploaded_files["arm64"]["size"]
                },
                "armv7": {
                    "name": "armeabi-v7a",
                    "filename": expected_apks["armv7"],
                    "url": download_urls["armv7"],
                    "size": uploaded_files["armv7"]["size"]
                },
                "x86_64": {
                    "name": "x86_64",
                    "filename": expected_apks["x86_64"],
                    "url": download_urls["x86_64"],
                    "size": uploaded_files["x86_64"]["size"]
                },
                "universal": {
                    "name": "universal",
                    "filename": expected_apks["universal"],
                    "url": download_urls["universal"],
                    "size": uploaded_files["universal"]["size"]
                }
            }
        }

        # Store in Firestore under downloads collection
        db.collection("downloads").document(version).set(downloads_doc)

        # Send Arabic notifications to Android users
        notification_count, notification_errors = _send_notifications_to_android_users(version, db)

        return jsonify({
            "success": True,
            "message": "APKs uploaded successfully",
            "version": version,
            "downloads": downloads_doc,
            "notifications": {
                "sent": notification_count,
                "errors": notification_errors if notification_errors else []
            }
        }), 200

    except Exception as e:
        return jsonify({
            "error": f"Upload failed: {str(e)}"
        }), 500


def get_apk_download_url(data, db):
    """
    Get APK download URL based on device architecture.
    Checks if user email exists before returning download URL.

    Args:
        data: Dict containing:
            - email: User email to verify
            - architecture: Optional device architecture (arm64, armv7, x86_64)
            - version: Optional specific version (defaults to latest)
        db: Firestore database instance

    Returns:
        JSON response with download URL or error
    """
    try:
        email = data.get("email")
        if not email:
            return jsonify({"error": "Email is required"}), 400

        # Verify user exists in Firestore
        users_query = db.collection("users").where("email", "==", email).limit(1).stream()
        user_exists = False
        for _ in users_query:
            user_exists = True
            break

        if not user_exists:
            return jsonify({"error": "User not found"}), 404

        architecture = data.get("architecture", "universal").lower()
        version = data.get("version")

        # Valid architectures
        valid_archs = ["arm64", "armv7", "x86_64", "universal"]
        if architecture not in valid_archs:
            architecture = "universal"  # Default to universal if invalid

        # Get version from Firestore
        if version:
            downloads_ref = db.collection("downloads").document(version)
        else:
            # Get latest version
            docs = list(db.collection("downloads").order_by("uploaded_at", direction=firestore.Query.DESCENDING).limit(1).stream())
            if not docs:
                return jsonify({"error": "No APK versions available"}), 404
            downloads_ref = docs[0].reference
            version = docs[0].id

        downloads_doc = downloads_ref.get()
        if not downloads_doc.exists:
            return jsonify({"error": "APK version not found"}), 404

        apks = downloads_doc.to_dict().get("apks", {})

        # Get requested architecture, fallback to universal if not available
        apk_info = apks.get(architecture)
        if not apk_info:
            apk_info = apks.get("universal")
            if not apk_info:
                return jsonify({"error": "APK not available"}), 404

        return jsonify({
            "success": True,
            "email": email,
            "version": version,
            "architecture": architecture,
            "download_url": apk_info.get("url"),
            "filename": apk_info.get("filename"),
            "size": apk_info.get("size")
        }), 200

    except Exception as e:
        return jsonify({
            "error": f"Failed to get download URL: {str(e)}"
        }), 500


def get_all_apk_versions(decoded_token, db):
    """
    Get all available APK versions (admin only).

    Args:
        decoded_token: Decoded Firebase Auth token
        db: Firestore database instance

    Returns:
        JSON response with all available versions
    """
    try:
        # Check admin role
        uid = decoded_token.get("uid") or decoded_token.get("user_id")
        if not uid:
            return jsonify({"error": "Invalid token"}), 403

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists:
            return jsonify({"error": "User not found"}), 404

        user_data = user_doc.to_dict()
        if user_data.get("role") != "admin":
            return jsonify({"error": "Only admins can view all versions"}), 403

        # Get all versions
        versions = []
        docs = db.collection("downloads").order_by("uploaded_at", direction=firestore.Query.DESCENDING).stream()

        for doc in docs:
            doc_data = doc.to_dict()
            versions.append({
                "version": doc.id,
                "uploaded_at": doc_data.get("uploaded_at"),
                "uploaded_by": doc_data.get("uploaded_by"),
                "release_notes": doc_data.get("release_notes"),
                "apks_count": len(doc_data.get("apks", {}))
            })

        return jsonify({
            "success": True,
            "versions": versions,
            "total": len(versions)
        }), 200

    except Exception as e:
        return jsonify({
            "error": f"Failed to get versions: {str(e)}"
        }), 500


def delete_apk_version(data, decoded_token, db):
    """
    Delete an APK version from storage and Firestore (admin only).

    Args:
        data: Dict containing:
            - version: Version to delete
        decoded_token: Decoded Firebase Auth token
        db: Firestore database instance

    Returns:
        JSON response with deletion status
    """
    try:
        # Check admin role
        uid = decoded_token.get("uid") or decoded_token.get("user_id")
        if not uid:
            return jsonify({"error": "Invalid token"}), 403

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists:
            return jsonify({"error": "User not found"}), 404

        user_data = user_doc.to_dict()
        if user_data.get("role") != "admin":
            return jsonify({"error": "Only admins can delete versions"}), 403

        version = data.get("version")
        if not version:
            return jsonify({"error": "Version is required"}), 400

        bucket = storage.bucket()

        # Delete all APK files for this version
        prefix = f"downloads/{version}/"
        blobs = bucket.list_blobs(prefix=prefix)

        for blob in blobs:
            blob.delete()

        # Delete Firestore document
        db.collection("downloads").document(version).delete()

        return jsonify({
            "success": True,
            "message": f"Version {version} deleted successfully"
        }), 200

    except Exception as e:
        return jsonify({
            "error": f"Failed to delete version: {str(e)}"
        }), 500
