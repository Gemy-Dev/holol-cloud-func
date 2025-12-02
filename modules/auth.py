"""Authentication module for handling Firebase Auth token verification."""
import firebase_admin
from firebase_admin import auth
from flask import jsonify


def verify_token(request):
    """Verify Firebase Auth token from request header"""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return None, {"error": "Authorization header missing"}, 401
    
    try:
        token = auth_header.split('Bearer ')[1] if auth_header.startswith('Bearer ') else auth_header
        decoded_token = auth.verify_id_token(token)
        return decoded_token, None, None
    except Exception as e:
        return None, {"error": f"Invalid token: {str(e)}"}, 403


def reset_password(data, decoded_token):
    """Reset password for a user by generating a password reset link.
    
    Args:
        data: Request data containing 'uid' and 'email'
        decoded_token: Decoded Firebase Auth token (for authorization)
        
    Returns:
        JSON response with password reset link or error
    """
    uid = data.get("uid")
    email = data.get("email")
    
    if not uid and not email:
        return jsonify({"error": "Either uid or email is required"}), 400
    
    try:
        # Get user by UID or email
        if uid:
            user = auth.get_user(uid)
        else:
            user = auth.get_user_by_email(email)
        
        # Generate password reset link
        reset_link = auth.generate_password_reset_link(user.email)
        
        return jsonify({
            "success": True,
            "message": "Password reset link generated successfully",
            "resetLink": reset_link,
            "email": user.email,
            "uid": user.uid
        }), 200
        
    except auth.UserNotFoundError:
        return jsonify({"error": "User not found"}), 404
    except Exception as e:
        error_msg = f"Failed to reset password: {str(e)}"
        return jsonify({"error": error_msg}), 500

