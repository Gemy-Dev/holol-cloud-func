#!/usr/bin/env python3
"""
Test script for sending FCM notifications.
Can be used to test the notification endpoints locally or via HTTP.

Usage:
    # Test via HTTP (requires deployed function and auth token)
    python test_notification.py http <function_url> <auth_token> <fcm_token>
    
    # Test locally (requires Firebase Admin SDK setup)
    python test_notification.py local <fcm_token>
"""

import sys
import json
import requests
from firebase_admin import credentials, firestore, messaging
import firebase_admin


def test_via_http(function_url, auth_token, fcm_token):
    """Test notification via HTTP request to deployed function."""
    print("=" * 70)
    print("Testing notification via HTTP")
    print("=" * 70)
    print(f"Function URL: {function_url}")
    print(f"FCM Token: {fcm_token[:50]}...")
    print()
    
    payload = {
        "action": "sendNotification",
        "fcmToken": fcm_token,
        "title": "Test Notification",
        "body": "This is a test notification from the Cloud Function",
        "notificationAction": {
            "type": "test",
            "data": "test_data"
        }
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}"
    }
    
    try:
        print("Sending request...")
        response = requests.post(function_url, json=payload, headers=headers)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 200:
            print("\n✅ SUCCESS: Notification sent!")
        else:
            print("\n❌ FAILED: Check the error message above")
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


def test_locally(fcm_token):
    """Test notification locally using Firebase Admin SDK."""
    print("=" * 70)
    print("Testing notification locally")
    print("=" * 70)
    print(f"FCM Token: {fcm_token[:50]}...")
    print()
    
    # Initialize Firebase Admin if not already initialized
    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)
    
    message = messaging.Message(
        token=fcm_token,
        notification=messaging.Notification(
            title="Test Notification",
            body="This is a test notification from local script"
        ),
        data={
            "type": "test",
            "data": "test_data"
        }
    )
    
    try:
        print("Sending notification...")
        response = messaging.send(message)
        print(f"✅ SUCCESS: Notification sent!")
        print(f"Message ID: {response}")
        
    except messaging.UnregisteredError:
        print("❌ FAILED: FCM token is invalid or unregistered")
    except Exception as e:
        print(f"❌ FAILED: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage:")
        print("  # Test via HTTP (requires deployed function)")
        print("  python test_notification.py http <function_url> <auth_token> <fcm_token>")
        print()
        print("  # Test locally (requires Firebase Admin SDK)")
        print("  python test_notification.py local <fcm_token>")
        print()
        print("Example:")
        print("  python test_notification.py local cFsRMBcq4LTZv16bnryeyo:APA91bHmBc2NmsPGtet76zaaL26udELS66iw7UgN95uqRWpI15Xf4vIMNLIcC0Cbmei_uY_blWl6-cqWBX3Sq9sGIh-oY_BBg1zOJYy0f507U9fRt-QuUUw")
        sys.exit(1)
    
    mode = sys.argv[1]
    
    if mode == "http":
        if len(sys.argv) < 5:
            print("Error: HTTP mode requires function_url, auth_token, and fcm_token")
            sys.exit(1)
        function_url = sys.argv[2]
        auth_token = sys.argv[3]
        fcm_token = sys.argv[4]
        test_via_http(function_url, auth_token, fcm_token)
        
    elif mode == "local":
        fcm_token = sys.argv[2]
        test_locally(fcm_token)
        
    else:
        print(f"Error: Unknown mode '{mode}'. Use 'http' or 'local'")
        sys.exit(1)

