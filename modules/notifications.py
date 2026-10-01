"""Notification module for handling push notifications."""
from firebase_admin import messaging
from flask import jsonify
import traceback
from datetime import datetime, date, timezone, timedelta
from email.utils import parsedate_to_datetime
from modules.config import IRAQ_TIMEZONE
from modules import notification_log
import random


def _normalize_target_date(value):
    """Normalize various targetDate representations to an ISO date string (YYYY-MM-DD).

    Supports:
    - datetime.date / datetime.datetime
    - dict with 'seconds' and 'nanoseconds' (Firestore REST style)
    - objects with 'seconds' and 'nanos' attributes (protobuf Timestamp)
    - int/float UNIX timestamps (seconds or milliseconds)
    - common string formats (ISO, RFC-2822, 'YYYY-MM-DD HH:MM:SS', 'dd/mm/yyyy', 'mm/dd/yyyy', 'Jan 1, 2026', ...)
    Returns ISO date string or None if unable to parse.
    """
    if value is None:
        return None

    try:
        # datetime / date
        if isinstance(value, datetime):
            dt = value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.date().isoformat()

        if isinstance(value, date):
            return value.isoformat()

        # dict-like from REST: {'seconds': ..., 'nanoseconds': ...}
        if isinstance(value, dict):
            secs = value.get('seconds') or value.get('sec') or value.get('s')
            nanos = value.get('nanoseconds') or value.get('nanos') or value.get('ns') or 0
            if secs is not None:
                ts = float(secs) + float(nanos) / 1e9
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                return dt.date().isoformat()

        # protobuf-like Timestamp object
        if hasattr(value, 'seconds') and hasattr(value, 'nanos'):
            try:
                secs = float(getattr(value, 'seconds'))
                nanos = float(getattr(value, 'nanos'))
                ts = secs + nanos / 1e9
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                return dt.date().isoformat()
            except Exception:
                pass

        # numeric timestamp (seconds or milliseconds)
        if isinstance(value, (int, float)):
            v = float(value)
            if v > 1e12:  # milliseconds
                v = v / 1000.0
            dt = datetime.fromtimestamp(v, tz=timezone.utc)
            return dt.date().isoformat()

        # string parsing
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None

            # ISO-like with trailing Z -> fromisoformat requires replacing Z
            try:
                iso = s.replace('Z', '+00:00') if s.endswith('Z') else s
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return dt.date().isoformat()
            except Exception:
                pass

            # RFC-2822 / HTTP-date
            try:
                dt = parsedate_to_datetime(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return dt.date().isoformat()
            except Exception:
                pass

            # numeric string timestamp
            if s.isdigit():
                try:
                    v = float(s)
                    if v > 1e12:
                        v = v / 1000.0
                    dt = datetime.fromtimestamp(v, tz=timezone.utc)
                    return dt.date().isoformat()
                except Exception:
                    pass

            # try common human formats
            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%d/%m/%Y",
                "%m/%d/%Y",
                "%b %d, %Y",
                "%B %d, %Y",
            ):
                try:
                    dt = datetime.strptime(s, fmt)
                    dt = dt.replace(tzinfo=timezone.utc)
                    return dt.date().isoformat()
                except Exception:
                    continue

            # last resort: try fromisoformat again
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return dt.date().isoformat()
            except Exception:
                return None

        return None
    except Exception:
        return None


