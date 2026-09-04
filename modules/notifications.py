"""Notification module for handling push notifications."""
from firebase_admin import messaging
from flask import jsonify
import traceback
from datetime import datetime, date, timezone, timedelta
from email.utils import parsedate_to_datetime
from modules.config import IRAQ_TIMEZONE
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
    """Handle task notifications for a specific date.
    
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

        users_ref = db.collection("users").stream()
        notification_count = 0
        
        for user_doc in users_ref:
            user = user_doc.to_dict()
            fcm_token = user.get("fcmToken")
            if not fcm_token:
                continue

            # Collect all tasks for this user that are due on target date
            tasks_ref = db.collection("tasks").stream()
            today_tasks = []
            
            for task_doc in tasks_ref:
                task = task_doc.to_dict()
                task_date = task.get("targetDate")

                # Normalize various date formats to ISO date string (YYYY-MM-DD)
                normalized = _normalize_target_date(task_date)
                if normalized == target_date:
                    today_tasks.append({
                        "id": task_doc.id,
                        "title": task.get("title", "بدون عنوان")
                    })
            
            # Send notification only if there are tasks due on target date
            if today_tasks:
                task_count = len(today_tasks)
                task_ids = [task["id"] for task in today_tasks]
                
                # Create notification body based on offset
                if days_offset == 0:
                    # Today's tasks
                    if task_count == 1:
                        body = f"عندك اليوم مهمة: {today_tasks[0]['title']}"
                    else:
                        body = f"عندك اليوم {task_count} مهام"
                else:
                    # Tomorrow's tasks
                    if task_count == 1:
                        body = f"عندك غدا مهمة: {today_tasks[0]['title']}"
                    else:
                        body = f"عندك غدا {task_count} مهام"
                
                message = messaging.Message(
                    token=fcm_token,
                    notification=messaging.Notification(
                        title="تذكير بالمهام",
                        body=body
                    ),
                    data={
                        "taskCount": str(task_count),
                        "taskIds": ",".join(task_ids),
                        "date": target_date,
                        "action": "daily_tasks"
                    }
                )
                try:
                    response = messaging.send(message)
                    notification_count += 1
                    print(f"✅ Sent to {user_doc.id}: {task_count} tasks, IDs: {task_ids}")
                except Exception as e:
                    print(f"❌ Error sending to {user_doc.id}: {str(e)}")
        
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
    broadcasting instead. ``fcmToken`` stays accepted for existing callers.
    """
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

        if not fcm_token:
            # Not an error: the user simply has no device registered. Reporting
            # this as a failure would make an ordinary state look like an
            # outage every time someone had not installed the app yet.
            print(f"📭 No valid registration for user {target_user_id}")
            return jsonify({
                "success": False,
                "reason": "no_valid_registration"
            }), 200

        # Prepare message data
        message_data = {}

        if notification_action:
            if isinstance(notification_action, dict):
                import json
                for k, v in notification_action.items():
                    if isinstance(v, (dict, list)):
                        message_data[str(k)] = json.dumps(v)
                    else:
                        message_data[str(k)] = str(v)
            else:
                message_data["action"] = str(notification_action)

        # Build FCM message
        message = messaging.Message(
            token=fcm_token,
            notification=messaging.Notification(title=title, body=body),
            data=message_data  # always a dict
        )

        # Send notification
        response = messaging.send(message)
        print(f"✅ Notification sent successfully: {response}")

        return jsonify({
            "success": True,
            "message": "Notification sent successfully",
            "messageId": response
        }), 200

    except messaging.UnregisteredError:
        # 200, not 400: the request was well-formed and the outcome is known
        # and final. Clear the registration so it is not retried forever —
        # only possible when addressed by id, since a raw token carries no
        # owner and finding one would need a query with no index behind it.
        if target_user_id:
            _prune_token(db, target_user_id, fcm_token)
        else:
            print("⚠️ Unregistered token supplied directly — no owner to clear")

        return jsonify({
            "success": False,
            "reason": "token_pruned"
        }), 200

    except Exception as e:
        error_msg = f"Error sending notification: {str(e)}"
        print(f"❌ {error_msg}")
        print(traceback.format_exc())

        return jsonify({
            "success": False,
            "error": error_msg
        }), 500


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
        
        # Get the sender's UID so we can exclude them from recipients
        sender_id = data.get("senderId") or decoded_token.get("uid")
        
        # Build message data
        message_data = {}
        if notification_action:
            if isinstance(notification_action, dict):
                import json
                for k, v in notification_action.items():
                    if isinstance(v, (dict, list)):
                        message_data[str(k)] = json.dumps(v)
                    else:
                        message_data[str(k)] = str(v)
            else:
                message_data["action"] = str(notification_action)
        
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

        if not tokens:
            # A valid outcome, not an error. FR-023 keeps the record saved
            # regardless, and the previous 404 read to the caller as a failure
            # to save the record that triggered the notification.
            print("📭 No eligible recipients — nothing to send")
            return jsonify({
                "success": True,
                "message": "Notification sent to 0 users",
                "successCount": 0,
                "failureCount": 0,
                "totalTokens": 0,
                "prunedTokens": 0
            }), 200

        print(f"📢 Sending notification to {len(tokens)} users (excluded sender: {sender_id})")
        
        # Build multicast message
        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(
                title=title,
                body=body
            ),
            data=message_data if message_data else None
        )
        
        try:
            response = messaging.send_each_for_multicast(message)  # type: ignore[attr-defined]
            success_count = response.success_count
            failure_count = response.failure_count
            
            print(f"✅ Sent to {success_count} users, {failure_count} failed")

            # Clear registrations FCM says are gone for good. Left in place they
            # are retried on every send forever, dragging the delivery rate down
            # and hiding the fact that a real user has stopped receiving.
            pruned = 0
            if failure_count > 0:
                for idx, resp in enumerate(response.responses):
                    if resp.success:
                        continue
                    print(f"❌ Failed to send to token {idx}: {resp.exception}")
                    if _is_permanently_invalid_token(resp.exception):
                        pruned += _prune_token(db, token_owners[idx], tokens[idx])

            if pruned:
                print(f"🧹 Cleared {pruned} dead device registration(s)")

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
