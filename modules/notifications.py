"""Notification module for handling push notifications."""
from firebase_admin import messaging
from flask import jsonify
import traceback
from datetime import datetime


def handle_daily_notifications(db):
    """Handle daily notification tasks"""
    try:
        # Get today's date for user stats
        today = datetime.utcnow().date().isoformat()
        print(f"🔔 Running daily notifications for: {today}")

        users_ref = db.collection("users").stream()
        notification_count = 0
        
        for user_doc in users_ref:
            user = user_doc.to_dict()
            fcm_token = user.get("fcmToken")
            if not fcm_token:
                continue

            tasks_ref = db.collection("tasks").where("salesRepresentativeId", "==", user_doc.id).stream()
            for task_doc in tasks_ref:
                task = task_doc.to_dict()
                task_date = task.get("targetDate")
                if task_date == today:
                    message = messaging.Message(
                        token=fcm_token,
                        notification=messaging.Notification(
                            title="تذكير بالمهام",
                            body=f"عندك اليوم مهمة: {task.get('title', 'بدون عنوان')}"
                        )
                    )
                    try:
                        response = messaging.send(message)
                        notification_count += 1
                        print(f"✅ Sent to {user_doc.id}: {response}")
                    except Exception as e:
                        print(f"❌ Error sending to {user_doc.id}: {str(e)}")
        
        return jsonify({
            "success": True, 
            "message": f"Daily task completed. Sent {notification_count} notifications.",
            "date": today,
            "count": notification_count
        })
        
    except Exception as e:
        error_msg = f"Error in daily task: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return jsonify({"error": error_msg}), 500


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