def handle_daily_notifications(db, days_offset=0):
    """Remind each representative of their own tasks for a specific date.
    
    Args:
        db: Firestore database instance
        days_offset: Days from today (0=today, 1=tomorrow, etc.)
    
    Used by Cloud Scheduler:
    - 8 AM Iraq time (UTC+3): days_offset=0 (today's tasks)
    - 8 PM Iraq time (UTC+3): days_offset=1 (tomorrow's tasks)
    """
    try:
        # Calculate target date in Iraq time (UTC+3)
        iraq_now = datetime.now(IRAQ_TIMEZONE)
        target_date = (iraq_now.date() + timedelta(days=days_offset)).isoformat()
        
        print(f"🔔 Running task notifications for date: {target_date} (offset: {days_offset})")

        # Read the day's tasks once, grouped by assignee. This used to re-read
        # every task for every user and tell each of them about every task due
        # that day, whoever it belonged to — which the stored notification
        # list would now have shown to everyone.
        tasks_by_user = {}
        for task_doc in db.collection("tasks").stream():
            task = task_doc.to_dict() or {}
            if task.get("reviewState") == "deleted":
                continue
            assignee = task.get("assignedToId")
            if not assignee:
                continue
            if _normalize_target_date(task.get("targetDate")) != target_date:
                continue
            tasks_by_user.setdefault(assignee, []).append({
                "id": task_doc.id,
                "title": task.get("title", "بدون عنوان")
            })

        # Record every reminder before sending any, so the badge counts below
        # include the reminder each device is about to receive.
        reminders = []
        for user_id, user_tasks in tasks_by_user.items():
            user_snapshot = db.collection("users").document(user_id).get()
            if not user_snapshot.exists:
                continue

            # A fixed id per user, date and run: the scheduler has held two
            # jobs for the same 05:00 run, and a second run must not remind
            # anyone twice.
            reminder_id = f"daily_tasks-{target_date}-{days_offset}-{user_id}"
            if db.collection(notification_log.COLLECTION).document(reminder_id).get().exists:
                continue

            task_count = len(user_tasks)
            task_ids = [task["id"] for task in user_tasks]
            
            # Create notification body based on offset
            if days_offset == 0:
                # Today's tasks
                if task_count == 1:
                    body = f"عندك اليوم مهمة: {user_tasks[0]['title']}"
                else:
                    body = f"عندك اليوم {task_count} مهام"
            else:
                # Tomorrow's tasks
                if task_count == 1:
                    body = f"عندك غدا مهمة: {user_tasks[0]['title']}"
                else:
                    body = f"عندك غدا {task_count} مهام"

            message_data = {
                "taskCount": str(task_count),
                "taskIds": ",".join(task_ids),
                "date": target_date,
                "action": "daily_tasks"
            }
            notification_id = notification_log.record(
                db,
                title="تذكير بالمهام",
                body=body,
                data=message_data,
                kind="daily_tasks",
                source="system",
                sender_id=None,
                recipient_ids=[user_id],
                notification_id=reminder_id,
            )
            reminders.append((user_id, (user_snapshot.to_dict() or {}).get("fcmToken"),
                              body, message_data, notification_id))

        badges = notification_log.todays_counts(db, [reminder[0] for reminder in reminders])
        notification_count = 0

        for user_id, fcm_token, body, message_data, notification_id in reminders:
            if not fcm_token:
                notification_log.record_delivery(
                    db, notification_id, success_count=0, failure_count=0,
                    reason="no_valid_registration"
                )
                continue

            if notification_id:
                message_data = {**message_data, "notificationId": notification_id}
            message = messaging.Message(
                token=fcm_token,
                notification=messaging.Notification(
                    title="تذكير بالمهام",
                    body=body
                ),
                data=message_data,
                **notification_log.badge_config(badges.get(user_id))
            )
            try:
                messaging.send(message)
                notification_count += 1
                notification_log.record_delivery(db, notification_id, success_count=1, failure_count=0)
                print(f"✅ Sent to {user_id}: {message_data['taskCount']} tasks")
            except Exception as e:
                notification_log.record_delivery(db, notification_id, success_count=0, failure_count=1)
                print(f"❌ Error sending to {user_id}: {str(e)}")
        
        return jsonify({
            "success": True, 
            "message": f"Task notifications completed. Sent {notification_count} notifications.",
            "date": target_date,
            "offset": days_offset,
            "count": notification_count
        })
        
    except Exception as e:
        error_msg = f"Error in task notifications: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return jsonify({"error": error_msg        }), 500


