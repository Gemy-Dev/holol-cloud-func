"""Notification module for handling push notifications."""
from firebase_admin import auth, messaging
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

            tasks_ref = db.collection("tasks").where("deliveryId", "==", user_doc.id).where("taskOperation", "==", "عمل").stream()
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

