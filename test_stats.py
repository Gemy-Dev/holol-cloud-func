#!/usr/bin/env python3
"""
Test script for getStats cloud function.
"""

import sys
import json
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask

# Add current directory to path so we can import modules
sys.path.append('.')
from modules.tasks import get_task_stats

def test_get_stats_local():
    """Test getStats logic locally using Firebase Admin SDK."""
    print("=" * 70)
    print("Testing getStats locally")
    print("=" * 70)
    
    # Initialize Firebase Admin if not already initialized
    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)
    
    db = firestore.client()
    
    # Mock Flask app context because get_task_stats uses jsonify
    app = Flask(__name__)
    with app.app_context():
        try:
            # Mock token with a test UID
            mock_token = {"uid": "test_user_id"}
            print(f"Calling get_task_stats(token={mock_token}, db)...")
            response = get_task_stats(mock_token, db)
            
            # response is a Flask Response object
            # We need to get the JSON data from it
            if hasattr(response, 'get_json'):
                 # It's a Response object
                 data = response.get_json()
                 status_code = response.status_code
            else:
                 # It might be a tuple (response, status)
                 print(f"Response type: {type(response)}")
                 if isinstance(response, tuple):
                     data = response[0].get_json()
                     status_code = response[1]
                 else:
                     data = response.get_json()
                     status_code = response.status_code
            
            print(f"Status Code: {status_code}")
            print(f"Response Data: {json.dumps(data, indent=2)}")
            
            if status_code == 200 and data.get('success'):
                print("\n✅ SUCCESS: Stats retrieved successfully!")
                
                # Verify structure
                stats_list = data.get('data', [])
                print(f"Received {len(stats_list)} date entries.")
                if len(stats_list) > 0:
                    first = stats_list[0]
                    if 'date' in first and 'count' in first:
                         print("Structure check passed: found 'date' and 'count' keys.")
                    else:
                         print(f"❌ Structure check failed: keys are {first.keys()}")
            else:
                print("\n❌ FAILED: API returned error")
                
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    test_get_stats_local()
