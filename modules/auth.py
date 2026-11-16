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
        # The reason stays in the logs; the caller only learns it was refused.
        print(f"verify_token: rejected token: {e}")
        return None, {"error": "Invalid token"}, 403


RESET_EMAIL_SUBJECT = "إعادة تعيين كلمة المرور - هلول طبية"


def _reset_email_body(link):
    return (
        "تم طلب إعادة تعيين كلمة المرور لحسابك.\n\n"
        f"لتعيين كلمة مرور جديدة افتح الرابط التالي:\n{link}\n\n"
        "إذا لم تطلب ذلك يمكنك تجاهل هذه الرسالة."
    )


def reset_password(data, decoded_token):
    """Email a password reset link to the account's own address.

    The link is never returned in the response: whoever holds it can set the
    account's password, so it goes only to the inbox of the account it opens.
    The route is gated on `updateUser` (modules/authz.py).
    """
    from modules.email import send_text_email

    uid = data.get("uid")
    email = data.get("email")

    if not uid and not email:
        return jsonify({"error": "Either uid or email is required"}), 400

    try:
        user = auth.get_user(uid) if uid else auth.get_user_by_email(email)
    except auth.UserNotFoundError:
        return jsonify({"error": "User not found"}), 404
    except Exception as e:
        print(f"reset_password: lookup failed: {e}")
        return jsonify({"error": "Failed to reset password"}), 500

    if not user.email:
        return jsonify({"error": "User has no email address"}), 400

    try:
        reset_link = auth.generate_password_reset_link(user.email)
        result = send_text_email(
            [{"email": user.email, "name": user.display_name or ""}],
            RESET_EMAIL_SUBJECT,
            _reset_email_body(reset_link),
        )
    except Exception as e:
        print(f"reset_password: sending failed for {user.uid}: {e}")
        return jsonify({"error": "Failed to send the reset email"}), 502

    if not result.get("sent"):
        return jsonify({"error": "Failed to send the reset email"}), 502

    return jsonify({
        "success": True,
        "message": "Password reset link sent to the user's email",
        "email": user.email,
        "uid": user.uid,
    }), 200


def set_password(data, caller):
    """Set a user's password directly.

    Allowed for the account itself or a holder of `updateUser`; the route gate
    in modules/authz.py has already enforced that, and it is checked again here
    so the function is safe wherever it is called from.
    """
    uid = data.get("uid")
    password = data.get("password")

    if not uid or not password:
        return jsonify({"error": "Both 'uid' and 'password' are required"}), 400
    if not isinstance(password, str) or len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long"}), 400

    if caller.uid != uid and not caller.has("updateUser"):
        return jsonify({"error": "Not authorized to change this user's password"}), 403

    try:
        user = auth.update_user(uid, password=password)
        return jsonify({
            "success": True,
            "message": "Password updated successfully",
            "uid": user.uid,
            "email": user.email,
        }), 200
    except auth.UserNotFoundError:
        return jsonify({"error": "User not found"}), 404
    except Exception as e:
        print(f"set_password: failed for {uid}: {e}")
        return jsonify({"error": "Failed to set password"}), 500
