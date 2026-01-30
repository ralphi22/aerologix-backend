#!/usr/bin/env python3
"""
Test Critical Mentions with Sample Data

Creates sample limitation data and tests the endpoint with actual data
"""

import requests
import sys
import json
from datetime import datetime
from typing import Dict, Any, Optional

class CriticalMentionsDataTest:
    def __init__(self, base_url="http://localhost:8001"):
        self.base_url = base_url
        self.token = None
        self.aircraft_id = None
        
    def authenticate(self):
        """Authenticate and get token"""
        response = requests.post(
            f"{self.base_url}/api/auth/login",
            data={"username": "test@aerologix.ca", "password": "password123"},
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        if response.status_code == 200:
            data = response.json()
            self.token = data['access_token']
            return True
        return False
    
    def get_aircraft_id(self):
        """Get the test aircraft ID"""
        headers = {'Authorization': f'Bearer {self.token}'}
        response = requests.get(f"{self.base_url}/api/aircraft", headers=headers)
        
        if response.status_code == 200:
            aircraft_list = response.json()
            if isinstance(aircraft_list, list) and len(aircraft_list) > 0:
                self.aircraft_id = aircraft_list[0].get('_id') or aircraft_list[0].get('id')
                return True
        return False
    
    def create_sample_limitations(self):
        """Create sample limitation data directly in MongoDB"""
        print("📝 Creating sample limitation data...")
        
        # We'll use a simple Python script to insert data directly
        script = f'''
import asyncio
from database.mongodb import get_database
from datetime import datetime

async def create_sample_data():
    try:
        from database.mongodb import db
        await db.connect("mongodb://localhost:27017", "aerologix")
        
        # Sample limitations data
        limitations = [
            {{
                "_id": "limit_elt_001",
                "aircraft_id": "{self.aircraft_id}",
                "user_id": "1769733120179849",  # Test user ID
                "category": "ELT",
                "limitation_text": "ELT battery expires 2025-06-15. Replace before expiry.",
                "detected_keywords": ["ELT", "battery", "expires"],
                "confidence": 0.9,
                "report_id": "report_001",
                "report_date": datetime(2024, 1, 15),
                "created_at": datetime.utcnow()
            }},
            {{
                "_id": "limit_avionics_001", 
                "aircraft_id": "{self.aircraft_id}",
                "user_id": "1769733120179849",
                "category": "AVIONICS",
                "limitation_text": "Transponder requires recertification by 2025-03-01.",
                "detected_keywords": ["transponder", "recertification"],
                "confidence": 0.85,
                "report_id": "report_002",
                "report_date": datetime(2024, 2, 10),
                "created_at": datetime.utcnow()
            }},
            {{
                "_id": "limit_fire_001",
                "aircraft_id": "{self.aircraft_id}", 
                "user_id": "1769733120179849",
                "category": "FIRE_EXTINGUISHER",
                "limitation_text": "Fire extinguisher inspection due 2025-04-20.",
                "detected_keywords": ["fire", "extinguisher", "inspection"],
                "confidence": 0.8,
                "report_id": "report_003", 
                "report_date": datetime(2024, 3, 5),
                "created_at": datetime.utcnow()
            }},
            {{
                "_id": "limit_general_001",
                "aircraft_id": "{self.aircraft_id}",
                "user_id": "1769733120179849", 
                "category": "GENERAL",
                "limitation_text": "Annual inspection required by 2025-05-10.",
                "detected_keywords": ["annual", "inspection"],
                "confidence": 0.95,
                "report_id": "report_004",
                "report_date": datetime(2024, 4, 1),
                "created_at": datetime.utcnow()
            }}
        ]
        
        # Insert limitations
        result = await db.get_db().operational_limitations.insert_many(limitations)
        print(f"Inserted {{len(result.inserted_ids)}} limitation records")
        
        await db.disconnect()
        return True
        
    except Exception as e:
        print(f"Error creating sample data: {{e}}")
        return False

asyncio.run(create_sample_data())
'''
        
        # Write and execute the script
        with open('/tmp/create_sample_data.py', 'w') as f:
            f.write(script)
        
        import subprocess
        result = subprocess.run([sys.executable, '/tmp/create_sample_data.py'], 
                              cwd='/app', capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ Sample limitation data created")
            return True
        else:
            print(f"❌ Failed to create sample data: {result.stderr}")
            return False
    
    def test_with_data(self):
        """Test the endpoint with actual data"""
        print("🔍 Testing endpoint with sample data...")
        
        headers = {'Authorization': f'Bearer {self.token}'}
        response = requests.get(
            f"{self.base_url}/api/limitations/{self.aircraft_id}/critical-mentions",
            headers=headers
        )
        
        if response.status_code != 200:
            print(f"❌ Request failed: {response.status_code}")
            return False
        
        data = response.json()
        
        # Print the response for verification
        print("\n📋 Response Data:")
        print(f"Aircraft ID: {data.get('aircraft_id')}")
        print(f"Registration: {data.get('registration')}")
        
        critical_mentions = data.get('critical_mentions', {})
        summary = data.get('summary', {})
        
        print(f"\n📊 Summary:")
        print(f"  ELT: {summary.get('elt_count', 0)} mentions")
        print(f"  Avionics: {summary.get('avionics_count', 0)} mentions")
        print(f"  Fire Extinguisher: {summary.get('fire_extinguisher_count', 0)} mentions")
        print(f"  General Limitations: {summary.get('general_limitations_count', 0)} mentions")
        print(f"  Total: {summary.get('total_count', 0)} mentions")
        
        # Verify we have the expected data
        expected_counts = {
            'elt_count': 1,
            'avionics_count': 1, 
            'fire_extinguisher_count': 1,
            'general_limitations_count': 1,
            'total_count': 4
        }
        
        success = True
        for key, expected in expected_counts.items():
            actual = summary.get(key, 0)
            if actual != expected:
                print(f"❌ Count mismatch for {key}: expected {expected}, got {actual}")
                success = False
        
        if success:
            print("\n✅ All data validation passed!")
            
            # Print sample mentions
            for category, mentions in critical_mentions.items():
                if mentions:
                    print(f"\n{category.upper()} mentions:")
                    for mention in mentions:
                        print(f"  - {mention.get('text', 'N/A')}")
        
        return success
    
    def run_test(self):
        """Run the complete test"""
        print("🚀 Testing Critical Mentions with Sample Data")
        print("=" * 60)
        
        if not self.authenticate():
            print("❌ Authentication failed")
            return False
        
        if not self.get_aircraft_id():
            print("❌ Failed to get aircraft ID")
            return False
        
        if not self.create_sample_limitations():
            print("❌ Failed to create sample data")
            return False
        
        return self.test_with_data()

def main():
    tester = CriticalMentionsDataTest()
    success = tester.run_test()
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())