def handle_send_notification(decoded_token, data, db):
    """Send a notification to one user, addressed by id or by raw token.

    ``targetUserId`` is the path callers should use: the field app has no read
    access to another user's document, so requiring an ``fcmToken`` up front
    meant it could never address anyone but itself and fell back to
    broadcasting instead. ``fcmToken`` stays accepted for existing callers,
    and its owner is looked up so the stored record still names a recipient.
    """
    target_user_id = None
    fcm_token = None
    notification_id = None
    try:
        target_user_id = data.get("targetUserId")
        fcm_token = data.get("fcmToken")
        title = data.get("title")
        body = data.get("body")
        notification_action = data.get("notificationAction")

        if not target_user_id and not fcm_token:
            return jsonify({
                "success": False,
                "error": "targetUserId or fcmToken is required"
            }), 400

        if not title or not body:
            return jsonify({"success": False, "error": "title and body are required"}), 400

        # Resolve server-side when addressed by id. A token read now also beats
        # one the caller cached earlier, so the id wins if both are supplied.
        if target_user_id:
            snapshot = db.collection("users").document(target_user_id).get()
            fcm_token = (snapshot.to_dict() or {}).get("fcmToken") if snapshot.exists else None
        else:
            target_user_id = _token_owner(db, fcm_token)

        message_data = _message_data(notification_action)
        notification_id = notification_log.record(
            db,
            title=title,
            body=body,
            data=message_data,
            kind="direct",
            source=notification_log.request_source(data),
            sender_id=decoded_token.get("uid"),
            recipient_ids=[target_user_id] if target_user_id else [],
        )

        if not fcm_token:
            # Not an error: the user simply has no device registered. Reporting
            # this as a failure would make an ordinary state look like an
            # outage every time someone had not installed the app yet. The
            # record above still reaches their notification list.
            print(f"📭 No valid registration for user {target_user_id}")
            notification_log.record_delivery(
                db, notification_id, success_count=0, failure_count=0,
                reason="no_valid_registration"
            )
            return jsonify({
                "success": False,
                "reason": "no_valid_registration"
            }), 200

        if notification_id:
            message_data["notificationId"] = notification_id
        badge = notification_log.todays_counts(db, [target_user_id]).get(target_user_id)

        # Build FCM message
        message = messaging.Message(
            token=fcm_token,
            notification=messaging.Notification(title=title, body=body),
            data=message_data,  # always a dict
            **notification_log.badge_config(badge)
        )

        # Send notification
        response = messaging.send(message)
        print(f"✅ Notification sent successfully: {response}")
        notification_log.record_delivery(db, notification_id, success_count=1, failure_count=0)

        return jsonify({
            "success": True,
            "message": "Notification sent successfully",
            "messageId": response
        }), 200

    except messaging.UnregisteredError:
        # 200, not 400: the request was well-formed and the outcome is known
        # and final. Clear the registration so it is not retried forever —
        # possible whenever the token's owner is known, by id or by lookup.
        notification_log.record_delivery(
            db, notification_id, success_count=0, failure_count=1, reason="token_pruned"
        )
        if target_user_id:
            _prune_token(db, target_user_id, fcm_token)
        else:
            print("⚠️ Unregistered token has no owner to clear")

        return jsonify({
            "success": False,
            "reason": "token_pruned"
        }), 200

    except Exception as e:
        error_msg = f"Error sending notification: {str(e)}"
        print(f"❌ {error_msg}")
        print(traceback.format_exc())
        notification_log.record_delivery(
            db, notification_id, success_count=0, failure_count=1, reason="error"
        )

        return jsonify({
            "success": False,
            "error": error_msg
        }), 500


def _token_owner(db, fcm_token):
    """The id of the user holding ``fcm_token``, or None. Never raises.

    A single-field equality filter, served by Firestore's automatic index.
    """
    try:
        for user_doc in db.collection("users").where("fcmToken", "==", fcm_token).limit(1).stream():
            return user_doc.id
    except Exception as error:
        print(f"⚠️ Could not resolve the owner of a token: {error}")
    return None


