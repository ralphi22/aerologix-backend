#!/usr/bin/env python3
"""
AeroLogix AI Backend Testing

Tests the backend API endpoints including:
- Authentication
- Aircraft management
- Operational Limitations (Critical Mentions endpoint)
- TC AD/SB Detection system
- Alert management
- Audit logging
"""

import requests
import sys
import json
from datetime import datetime
from typing import Dict, Any, Optional

class AeroLogixBackendTester:
    def __init__(self, base_url="http://localhost:8001"):
        self.base_url = base_url
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []
        self.aircraft_id = "aircraft_001"  # Test aircraft from seeded data
        
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

    def create_test_user(self):
        """Create a test user if it doesn't exist"""
        print("👤 Creating test user...")
        
        # Try to create a test user
        success, response = self.run_test(
            "Create test user",
            "POST",
            "api/auth/signup",
            200,
            data={
                "email": "test@aerologix.ca",
                "password": "password123",
                "name": "Test User"
            }
        )
        
        if success and 'access_token' in response:
            self.token = response['access_token']
            print(f"✅ Test user created and authenticated")
            return True
        elif not success and "Email already registered" in str(response):
            print("ℹ️ Test user already exists, proceeding with login")
            return True
        else:
            print(f"❌ Failed to create test user: {response}")
            return False

    def test_authentication(self):
        """Test login with test user, create if needed"""
        print("🔐 Testing Authentication...")
        
        # First try to login
        success, response = self.run_test(
            "Login with test user",
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
            # Try to create user if login failed
            print("ℹ️ Login failed, attempting to create test user...")
            if self.create_test_user():
                # Try login again after user creation
                success, response = self.run_test(
                    "Login after user creation",
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
                    print(f"✅ Authentication successful after user creation.")
                    return True
            
            print(f"❌ Authentication failed. Response: {response}")
            return False

    def create_test_aircraft(self):
        """Create a test aircraft for testing"""
        print("✈️ Creating test aircraft...")
        
        success, response = self.run_test(
            "Create test aircraft",
            "POST",
            "api/aircraft",
            201,
            data={
                "registration": "C-GTEST",
                "make": "Cessna",
                "model": "172",
                "year": 2020,
                "serial_number": "TEST123456"
            }
        )
        
        if success:
            aircraft_id = response.get('_id') or response.get('id')
            print(f"✅ Test aircraft created: C-GTEST (ID: {aircraft_id})")
            return aircraft_id
        else:
            print(f"❌ Failed to create test aircraft: {response}")
            return None

    def get_aircraft_list(self):
        """Get list of aircraft for the authenticated user"""
        print("✈️ Getting aircraft list...")
        
        success, response = self.run_test(
            "Get aircraft list",
            "GET",
            "api/aircraft",
            200
        )
        
        if success and isinstance(response, list) and len(response) > 0:
            aircraft_id = response[0].get('_id') or response[0].get('id')
            registration = response[0].get('registration', 'Unknown')
            print(f"✅ Found aircraft: {registration} (ID: {aircraft_id})")
            return aircraft_id, registration
        elif success and isinstance(response, dict) and 'aircraft' in response:
            # Handle paginated response
            aircraft_list = response['aircraft']
            if len(aircraft_list) > 0:
                aircraft_id = aircraft_list[0].get('_id') or aircraft_list[0].get('id')
                registration = aircraft_list[0].get('registration', 'Unknown')
                print(f"✅ Found aircraft: {registration} (ID: {aircraft_id})")
                return aircraft_id, registration
        
        # No aircraft found, try to create one
        print("ℹ️ No aircraft found, creating test aircraft...")
        aircraft_id = self.create_test_aircraft()
        if aircraft_id:
            return aircraft_id, "C-GTEST"
        
        print("❌ No aircraft found and failed to create test aircraft")
        return None, None

    def test_tc_version_endpoint(self):
        """Test GET /api/tc-adsb/version"""
        print("📋 Testing TC Version Endpoint...")
        
        success, response = self.run_test(
            "Get TC AD/SB version",
            "GET",
            "api/tc-adsb/version",
            200
        )
        
        if success and 'tc_adsb_version' in response:
            print(f"✅ TC Version: {response['tc_adsb_version']}")
            return response['tc_adsb_version']
        return None

    def test_critical_mentions_endpoint(self):
        """Test GET /api/limitations/{aircraft_id}/critical-mentions"""
        print("🔍 Testing Critical Mentions Endpoint...")
        
        # Get aircraft ID first
        aircraft_id, registration = self.get_aircraft_list()
        
        if not aircraft_id:
            self.log_test("Critical mentions test", False, "No aircraft available for testing")
            return False
        
        # Test the critical-mentions endpoint
        success, response = self.run_test(
            f"Get critical mentions for aircraft {registration}",
            "GET",
            f"api/limitations/{aircraft_id}/critical-mentions",
            200
        )
        
        if not success:
            return False
        
        # Validate response structure
        required_fields = ["aircraft_id", "registration", "critical_mentions", "summary", "disclaimer"]
        missing_fields = [field for field in required_fields if field not in response]
        
        if missing_fields:
            self.log_test(
                "Critical mentions response structure",
                False,
                f"Missing required fields: {missing_fields}"
            )
            return False
        
        # Validate critical_mentions structure
        critical_mentions = response.get("critical_mentions", {})
        expected_categories = ["elt", "avionics", "fire_extinguisher", "general_limitations"]
        missing_categories = [cat for cat in expected_categories if cat not in critical_mentions]
        
        if missing_categories:
            self.log_test(
                "Critical mentions categories",
                False,
                f"Missing categories: {missing_categories}"
            )
            return False
        
        # Validate summary structure
        summary = response.get("summary", {})
        expected_summary_fields = ["elt_count", "avionics_count", "fire_extinguisher_count", "general_limitations_count", "total_count"]
        missing_summary_fields = [field for field in expected_summary_fields if field not in summary]
        
        if missing_summary_fields:
            self.log_test(
                "Critical mentions summary structure",
                False,
                f"Missing summary fields: {missing_summary_fields}"
            )
            return False
        
        # Validate that all categories are arrays
        for category in expected_categories:
            if not isinstance(critical_mentions[category], list):
                self.log_test(
                    f"Critical mentions {category} format",
                    False,
                    f"{category} should be an array, got {type(critical_mentions[category])}"
                )
                return False
        
        # Validate counts match actual data
        actual_counts = {
            "elt_count": len(critical_mentions["elt"]),
            "avionics_count": len(critical_mentions["avionics"]),
            "fire_extinguisher_count": len(critical_mentions["fire_extinguisher"]),
            "general_limitations_count": len(critical_mentions["general_limitations"])
        }
        
        actual_total = sum(actual_counts.values())
        
        counts_match = all(
            summary[key] == actual_counts[key] for key in actual_counts.keys()
        ) and summary["total_count"] == actual_total
        
        if not counts_match:
            self.log_test(
                "Critical mentions count validation",
                False,
                f"Summary counts don't match actual data. Expected: {actual_counts}, total: {actual_total}, Got: {summary}"
            )
            return False
        
        # Log successful validation
        self.log_test(
            "Critical mentions endpoint validation",
            True,
            f"All validations passed. Total mentions: {actual_total}, Categories: {actual_counts}"
        )
        
        # Test with invalid aircraft ID
        success_invalid, response_invalid = self.run_test(
            "Get critical mentions for invalid aircraft",
            "GET",
            "api/limitations/invalid_aircraft_id/critical-mentions",
            404
        )
        
        return success and success_invalid
            200
        )
        
        if not success:
            return False
        
        # Validate response structure
        required_fields = ["aircraft_id", "registration", "critical_mentions", "summary", "disclaimer"]
        missing_fields = [field for field in required_fields if field not in response]
        
        if missing_fields:
            self.log_test(
                "Critical mentions response structure",
                False,
                f"Missing required fields: {missing_fields}"
            )
            return False
        
        # Validate critical_mentions structure
        critical_mentions = response.get("critical_mentions", {})
        expected_categories = ["elt", "avionics", "fire_extinguisher", "general_limitations"]
        missing_categories = [cat for cat in expected_categories if cat not in critical_mentions]
        
        if missing_categories:
            self.log_test(
                "Critical mentions categories",
                False,
                f"Missing categories: {missing_categories}"
            )
            return False
        
        # Validate summary structure
        summary = response.get("summary", {})
        expected_summary_fields = ["elt_count", "avionics_count", "fire_extinguisher_count", "general_limitations_count", "total_count"]
        missing_summary_fields = [field for field in expected_summary_fields if field not in summary]
        
        if missing_summary_fields:
            self.log_test(
                "Critical mentions summary structure",
                False,
                f"Missing summary fields: {missing_summary_fields}"
            )
            return False
        
        # Validate that all categories are arrays
        for category in expected_categories:
            if not isinstance(critical_mentions[category], list):
                self.log_test(
                    f"Critical mentions {category} format",
                    False,
                    f"{category} should be an array, got {type(critical_mentions[category])}"
                )
                return False
        
        # Validate counts match actual data
        actual_counts = {
            "elt_count": len(critical_mentions["elt"]),
            "avionics_count": len(critical_mentions["avionics"]),
            "fire_extinguisher_count": len(critical_mentions["fire_extinguisher"]),
            "general_limitations_count": len(critical_mentions["general_limitations"])
        }
        
        actual_total = sum(actual_counts.values())
        
        counts_match = all(
            summary[key] == actual_counts[key] for key in actual_counts.keys()
        ) and summary["total_count"] == actual_total
        
        if not counts_match:
            self.log_test(
                "Critical mentions count validation",
                False,
                f"Summary counts don't match actual data. Expected: {actual_counts}, total: {actual_total}, Got: {summary}"
            )
            return False
        
        # Log successful validation
        self.log_test(
            "Critical mentions endpoint validation",
            True,
            f"All validations passed. Total mentions: {actual_total}, Categories: {actual_counts}"
        )
        
        # Test with invalid aircraft ID
        success_invalid, response_invalid = self.run_test(
            "Get critical mentions for invalid aircraft",
            "GET",
            "api/limitations/invalid_aircraft_id/critical-mentions",
            404
        )
        
        return success and success_invalid
        """Test GET /api/tc-adsb/version"""
        print("📋 Testing TC Version Endpoint...")
        
        success, response = self.run_test(
            "Get TC AD/SB version",
            "GET",
            "api/tc-adsb/version",
            200
        )
        
        if success and 'tc_adsb_version' in response:
            print(f"✅ TC Version: {response['tc_adsb_version']}")
            return response['tc_adsb_version']
        return None

    def test_detection_endpoints(self):
        """Test all detection endpoints"""
        print("🔍 Testing Detection Endpoints...")
        
        # Test user detection
        success1, response1 = self.run_test(
            "Trigger detection for user aircraft",
            "POST",
            "api/tc-adsb/detect",
            200,
            data={"force_all": True}
        )
        
        # Test system-wide detection (admin)
        success2, response2 = self.run_test(
            "Trigger system-wide detection",
            "POST",
            "api/tc-adsb/detect-all",
            200,
            data={"force_all": True}
        )
        
        # Test scheduled detection (no auth required)
        success3, response3 = self.run_test(
            "Trigger scheduled detection",
            "POST",
            "api/tc-adsb/detect-scheduled?tc_version=2025-01",
            200
        )
        
        return success1 and success2 and success3

    def test_alert_management(self):
        """Test alert status and mark-reviewed endpoints"""
        print("🚨 Testing Alert Management...")
        
        # Get alert status
        success1, response1 = self.run_test(
            "Get alert status for aircraft",
            "GET",
            f"api/tc-adsb/alert/{self.aircraft_id}",
            200
        )
        
        # Mark as reviewed
        success2, response2 = self.run_test(
            "Mark AD/SB as reviewed",
            "POST",
            f"api/tc-adsb/mark-reviewed/{self.aircraft_id}",
            200
        )
        
        # Verify alert was cleared
        success3, response3 = self.run_test(
            "Verify alert cleared after review",
            "GET",
            f"api/tc-adsb/alert/{self.aircraft_id}",
            200
        )
        
        if success3 and response3:
            alert_cleared = not response3.get('adsb_has_new_tc_items', True)
            self.log_test(
                "Alert flag cleared after review",
                alert_cleared,
                f"adsb_has_new_tc_items: {response3.get('adsb_has_new_tc_items')}"
            )
        
        return success1 and success2 and success3

    def test_audit_logging(self):
        """Test audit log endpoint"""
        print("📝 Testing Audit Logging...")
        
        # Get audit log
        success1, response1 = self.run_test(
            "Get audit log entries",
            "GET",
            "api/tc-adsb/audit-log?limit=50",
            200
        )
        
        # Get audit log filtered by aircraft
        success2, response2 = self.run_test(
            "Get audit log for specific aircraft",
            "GET",
            f"api/tc-adsb/audit-log?aircraft_id={self.aircraft_id}&limit=10",
            200
        )
        
        if success1 and response1:
            total_entries = response1.get('total', 0)
            self.log_test(
                "Audit log contains entries",
                total_entries > 0,
                f"Total entries: {total_entries}"
            )
        
        return success1 and success2

    def test_aircraft_integration(self):
        """Test aircraft model integration with AD/SB alert fields"""
        print("✈️ Testing Aircraft Integration...")
        
        # Get aircraft details to check AD/SB fields
        success, response = self.run_test(
            "Get aircraft with AD/SB fields",
            "GET",
            f"api/aircraft/{self.aircraft_id}",
            200
        )
        
        if success and response:
            # Check if AD/SB alert fields are present
            has_alert_field = 'adsb_has_new_tc_items' in response
            has_count_field = 'count_new_adsb' in response
            has_version_field = 'last_tc_adsb_version' in response
            has_reviewed_field = 'last_adsb_reviewed_at' in response
            
            self.log_test(
                "Aircraft has adsb_has_new_tc_items field",
                has_alert_field,
                f"Field present: {has_alert_field}"
            )
            
            self.log_test(
                "Aircraft has count_new_adsb field",
                has_count_field,
                f"Field present: {has_count_field}"
            )
            
            self.log_test(
                "Aircraft has last_tc_adsb_version field",
                has_version_field,
                f"Field present: {has_version_field}"
            )
            
            self.log_test(
                "Aircraft has last_adsb_reviewed_at field",
                has_reviewed_field,
                f"Field present: {has_reviewed_field}"
            )
            
            return has_alert_field and has_count_field
        
        return False

    def test_auto_clear_integration(self):
        """Test auto-clear when viewing AD/SB structured endpoint"""
        print("🔄 Testing Auto-Clear Integration...")
        
        # First trigger detection to potentially set alert
        self.run_test(
            "Trigger detection before auto-clear test",
            "POST",
            "api/tc-adsb/detect",
            200,
            data={"force_all": True}
        )
        
        # View structured AD/SB endpoint (should auto-clear)
        success, response = self.run_test(
            "View structured AD/SB (auto-clear trigger)",
            "GET",
            f"api/adsb/structured/{self.aircraft_id}",
            200
        )
        
        if success:
            # Check alert status after viewing
            success2, response2 = self.run_test(
                "Check alert status after auto-clear",
                "GET",
                f"api/tc-adsb/alert/{self.aircraft_id}",
                200
            )
            
            if success2 and response2:
                alert_cleared = not response2.get('adsb_has_new_tc_items', True)
                self.log_test(
                    "Auto-clear worked on structured view",
                    alert_cleared,
                    f"Alert cleared: {alert_cleared}"
                )
        
        return success

    def test_detection_logic(self):
        """Test detection logic - skip if same version"""
        print("🧠 Testing Detection Logic...")
        
        # Get current version
        tc_version = self.test_tc_version_endpoint()
        
        if not tc_version:
            self.log_test("Detection logic test", False, "Could not get TC version")
            return False
        
        # Run detection twice with same version
        success1, response1 = self.run_test(
            "First detection run",
            "POST",
            "api/tc-adsb/detect",
            200,
            data={"tc_adsb_version": tc_version, "force_all": False}
        )
        
        success2, response2 = self.run_test(
            "Second detection run (should skip)",
            "POST",
            "api/tc-adsb/detect",
            200,
            data={"tc_adsb_version": tc_version, "force_all": False}
        )
        
        if success1 and success2:
            # Check if second run had more skipped aircraft
            skipped1 = response1.get('aircraft_skipped', 0)
            skipped2 = response2.get('aircraft_skipped', 0)
            
            skip_logic_works = skipped2 >= skipped1
            self.log_test(
                "Skip logic works for same version",
                skip_logic_works,
                f"First run skipped: {skipped1}, Second run skipped: {skipped2}"
            )
            
            return skip_logic_works
        
        return False

    def test_error_handling(self):
        """Test error handling for invalid requests"""
        print("⚠️ Testing Error Handling...")
        
        # Test with invalid aircraft ID
        success1, response1 = self.run_test(
            "Get alert for invalid aircraft",
            "GET",
            "api/tc-adsb/alert/invalid_aircraft_id",
            404
        )
        
        # Test mark reviewed for invalid aircraft
        success2, response2 = self.run_test(
            "Mark reviewed for invalid aircraft",
            "POST",
            "api/tc-adsb/mark-reviewed/invalid_aircraft_id",
            404
        )
        
        return success1 and success2

    def test_counter_guard_implementation(self):
        """Test Counter Guard implementation for aircraft hours"""
        print("⚡ Testing Counter Guard Implementation...")
        
        # Get or create aircraft for testing
        aircraft_id, registration = self.get_aircraft_list()
        
        if not aircraft_id:
            self.log_test("Counter Guard test setup", False, "No aircraft available for testing")
            return False
        
        # Test Scenario 1: Engine > Airframe should be normalized
        print("📋 Test Scenario 1: Engine > Airframe normalization")
        
        # First set up baseline aircraft with known values
        setup_success, setup_response = self.run_test(
            "Setup aircraft with baseline hours",
            "PUT",
            f"api/aircraft/{aircraft_id}",
            200,
            data={
                "airframe_hours": 1000.0,
                "engine_hours": 900.0,
                "propeller_hours": 800.0
            }
        )
        
        if not setup_success:
            self.log_test("Counter Guard setup", False, "Failed to setup baseline aircraft hours")
            return False
        
        # Test engine hours > airframe hours (should be normalized)
        success1, response1 = self.run_test(
            "Update engine_hours > airframe_hours (1200 > 1000)",
            "PUT",
            f"api/aircraft/{aircraft_id}",
            200,
            data={"engine_hours": 1200.0}
        )
        
        if success1:
            normalized_engine = response1.get("engine_hours")
            airframe_hours = response1.get("airframe_hours")
            
            if normalized_engine == airframe_hours == 1000.0:
                self.log_test(
                    "Engine hours normalized to airframe",
                    True,
                    f"Engine hours correctly normalized from 1200 to {normalized_engine}"
                )
            else:
                self.log_test(
                    "Engine hours normalization failed",
                    False,
                    f"Expected engine_hours=1000, got {normalized_engine}"
                )
                return False
        else:
            return False
        
        # Test Scenario 2: Propeller > Airframe should be normalized
        print("📋 Test Scenario 2: Propeller > Airframe normalization")
        
        success2, response2 = self.run_test(
            "Update propeller_hours > airframe_hours (1500 > 1000)",
            "PUT",
            f"api/aircraft/{aircraft_id}",
            200,
            data={"propeller_hours": 1500.0}
        )
        
        if success2:
            normalized_propeller = response2.get("propeller_hours")
            airframe_hours = response2.get("airframe_hours")
            
            if normalized_propeller == airframe_hours == 1000.0:
                self.log_test(
                    "Propeller hours normalized to airframe",
                    True,
                    f"Propeller hours correctly normalized from 1500 to {normalized_propeller}"
                )
            else:
                self.log_test(
                    "Propeller hours normalization failed",
                    False,
                    f"Expected propeller_hours=1000, got {normalized_propeller}"
                )
                return False
        else:
            return False
        
        # Test Scenario 3: Valid update should pass
        print("📋 Test Scenario 3: Valid update (engine < airframe)")
        
        success3, response3 = self.run_test(
            "Update engine_hours < airframe_hours (500 < 1000)",
            "PUT",
            f"api/aircraft/{aircraft_id}",
            200,
            data={"engine_hours": 500.0}
        )
        
        if success3:
            engine_hours = response3.get("engine_hours")
            
            if engine_hours == 500.0:
                self.log_test(
                    "Valid engine hours update accepted",
                    True,
                    f"Engine hours correctly set to {engine_hours}"
                )
            else:
                self.log_test(
                    "Valid engine hours update failed",
                    False,
                    f"Expected engine_hours=500, got {engine_hours}"
                )
                return False
        else:
            return False
        
        # Test Scenario 4: Updating airframe should allow higher engine
        print("📋 Test Scenario 4: Airframe update allows higher engine")
        
        success4, response4 = self.run_test(
            "Update airframe_hours=2000 AND engine_hours=1800",
            "PUT",
            f"api/aircraft/{aircraft_id}",
            200,
            data={
                "airframe_hours": 2000.0,
                "engine_hours": 1800.0
            }
        )
        
        if success4:
            airframe_hours = response4.get("airframe_hours")
            engine_hours = response4.get("engine_hours")
            
            if airframe_hours == 2000.0 and engine_hours == 1800.0:
                self.log_test(
                    "Airframe and engine hours update accepted",
                    True,
                    f"Airframe: {airframe_hours}, Engine: {engine_hours}"
                )
            else:
                self.log_test(
                    "Airframe and engine hours update failed",
                    False,
                    f"Expected airframe=2000, engine=1800, got airframe={airframe_hours}, engine={engine_hours}"
                )
                return False
        else:
            return False
        
        # All scenarios passed
        self.log_test(
            "Counter Guard implementation complete",
            True,
            "All 4 test scenarios passed: normalization, valid updates, and airframe expansion"
        )
        
        return success1 and success2 and success3 and success4

    def test_ocr_scan_adsb_endpoint(self):
        """Test OCR Scan AD/SB Aggregation endpoint"""
        print("🔍 Testing OCR Scan AD/SB Aggregation Endpoint...")
        
        # Get aircraft ID first
        aircraft_id, registration = self.get_aircraft_list()
        
        if not aircraft_id:
            self.log_test("OCR Scan AD/SB test setup", False, "No aircraft available for testing")
            return False
        
        # Test the OCR scan AD/SB endpoint
        success, response = self.run_test(
            f"Get OCR scan AD/SB for aircraft {registration}",
            "GET",
            f"api/adsb/ocr-scan/{aircraft_id}",
            200
        )
        
        if not success:
            return False
        
        # Validate response structure
        required_fields = [
            "aircraft_id", "registration", "items", "total_unique_references",
            "total_ad", "total_sb", "documents_analyzed", "source", "disclaimer"
        ]
        missing_fields = [field for field in required_fields if field not in response]
        
        if missing_fields:
            self.log_test(
                "OCR scan AD/SB response structure",
                False,
                f"Missing required fields: {missing_fields}"
            )
            return False
        
        # Validate specific field values
        if response.get("aircraft_id") != aircraft_id:
            self.log_test(
                "OCR scan AD/SB aircraft_id validation",
                False,
                f"Expected aircraft_id={aircraft_id}, got {response.get('aircraft_id')}"
            )
            return False
        
        if response.get("source") != "scanned_documents":
            self.log_test(
                "OCR scan AD/SB source validation",
                False,
                f"Expected source='scanned_documents', got {response.get('source')}"
            )
            return False
        
        # Validate items structure (if any items exist)
        items = response.get("items", [])
        if not isinstance(items, list):
            self.log_test(
                "OCR scan AD/SB items format",
                False,
                f"Items should be an array, got {type(items)}"
            )
            return False
        
        # Validate each item structure (if items exist)
        for i, item in enumerate(items):
            required_item_fields = [
                "reference", "type", "occurrence_count", "source",
                "first_seen_date", "last_seen_date", "scan_ids"
            ]
            missing_item_fields = [field for field in required_item_fields if field not in item]
            
            if missing_item_fields:
                self.log_test(
                    f"OCR scan AD/SB item {i} structure",
                    False,
                    f"Missing item fields: {missing_item_fields}"
                )
                return False
            
            # Validate item type
            if item.get("type") not in ["AD", "SB"]:
                self.log_test(
                    f"OCR scan AD/SB item {i} type",
                    False,
                    f"Invalid type: {item.get('type')}, expected 'AD' or 'SB'"
                )
                return False
            
            # Validate occurrence_count is positive integer
            if not isinstance(item.get("occurrence_count"), int) or item.get("occurrence_count") < 0:
                self.log_test(
                    f"OCR scan AD/SB item {i} occurrence_count",
                    False,
                    f"Invalid occurrence_count: {item.get('occurrence_count')}"
                )
                return False
        
        # Validate counts match items
        total_ad = sum(1 for item in items if item.get("type") == "AD")
        total_sb = sum(1 for item in items if item.get("type") == "SB")
        
        if response.get("total_ad") != total_ad:
            self.log_test(
                "OCR scan AD/SB total_ad count",
                False,
                f"Expected total_ad={total_ad}, got {response.get('total_ad')}"
            )
            return False
        
        if response.get("total_sb") != total_sb:
            self.log_test(
                "OCR scan AD/SB total_sb count",
                False,
                f"Expected total_sb={total_sb}, got {response.get('total_sb')}"
            )
            return False
        
        if response.get("total_unique_references") != len(items):
            self.log_test(
                "OCR scan AD/SB total_unique_references count",
                False,
                f"Expected total_unique_references={len(items)}, got {response.get('total_unique_references')}"
            )
            return False
        
        # Validate disclaimer exists
        if not response.get("disclaimer"):
            self.log_test(
                "OCR scan AD/SB disclaimer",
                False,
                "Disclaimer field is missing or empty"
            )
            return False
        
        # Log successful validation
        self.log_test(
            "OCR scan AD/SB endpoint validation",
            True,
            f"All validations passed. Items: {len(items)}, AD: {total_ad}, SB: {total_sb}, Documents: {response.get('documents_analyzed', 0)}"
        )
        
        # Test with invalid aircraft ID
        success_invalid, response_invalid = self.run_test(
            "Get OCR scan AD/SB for invalid aircraft",
            "GET",
            "api/adsb/ocr-scan/invalid_aircraft_id",
            404
        )
        
        if not success_invalid:
            self.log_test(
                "OCR scan AD/SB invalid aircraft test",
                False,
                "Expected 404 for invalid aircraft_id"
            )
            return False
        
        return success and success_invalid

    def run_all_tests(self):
        """Run all tests in sequence"""
        print("🚀 Starting AeroLogix AI Backend Tests")
        print("=" * 60)
        
        # Authentication is required for most endpoints
        if not self.test_authentication():
            print("❌ Authentication failed. Cannot proceed with other tests.")
            return False
        
        # Run all test suites
        test_suites = [
            ("Counter Guard Implementation", self.test_counter_guard_implementation),
            ("Critical Mentions Endpoint", self.test_critical_mentions_endpoint),
            ("TC Version Endpoint", self.test_tc_version_endpoint),
            ("Detection Endpoints", self.test_detection_endpoints),
            ("Alert Management", self.test_alert_management),
            ("Audit Logging", self.test_audit_logging),
            ("Aircraft Integration", self.test_aircraft_integration),
            ("Auto-Clear Integration", self.test_auto_clear_integration),
            ("Detection Logic", self.test_detection_logic),
            ("Error Handling", self.test_error_handling),
        ]
        
        suite_results = []
        for suite_name, test_func in test_suites:
            print(f"\n📋 Running {suite_name} Tests...")
            try:
                result = test_func()
                suite_results.append((suite_name, result))
                status = "✅ PASS" if result else "❌ FAIL"
                print(f"{status} | {suite_name} Suite")
            except Exception as e:
                print(f"❌ FAIL | {suite_name} Suite - Error: {e}")
                suite_results.append((suite_name, False))
        
        # Print final summary
        print("\n" + "=" * 60)
        print("📊 TEST SUMMARY")
        print("=" * 60)
        
        passed_suites = sum(1 for _, result in suite_results if result)
        total_suites = len(suite_results)
        
        print(f"Test Suites: {passed_suites}/{total_suites} passed")
        print(f"Individual Tests: {self.tests_passed}/{self.tests_run} passed")
        print(f"Success Rate: {(self.tests_passed/self.tests_run)*100:.1f}%")
        
        print("\nSuite Results:")
        for suite_name, result in suite_results:
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"  {status} {suite_name}")
        
        # Return overall success
        return self.tests_passed == self.tests_run

def main():
    """Main test runner"""
    tester = AeroLogixBackendTester()
    
    try:
        success = tester.run_all_tests()
        
        # Save detailed test results
        with open('/app/test_reports/tc_adsb_backend_test_results.json', 'w') as f:
            json.dump({
                'summary': {
                    'total_tests': tester.tests_run,
                    'passed_tests': tester.tests_passed,
                    'success_rate': (tester.tests_passed/tester.tests_run)*100 if tester.tests_run > 0 else 0,
                    'timestamp': datetime.now().isoformat()
                },
                'detailed_results': tester.test_results
            }, f, indent=2)
        
        return 0 if success else 1
        
    except KeyboardInterrupt:
        print("\n⚠️ Tests interrupted by user")
        return 1
    except Exception as e:
        print(f"\n❌ Test runner failed: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())