#!/usr/bin/env python3
"""
Seed TC AD/SB Collections with Sample Data

Creates sample AD/SB entries for testing the comparison engine.
These are EXAMPLE entries only - not official TC data.

Usage:
    python scripts/seed_tc_adsb.py
"""

import asyncio
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
from config import get_settings


# Sample TC AD data (based on real patterns but NOT official)
SAMPLE_ADS = [
    {
        "_id": "CF-2020-01",
        "ref": "CF-2020-01",
        "type": "AD",
        "designator": "3A19",  # Cessna 150/152
        "manufacturer": "Cessna",
        "model": "150, 152",
        "effective_date": datetime(2020, 3, 15),
        "recurrence_type": "ONCE",
        "recurrence_value": None,
        "title": "Wing Spar Inspection",
        "compliance_text": "Inspect wing spar for cracks per SB-150-01",
        "source_url": "https://tc.canada.ca/ad/CF-2020-01",
        "is_active": True,
    },
    {
        "_id": "CF-2019-12",
        "ref": "CF-2019-12",
        "type": "AD",
        "designator": "3A19",
        "manufacturer": "Cessna",
        "model": "150, 152, 172",
        "effective_date": datetime(2019, 6, 1),
        "recurrence_type": "YEARS",
        "recurrence_value": 5,
        "title": "Fuel Tank Inspection",
        "compliance_text": "Inspect fuel tanks every 5 years or at overhaul",
        "source_url": "https://tc.canada.ca/ad/CF-2019-12",
        "is_active": True,
    },
    {
        "_id": "CF-2023-05",
        "ref": "CF-2023-05",
        "type": "AD",
        "designator": "2A13",  # Piper PA-28
        "manufacturer": "Piper",
        "model": "PA-28",
        "effective_date": datetime(2023, 9, 1),
        "recurrence_type": "HOURS",
        "recurrence_value": 500,
        "title": "Elevator Trim Tab Inspection",
        "compliance_text": "Inspect elevator trim tab every 500 hours",
        "source_url": "https://tc.canada.ca/ad/CF-2023-05",
        "is_active": True,
    },
    {
        "_id": "CF-2024-02",
        "ref": "CF-2024-02",
        "type": "AD",
        "designator": "3A19",
        "manufacturer": "Cessna",
        "model": "150, 152, 172, 182",
        "effective_date": datetime(2024, 1, 15),
        "recurrence_type": "ONCE",
        "recurrence_value": None,
        "title": "Magneto Impulse Coupling",
        "compliance_text": "Replace magneto impulse coupling per SB",
        "source_url": "https://tc.canada.ca/ad/CF-2024-02",
        "is_active": True,
    },
    {
        "_id": "CF-2025-01",
        "ref": "CF-2025-01",
        "type": "AD",
        "designator": "3A19",
        "manufacturer": "Cessna",
        "model": "150, 152",
        "effective_date": datetime(2025, 1, 5),  # Very recent - should show as "new"
        "recurrence_type": "ONCE",
        "recurrence_value": None,
        "title": "Rudder Cable Inspection",
        "compliance_text": "One-time inspection of rudder cables",
        "source_url": "https://tc.canada.ca/ad/CF-2025-01",
        "is_active": True,
    },
]

# Sample TC SB data
SAMPLE_SBS = [
    {
        "_id": "SB-150-2019-01",
        "ref": "SB-150-2019-01",
        "type": "SB",
        "designator": "3A19",
        "manufacturer": "Cessna",
        "model": "150, 152",
        "effective_date": datetime(2019, 4, 1),
        "recurrence_type": "ONCE",
        "recurrence_value": None,
        "title": "Nose Gear Fork Reinforcement",
        "compliance_text": "Recommended reinforcement of nose gear fork",
        "source_url": None,
        "is_mandatory": False,
        "related_ad": None,
        "is_active": True,
    },
    {
        "_id": "SB-172-2021-03",
        "ref": "SB-172-2021-03",
        "type": "SB",
        "designator": "3A19",
        "manufacturer": "Cessna",
        "model": "172",
        "effective_date": datetime(2021, 8, 15),
        "recurrence_type": "YEARS",
        "recurrence_value": 3,
        "title": "Avionics Cooling Fan Inspection",
        "compliance_text": "Inspect avionics cooling fan every 3 years",
        "source_url": None,
        "is_mandatory": False,
        "related_ad": None,
        "is_active": True,
    },
    {
        "_id": "SB-PA28-2022-05",
        "ref": "SB-PA28-2022-05",
        "type": "SB",
        "designator": "2A13",
        "manufacturer": "Piper",
        "model": "PA-28",
        "effective_date": datetime(2022, 5, 20),
        "recurrence_type": "ONCE",
        "recurrence_value": None,
        "title": "Fuel Selector Valve Upgrade",
        "compliance_text": "Upgrade fuel selector valve to new design",
        "source_url": None,
        "is_mandatory": True,
        "related_ad": "CF-2022-08",
        "is_active": True,
    },
]


async def seed_tc_adsb():
    """Seed TC AD/SB collections"""
    
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "aerologix")
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    print("=" * 60)
    print("SEEDING TC AD/SB COLLECTIONS")
    print("=" * 60)
    
    now = datetime.utcnow()
    
    # Seed ADs
    print("\nSeeding TC_AD collection...")
    ad_inserted = 0
    ad_updated = 0
    
    for ad in SAMPLE_ADS:
        ad["created_at"] = now
        ad["updated_at"] = now
        
        result = await db.tc_ad.update_one(
            {"_id": ad["_id"]},
            {"$set": ad},
            upsert=True
        )
        
        if result.upserted_id:
            ad_inserted += 1
        elif result.modified_count:
            ad_updated += 1
    
    print(f"  AD: {ad_inserted} inserted, {ad_updated} updated")
    
    # Seed SBs
    print("\nSeeding TC_SB collection...")
    sb_inserted = 0
    sb_updated = 0
    
    for sb in SAMPLE_SBS:
        sb["created_at"] = now
        sb["updated_at"] = now
        
        result = await db.tc_sb.update_one(
            {"_id": sb["_id"]},
            {"$set": sb},
            upsert=True
        )
        
        if result.upserted_id:
            sb_inserted += 1
        elif result.modified_count:
            sb_updated += 1
    
    print(f"  SB: {sb_inserted} inserted, {sb_updated} updated")
    
    # Create indexes
    print("\nCreating indexes...")
    
    await db.tc_ad.create_index([("ref", 1)], unique=True, name="ref_unique")
    await db.tc_ad.create_index([("designator", 1)], name="designator_idx")
    await db.tc_ad.create_index([("manufacturer", 1)], name="manufacturer_idx")
    
    await db.tc_sb.create_index([("ref", 1)], unique=True, name="ref_unique")
    await db.tc_sb.create_index([("designator", 1)], name="designator_idx")
    await db.tc_sb.create_index([("manufacturer", 1)], name="manufacturer_idx")
    
    print("  Indexes created")
    
    # Summary
    ad_count = await db.tc_ad.count_documents({})
    sb_count = await db.tc_sb.count_documents({})
    
    print("\n" + "=" * 60)
    print("SEED COMPLETE")
    print(f"  TC_AD: {ad_count} documents")
    print(f"  TC_SB: {sb_count} documents")
    print("=" * 60)
    
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_tc_adsb())
