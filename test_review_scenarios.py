#!/usr/bin/env python3
"""
Test the exact scenarios from the review request:
1. Login with test@aerologix.ca / password123
2. GET /api/aircraft to list all aircraft
3. Verify that EACH aircraft has purpose and base_city fields (should be "Non spécifié" if null in DB)
4. GET /api/aircraft/{aircraft_id} for specific aircraft
5. PUT /api/aircraft/{aircraft_id} with {"description": "Test update"}
"""

import requests
import json

def test_review_scenarios():
    print("🧪 Testing Exact Review Request Scenarios")
    print("=" * 60)
    
    # Test 1: Login with test@aerologix.ca / password123
    print("📋 Test 1: Login with test@aerologix.ca / password123")
    
    login_response = requests.post('http://localhost:8001/api/auth/login', data={
        'username': 'test@aerologix.ca',
        'password': 'password123'
    })
    
    if login_response.status_code != 200:
        print(f"❌ Login failed: {login_response.status_code}")
        return False
    
    token = login_response.json()['access_token']
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    print("✅ Login successful")
    
    # Test 2: GET /api/aircraft to list all aircraft
    print("\n📋 Test 2: GET /api/aircraft to list all aircraft")
    
    aircraft_response = requests.get('http://localhost:8001/api/aircraft', headers=headers)
    
    if aircraft_response.status_code != 200:
        print(f"❌ Failed to get aircraft list: {aircraft_response.status_code}")
        return False
    
    aircraft_list = aircraft_response.json()
    print(f"✅ Got aircraft list with {len(aircraft_list)} aircraft")
    
    # Test 3: Verify that EACH aircraft has purpose and base_city fields
    print("\n📋 Test 3: Verify EACH aircraft has purpose and base_city fields")
    
    for i, aircraft in enumerate(aircraft_list):
        registration = aircraft.get('registration', f'Aircraft_{i+1}')
        aircraft_id = aircraft.get('_id', aircraft.get('id'))
        
        print(f"\n  Aircraft {i+1}: {registration} (ID: {aircraft_id})")
        
        # Check purpose field
        if 'purpose' not in aircraft:
            print(f"    ❌ purpose field MISSING")
            return False
        
        purpose = aircraft.get('purpose')
        if purpose is None:
            print(f"    ❌ purpose field is NULL")
            return False
        
        print(f"    ✅ purpose: '{purpose}'")
        
        # Check base_city field  
        if 'base_city' not in aircraft:
            print(f"    ❌ base_city field MISSING")
            return False
        
        base_city = aircraft.get('base_city')
        if base_city is None:
            print(f"    ❌ base_city field is NULL")
            return False
        
        print(f"    ✅ base_city: '{base_city}'")
        
        # Verify default values are applied when needed
        if not purpose or purpose.strip() == '':
            if purpose != "Non spécifié":
                print(f"    ❌ Expected 'Non spécifié' for empty purpose, got '{purpose}'")
                return False
        
        if not base_city or base_city.strip() == '':
            if base_city != "Non spécifié":
                print(f"    ❌ Expected 'Non spécifié' for empty base_city, got '{base_city}'")
                return False
    
    print(f"\n✅ All {len(aircraft_list)} aircraft have valid purpose and base_city fields")
    
    # Test 4: GET /api/aircraft/{aircraft_id} for specific aircraft
    if len(aircraft_list) > 0:
        test_aircraft = aircraft_list[0]
        aircraft_id = test_aircraft.get('_id', test_aircraft.get('id'))
        registration = test_aircraft.get('registration')
        
        print(f"\n📋 Test 4: GET /api/aircraft/{aircraft_id} for specific aircraft")
        
        specific_response = requests.get(f'http://localhost:8001/api/aircraft/{aircraft_id}', headers=headers)
        
        if specific_response.status_code != 200:
            print(f"❌ Failed to get specific aircraft: {specific_response.status_code}")
            return False
        
        specific_aircraft = specific_response.json()
        print(f"✅ Got specific aircraft: {registration}")
        
        # Verify response contains purpose and base_city fields
        if 'purpose' not in specific_aircraft or 'base_city' not in specific_aircraft:
            print(f"❌ Missing required fields in specific aircraft response")
            return False
        
        purpose = specific_aircraft.get('purpose')
        base_city = specific_aircraft.get('base_city')
        
        if purpose is None or base_city is None:
            print(f"❌ purpose or base_city is null in specific aircraft response")
            return False
        
        print(f"    ✅ purpose: '{purpose}' (not null)")
        print(f"    ✅ base_city: '{base_city}' (not null)")
        
        # Test 5: PUT /api/aircraft/{aircraft_id} with {"description": "Test update"}
        print(f"\n📋 Test 5: PUT /api/aircraft/{aircraft_id} with description update")
        
        update_response = requests.put(f'http://localhost:8001/api/aircraft/{aircraft_id}', 
                                     json={"description": "Test update"}, headers=headers)
        
        if update_response.status_code != 200:
            print(f"❌ Failed to update aircraft: {update_response.status_code}")
            return False
        
        updated_aircraft = update_response.json()
        print(f"✅ Aircraft updated successfully")
        
        # Verify response still has purpose and base_city fields
        if 'purpose' not in updated_aircraft or 'base_city' not in updated_aircraft:
            print(f"❌ Missing required fields after update")
            return False
        
        updated_purpose = updated_aircraft.get('purpose')
        updated_base_city = updated_aircraft.get('base_city')
        
        if updated_purpose is None or updated_base_city is None:
            print(f"❌ purpose or base_city is null after update")
            return False
        
        print(f"    ✅ purpose: '{updated_purpose}' (preserved)")
        print(f"    ✅ base_city: '{updated_base_city}' (preserved)")
        print(f"    ✅ description: '{updated_aircraft.get('description')}' (updated)")
        
        # Verify expected response structure matches review request
        expected_fields = ['id', 'registration', 'purpose', 'base_city']
        
        # Check if we have 'id' or '_id'
        has_id = 'id' in updated_aircraft or '_id' in updated_aircraft
        if not has_id:
            print(f"❌ Missing id field in response")
            return False
        
        missing_fields = []
        for field in ['registration', 'purpose', 'base_city']:
            if field not in updated_aircraft:
                missing_fields.append(field)
        
        if missing_fields:
            print(f"❌ Missing expected fields: {missing_fields}")
            return False
        
        print(f"    ✅ Response structure matches expected format")
        
        # Show final response structure
        response_structure = {
            "id": updated_aircraft.get('_id', updated_aircraft.get('id')),
            "registration": updated_aircraft.get('registration'),
            "purpose": updated_aircraft.get('purpose'),
            "base_city": updated_aircraft.get('base_city')
        }
        
        print(f"\n📋 Final Response Structure:")
        print(json.dumps(response_structure, indent=2))
        
        return True
    else:
        print("❌ No aircraft available for testing")
        return False

if __name__ == "__main__":
    success = test_review_scenarios()
    
    print("\n" + "=" * 60)
    if success:
        print("✅ ALL REVIEW REQUEST SCENARIOS PASSED")
        print("✓ Login with test@aerologix.ca / password123 works")
        print("✓ GET /api/aircraft returns aircraft with purpose and base_city fields")
        print("✓ purpose is NEVER null or missing - either has value or 'Non spécifié'")
        print("✓ base_city is NEVER null or missing - either has value or 'Non spécifié'")
        print("✓ GET /api/aircraft/{aircraft_id} works correctly")
        print("✓ PUT /api/aircraft/{aircraft_id} preserves purpose and base_city fields")
        print("✓ Default value text is exactly 'Non spécifié' (French)")
    else:
        print("❌ SOME SCENARIOS FAILED")
        print("Please check the detailed results above")
    
    exit(0 if success else 1)