def _is_permanently_invalid_token(exception):
    """Whether ``exception`` means this registration will never work again.

    Only permanent rejections qualify. A transient send failure is not evidence
    that the device is gone, and clearing a live token would end delivery for a
    user who was reachable all along.
    """
    if exception is None:
        return False

    permanent = []
    for name in ("UnregisteredError", "SenderIdMismatchError"):
        candidate = getattr(messaging, name, None)
        # Guarded because a stubbed messaging module hands back non-classes,
        # and isinstance() against one of those raises.
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            permanent.append(candidate)

    return bool(permanent) and isinstance(exception, tuple(permanent))


def _prune_token(db, user_id, token):
    """Clear ``token`` from ``user_id``. Returns 1 if it was cleared, else 0.

    Re-reads the document first so a token the user has already replaced — a
    rotation that landed while this send was in flight — is left alone.
    """
    try:
        doc_ref = db.collection("users").document(user_id)
        snapshot = doc_ref.get()
        if not snapshot.exists:
            return 0
        if (snapshot.to_dict() or {}).get("fcmToken") != token:
            return 0
        doc_ref.update({"fcmToken": None})
        print(f"🧹 Cleared dead registration for user {user_id}")
        return 1
    except Exception as prune_error:
        # Pruning is housekeeping — never fail a delivered send over it.
        print(f"⚠️ Could not clear registration for user {user_id}: {prune_error}")
        return 0


def _message_data(notification_action):
    """The FCM ``data`` payload for a caller's ``notificationAction``.

    A map is spread into string entries (nested values JSON-encoded, since FCM
    data values must be strings); anything else becomes the ``action`` entry.
    """
    message_data = {}
    if not notification_action:
        return message_data
    if isinstance(notification_action, dict):
        import json
        for k, v in notification_action.items():
            if isinstance(v, (dict, list)):
                message_data[str(k)] = json.dumps(v)
            else:
                message_data[str(k)] = str(v)
    else:
        message_data["action"] = str(notification_action)
    return message_data


def _prune_failed_sends(db, response, tokens, token_owners):
    """Clear every registration a multicast send reports as gone for good.

    Left in place they are retried on every send forever, dragging the delivery
    rate down and hiding the fact that a real user has stopped receiving.
    ``token_owners[i]`` is the user holding ``tokens[i]``. Returns the count.
    """
    pruned = 0
    if response.failure_count > 0:
        for idx, resp in enumerate(response.responses):
            if resp.success:
                continue
            print(f"❌ Failed to send to token {idx}: {resp.exception}")
            if _is_permanently_invalid_token(resp.exception):
                pruned += _prune_token(db, token_owners[idx], tokens[idx])

    if pruned:
        print(f"🧹 Cleared {pruned} dead device registration(s)")
    return pruned


def _send_multicast(db, tokens, token_owners, title, body, message_data, badges):
    """Send one notification to ``tokens``, each device showing its owner's badge.

    Devices are grouped by badge value, so a broadcast is still a handful of
    multicast calls rather than one call per device. ``badges`` maps a user id
    to today's count; an owner missing from it gets no badge.

    Returns ``(success_count, failure_count, pruned_count)``.
    """
    groups = {}
    for token, owner in zip(tokens, token_owners):
        group_tokens, group_owners = groups.setdefault(badges.get(owner), ([], []))
        group_tokens.append(token)
        group_owners.append(owner)

    success_count = failure_count = pruned = 0
    for badge, (group_tokens, group_owners) in groups.items():
        message = messaging.MulticastMessage(
            tokens=group_tokens,
            notification=messaging.Notification(title=title, body=body),
            data=message_data or None,
            **notification_log.badge_config(badge)
        )
        response = messaging.send_each_for_multicast(message)  # type: ignore[attr-defined]
        success_count += response.success_count
        failure_count += response.failure_count
        pruned += _prune_failed_sends(db, response, group_tokens, group_owners)
    return success_count, failure_count, pruned


