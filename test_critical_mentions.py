#!/usr/bin/env python3
"""
Critical Mentions Endpoint Test

Tests the new GET /api/limitations/{aircraft_id}/critical-mentions endpoint
"""

import requests
import sys
import json
from datetime import datetime
from typing import Dict, Any, Optional

class CriticalMentionsTest:
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

    def authenticate(self):
        """Authenticate and get token"""
        print("🔐 Authenticating...")
        
        # Try to login first
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
            print(f"✅ Authentication successful")
            return True
        
        # If login failed, try to create user
        print("ℹ️ Login failed, creating test user...")
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
        
        print(f"❌ Authentication failed")
        return False

    def get_or_create_aircraft(self):
        """Get existing aircraft or create one for testing"""
        print("✈️ Getting aircraft...")
        
        # Try to get existing aircraft
        success, response = self.run_test(
            "Get aircraft list",
            "GET",
            "api/aircraft",
            200
        )
        
        aircraft_id = None
        registration = None
        
        if success:
            # Handle different response formats
            aircraft_list = []
            if isinstance(response, list):
                aircraft_list = response
            elif isinstance(response, dict) and 'aircraft' in response:
                aircraft_list = response['aircraft']
            elif isinstance(response, dict) and response.get('data'):
                aircraft_list = response['data']
            
            if aircraft_list and len(aircraft_list) > 0:
                aircraft_id = aircraft_list[0].get('_id') or aircraft_list[0].get('id')
                registration = aircraft_list[0].get('registration', 'Unknown')
                print(f"✅ Found existing aircraft: {registration} (ID: {aircraft_id})")
                return aircraft_id, registration
        
        # No aircraft found, create one
        print("ℹ️ No aircraft found, creating test aircraft...")
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
            registration = response.get('registration', 'C-GTEST')
            print(f"✅ Created test aircraft: {registration} (ID: {aircraft_id})")
            return aircraft_id, registration
        
        print(f"❌ Failed to get or create aircraft")
        return None, None

    def test_critical_mentions_endpoint(self, aircraft_id: str, registration: str):
        """Test the critical-mentions endpoint"""
        print(f"🔍 Testing Critical Mentions Endpoint for {registration}...")
        
        # Test the critical-mentions endpoint
        success, response = self.run_test(
            f"Get critical mentions for {registration}",
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
                "Response structure validation",
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
                "Critical mentions categories validation",
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
                "Summary structure validation",
                False,
                f"Missing summary fields: {missing_summary_fields}"
            )
            return False
        
        # Validate that all categories are arrays
        for category in expected_categories:
            if not isinstance(critical_mentions[category], list):
                self.log_test(
                    f"Category {category} format validation",
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
                "Count validation",
                False,
                f"Summary counts don't match actual data. Expected: {actual_counts}, total: {actual_total}, Got: {summary}"
            )
            return False
        
        # Validate aircraft_id matches
        if response["aircraft_id"] != aircraft_id:
            self.log_test(
                "Aircraft ID validation",
                False,
                f"Response aircraft_id {response['aircraft_id']} doesn't match requested {aircraft_id}"
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

    def run_test_suite(self):
        """Run the complete test suite"""
        print("🚀 Starting Critical Mentions Endpoint Test")
        print("=" * 60)
        
        # Step 1: Authenticate
        if not self.authenticate():
            print("❌ Authentication failed. Cannot proceed.")
            return False
        
        # Step 2: Get or create aircraft
        aircraft_id, registration = self.get_or_create_aircraft()
        if not aircraft_id:
            print("❌ Failed to get aircraft. Cannot proceed.")
            return False
        
        # Step 3: Test critical mentions endpoint
        success = self.test_critical_mentions_endpoint(aircraft_id, registration)
        
        # Print summary
        print("\n" + "=" * 60)
        print("📊 TEST SUMMARY")
        print("=" * 60)
        print(f"Tests Run: {self.tests_run}")
        print(f"Tests Passed: {self.tests_passed}")
        print(f"Success Rate: {(self.tests_passed/self.tests_run)*100:.1f}%")
        
        if success:
            print("\n✅ Critical Mentions Endpoint Test PASSED")
        else:
            print("\n❌ Critical Mentions Endpoint Test FAILED")
        
        return success

def main():
    """Main test runner"""
    tester = CriticalMentionsTest()
    
    try:
        success = tester.run_test_suite()
        return 0 if success else 1
        
    except KeyboardInterrupt:
        print("\n⚠️ Test interrupted by user")
        return 1
    except Exception as e:
        print(f"\n❌ Test runner failed: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())