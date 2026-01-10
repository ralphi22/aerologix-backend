"""
Test Critical Components API - OCR → Installed Components Feature

Tests for:
- GET /api/components/critical/{aircraft_id} - Returns components with time calculations
- POST /api/components/critical/{aircraft_id}/reprocess - Reprocess OCR history
- MongoDB index verification
- Status calculation (OK, WARNING, CRITICAL, OVERDUE)
"""

import pytest
import requests
import os
from datetime import datetime, timedelta

# Use localhost since we're testing backend directly
BASE_URL = "http://localhost:8001"

# Test credentials
TEST_EMAIL = "test@aerologix.com"
TEST_PASSWORD = "testpassword123"


class TestCriticalComponentsAPI:
    """Tests for Critical Components API endpoints"""
    
    @pytest.fixture(scope="class")
    def auth_token(self):
        """Get authentication token - uses OAuth2 form data"""
        # First try to login
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            data={
                "username": TEST_EMAIL,
                "password": TEST_PASSWORD
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if response.status_code == 200:
            return response.json().get("access_token")
        
        # If login fails, try to signup first
        signup_response = requests.post(
            f"{BASE_URL}/api/auth/signup",
            json={
                "email": TEST_EMAIL,
                "password": TEST_PASSWORD,
                "name": "Test User"
            }
        )
        
        if signup_response.status_code == 200:
            return signup_response.json().get("access_token")
        
        # Try login again after signup
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            data={
                "username": TEST_EMAIL,
                "password": TEST_PASSWORD
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if response.status_code == 200:
            return response.json().get("access_token")
        
        pytest.skip(f"Authentication failed: {response.text}")
    
    @pytest.fixture(scope="class")
    def api_client(self, auth_token):
        """Create authenticated session"""
        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json"
        })
        return session
    
    @pytest.fixture(scope="class")
    def test_aircraft(self, api_client):
        """Create a test aircraft for component testing"""
        aircraft_data = {
            "registration": "TEST-COMP-001",
            "make": "Cessna",
            "model": "172S",
            "year": 2020,
            "serial_number": "TEST-SN-COMP-001",
            "airframe_hours": 1500.0
        }
        
        response = api_client.post(f"{BASE_URL}/api/aircraft", json=aircraft_data)
        
        if response.status_code == 201:
            data = response.json()
            aircraft_id = data.get("id") or data.get("_id")
            yield {"id": aircraft_id, **aircraft_data}
            # Cleanup - delete aircraft after tests
            api_client.delete(f"{BASE_URL}/api/aircraft/{aircraft_id}")
        else:
            # Try to find existing aircraft
            list_response = api_client.get(f"{BASE_URL}/api/aircraft")
            if list_response.status_code == 200:
                aircrafts = list_response.json()
                if aircrafts:
                    yield {"id": aircrafts[0].get("id") or aircrafts[0].get("_id"), **aircrafts[0]}
                    return
            pytest.skip(f"Could not create test aircraft: {response.text}")
    
    # ============================================================
    # Test: GET /api/components/critical/{aircraft_id} - Empty list
    # ============================================================
    
    def test_get_critical_components_empty_list(self, api_client, test_aircraft):
        """Test GET returns empty list for aircraft with no components"""
        aircraft_id = test_aircraft["id"]
        
        response = api_client.get(f"{BASE_URL}/api/components/critical/{aircraft_id}")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Verify response structure
        assert "aircraft_id" in data, "Response should contain aircraft_id"
        assert "components" in data, "Response should contain components list"
        assert "current_airframe_hours" in data, "Response should contain current_airframe_hours"
        
        # Components should be a list (may be empty or have data)
        assert isinstance(data["components"], list), "Components should be a list"
        
        print(f"✓ GET /api/components/critical/{aircraft_id} returns valid response")
        print(f"  - Aircraft ID: {data['aircraft_id']}")
        print(f"  - Components count: {len(data['components'])}")
        print(f"  - Current airframe hours: {data['current_airframe_hours']}")
    
    # ============================================================
    # Test: GET /api/components/critical/{aircraft_id} - 404 for invalid aircraft
    # ============================================================
    
    def test_get_critical_components_invalid_aircraft(self, api_client):
        """Test GET returns 404 for non-existent aircraft"""
        fake_aircraft_id = "nonexistent_aircraft_12345"
        
        response = api_client.get(f"{BASE_URL}/api/components/critical/{fake_aircraft_id}")
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
        
        print(f"✓ GET /api/components/critical/{fake_aircraft_id} returns 404 for invalid aircraft")
    
    # ============================================================
    # Test: POST /api/components/critical/{aircraft_id}/reprocess
    # ============================================================
    
    def test_reprocess_components_endpoint_exists(self, api_client, test_aircraft):
        """Test POST /reprocess endpoint exists and works"""
        aircraft_id = test_aircraft["id"]
        
        response = api_client.post(f"{BASE_URL}/api/components/critical/{aircraft_id}/reprocess")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Verify response structure
        assert "message" in data, "Response should contain message"
        assert "components_created" in data, "Response should contain components_created count"
        
        print(f"✓ POST /api/components/critical/{aircraft_id}/reprocess works")
        print(f"  - Message: {data['message']}")
        print(f"  - Components created: {data['components_created']}")
    
    def test_reprocess_invalid_aircraft(self, api_client):
        """Test POST /reprocess returns 404 for non-existent aircraft"""
        fake_aircraft_id = "nonexistent_aircraft_12345"
        
        response = api_client.post(f"{BASE_URL}/api/components/critical/{fake_aircraft_id}/reprocess")
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
        
        print(f"✓ POST /api/components/critical/{fake_aircraft_id}/reprocess returns 404 for invalid aircraft")
    
    # ============================================================
    # Test: Unauthorized access
    # ============================================================
    
    def test_unauthorized_access(self):
        """Test endpoints require authentication"""
        # Test without auth token
        response = requests.get(f"{BASE_URL}/api/components/critical/test123")
        
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        
        print("✓ GET /api/components/critical requires authentication")
        
        # Test reprocess without auth
        response = requests.post(f"{BASE_URL}/api/components/critical/test123/reprocess")
        
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        
        print("✓ POST /api/components/critical/reprocess requires authentication")


