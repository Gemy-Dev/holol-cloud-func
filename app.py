"""Main application file - refactored and cleaned."""
import functions_framework
from flask import request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
import traceback
from datetime import datetime
import os

# Import modules
from modules.auth import verify_token, reset_password
from modules.users import create_user, update_user, delete_user
from modules.products import get_products, get_plan_products, get_clients, delete_client_and_tasks
from modules.tasks import create_plan_tasks, create_tasks_for_new_client, create_tasks_from_product, get_task_stats, get_tasks_by_date_range
from modules.backups import (
    handle_manual_backup,
    handle_backup_status,
    handle_list_backups,
    handle_restore_backup,
    handle_restore_status,
    handle_download_backup_archive,
    handle_upload_backup_archive,
)
from modules.notifications import (
    handle_daily_notifications,
    handle_send_notification,
    handle_send_notification_to_all,
)
from modules.email import send_email

# Initialize Firebase Admin SDK (once)
cred = credentials.ApplicationDefault()
firebase_admin.initialize_app(cred)
db = firestore.client()


def get_cors_headers(request):
    """Get CORS headers based on request origin"""
    allowed_origins = os.getenv('ALLOWED_ORIGINS', '*').split(',')
    origin = request.headers.get('Origin', '')
    
    # Allow all origins for development, or specific origins for production
    if '*' in allowed_origins or origin in allowed_origins:
        cors_origin = origin if origin else '*'
    else:
        cors_origin = '*'  # Fallback to allow all for development
    
    return {
        'Access-Control-Allow-Origin': cors_origin,
        'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Requested-With, Accept, Origin',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
        'Access-Control-Max-Age': '3600',
        'Access-Control-Allow-Credentials': 'true'
    }


def handle_health_check():
    """Handle health check requests"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "Medical Advisor API"
    }), 200


def validate_action(action, data):
    """Validate action and required data"""
    if not action:
        return None, jsonify({"error": "Action is required"}), 400
    
    # Validate action parameter
    if not isinstance(action, str) or len(action) > 50:
        return None, jsonify({"error": "Invalid action parameter"}), 400
    
    return True, None, None


def route_request(action, data, request):
    """Route request to appropriate handler"""
    # Scheduled notifications (no auth needed)
    if action == "daily_notifications":
        return handle_daily_notifications(db)
    
    # API actions (auth required)
    decoded_token, error, status = verify_token(request)
    if error:
        return error, status
    
    if action == "getProducts":
        return get_products(decoded_token, db)
    
    elif action == "getPlanProducts":
        plan_id = data.get("planId")
        if not plan_id:
            return jsonify({"error": "planId is required"}), 400
        return get_plan_products(plan_id, db)
    
    elif action == "update":
        if "uid" not in data:
            return jsonify({"error": "uid is required for update"}), 400
        return update_user(data, decoded_token, db)
    
    elif action == "delete":
        if "uid" not in data:
            return jsonify({"error": "uid is required for delete"}), 400
        return delete_user(data, decoded_token, db)
    
    elif action == "create":
        if "email" not in data:
            return jsonify({"error": "email is required for create"}), 400
        return create_user(data, decoded_token, db)
    
    elif action == "getClients":
        return get_clients(decoded_token, db)
    
    elif action == "deleteClient":
        return delete_client_and_tasks(data, decoded_token, db)

    elif action == "createPlanTasks":
        return create_plan_tasks(data, db)
    
    elif action == "createTasksForNewClient":
        return create_tasks_for_new_client(data, db)
    
    elif action == "createTasksFromProduct":
        return create_tasks_from_product(data, db)
    
    elif action == "getStats":
        return get_task_stats(decoded_token, db)

    elif action == "getTasksByDateRange":
        return get_tasks_by_date_range(data, decoded_token, db)
    
    # Backup actions (admin only)
    elif action == "manualBackup":
        return handle_manual_backup(decoded_token)
    
    elif action == "backupStatus":
        return handle_backup_status(decoded_token)
    
    elif action == "listBackups":
        return handle_list_backups(decoded_token)
    
    elif action == "restoreBackup":
        return handle_restore_backup(decoded_token, data)
    
    elif action == "restoreStatus":
        return handle_restore_status(decoded_token, data)

    elif action == "downloadBackupArchive":
        return handle_download_backup_archive(decoded_token, data)
    
    elif action == "uploadBackupArchive":
        return handle_upload_backup_archive(decoded_token, data)
    
    elif action == "sendNotification":
        return handle_send_notification(decoded_token, data, db)
    
    elif action == "sendNotificationToAll":
        return handle_send_notification_to_all(decoded_token, data, db)
    
    elif action == "resetPassword":
        return reset_password(data, decoded_token)
    
    elif action == "sendEmail":
        title = data.get("title")
        body = data.get("body")
        
        if not title:
            return jsonify({"error": "title is required"}), 400
        
        if not body:
            return jsonify({"error": "body is required"}), 400
        
        return send_email(title, body, db)
    
    else:
        return jsonify({"error": "Invalid action"}), 400


@functions_framework.http
def app(request):
    """Main application handler"""
    headers = get_cors_headers(request)
    
    # Handle OPTIONS requests
    if request.method == 'OPTIONS':
        return ('', 204, headers)
    
    # Handle GET requests for health check
    if request.method == 'GET':
        path = request.path
        if path == '/health' or path == '/':
            body, status = handle_health_check()
            return (body, status, headers)
        else:
            return jsonify({"error": "Endpoint not found"}), 404, headers
    
    try:
        # Parse JSON for POST requests
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400, headers
        
        action = data.get("action")
        
        # Validate action
        valid, error_response, error_status = validate_action(action, data)
        if not valid:
            return (error_response, error_status, headers)
        
        # Route request
        response = route_request(action, data, request)
        
        # Handle response
        if isinstance(response, tuple):
            if len(response) == 2:
                body, status = response
                return (body, status, headers)
            else:
                return response + (headers,)
        else:
            return (response, 200, headers)
    
    except Exception as e:
        # Log full error internally but return sanitized message
        tb = traceback.format_exc()
        print(f"Application error: {str(e)}")
        print(f"Traceback: {tb}")
        
        # Return sanitized error message
        return jsonify({
            "error": "Internal server error occurred",
            "timestamp": datetime.now().isoformat()
        }), 500, headers

