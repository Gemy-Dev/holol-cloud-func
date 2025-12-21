#!/usr/bin/env python3
"""
Test script for getTasksByDateRange cloud function.
"""

import sys
import json
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask
from datetime import datetime

# Add current directory to path so we can import modules
sys.path.append('.')
from modules.tasks import get_tasks_by_date_range

def test_get_tasks_by_date_range():
    """Test getTasksByDateRange logic locally."""
    print("=" * 70)
    print("Testing getTasksByDateRange locally")
    print("=" * 70)
    
    # Initialize Firebase Admin
    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)
    
    db = firestore.client()
    
    # Mock data
    # Test with today's date
    today = datetime.now().strftime("%Y-%m-%d")
    data = {"date": today, "days": 7}
    
    # Mock token
    mock_token = {"uid": "test_user_id"}
    
    # Mock Flask app context
    app = Flask(__name__)
    with app.app_context():
        try:
            print(f"Calling get_tasks_by_date_range(data={data}, token={mock_token})...")
            response = get_tasks_by_date_range(data, mock_token, db)
            
            # Helper to extract JSON from response
            if hasattr(response, 'get_json'):
                 resp_data = response.get_json()
                 status_code = response.status_code
            elif isinstance(response, tuple):
                 resp_data = response[0].get_json()
                 status_code = response[1]
            else:
                 resp_data = response.get_json()
                 status_code = response.status_code
            
            print(f"Status Code: {status_code}")
            print(f"Response Summary: {json.dumps(resp_data.get('dateRange', {}), indent=2)}")
            print(f"Count: {resp_data.get('count')}")
            
            if status_code == 200 and resp_data.get('success'):
                print("\n✅ SUCCESS: Tasks retrieved successfully!")
            else:
                print("\n❌ FAILED: API returned error")
                print(json.dumps(resp_data, indent=2))
                
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    test_get_tasks_by_date_range()