def handle_send_notification_to_all(decoded_token, data, db):
    """Send a notification to all users who have FCM tokens."""
    try:
        title = data.get("title")
        body = data.get("body")
        notification_action = data.get("notificationAction")
        
        if not title or not body:
            return jsonify({
                "success": False,
                "error": "title and body are required"
            }), 400
        
        # Taken from the verified token only. A payload `senderId` used to win,
        # which let any caller exclude someone else from a broadcast — and
        # would now let them sign a stored notification with another name.
        sender_id = decoded_token.get("uid")
        
        message_data = _message_data(notification_action)
        
        # The audience, per data-model.md §2. The preference and the active
        # flag are a composite Firestore query; excluding the sender and
        # tokenless users is done here because Firestore cannot express
        # "document id !=" alongside the other filters without a further index,
        # and an empty-string check is cheaper in code than as an index term.
        #
        # This used to stream the entire users collection: a read per user on
        # every business event, and — worse — no preference check at all, so
        # someone who had switched notifications off still received every one.
        #
        # Requires the composite index on users (receiveEmailNotifications ASC,
        # isActive ASC).
        audience = (
            db.collection("users")
            .where("receiveEmailNotifications", "==", True)
            .where("isActive", "==", True)
        )

        # The owning user id is kept alongside each token so a registration FCM
        # rejects can be traced back to the document holding it.
        tokens = []
        token_owners = []

        for user_doc in audience.stream():
            # Skip the sender — they don't need their own notification
            if sender_id and user_doc.id == sender_id:
                continue
            fcm_token = (user_doc.to_dict() or {}).get("fcmToken")
            # Opted in but unregistered is not a delivery failure — they simply
            # have no device. SC-009 measures success against registered
            # devices, not against everyone opted in.
            if not fcm_token:
                continue
            tokens.append(fcm_token)
            token_owners.append(user_doc.id)

        # Stored for everyone, not only the push audience above: the
        # preference switches off the phone alert, not the notification list.
        notification_id = notification_log.record(
            db,
            title=title,
            body=body,
            data=message_data,
            kind="broadcast",
            source=notification_log.request_source(data),
            sender_id=sender_id,
            recipient_ids=[notification_log.ALL],
        )

        if not tokens:
            # A valid outcome, not an error. FR-023 keeps the record saved
            # regardless, and the previous 404 read to the caller as a failure
            # to save the record that triggered the notification.
            print("📭 No eligible recipients — nothing to send")
            notification_log.record_delivery(db, notification_id, success_count=0, failure_count=0)
            return jsonify({
                "success": True,
                "message": "Notification sent to 0 users",
                "successCount": 0,
                "failureCount": 0,
                "totalTokens": 0,
                "prunedTokens": 0
            }), 200

        print(f"📢 Sending notification to {len(tokens)} users (excluded sender: {sender_id})")

        if notification_id:
            message_data["notificationId"] = notification_id
        badges = notification_log.todays_counts(db, token_owners)

        try:
            success_count, failure_count, pruned = _send_multicast(
                db, tokens, token_owners, title, body, message_data, badges
            )
            notification_log.record_delivery(
                db, notification_id, success_count=success_count, failure_count=failure_count
            )

            print(f"✅ Sent to {success_count} users, {failure_count} failed")

            return jsonify({
                "success": True,
                "message": f"Notification sent to {success_count} users",
                "successCount": success_count,
                "failureCount": failure_count,
                "totalTokens": len(tokens),
                "prunedTokens": pruned
            })
        except Exception as send_error:
            error_msg = str(send_error)
            print(f"❌ Error sending multicast notification: {error_msg}")
            notification_log.record_delivery(
                db, notification_id, success_count=0, failure_count=len(tokens), reason="error"
            )
            return jsonify({
                "success": False,
                "error": f"Failed to send notifications: {error_msg}"
            }), 500
            
    except Exception as e:
        error_msg = f"Error sending notification to all: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return jsonify({
            "success": False,
            "error": error_msg
        }), 500


