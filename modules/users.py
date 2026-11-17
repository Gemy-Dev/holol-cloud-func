"""User management module for CRUD operations."""
import re

from firebase_admin import auth, firestore
import firebase_admin
from flask import jsonify
from modules.dates import now_iso


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
            # An account created inactive must not be able to sign in either.
            disabled=data.get("isActive", True) is False,
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
            "createdAt": now_iso(),
            "updatedAt": None,
            "isActive": data.get("isActive", True) is not False,
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


# What an account may change on its own document through `update`. Everything
# else (role, permissions, activation, status) needs `updateUser`.
SELF_EDITABLE_FIELDS = ("name", "phoneNumber", "profileImageUrl")

# What a holder of `updateUser` may change through `update`. Email is absent on
# purpose: it lives in Firebase Auth too, and changing only the document leaves
# the sign-in address behind. Activation goes through `_apply_active` so the
# Auth account follows the flag.
MANAGED_FIELDS = SELF_EDITABLE_FIELDS + (
    "role",
    "permissions",
    "status",
    "plans",
    "visits",
    "customers",
    "receiveEmailNotifications",
)


def _apply_active(uid, is_active):
    """Enable or disable the Auth account so the flag is enforced at sign-in.

    Deactivating also revokes refresh tokens: a disabled account cannot mint a
    new ID token, and the one it holds expires within the hour.
    """
    auth.update_user(uid, disabled=not is_active)
    if not is_active:
        auth.revoke_refresh_tokens(uid)


def update_user(data, caller, db):
    """Update fields on a user document.

    Only the fields present in the request are written; a missing key is left
    as it is, never reset to an empty default. The caller's own account may
    change only SELF_EDITABLE_FIELDS unless they hold `updateUser`.
    """
    uid = data.get("uid") or data.get("id")
    if not uid:
        return jsonify({"error": "uid is required"}), 400

    if caller.has("updateUser"):
        allowed = MANAGED_FIELDS
    elif uid == caller.uid:
        allowed = SELF_EDITABLE_FIELDS
    else:
        return jsonify({"error": "Permission denied"}), 403

    user_ref = db.collection("users").document(uid)
    if not user_ref.get().exists:
        return jsonify({"error": "User not found"}), 404

    user_data = {k: data[k] for k in allowed if k in data}

    if "isActive" in data and caller.has("updateUser"):
        is_active = data["isActive"] is not False
        if uid == caller.uid and not is_active:
            return jsonify({"error": "You cannot deactivate your own account"}), 400
        try:
            _apply_active(uid, is_active)
        except auth.UserNotFoundError:
            pass
        user_data["isActive"] = is_active

    if not user_data:
        return jsonify({"error": "No permitted fields to update"}), 400

    user_data["updatedAt"] = now_iso()
    user_data["updatedBy"] = caller.uid
    user_ref.update(user_data)
    return jsonify({"success": True})


_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def set_user_email(data, caller, db):
    """Change an account's e-mail in Firebase Auth and on its document (WEB-15).

    The dashboard wrote the new address to the document only, so the account
    went on signing in — and receiving password-reset links — at the old one.
    Auth is changed first: if it refuses (the address is taken, or invalid),
    the document keeps the address the account really signs in with.
    """
    uid = data.get("uid") or data.get("id")
    email = (data.get("email") or "").strip().lower()
    if not uid:
        return jsonify({"error": "uid is required"}), 400
    if not _EMAIL_PATTERN.match(email):
        return jsonify({"error": "A valid email is required"}), 400

    user_ref = db.collection("users").document(uid)
    if not user_ref.get().exists:
        return jsonify({"error": "User not found"}), 404

    try:
        auth.update_user(uid, email=email)
    except auth.EmailAlreadyExistsError:
        return jsonify({"error": "This email is already used by another account"}), 409
    except auth.UserNotFoundError:
        # A document without a sign-in account: nothing signs in with it.
        pass
    except ValueError:
        return jsonify({"error": "A valid email is required"}), 400
    except Exception as error:
        print(f"set_user_email: Auth update failed for {uid}: {error}")
        return jsonify({"error": "Failed to update the sign-in account"}), 500

    user_ref.update({
        "email": email,
        "updatedAt": now_iso(),
        "updatedBy": caller.uid,
    })
    return jsonify({"success": True, "email": email})


def set_user_active(data, caller, db):
    """Activate or deactivate an account in both Firebase Auth and Firestore."""
    uid = data.get("uid") or data.get("id")
    if not uid:
        return jsonify({"error": "uid is required"}), 400
    if not isinstance(data.get("isActive"), bool):
        return jsonify({"error": "isActive must be true or false"}), 400
    is_active = data["isActive"]
    if uid == caller.uid and not is_active:
        return jsonify({"error": "You cannot deactivate your own account"}), 400

    user_ref = db.collection("users").document(uid)
    if not user_ref.get().exists:
        return jsonify({"error": "User not found"}), 404

    try:
        _apply_active(uid, is_active)
    except auth.UserNotFoundError:
        # A document without a sign-in account: the flag still stops the
        # server and the rules from acting for it.
        pass
    except Exception as error:
        print(f"set_user_active: Auth update failed for {uid}: {error}")
        return jsonify({"error": "Failed to update the sign-in account"}), 500

    user_ref.update({
        "isActive": is_active,
        "updatedAt": now_iso(),
        "updatedBy": caller.uid,
    })
    return jsonify({"success": True, "uid": uid, "isActive": is_active})


