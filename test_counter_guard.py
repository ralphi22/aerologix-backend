#!/usr/bin/env python3
"""
Counter Guard Test for AeroLogix AI Backend

Tests the Counter Guard implementation for aircraft hours normalization.
"""

import requests
import sys
import json
from datetime import datetime

class CounterGuardTester:
    def __init__(self, base_url="http://localhost:8001"):
        self.base_url = base_url
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []
        
    def log_test(self, name: str, success: bool, details: str = "", response_data=None):
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
                 data=None, headers=None):
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
        """Login with test user"""
        print("🔐 Authenticating...")
        
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
        else:
            print(f"❌ Authentication failed: {response}")
            return False

    def get_or_create_aircraft(self):
        """Get existing aircraft or create one for testing"""
        print("✈️ Getting aircraft for testing...")
        
        # Try to get existing aircraft
        success, response = self.run_test(
            "Get aircraft list",
            "GET",
            "api/aircraft",
            200
        )
        
        if success and isinstance(response, list) and len(response) > 0:
            aircraft_id = response[0].get('_id') or response[0].get('id')
            registration = response[0].get('registration', 'Unknown')
            print(f"✅ Using existing aircraft: {registration} (ID: {aircraft_id})")
            return aircraft_id
        
        # Create new aircraft if none exists
        print("ℹ️ No aircraft found, creating test aircraft...")
        success, response = self.run_test(
            "Create test aircraft",
            "POST",
            "api/aircraft",
            201,
            data={
                "registration": "C-GUARD",
                "manufacturer": "Cessna",
                "model": "172",
                "year": 2020,
                "serial_number": "GUARD123456",
                "airframe_hours": 100.0,
                "engine_hours": 90.0,
                "propeller_hours": 80.0
            }
        )
        
        if success:
            aircraft_id = response.get('_id') or response.get('id')
            print(f"✅ Test aircraft created: C-GUARD (ID: {aircraft_id})")
            return aircraft_id
        else:
            print(f"❌ Failed to create test aircraft: {response}")
            return None

    def test_counter_guard_scenarios(self):
        """Test all Counter Guard scenarios"""
        print("⚡ Testing Counter Guard Implementation...")
        
        aircraft_id = self.get_or_create_aircraft()
        if not aircraft_id:
            return False
        
        # Test Scenario 1: Engine > Airframe should be normalized
        print("\n📋 Test Scenario 1: Engine > Airframe normalization")
        
        # Setup baseline
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
            return False
        
        # Test engine > airframe (should normalize)
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
            
            if normalized_engine == 1000.0 and airframe_hours == 1000.0:
                print(f"✅ Engine hours correctly normalized from 1200 to {normalized_engine}")
            else:
                print(f"❌ Engine normalization failed: expected 1000, got {normalized_engine}")
                return False
        else:
            return False
        
        # Test Scenario 2: Propeller > Airframe should be normalized
        print("\n📋 Test Scenario 2: Propeller > Airframe normalization")
        
        success2, response2 = self.run_test(
            "Update propeller_hours > airframe_hours (1500 > 1000)",
            "PUT",
            f"api/aircraft/{aircraft_id}",
            200,
            data={"propeller_hours": 1500.0}
        )
        
        if success2:
            normalized_propeller = response2.get("propeller_hours")
            
            if normalized_propeller == 1000.0:
                print(f"✅ Propeller hours correctly normalized from 1500 to {normalized_propeller}")
            else:
                print(f"❌ Propeller normalization failed: expected 1000, got {normalized_propeller}")
                return False
        else:
            return False
        
        # Test Scenario 3: Valid update should pass
        print("\n📋 Test Scenario 3: Valid update (engine < airframe)")
        
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
                print(f"✅ Valid engine hours update accepted: {engine_hours}")
            else:
                print(f"❌ Valid update failed: expected 500, got {engine_hours}")
                return False
        else:
            return False
        
        # Test Scenario 4: Updating airframe should allow higher engine
        print("\n📋 Test Scenario 4: Airframe update allows higher engine")
        
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
                print(f"✅ Airframe and engine update accepted: Airframe={airframe_hours}, Engine={engine_hours}")
            else:
                print(f"❌ Airframe update failed: expected airframe=2000, engine=1800, got airframe={airframe_hours}, engine={engine_hours}")
                return False
        else:
            return False
        
        return success1 and success2 and success3 and success4

    def check_server_logs(self):
        """Check server logs for COUNTER_GUARD warnings"""
        print("\n📋 Checking server logs for COUNTER_GUARD warnings...")
        
        try:
            import subprocess
            result = subprocess.run(
                ["tail", "-n", "50", "/var/log/supervisor/backend.err.log"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                log_content = result.stdout
                counter_guard_logs = [line for line in log_content.split('\n') if '[COUNTER_GUARD]' in line]
                
                if counter_guard_logs:
                    print(f"✅ Found {len(counter_guard_logs)} COUNTER_GUARD log entries:")
                    for log in counter_guard_logs[-3:]:  # Show last 3 entries
                        print(f"    {log}")
                    return True
                else:
                    print("ℹ️ No COUNTER_GUARD log entries found in recent logs")
                    return True  # Not a failure, just informational
            else:
                print("⚠️ Could not read server logs")
                return True  # Not a failure
                
        except Exception as e:
            print(f"⚠️ Error checking logs: {e}")
            return True  # Not a failure

    def run_all_tests(self):
        """Run all Counter Guard tests"""
        print("🚀 Starting Counter Guard Tests")
        print("=" * 60)
        
        if not self.authenticate():
            return False
        
        success = self.test_counter_guard_scenarios()
        
        # Check logs regardless of test results
        self.check_server_logs()
        
        # Print summary
        print("\n" + "=" * 60)
        print("📊 COUNTER GUARD TEST SUMMARY")
        print("=" * 60)
        print(f"Tests: {self.tests_passed}/{self.tests_run} passed")
        print(f"Success Rate: {(self.tests_passed/self.tests_run)*100:.1f}%")
        
        if success:
            print("✅ All Counter Guard scenarios passed!")
        else:
            print("❌ Some Counter Guard tests failed")
        
        return success

def main():
    """Main test runner"""
    tester = CounterGuardTester()
    
    try:
        success = tester.run_all_tests()
        return 0 if success else 1
        
    except KeyboardInterrupt:
        print("\n⚠️ Tests interrupted by user")
        return 1
    except Exception as e:
        print(f"\n❌ Test runner failed: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())