# Every spelling each reviewer role has been stored under. The dashboard writes
# the enum name, but accounts created before it did carry the English or Arabic
# label — the set the dashboard's UserRoleTemplate.fromStorageValue accepts.
_ROLE_SPELLINGS = {
    "admin": ["admin", "Admin", "ADMIN", "مسؤول النظام"],
    "salesManager": [
        "salesManager",
        "Sales Manager",
        "sales manager",
        "sales_manager",
        "مدير المبيعات",
    ],
}

# A review signed in one slot is reported to the holders of the other role.
_OTHER_REVIEWER_ROLE = {"salesManager": "admin", "admin": "salesManager"}


def _unreachable_reason(user_id, user, actor_id):
    """Why ``user`` cannot be notified, or None when they can.

    The broadcast audience's rules (data-model.md §2), applied in code because
    a named recipient is read by id or by role rather than through the audience
    query. ``"actor"`` marks the person who performed the action.
    """
    if actor_id and user_id == actor_id:
        return "actor"
    if user.get("receiveEmailNotifications") is not True:
        return "preference_off"
    if user.get("isActive") is not True:
        return "inactive"
    if not user.get("fcmToken"):
        return "no_valid_registration"
    return None


def _deliver_to_candidates(db, *, candidates, unreachable, title, body, message_data,
                           kind, source, actor_id, notification_id=None,
                           create_only=False):
    """Record a notification for ``candidates`` and push to the reachable ones.

    Shared by the review notification and the spec-2038 role/user sends.
    Every addressed user is recorded, reachable or not (an unreachable phone is
    exactly when the in-app list is the only way to find out); the actor is
    neither recorded nor pushed. With ``create_only`` a taken ``notification_id``
    returns ``{"skipped": "already_notified"}`` without pushing, while a failed
    record write (``None``) still pushes.
    """
    tokens = []
    token_owners = []
    for user_id, user in candidates.items():
        reason = _unreachable_reason(user_id, user, actor_id)
        if reason == "actor":
            continue
        if reason:
            unreachable.append({"userId": user_id, "reason": reason})
            continue
        # One device signed in to two of the recipients still alerts once.
        if user["fcmToken"] in tokens:
            continue
        tokens.append(user["fcmToken"])
        token_owners.append(user_id)

    for entry in unreachable:
        print(f"📭 Recipient {entry['userId']} unreachable: {entry['reason']}")

    message_data = dict(message_data or {})
    recorded_id = notification_log.record(
        db,
        title=title,
        body=body,
        data=message_data,
        kind=kind,
        source=source,
        sender_id=actor_id,
        recipient_ids=[user_id for user_id in candidates if user_id != actor_id],
        notification_id=notification_id,
        create_only=create_only,
    )
    if recorded_id is notification_log.ALREADY_RECORDED:
        return {"skipped": "already_notified"}

    if not tokens:
        notification_log.record_delivery(db, recorded_id, success_count=0, failure_count=0)
        return {
            "successCount": 0, "failureCount": 0, "totalTokens": 0,
            "prunedTokens": 0, "unreachable": unreachable,
            "notificationId": recorded_id,
        }

    if recorded_id:
        message_data["notificationId"] = recorded_id
    badges = notification_log.todays_counts(db, token_owners)
    try:
        success_count, failure_count, pruned = _send_multicast(
            db, tokens, token_owners, title, body, message_data, badges
        )
        reason = None
    except Exception as error:
        # The notice is already recorded (and, with create_only, claimed), so a
        # retry would find it taken and never push. Report the failed push on
        # the record instead of raising: the in-app list still shows it.
        print(f"⚠️ Push failed for {recorded_id}: {error}")
        success_count, failure_count, pruned = 0, len(tokens), 0
        reason = "send_error"
    notification_log.record_delivery(
        db, recorded_id, success_count=success_count, failure_count=failure_count,
        reason=reason,
    )
    return {
        "successCount": success_count, "failureCount": failure_count,
        "totalTokens": len(tokens), "prunedTokens": pruned,
        "unreachable": unreachable, "notificationId": recorded_id,
    }


