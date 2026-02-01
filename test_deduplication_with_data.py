#!/usr/bin/env python3
"""
Test Critical Mentions Deduplication with actual test data
"""

import requests
import json
import sys
from datetime import datetime

class DeduplicationTester:
    def __init__(self, base_url="http://localhost:8001"):
        self.base_url = base_url
        self.token = None
        self.aircraft_id = None
        
    def authenticate(self):
        """Login and get token"""
        url = f"{self.base_url}/api/auth/login"
        data = {
            "username": "test@aerologix.ca",
            "password": "password123"
        }
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        
        response = requests.post(url, data=data, headers=headers)
        if response.status_code == 200:
            result = response.json()
            self.token = result.get('access_token')
            print("✅ Authentication successful")
            return True
        else:
            print(f"❌ Authentication failed: {response.status_code}")
            return False
    
    def get_aircraft_id(self):
        """Get aircraft ID for testing"""
        url = f"{self.base_url}/api/aircraft"
        headers = {'Authorization': f'Bearer {self.token}'}
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            aircraft_list = response.json()
            if isinstance(aircraft_list, list) and len(aircraft_list) > 0:
                self.aircraft_id = aircraft_list[0].get('_id') or aircraft_list[0].get('id')
                registration = aircraft_list[0].get('registration')
                print(f"✅ Found aircraft: {registration} (ID: {self.aircraft_id})")
                return True
        
        print("❌ No aircraft found")
        return False
    
    def create_test_limitations(self):
        """Create test limitations to verify deduplication"""
        print("📝 Creating test limitations for deduplication testing...")
        
        # Test data with duplicates that should be deduplicated
        test_limitations = [
            {
                "limitation_text": "ELT removed for certification - limited to 25 N.M. from aerodrome",
                "category": "ELT",
                "detected_keywords": ["ELT", "removed", "certification", "25"],
                "confidence": 0.9,
                "report_id": "test_report_1",
                "report_date": "2024-01-15"
            },
            {
                "limitation_text": "E.L.T REMOVED FOR CERTIFICATION LIMITED TO 25 NM FROM AERODROME",
                "category": "ELT", 
                "detected_keywords": ["ELT", "removed", "certification", "25"],
                "confidence": 0.85,
                "report_id": "test_report_2",
                "report_date": "2024-01-20"  # Newer date - should be kept
            },
            {
                "limitation_text": "PITOT STATIC SYSTEM 24 MONTH INSPECTION OVERDUE",
                "category": "AVIONICS",
                "detected_keywords": ["PITOT", "STATIC", "24", "MONTH", "OVERDUE"],
                "confidence": 0.9,
                "report_id": "test_report_3",
                "report_date": "2024-01-10"
            },
            {
                "limitation_text": "Pitot-static system inspection overdue (24 month)",
                "category": "AVIONICS",
                "detected_keywords": ["PITOT", "STATIC", "OVERDUE", "24"],
                "confidence": 0.8,
                "report_id": "test_report_4", 
                "report_date": "2024-01-05"  # Older date - should be removed
            },
            {
                "limitation_text": "TRANSPONDER ENCODER 24 MONTH INSPECTION OVERDUE",
                "category": "AVIONICS",
                "detected_keywords": ["TRANSPONDER", "ENCODER", "24", "OVERDUE"],
                "confidence": 0.9,
                "report_id": "test_report_5",
                "report_date": "2024-01-12"
            },
            {
                "limitation_text": "FIRE EXTINGUISHER NOT SERVICEABLE",
                "category": "FIRE_EXTINGUISHER",
                "detected_keywords": ["FIRE", "EXTINGUISHER", "SERVICEABLE"],
                "confidence": 0.95,
                "report_id": "test_report_6",
                "report_date": "2024-01-08"
            },
            {
                "limitation_text": "Fire extinguisher not serviciable",  # Typo but should be deduplicated
                "category": "FIRE_EXTINGUISHER",
                "detected_keywords": ["FIRE", "EXTINGUISHER"],
                "confidence": 0.8,
                "report_id": "test_report_7",
                "report_date": "2024-01-03"  # Older - should be removed
            }
        ]
        
        # Insert test limitations directly via MongoDB (simulating OCR detection)
        import pymongo
        
        try:
            client = pymongo.MongoClient("mongodb://localhost:27017")
            db = client.aerologix
            
            # Get user ID from token (decode JWT)
            import jwt
            decoded = jwt.decode(self.token, options={"verify_signature": False})
            user_id = decoded.get('sub')
            
            # Prepare documents for insertion
            docs_to_insert = []
            for limitation in test_limitations:
                doc = {
                    "_id": f"test_{limitation['report_id']}_{limitation['category'].lower()}",
                    "aircraft_id": self.aircraft_id,
                    "user_id": user_id,
                    "limitation_text": limitation["limitation_text"],
                    "category": limitation["category"],
                    "detected_keywords": limitation["detected_keywords"],
                    "confidence": limitation["confidence"],
                    "report_id": limitation["report_id"],
                    "report_date": datetime.strptime(limitation["report_date"], "%Y-%m-%d"),
                    "created_at": datetime.now()
                }
                docs_to_insert.append(doc)
            
            # Insert test data
            result = db.operational_limitations.insert_many(docs_to_insert)
            print(f"✅ Created {len(result.inserted_ids)} test limitations")
            
            # Show what we created
            print("\n📋 Test data created:")
            for doc in docs_to_insert:
                print(f"  - {doc['category']}: {doc['limitation_text'][:50]}... (Date: {doc['report_date'].strftime('%Y-%m-%d')})")
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to create test data: {e}")
            return False
    
    def test_deduplication_logic(self):
        """Test the actual deduplication logic"""
        print("\n🔍 Testing Critical Mentions Deduplication Logic...")
        
        url = f"{self.base_url}/api/limitations/{self.aircraft_id}/critical-mentions"
        headers = {'Authorization': f'Bearer {self.token}'}
        
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            print(f"❌ API call failed: {response.status_code}")
            return False
        
        data = response.json()
        
        # Validate response structure
        required_fields = ["aircraft_id", "critical_mentions", "summary"]
        for field in required_fields:
            if field not in data:
                print(f"❌ Missing field: {field}")
                return False
        
        critical_mentions = data["critical_mentions"]
        summary = data["summary"]
        
        print(f"\n📊 Deduplication Results:")
        print(f"  Raw total: {summary.get('raw_total', 0)}")
        print(f"  Final total: {summary.get('total_count', 0)}")
        print(f"  Duplicates removed: {summary.get('duplicates_removed', 0)}")
        
        # Detailed breakdown
        categories = ["elt", "avionics", "fire_extinguisher", "general_limitations"]
        for category in categories:
            items = critical_mentions.get(category, [])
            count = len(items)
            if count > 0:
                print(f"\n  {category.upper()} ({count} items):")
                for item in items:
                    print(f"    - {item.get('text', '')[:60]}...")
                    print(f"      Source: {item.get('source', 'unknown')}, Date: {item.get('report_date', 'unknown')}")
        
        # Verify deduplication worked
        expected_deduplication = {
            "elt": 1,  # Should keep the newer ELT limitation (2024-01-20)
            "avionics": 2,  # Should keep PITOT_STATIC (2024-01-10) and TRANSPONDER (2024-01-12)
            "fire_extinguisher": 1,  # Should keep the newer fire extinguisher (2024-01-08)
            "general_limitations": 0
        }
        
        success = True
        for category, expected_count in expected_deduplication.items():
            actual_count = len(critical_mentions.get(category, []))
            if actual_count != expected_count:
                print(f"❌ {category}: Expected {expected_count}, got {actual_count}")
                success = False
            else:
                print(f"✅ {category}: {actual_count} items (deduplication worked)")
        
        # Verify math
        raw_total = summary.get('raw_total', 0)
        final_total = summary.get('total_count', 0)
        duplicates_removed = summary.get('duplicates_removed', 0)
        
        if raw_total - duplicates_removed != final_total:
            print(f"❌ Math error: {raw_total} - {duplicates_removed} != {final_total}")
            success = False
        else:
            print(f"✅ Math correct: {raw_total} - {duplicates_removed} = {final_total}")
        
        # Check that newer dates were kept
        elt_items = critical_mentions.get("elt", [])
        if len(elt_items) == 1:
            elt_date = elt_items[0].get("report_date")
            if elt_date == "2024-01-20":
                print("✅ ELT: Newer date (2024-01-20) was kept")
            else:
                print(f"❌ ELT: Expected 2024-01-20, got {elt_date}")
                success = False
        
        fire_items = critical_mentions.get("fire_extinguisher", [])
        if len(fire_items) == 1:
            fire_date = fire_items[0].get("report_date")
            if fire_date == "2024-01-08":
                print("✅ Fire Extinguisher: Newer date (2024-01-08) was kept")
            else:
                print(f"❌ Fire Extinguisher: Expected 2024-01-08, got {fire_date}")
                success = False
        
        return success
    
    def cleanup_test_data(self):
        """Clean up test data"""
        print("\n🧹 Cleaning up test data...")
        
        try:
            import pymongo
            client = pymongo.MongoClient("mongodb://localhost:27017")
            db = client.aerologix
            
            # Delete test limitations
            result = db.operational_limitations.delete_many({
                "_id": {"$regex": "^test_"}
            })
            
            print(f"✅ Cleaned up {result.deleted_count} test limitations")
            return True
            
        except Exception as e:
            print(f"❌ Cleanup failed: {e}")
            return False
    
    def run_full_test(self):
        """Run the complete deduplication test"""
        print("🚀 Starting Critical Mentions Deduplication Test with Real Data")
        print("=" * 60)
        
        if not self.authenticate():
            return False
        
        if not self.get_aircraft_id():
            return False
        
        if not self.create_test_limitations():
            return False
        
        success = self.test_deduplication_logic()
        
        self.cleanup_test_data()
        
        print("\n" + "=" * 60)
        if success:
            print("🎉 DEDUPLICATION TEST PASSED!")
            print("✅ Key phrase extraction working correctly")
            print("✅ Duplicate removal based on normalized keys")
            print("✅ Newer dates preserved over older ones")
            print("✅ Summary counts accurate")
        else:
            print("❌ DEDUPLICATION TEST FAILED!")
        
        return success

if __name__ == "__main__":
    tester = DeduplicationTester()
    success = tester.run_full_test()
    sys.exit(0 if success else 1)