def delete_user(data, caller, db):
    """Delete a user from Firebase Auth, then from Firestore.

    The document is removed only once the sign-in account is gone. Deleting the
    document alone used to leave an account that could still sign in.
    """
    uid = data.get("uid") or data.get("id")
    if not uid:
        return jsonify({"error": "uid is required"}), 400
    if uid == caller.uid:
        return jsonify({"error": "You cannot delete your own account"}), 400

    try:
        auth.delete_user(uid)
    except auth.UserNotFoundError:
        pass
    except Exception as auth_error:
        print(f"delete_user: Auth deletion failed for {uid}: {auth_error}")
        return jsonify({"error": "Failed to delete the sign-in account", "uid": uid}), 500

    try:
        db.collection("users").document(uid).delete()
    except Exception as db_error:
        print(f"delete_user: Firestore deletion failed for {uid}: {db_error}")
        return jsonify({"error": "Sign-in account deleted, but the user record was not", "uid": uid}), 500

    return jsonify({
        "success": True,
        "message": "User deleted from both Firebase Auth and Firestore",
        "uid": uid
    })



# The notification preference exists under two spellings in production. The
# dashboard writes the correctly spelled key; the field app has always read the
# misspelled one. Renaming the *stored* key is deliberately not part of this
# feature — separating the backfill from a rename means a failure here is
# attributable to the data rather than to the rename.
CANONICAL_PREFERENCE_KEY = "receiveEmailNotifications"
LEGACY_PREFERENCE_KEY = "reciveEmailNotifications"

# Firestore caps a batch at 500 writes.
_MIGRATION_BATCH_SIZE = 400


def migrate_notification_preference(data, decoded_token, db):
    """Backfill the notification preference onto one canonical key.

    Ordered so that no configured value is lost (data-model.md §1):

    1. canonical present -> keep it; the dashboard is where it is administered
    2. else legacy present -> copy it across
    3. else -> write False; a missing preference is not consent
    4. ``deleteLegacyKey`` -> drop the misspelled key. Only safe once the app
       release that stops reading it has fully rolled out.

    Steps 1-3 are additive, so the run is reversible until step 4.

    ``valueChanges`` reports any user whose *effective* preference differs
    before and after. It must come back empty (SC-013); a non-empty list means
    the precedence logic is wrong, not that a few users need review.
    """
    try:
        requester_uid = decoded_token.get("uid") or decoded_token.get("user_id")
        if not requester_uid:
            return jsonify({
                "success": False,
                "error": "Unauthorized - no requesting user"
            }), 403

        requester = db.collection("users").document(requester_uid).get()
        if not requester.exists:
            return jsonify({
                "success": False,
                "error": "Unauthorized - requesting user not found"
            }), 403

        # Both flags fail safe. A JSON string such as "false" is truthy in
        # Python, so a malformed call must land on "do nothing", never on
        # "rewrite every user document" or "delete the rollback data".
        dry_run = data.get("dryRun", True) is not False
        delete_legacy_key = data.get("deleteLegacyKey", False) is True

        scanned = 0
        canonical_kept = 0
        backfilled = 0
        defaulted_false = 0
        legacy_deleted = 0
        value_changes = []

        batch = None if dry_run else db.batch()
        pending = 0

        for user_doc in db.collection("users").stream():
            scanned += 1
            doc = user_doc.to_dict() or {}
            has_canonical = CANONICAL_PREFERENCE_KEY in doc
            has_legacy = LEGACY_PREFERENCE_KEY in doc

            if has_canonical:
                resolved = bool(doc[CANONICAL_PREFERENCE_KEY])
                canonical_kept += 1
            elif has_legacy:
                resolved = bool(doc[LEGACY_PREFERENCE_KEY])
                backfilled += 1
            else:
                resolved = False
                defaulted_false += 1

            # What the user effectively had before this run, under the same
            # precedence the readers use. Computed independently of `resolved`
            # so the comparison below is a real check and not a tautology.
            if has_canonical:
                effective_before = bool(doc[CANONICAL_PREFERENCE_KEY])
            elif has_legacy:
                effective_before = bool(doc[LEGACY_PREFERENCE_KEY])
            else:
                effective_before = False

            if effective_before != resolved:
                value_changes.append({
                    "userId": user_doc.id,
                    "before": effective_before,
                    "after": resolved,
                })

            update = {}
            if not has_canonical or doc[CANONICAL_PREFERENCE_KEY] is not resolved:
                update[CANONICAL_PREFERENCE_KEY] = resolved
            if delete_legacy_key and has_legacy:
                update[LEGACY_PREFERENCE_KEY] = firestore.DELETE_FIELD
                legacy_deleted += 1

            if not update:
                continue

            if dry_run:
                continue

            batch.update(db.collection("users").document(user_doc.id), update)
            pending += 1
            if pending >= _MIGRATION_BATCH_SIZE:
                batch.commit()
                batch = db.batch()
                pending = 0

        if not dry_run and pending:
            batch.commit()

        print(
            f"🔁 Preference migration ({'dry run' if dry_run else 'applied'}): "
            f"scanned={scanned} kept={canonical_kept} backfilled={backfilled} "
            f"defaulted={defaulted_false} legacyDeleted={legacy_deleted}"
        )
        if value_changes:
            print(f"❌ {len(value_changes)} preference value(s) would change — "
                  f"this is a failed migration, not a warning")

        return jsonify({
            "success": True,
            "dryRun": dry_run,
            "scanned": scanned,
            "canonicalKept": canonical_kept,
            "backfilledFromLegacy": backfilled,
            "defaultedFalse": defaulted_false,
            "legacyKeysDeleted": legacy_deleted,
            "valueChanges": value_changes,
        }), 200

    except Exception as e:
        error_msg = f"Error migrating notification preference: {str(e)}"
        print(f"❌ {error_msg}")
        return jsonify({"success": False, "error": error_msg}), 500