def send_to_roles(db, *, roles, title, body, message_data, kind, source, actor_id,
                  notification_id=None, create_only=False, extra_user_ids=()):
    """Notify every holder of ``roles`` (plus ``extra_user_ids``), minus the actor.

    ``roles`` are the keys of ``_ROLE_SPELLINGS`` (``admin``, ``salesManager``).
    """
    users = db.collection("users")
    candidates = {}
    unreachable = []
    for user_id in extra_user_ids:
        snapshot = users.document(user_id).get()
        if snapshot.exists:
            candidates[user_id] = snapshot.to_dict() or {}
        else:
            unreachable.append({"userId": user_id, "reason": "not_found"})

    spellings = [spelling for role in roles for spelling in _ROLE_SPELLINGS[role]]
    # A single-field `in` filter needs no composite index (9 values < 30).
    for user_doc in users.where("role", "in", spellings).stream():
        candidates.setdefault(user_doc.id, user_doc.to_dict() or {})

    return _deliver_to_candidates(
        db, candidates=candidates, unreachable=unreachable, title=title, body=body,
        message_data=message_data, kind=kind, source=source, actor_id=actor_id,
        notification_id=notification_id, create_only=create_only,
    )


def send_to_user(db, *, user_id, title, body, message_data, kind, source, actor_id,
                 notification_id=None, create_only=False):
    """Notify one user; recorded even when their push is off or unregistered."""
    snapshot = db.collection("users").document(user_id).get()
    if not snapshot.exists:
        return {
            "successCount": 0, "failureCount": 0, "totalTokens": 0, "prunedTokens": 0,
            "unreachable": [{"userId": user_id, "reason": "not_found"}],
            "notificationId": None,
        }
    return _deliver_to_candidates(
        db, candidates={user_id: snapshot.to_dict() or {}}, unreachable=[], title=title,
        body=body, message_data=message_data, kind=kind, source=source,
        actor_id=actor_id, notification_id=notification_id, create_only=create_only,
    )


def handle_send_review_notification(decoded_token, data, db):
    """Notify the representative and the other manager role about a review.

    Spec 2035 FR-015/FR-016: a sales manager's review reaches the representative
    who carried out the work and every system administrator; an administrator's
    review reaches the representative and every sales manager. Recipients are
    resolved here because the field app cannot read other users' documents.

    The reviewer is never told about their own review (FR-022), and a recipient
    who cannot be reached never stops the others — each is reported back under
    ``unreachable`` instead (FR-024).
    """
    try:
        title = data.get("title")
        body = data.get("body")
        if not title or not body:
            return jsonify({
                "success": False,
                "error": "title and body are required"
            }), 400

        other_role = _OTHER_REVIEWER_ROLE.get(data.get("reviewerRole"))
        if other_role is None:
            return jsonify({
                "success": False,
                "error": "reviewerRole must be salesManager or admin"
            }), 400

        # Taken from the verified token, not the payload, so a caller cannot
        # exclude or impersonate someone else as the reviewer.
        reviewer_id = decoded_token.get("uid")
        representative_id = data.get("representativeId")
        result = send_to_roles(
            db,
            roles=[other_role],
            title=title,
            body=body,
            message_data=_message_data(data.get("notificationAction")),
            kind="review",
            source=notification_log.request_source(data),
            actor_id=reviewer_id,
            extra_user_ids=[representative_id] if representative_id else [],
        )
        result.pop("notificationId", None)
        print(
            f"✅ Review notification sent to {result['successCount']} users, "
            f"{result['failureCount']} failed"
        )
        return jsonify({
            "success": True,
            "message": f"Notification sent to {result['successCount']} users",
            **result,
        }), 200

    except Exception as e:
        error_msg = f"Error sending review notification: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return jsonify({
            "success": False,
            "error": error_msg
        }), 500
