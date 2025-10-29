"""User management module for CRUD operations."""
from firebase_admin import auth
from firebase_admin.firestore import SERVER_TIMESTAMP
from flask import jsonify


def create_user(data, decoded_token, db):
    """Create a new user"""
    # Validate required fields
    email = data.get("email")
    password = data.get("password")
    
    if not email:
        return jsonify({"error": "Email is required"}), 400
    
    if not password:
        return jsonify({"error": "Password is required"}), 400
    
    # Password validation
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters long"}), 400

    try:
        user = auth.create_user(
            email=email,
            password=password,
            display_name=data.get("name"),
        )
        
        # Rest of your user creation logic...
        user_data = {
            "name": data.get("name"),
            "email": email,
            "role": data.get("role"),
            "phoneNumber": data.get("phoneNumber"),
            "department": data.get("department"),
            "permissions": data.get("permissions", []),
            "profileImageUrl": data.get("profileImageUrl"),
            "createdAt": SERVER_TIMESTAMP,
            "updatedAt": None,
            "isActive": data.get("isActive", True),
            "lastLogin": None,
            "plans": data.get("plans", []),
            "visits": data.get("visits", []),
            "customers": data.get("customers", []),
            "status": data.get("status"),
            "isInGeofence": data.get("isInGeofence", False),
            "createdBy": decoded_token["uid"]
        }

        db.collection("users").document(user.uid).set(user_data)
        return jsonify({"success": True, "uid": user.uid})
        
    except Exception as e:
        # Handle Firebase Auth errors
        error_message = str(e)
        if "WEAK_PASSWORD" in error_message:
            return jsonify({"error": "Password is too weak"}), 400
        elif "EMAIL_EXISTS" in error_message:
            return jsonify({"error": "Email already exists"}), 400
        elif "INVALID_EMAIL" in error_message:
            return jsonify({"error": "Invalid email format"}), 400
        else:
            return jsonify({"error": "Failed to create user"}), 500


def update_user(data, decoded_token, db):
    """Update an existing user"""
    uid = data.get("uid")
    if not uid:
        return jsonify({"error": "uid is required"}), 400

    if decoded_token["uid"] != uid:
        user_doc = db.collection("users").document(decoded_token["uid"]).get()
        if not user_doc.exists:
            return jsonify({"error": "Unauthorized"}), 403
        user_data_check = user_doc.to_dict()
        if user_data_check.get("role") != "admin":
            return jsonify({"error": "Unauthorized to update other users"}), 403

    user_data = {
        "name": data.get("name"),
        "email": data.get("email"),
        "role": data.get("role"),
        "phoneNumber": data.get("phoneNumber"),
        "permissions": data.get("permissions", []),
        "profileImageUrl": data.get("profileImageUrl"),
        "updatedAt": SERVER_TIMESTAMP,
        "isActive": data.get("isActive", True),
        "lastLogin": data.get("lastLogin"),
        "plans": data.get("plans", []),
        "visits": data.get("visits", []),
        "customers": data.get("customers", []),
        "status": data.get("status"),
        "updatedBy": decoded_token["uid"]
    }

    user_data = {k: v for k, v in user_data.items() if v is not None}
    db.collection("users").document(uid).update(user_data)
    return jsonify({"success": True})


def delete_user(data, decoded_token, db):
    """Delete a user"""
    uid = data.get("uid")
    if not uid:
        return jsonify({"error": "uid is required"}), 400

    user_doc = db.collection("users").document(decoded_token["uid"]).get()
    if not user_doc.exists:
        return jsonify({"error": "Unauthorized"}), 403
    user_data_check = user_doc.to_dict()
    if user_data_check.get("role") != "admin":
        return jsonify({"error": "Unauthorized to delete users"}), 403

    auth.delete_user(uid)
    db.collection("users").document(uid).delete()
    return jsonify({"success": True})