class TestComponentCalculations:
    """Tests for component time calculations and status"""
    
    @pytest.fixture(scope="class")
    def auth_token(self):
        """Get authentication token"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            data={
                "username": TEST_EMAIL,
                "password": TEST_PASSWORD
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if response.status_code == 200:
            return response.json().get("access_token")
        
        pytest.skip("Authentication failed")
    
    @pytest.fixture(scope="class")
    def api_client(self, auth_token):
        """Create authenticated session"""
        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json"
        })
        return session
    
    @pytest.fixture(scope="class")
    def aircraft_with_components(self, api_client):
        """Create aircraft and insert test components directly into MongoDB"""
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        
        # Create aircraft with known hours
        aircraft_data = {
            "registration": "TEST-CALC-001",
            "make": "Piper",
            "model": "PA-28",
            "year": 2015,
            "serial_number": "TEST-SN-CALC-001",
            "airframe_hours": 2000.0  # Current hours
        }
        
        response = api_client.post(f"{BASE_URL}/api/aircraft", json=aircraft_data)
        
        if response.status_code != 201:
            # Try to find existing
            list_response = api_client.get(f"{BASE_URL}/api/aircraft")
            if list_response.status_code == 200:
                aircrafts = list_response.json()
                for ac in aircrafts:
                    if ac.get("registration") == "TEST-CALC-001":
                        aircraft_id = ac.get("id") or ac.get("_id")
                        break
                else:
                    pytest.skip(f"Could not create test aircraft: {response.text}")
            else:
                pytest.skip(f"Could not create test aircraft: {response.text}")
        else:
            data = response.json()
            aircraft_id = data.get("id") or data.get("_id")
        
        # Get user_id from token
        me_response = api_client.get(f"{BASE_URL}/api/auth/me")
        user_id = me_response.json().get("id")
        
        # Insert test components directly into MongoDB
        async def insert_components():
            client = AsyncIOMotorClient("mongodb://localhost:27017")
            db = client["aerologix"]
            
            now = datetime.utcnow()
            
            # Test components with different statuses
            test_components = [
                {
                    "aircraft_id": aircraft_id,
                    "user_id": user_id,
                    "component_type": "ENGINE",
                    "part_no": "IO-360-A1A",
                    "description": "Test engine - OK status",
                    "installed_at_hours": 500.0,  # 1500h since install, TBO 2000 = 500 remaining = OK
                    "installed_date": now - timedelta(days=365),
                    "tbo": 2000,
                    "source_report_id": "test_scan_001",
                    "confidence": 0.95,
                    "created_at": now,
                    "updated_at": now
                },
                {
                    "aircraft_id": aircraft_id,
                    "user_id": user_id,
                    "component_type": "MAGNETO",
                    "part_no": "SLICK-4370",
                    "description": "Test magneto - WARNING status",
                    "installed_at_hours": 1450.0,  # 550h since install, TBO 500 = -50 remaining = OVERDUE
                    "installed_date": now - timedelta(days=180),
                    "tbo": 500,
                    "source_report_id": "test_scan_002",
                    "confidence": 0.90,
                    "created_at": now,
                    "updated_at": now
                },
                {
                    "aircraft_id": aircraft_id,
                    "user_id": user_id,
                    "component_type": "VACUUM_PUMP",
                    "part_no": "RAPCO-RA215CC",
                    "description": "Test vacuum pump - CRITICAL status",
                    "installed_at_hours": 1550.0,  # 450h since install, TBO 500 = 50 remaining = CRITICAL
                    "installed_date": now - timedelta(days=90),
                    "tbo": 500,
                    "source_report_id": "test_scan_003",
                    "confidence": 0.85,
                    "created_at": now,
                    "updated_at": now
                },
                {
                    "aircraft_id": aircraft_id,
                    "user_id": user_id,
                    "component_type": "PROP",
                    "part_no": "HC-C2YK-1BF",
                    "description": "Test propeller - WARNING status",
                    "installed_at_hours": 1920.0,  # 80h since install, TBO 2400 = 2320 remaining = OK
                    "installed_date": now - timedelta(days=30),
                    "tbo": 2400,
                    "source_report_id": "test_scan_004",
                    "confidence": 0.92,
                    "created_at": now,
                    "updated_at": now
                }
            ]
            
            # Delete existing test components first
            await db.installed_components.delete_many({"aircraft_id": aircraft_id})
            
            # Insert new test components
            for comp in test_components:
                try:
                    await db.installed_components.update_one(
                        {
                            "aircraft_id": comp["aircraft_id"],
                            "component_type": comp["component_type"],
                            "part_no": comp["part_no"],
                            "installed_at_hours": comp["installed_at_hours"]
                        },
                        {"$set": comp},
                        upsert=True
                    )
                except Exception as e:
                    print(f"Error inserting component: {e}")
            
            client.close()
            return test_components
        
        components = asyncio.run(insert_components())
        
        yield {
            "aircraft_id": aircraft_id,
            "user_id": user_id,
            "airframe_hours": 2000.0,
            "components": components
        }
        
        # Cleanup
        async def cleanup():
            client = AsyncIOMotorClient("mongodb://localhost:27017")
            db = client["aerologix"]
            await db.installed_components.delete_many({"aircraft_id": aircraft_id})
            client.close()
        
        asyncio.run(cleanup())
        api_client.delete(f"{BASE_URL}/api/aircraft/{aircraft_id}")
    
    def test_time_since_install_calculation(self, api_client, aircraft_with_components):
        """Test time_since_install = current_airframe_hours - installed_at_hours"""
        aircraft_id = aircraft_with_components["aircraft_id"]
        current_hours = aircraft_with_components["airframe_hours"]
        
        response = api_client.get(f"{BASE_URL}/api/components/critical/{aircraft_id}")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        components = data["components"]
        
        assert len(components) > 0, "Should have test components"
        
        for comp in components:
            installed_at = comp["installed_at_hours"]
            time_since = comp["time_since_install"]
            expected_time_since = current_hours - installed_at
            
            # Allow small floating point differences
            assert abs(time_since - expected_time_since) < 0.2, \
                f"time_since_install mismatch for {comp['part_no']}: expected {expected_time_since}, got {time_since}"
            
            print(f"✓ {comp['component_type']} ({comp['part_no']}): time_since_install = {time_since}h (correct)")
    
    def test_remaining_hours_calculation(self, api_client, aircraft_with_components):
        """Test remaining = TBO - time_since_install"""
        aircraft_id = aircraft_with_components["aircraft_id"]
        
        response = api_client.get(f"{BASE_URL}/api/components/critical/{aircraft_id}")
        
        assert response.status_code == 200
        
        data = response.json()
        components = data["components"]
        
        for comp in components:
            if comp["tbo"] is not None:
                time_since = comp["time_since_install"]
                tbo = comp["tbo"]
                remaining = comp["remaining"]
                expected_remaining = max(0, tbo - time_since)
                
                # Allow small floating point differences
                assert abs(remaining - expected_remaining) < 0.2, \
                    f"remaining mismatch for {comp['part_no']}: expected {expected_remaining}, got {remaining}"
                
                print(f"✓ {comp['component_type']} ({comp['part_no']}): remaining = {remaining}h (TBO={tbo}, correct)")
    
    def test_status_calculation(self, api_client, aircraft_with_components):
        """Test status: OK (>100h), WARNING (<100h), CRITICAL (<50h), OVERDUE (<=0)"""
        aircraft_id = aircraft_with_components["aircraft_id"]
        
        response = api_client.get(f"{BASE_URL}/api/components/critical/{aircraft_id}")
        
        assert response.status_code == 200
        
        data = response.json()
        components = data["components"]
        
        status_counts = {"OK": 0, "WARNING": 0, "CRITICAL": 0, "OVERDUE": 0, "ON_CONDITION": 0, "UNKNOWN": 0}
        
        for comp in components:
            status = comp["status"]
            remaining = comp.get("remaining")
            
            # Verify status logic
            if remaining is not None:
                if remaining <= 0:
                    expected_status = "OVERDUE"
                elif remaining < 50:
                    expected_status = "CRITICAL"
                elif remaining < 100:
                    expected_status = "WARNING"
                else:
                    expected_status = "OK"
                
                assert status == expected_status, \
                    f"Status mismatch for {comp['part_no']}: expected {expected_status}, got {status} (remaining={remaining})"
            
            status_counts[status] = status_counts.get(status, 0) + 1
            print(f"✓ {comp['component_type']} ({comp['part_no']}): status = {status} (remaining={remaining}h)")
        
        print(f"\nStatus distribution: {status_counts}")


class TestMongoDBIndex:
    """Test MongoDB index exists"""
    
    def test_aircraft_component_unique_index_exists(self):
        """Verify the unique index exists on installed_components collection"""
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        
        async def check_index():
            client = AsyncIOMotorClient("mongodb://localhost:27017")
            db = client["aerologix"]
            
            indexes = await db.installed_components.index_information()
            client.close()
            
            return indexes
        
        indexes = asyncio.run(check_index())
        
        # Check for the unique index
        assert "aircraft_component_unique" in indexes, \
            f"Index 'aircraft_component_unique' not found. Available indexes: {list(indexes.keys())}"
        
        index_info = indexes["aircraft_component_unique"]
        
        # Verify it's unique
        assert index_info.get("unique") == True, "Index should be unique"
        
        # Verify key fields
        expected_keys = [("aircraft_id", 1), ("component_type", 1), ("part_no", 1), ("installed_at_hours", 1)]
        assert index_info["key"] == expected_keys, \
            f"Index keys mismatch: expected {expected_keys}, got {index_info['key']}"
        
        print("✓ MongoDB index 'aircraft_component_unique' exists with correct configuration")
        print(f"  - Unique: {index_info.get('unique')}")
        print(f"  - Keys: {index_info['key']}")


class TestResponseStructure:
    """Test API response structure and data types"""
    
    @pytest.fixture(scope="class")
    def auth_token(self):
        """Get authentication token"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            data={
                "username": TEST_EMAIL,
                "password": TEST_PASSWORD
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if response.status_code == 200:
            return response.json().get("access_token")
        
        pytest.skip("Authentication failed")
    
    @pytest.fixture(scope="class")
    def api_client(self, auth_token):
        """Create authenticated session"""
        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json"
        })
        return session
    
    @pytest.fixture(scope="class")
    def test_aircraft(self, api_client):
        """Get or create test aircraft"""
        list_response = api_client.get(f"{BASE_URL}/api/aircraft")
        if list_response.status_code == 200:
            aircrafts = list_response.json()
            if aircrafts:
                return {"id": aircrafts[0].get("id") or aircrafts[0].get("_id")}
        
        # Create new aircraft
        aircraft_data = {
            "registration": "TEST-STRUCT-001",
            "make": "Cessna",
            "model": "182",
            "year": 2018,
            "serial_number": "TEST-SN-STRUCT-001",
            "airframe_hours": 800.0
        }
        
        response = api_client.post(f"{BASE_URL}/api/aircraft", json=aircraft_data)
        if response.status_code == 201:
            data = response.json()
            return {"id": data.get("id") or data.get("_id")}
        
        pytest.skip("Could not get test aircraft")
    
    def test_critical_components_response_structure(self, api_client, test_aircraft):
        """Test response has correct structure"""
        aircraft_id = test_aircraft["id"]
        
        response = api_client.get(f"{BASE_URL}/api/components/critical/{aircraft_id}")
        
        assert response.status_code == 200
        
        data = response.json()
        
        # Required top-level fields
        required_fields = ["aircraft_id", "current_airframe_hours", "components"]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
        
        # Type checks
        assert isinstance(data["aircraft_id"], str), "aircraft_id should be string"
        assert isinstance(data["current_airframe_hours"], (int, float)), "current_airframe_hours should be numeric"
        assert isinstance(data["components"], list), "components should be list"
        
        # Optional fields
        if "registration" in data:
            assert data["registration"] is None or isinstance(data["registration"], str)
        
        if "last_updated" in data:
            assert data["last_updated"] is None or isinstance(data["last_updated"], str)
        
        print("✓ Response structure is correct")
        print(f"  - aircraft_id: {type(data['aircraft_id']).__name__}")
        print(f"  - current_airframe_hours: {type(data['current_airframe_hours']).__name__}")
        print(f"  - components: {type(data['components']).__name__} (length={len(data['components'])})")
    
    def test_component_item_structure(self, api_client, test_aircraft):
        """Test individual component items have correct structure"""
        aircraft_id = test_aircraft["id"]
        
        # First insert a test component
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        
        me_response = api_client.get(f"{BASE_URL}/api/auth/me")
        user_id = me_response.json().get("id")
        
        async def insert_test_component():
            client = AsyncIOMotorClient("mongodb://localhost:27017")
            db = client["aerologix"]
            
            now = datetime.utcnow()
            
            await db.installed_components.update_one(
                {
                    "aircraft_id": aircraft_id,
                    "component_type": "ENGINE",
                    "part_no": "TEST-STRUCT-ENGINE",
                    "installed_at_hours": 100.0
                },
                {"$set": {
                    "aircraft_id": aircraft_id,
                    "user_id": user_id,
                    "component_type": "ENGINE",
                    "part_no": "TEST-STRUCT-ENGINE",
                    "description": "Test engine for structure test",
                    "installed_at_hours": 100.0,
                    "installed_date": now,
                    "tbo": 2000,
                    "source_report_id": "test_struct_scan",
                    "confidence": 0.9,
                    "created_at": now,
                    "updated_at": now
                }},
                upsert=True
            )
            
            client.close()
        
        asyncio.run(insert_test_component())
        
        response = api_client.get(f"{BASE_URL}/api/components/critical/{aircraft_id}")
        
        assert response.status_code == 200
        
        data = response.json()
        components = data["components"]
        
        if len(components) > 0:
            comp = components[0]
            
            # Required component fields
            required_comp_fields = [
                "component_type", "part_no", "installed_at_hours",
                "current_airframe_hours", "time_since_install", "status", "confidence"
            ]
            
            for field in required_comp_fields:
                assert field in comp, f"Missing required component field: {field}"
            
            # Type checks
            assert isinstance(comp["component_type"], str), "component_type should be string"
            assert isinstance(comp["part_no"], str), "part_no should be string"
            assert isinstance(comp["installed_at_hours"], (int, float)), "installed_at_hours should be numeric"
            assert isinstance(comp["current_airframe_hours"], (int, float)), "current_airframe_hours should be numeric"
            assert isinstance(comp["time_since_install"], (int, float)), "time_since_install should be numeric"
            assert isinstance(comp["status"], str), "status should be string"
            assert isinstance(comp["confidence"], (int, float)), "confidence should be numeric"
            
            # Valid status values
            valid_statuses = ["OK", "WARNING", "CRITICAL", "OVERDUE", "ON_CONDITION", "UNKNOWN"]
            assert comp["status"] in valid_statuses, f"Invalid status: {comp['status']}"
            
            # Valid component types
            valid_types = ["ENGINE", "PROP", "MAGNETO", "VACUUM_PUMP", "LLP", "STARTER", "ALTERNATOR", "TURBO", "FUEL_PUMP", "OIL_PUMP"]
            assert comp["component_type"] in valid_types, f"Invalid component_type: {comp['component_type']}"
            
            print("✓ Component item structure is correct")
            print(f"  - component_type: {comp['component_type']}")
            print(f"  - part_no: {comp['part_no']}")
            print(f"  - status: {comp['status']}")
        
        # Cleanup
        async def cleanup():
            client = AsyncIOMotorClient("mongodb://localhost:27017")
            db = client["aerologix"]
            await db.installed_components.delete_one({
                "aircraft_id": aircraft_id,
                "part_no": "TEST-STRUCT-ENGINE"
            })
            client.close()
        
        asyncio.run(cleanup())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
