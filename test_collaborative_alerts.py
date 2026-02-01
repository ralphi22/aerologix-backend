#!/usr/bin/env python3
"""
Test Collaborative AD/SB Alert System - New Endpoints

Tests the collaborative alert system endpoints as specified in the review request:
- GET /api/alerts returns correct structure
- GET /api/alerts/count returns counts  
- Verify alert item structure (if alerts exist)
- PUT /api/alerts/read-all works

Authentication: test@aerologix.ca / password123
"""

import requests
import sys
import json
from datetime import datetime
from typing import Dict, Any, Optional

class CollaborativeAlertsBackendTester:
    def __init__(self, base_url="http://localhost:8001"):
        self.base_url = base_url
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []
        
    def log_test(self, name: str, success: bool, details: str = "", response_data: Any = None):
        """Log test result"""
        self.tests_run += 1
        if success:
            self.tests_passed += 1
            
        result = {
            "test_name": name,
            "success": success,
            "details": details,
            "response_data": response_data,
            "timestamp": datetime.now().isoformat()
        }
        self.test_results.append(result)
        
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} | {name}")
        if details:
            print(f"    Details: {details}")
        if not success and response_data:
            print(f"    Response: {response_data}")
        print()

    def run_test(self, name: str, method: str, endpoint: str, expected_status: int, 
                 data: Optional[Dict] = None, headers: Optional[Dict] = None) -> tuple[bool, Any]:
        """Run a single API test"""
        url = f"{self.base_url}/{endpoint}"
        
        # Default headers
        test_headers = {'Content-Type': 'application/json'}
        if self.token:
            test_headers['Authorization'] = f'Bearer {self.token}'
        if headers:
            test_headers.update(headers)

        try:
            if method == 'GET':
                response = requests.get(url, headers=test_headers, timeout=30)
            elif method == 'POST':
                if endpoint.startswith('api/auth/login'):
                    # Special handling for form-data login
                    test_headers['Content-Type'] = 'application/x-www-form-urlencoded'
                    response = requests.post(url, data=data, headers=test_headers, timeout=30)
                else:
                    response = requests.post(url, json=data, headers=test_headers, timeout=30)
            elif method == 'PUT':
                response = requests.put(url, json=data, headers=test_headers, timeout=30)
            elif method == 'DELETE':
                response = requests.delete(url, headers=test_headers, timeout=30)
            else:
                raise ValueError(f"Unsupported method: {method}")

            success = response.status_code == expected_status
            
            try:
                response_json = response.json()
            except:
                response_json = {"raw_response": response.text, "status_code": response.status_code}

            details = f"Status: {response.status_code}"
            if not success:
                details += f" (expected {expected_status})"
                
            self.log_test(name, success, details, response_json)
            return success, response_json

        except Exception as e:
            self.log_test(name, False, f"Error: {str(e)}")
            return False, {}

    def test_authentication(self):
        """Test login with test user"""
        print("🔐 Testing Authentication...")
        
        # Try to login
        success, response = self.run_test(
            "Login with test@aerologix.ca",
            "POST",
            "api/auth/login",
            200,
            data={
                "username": "test@aerologix.ca",
                "password": "password123"
            }
        )
        
        if success and 'access_token' in response:
            self.token = response['access_token']
            print(f"✅ Authentication successful. Token obtained.")
            return True
        else:
            print(f"❌ Authentication failed. Response: {response}")
            return False

    def test_collaborative_alerts_new_endpoints(self):
        """Test Collaborative AD/SB Alert System - New Endpoints as per review request"""
        print("🚨 Testing Collaborative AD/SB Alert System - New Endpoints...")
        
        # Test 1: GET /api/alerts - Get alerts list (new endpoint structure)
        print("📋 Test 1: GET /api/alerts returns correct structure")
        success1, response1 = self.run_test(
            "Get alerts list (new endpoint)",
            "GET",
            "api/alerts",
            200
        )
        
        if not success1:
            return False
        
        # Validate response structure matches expected format from review request
        required_fields = ["alerts", "total_count", "unread_count"]
        missing_fields = [field for field in required_fields if field not in response1]
        
        if missing_fields:
            self.log_test(
                "GET /api/alerts response structure",
                False,
                f"Missing required fields: {missing_fields}"
            )
            return False
        
        # Validate alerts is an array
        alerts = response1.get("alerts", [])
        if not isinstance(alerts, list):
            self.log_test(
                "GET /api/alerts alerts format",
                False,
                f"Alerts should be an array, got {type(alerts)}"
            )
            return False
        
        # Validate counts are integers
        total_count = response1.get("total_count")
        unread_count = response1.get("unread_count")
        
        if not isinstance(total_count, int) or not isinstance(unread_count, int):
            self.log_test(
                "GET /api/alerts count types",
                False,
                f"Counts should be integers, got total_count={type(total_count)}, unread_count={type(unread_count)}"
            )
            return False
        
        # Validate each alert structure (if any alerts exist)
        for i, alert in enumerate(alerts):
            required_alert_fields = [
                "id", "type", "aircraft_id", "aircraft_type_key", "manufacturer", 
                "model", "reference", "reference_type", "message", "status", "created_at"
            ]
            missing_alert_fields = [field for field in required_alert_fields if field not in alert]
            
            if missing_alert_fields:
                self.log_test(
                    f"Alert {i} structure validation",
                    False,
                    f"Missing alert fields: {missing_alert_fields}"
                )
                return False
            
            # Validate alert field values
            if alert.get("type") != "NEW_AD_SB":
                self.log_test(
                    f"Alert {i} type validation",
                    False,
                    f"Expected type='NEW_AD_SB', got '{alert.get('type')}'"
                )
                return False
            
            if alert.get("reference_type") not in ["AD", "SB"]:
                self.log_test(
                    f"Alert {i} reference_type validation",
                    False,
                    f"Expected reference_type='AD' or 'SB', got '{alert.get('reference_type')}'"
                )
                return False
            
            if alert.get("status") not in ["UNREAD", "READ", "DISMISSED"]:
                self.log_test(
                    f"Alert {i} status validation",
                    False,
                    f"Expected status in ['UNREAD', 'READ', 'DISMISSED'], got '{alert.get('status')}'"
                )
                return False
        
        self.log_test(
            "GET /api/alerts validation",
            True,
            f"All validations passed. Total alerts: {total_count}, Unread: {unread_count}"
        )
        
        # Test 2: GET /api/alerts/count - Get alert counts
        print("📋 Test 2: GET /api/alerts/count returns counts")
        success2, response2 = self.run_test(
            "Get alert counts",
            "GET",
            "api/alerts/count",
            200
        )
        
        if not success2:
            return False
        
        # Validate count response structure matches expected format
        required_count_fields = ["unread_count", "total_count"]
        missing_count_fields = [field for field in required_count_fields if field not in response2]
        
        if missing_count_fields:
            self.log_test(
                "GET /api/alerts/count response structure",
                False,
                f"Missing required fields: {missing_count_fields}"
            )
            return False
        
        # Validate count consistency with alerts list
        if response2.get("unread_count") != response1.get("unread_count"):
            self.log_test(
                "Alert count consistency",
                False,
                f"Unread count mismatch: list={response1.get('unread_count')}, count={response2.get('unread_count')}"
            )
            return False
        
        self.log_test(
            "GET /api/alerts/count validation",
            True,
            f"Count endpoint working. Unread: {response2.get('unread_count')}, Total: {response2.get('total_count')}"
        )
        
        # Test 3: Verify alert item structure (if alerts exist)
        print("📋 Test 3: Verify alert item structure")
        if len(alerts) > 0:
            first_alert = alerts[0]
            
            # Validate all required fields from review request
            expected_fields = {
                "id": str,
                "type": str,
                "aircraft_id": str,
                "aircraft_type_key": str,
                "manufacturer": (str, type(None)),
                "model": (str, type(None)),
                "reference": str,
                "reference_type": str,
                "message": str,
                "status": str,
                "created_at": str
            }
            
            for field, expected_type in expected_fields.items():
                if field not in first_alert:
                    self.log_test(
                        f"Alert item field presence - {field}",
                        False,
                        f"Field '{field}' missing from alert item"
                    )
                    return False
                
                field_value = first_alert.get(field)
                if isinstance(expected_type, tuple):
                    # Allow multiple types (e.g., str or None)
                    if not isinstance(field_value, expected_type):
                        self.log_test(
                            f"Alert item field type - {field}",
                            False,
                            f"Field '{field}' should be {expected_type}, got {type(field_value)}"
                        )
                        return False
                else:
                    # Single type expected
                    if not isinstance(field_value, expected_type):
                        self.log_test(
                            f"Alert item field type - {field}",
                            False,
                            f"Field '{field}' should be {expected_type.__name__}, got {type(field_value).__name__}"
                        )
                        return False
            
            # Validate aircraft_type_key format (should be like "CESSNA::172M")
            aircraft_type_key = first_alert.get("aircraft_type_key", "")
            if "::" not in aircraft_type_key:
                self.log_test(
                    "Alert item aircraft_type_key format",
                    False,
                    f"aircraft_type_key should contain '::' separator, got '{aircraft_type_key}'"
                )
                return False
            
            self.log_test(
                "Alert item structure validation",
                True,
                f"Alert item has all required fields with correct types. aircraft_type_key: '{aircraft_type_key}'"
            )
        else:
            self.log_test(
                "Alert item structure validation",
                True,
                "No alerts available for structure testing (expected for new system)"
            )
        
        # Test 4: PUT /api/alerts/read-all works
        print("📋 Test 4: PUT /api/alerts/read-all works")
        success4, response4 = self.run_test(
            "Mark all alerts as read",
            "PUT",
            "api/alerts/read-all",
            200
        )
        
        if not success4:
            return False
        
        # Validate read-all response structure
        if not isinstance(response4, dict) or "ok" not in response4 or "marked_count" not in response4:
            self.log_test(
                "PUT /api/alerts/read-all response structure",
                False,
                f"Expected dict with 'ok' and 'marked_count' fields, got {response4}"
            )
            return False
        
        if response4.get("ok") != True:
            self.log_test(
                "PUT /api/alerts/read-all ok field",
                False,
                f"Expected ok=True, got {response4.get('ok')}"
            )
            return False
        
        marked_count = response4.get("marked_count")
        if not isinstance(marked_count, int) or marked_count < 0:
            self.log_test(
                "PUT /api/alerts/read-all marked_count field",
                False,
                f"Expected marked_count to be non-negative integer, got {marked_count}"
            )
            return False
        
        self.log_test(
            "PUT /api/alerts/read-all validation",
            True,
            f"Read-all endpoint working. Marked {marked_count} alerts as read"
        )
        
        return success1 and success2 and success4

def main():
    """Main test runner"""
    print("=" * 80)
    print("🚀 AeroLogix AI Backend Testing - Collaborative AD/SB Alert System")
    print("=" * 80)
    print()
    
    tester = CollaborativeAlertsBackendTester()
    
    # Test authentication first
    if not tester.test_authentication():
        print("❌ Authentication failed. Cannot proceed with tests.")
        return False
    
    print()
    
    # Run collaborative alerts tests
    print("🚨 Testing Collaborative AD/SB Alert System...")
    success = tester.test_collaborative_alerts_new_endpoints()
    
    print()
    print("=" * 80)
    print("📊 TEST SUMMARY")
    print("=" * 80)
    print(f"Tests Run: {tester.tests_run}")
    print(f"Tests Passed: {tester.tests_passed}")
    print(f"Success Rate: {(tester.tests_passed/tester.tests_run)*100:.1f}%")
    
    if success:
        print("✅ All collaborative alerts tests passed!")
    else:
        print("❌ Some tests failed. Check output above.")
    
    return success

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)