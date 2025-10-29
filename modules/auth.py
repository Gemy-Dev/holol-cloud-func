"""Authentication module for handling Firebase Auth token verification."""
import firebase_admin
from firebase_admin import auth


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

