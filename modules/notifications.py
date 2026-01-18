"""Notification module for handling push notifications."""
from firebase_admin import messaging
from flask import jsonify
import traceback
from datetime import datetime, date, timezone, timedelta
from email.utils import parsedate_to_datetime
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
        iraq_tz_offset = timedelta(hours=3)
        iraq_now = datetime.utcnow() + iraq_tz_offset
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
    """Send a notification to a specific user by FCM token."""
    try:
        fcm_token = data.get("fcmToken")
        title = data.get("title")
        body = data.get("body")
        notification_action = data.get("notificationAction")

        if not fcm_token:
            return jsonify({"success": False, "error": "fcmToken is required"}), 400

        if not title or not body:
            return jsonify({"success": False, "error": "title and body are required"}), 400

        # Prepare message data
        message_data = {}

        if notification_action:
            if isinstance(notification_action, dict):
                message_data.update(notification_action)
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
        return jsonify({
            "success": False,
            "error": "FCM token is invalid or unregistered"
        }), 400

    except Exception as e:
        error_msg = f"Error sending notification: {str(e)}"
        print(f"❌ {error_msg}")
        print(traceback.format_exc())

        return jsonify({
            "success": False,
            "error": error_msg
        }), 500


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
        
        # Build message data
        message_data = {}
        if notification_action:
            if isinstance(notification_action, dict):
                message_data.update(notification_action)
            else:
                message_data["action"] = str(notification_action)
        
        # Get all users with FCM tokens
        users_ref = db.collection("users").stream()
        tokens = []
        
        for user_doc in users_ref:
            user = user_doc.to_dict()
            fcm_token = user.get("fcmToken")
            if fcm_token:
                tokens.append(fcm_token)
        
        if not tokens:
            return jsonify({
                "success": False,
                "error": "No users with FCM tokens found"
            }), 404
        
        print(f"📢 Sending notification to {len(tokens)} users")
        
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
            response = messaging.send_multicast(message)  # type: ignore[attr-defined]
            success_count = response.success_count
            failure_count = response.failure_count
            
            print(f"✅ Sent to {success_count} users, {failure_count} failed")
            
            # Log failures if any
            if failure_count > 0:
                for idx, resp in enumerate(response.responses):
                    if not resp.success:
                        print(f"❌ Failed to send to token {idx}: {resp.exception}")
            
            return jsonify({
                "success": True,
                "message": f"Notification sent to {success_count} users",
                "successCount": success_count,
                "failureCount": failure_count,
                "totalTokens": len(tokens)
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

