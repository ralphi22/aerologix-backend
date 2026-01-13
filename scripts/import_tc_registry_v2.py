#!/usr/bin/env python3
"""
TC Registry Import Script - Transport Canada Aircraft Registry

Imports aircraft data from official TC dump files:
- carscurr.txt: Aircraft records
- carsownr.txt: Owner records

Creates/updates tc_aircraft collection in MongoDB with:
- registration: C-XXXX format
- registration_norm: Uppercase, no dash (for search)
- manufacturer, model, serial_number
- designator (Type Certificate)
- owner_name (from carsownr.txt)
"""

import csv
import re
import sys
import os
from datetime import datetime
from typing import Dict, List, Optional

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient, UpdateOne, ASCENDING
from pymongo.errors import BulkWriteError


# ============================================================
# CONFIGURATION
# ============================================================

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "tc_data_new")
CARSCURR_FILE = os.path.join(DATA_DIR, "carscurr.txt")
CARSOWNR_FILE = os.path.join(DATA_DIR, "carsownr.txt")

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "aerologix")

BATCH_SIZE = 1000


# ============================================================
# COLUMN MAPPINGS (from carslayout.txt)
# ============================================================

# carscurr.txt columns (0-indexed)
CURRCOLS = {
    "mark": 0,                    # Aircraft Mark (e.g., "AAC" -> C-GAAC)
    "common_name": 3,             # Manufacturer
    "model_name": 4,              # Model
    "serial_number": 5,           # Serial Number
    "serial_compressed": 6,       # Serial Number compressed
    "id_plate_manufacturer": 7,   # Manufacturer on ID Plate
    "aircraft_category": 10,      # Category (Aeroplane, Helicopter, etc.)
    "engine_manufacturer": 14,    # Engine Manufacturer
    "engine_category": 16,        # Engine Category
    "num_engines": 18,            # Number of Engines
    "num_seats": 19,              # Number of Seats
    "weight_kg": 20,              # Takeoff Weight in Kilos
    "issue_date": 22,             # Certificate Issue Date
    "effective_date": 23,         # Registration Effective Date
    "purpose": 25,                # Purpose (Private, Commercial, etc.)
    "flight_authority": 27,       # Flight Authority
    "country_manufacture": 29,    # Country of Manufacture
    "date_manufacture": 32,       # Date Manufactured
    "base_province": 35,          # Base Province
    "city_airport": 37,           # Base City/Airport
    "type_certificate": 38,       # Type Certificate (designator)
    "status": 39,                 # Registration Status
    "modified_date": 42,          # Last Modified Date
    "trimmed_mark": 47,           # Trimmed Mark (without spaces)
}

# carsownr.txt columns (0-indexed)
OWNRCOLS = {
    "mark": 0,                    # Aircraft Mark (link key)
    "full_name": 1,               # Owner Full Name
    "trade_name": 2,              # Trade Name
    "street": 3,                  # Street
    "city": 5,                    # City
    "province": 6,                # Province
    "postal_code": 8,             # Postal Code
    "country": 9,                 # Country
    "owner_type": 11,             # Individual/Entity
    "trimmed_mark": 18,           # Trimmed Mark
}


# ============================================================
# HELPERS
# ============================================================

def normalize_registration(mark: str) -> str:
    """
    Normalize registration for search.
    Input: "AAC" or " AAC" or "FGSO"
    Output: "CFGSO" (uppercase, no dash, no spaces)
    """
    mark = mark.strip().upper()
    # Add C prefix if not present
    if not mark.startswith("C"):
        mark = "C" + mark
    # Remove any dashes
    mark = mark.replace("-", "")
    return mark


def format_registration(mark: str) -> str:
    """
    Format registration for display.
    Input: "AAC" or " AAC"
    Output: "C-GAAC"
    """
    mark = mark.strip().upper()
    # TC format: Mark is the suffix (e.g., "AAC")
    # Full registration is C-G + mark for most Canadian aircraft
    # But we need to handle the actual format
    if len(mark) == 3:
        return f"C-G{mark}"
    elif len(mark) == 4:
        return f"C-{mark}"
    else:
        return f"C-{mark}"


def clean_string(s: str) -> str:
    """Clean a string value."""
    if not s:
        return ""
    return s.strip().strip('"').strip()


def parse_date(date_str: str) -> Optional[datetime]:
    """Parse TC date format YYYY/MM/DD."""
    date_str = clean_string(date_str)
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y/%m/%d")
    except:
        return None


def parse_float(val: str) -> Optional[float]:
    """Parse a float value."""
    val = clean_string(val)
    if not val:
        return None
    try:
        return float(val)
    except:
        return None


def parse_int(val: str) -> Optional[int]:
    """Parse an integer value."""
    val = clean_string(val)
    if not val:
        return None
    try:
        return int(val)
    except:
        return None


# ============================================================
# IMPORT FUNCTIONS
# ============================================================

def load_owners(filepath: str) -> Dict[str, dict]:
    """
    Load owner data from carsownr.txt.
    Returns dict keyed by trimmed_mark.
    """
    print(f"Loading owners from {filepath}...")
    owners = {}
    
    with open(filepath, 'r', encoding='latin-1', errors='replace') as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 19:
                continue
            
            mark = clean_string(row[OWNRCOLS["mark"]])
            trimmed_mark = clean_string(row[OWNRCOLS["trimmed_mark"]])
            
            # Use trimmed_mark as key (more reliable)
            key = trimmed_mark or mark
            if not key:
                continue
            
            owners[key.upper()] = {
                "full_name": clean_string(row[OWNRCOLS["full_name"]]),
                "trade_name": clean_string(row[OWNRCOLS["trade_name"]]),
                "city": clean_string(row[OWNRCOLS["city"]]),
                "province": clean_string(row[OWNRCOLS["province"]]),
                "country": clean_string(row[OWNRCOLS["country"]]),
                "owner_type": clean_string(row[OWNRCOLS["owner_type"]]),
            }
    
    print(f"  Loaded {len(owners)} owner records")
    return owners


def import_aircraft(db, filepath: str, owners: Dict[str, dict]) -> int:
    """
    Import aircraft from carscurr.txt.
    Returns count of imported records.
    """
    print(f"Importing aircraft from {filepath}...")
    
    operations = []
    count = 0
    skipped = 0
    
    with open(filepath, 'r', encoding='latin-1', errors='replace') as f:
        reader = csv.reader(f)
        
        for row in reader:
            if len(row) < 48:
                skipped += 1
                continue
            
            mark = clean_string(row[CURRCOLS["mark"]])
            trimmed_mark = clean_string(row[CURRCOLS["trimmed_mark"]])
            
            if not mark and not trimmed_mark:
                skipped += 1
                continue
            
            # Use trimmed_mark if available
            key = (trimmed_mark or mark).upper()
            
            # Build registration
            registration = format_registration(key)
            registration_norm = normalize_registration(key)
            
            # Get owner info
            owner = owners.get(key, {})
            owner_name = owner.get("full_name") or owner.get("trade_name") or ""
            
            # Parse fields
            manufacturer = clean_string(row[CURRCOLS["common_name"]])
            model = clean_string(row[CURRCOLS["model_name"]])
            serial_number = clean_string(row[CURRCOLS["serial_number"]])
            designator = clean_string(row[CURRCOLS["type_certificate"]])
            
            # Additional fields
            aircraft_category = clean_string(row[CURRCOLS["aircraft_category"]])
            engine_manufacturer = clean_string(row[CURRCOLS["engine_manufacturer"]])
            engine_category = clean_string(row[CURRCOLS["engine_category"]])
            num_engines = parse_int(row[CURRCOLS["num_engines"]])
            num_seats = parse_int(row[CURRCOLS["num_seats"]])
            weight_kg = parse_float(row[CURRCOLS["weight_kg"]])
            
            issue_date = parse_date(row[CURRCOLS["issue_date"]])
            effective_date = parse_date(row[CURRCOLS["effective_date"]])
            date_manufacture = parse_date(row[CURRCOLS["date_manufacture"]])
            modified_date = parse_date(row[CURRCOLS["modified_date"]])
            
            base_province = clean_string(row[CURRCOLS["base_province"]])
            city_airport = clean_string(row[CURRCOLS["city_airport"]])
            country_manufacture = clean_string(row[CURRCOLS["country_manufacture"]])
            status = clean_string(row[CURRCOLS["status"]])
            purpose = clean_string(row[CURRCOLS["purpose"]])
            
            # Build document
            doc = {
                "registration": registration,
                "registration_norm": registration_norm,
                "mark": key,
                "manufacturer": manufacturer,
                "model": model,
                "serial_number": serial_number,
                "designator": designator,
                "owner_name": owner_name,
                "owner_city": owner.get("city", ""),
                "owner_province": owner.get("province", ""),
                "aircraft_category": aircraft_category,
                "engine_manufacturer": engine_manufacturer,
                "engine_category": engine_category,
                "num_engines": num_engines,
                "num_seats": num_seats,
                "weight_kg": weight_kg,
                "issue_date": issue_date,
                "effective_date": effective_date,
                "date_manufacture": date_manufacture,
                "country_manufacture": country_manufacture,
                "base_province": base_province,
                "city_airport": city_airport,
                "status": status,
                "purpose": purpose,
                "modified_date": modified_date,
                "tc_import_date": datetime.utcnow(),
            }
            
            # Upsert operation
            operations.append(
                UpdateOne(
                    {"registration_norm": registration_norm},
                    {"$set": doc},
                    upsert=True
                )
            )
            count += 1
            
            # Bulk write in batches
            if len(operations) >= BATCH_SIZE:
                try:
                    result = db.tc_aircraft.bulk_write(operations, ordered=False)
                    print(f"  Processed {count} records...")
                except BulkWriteError as e:
                    print(f"  Bulk write error (continuing): {len(e.details.get('writeErrors', []))} errors")
                operations = []
    
    # Final batch
    if operations:
        try:
            db.tc_aircraft.bulk_write(operations, ordered=False)
        except BulkWriteError as e:
            print(f"  Final bulk write error: {len(e.details.get('writeErrors', []))} errors")
    
    print(f"  Imported {count} aircraft, skipped {skipped} invalid rows")
    return count


def create_indexes(db):
    """Create indexes on tc_aircraft collection."""
    print("Creating indexes...")
    
    # Unique index on registration_norm
    db.tc_aircraft.create_index(
        [("registration_norm", ASCENDING)],
        unique=True,
        name="registration_norm_unique"
    )
    
    # Index for prefix search
    db.tc_aircraft.create_index(
        [("registration_norm", ASCENDING)],
        name="registration_norm_idx"
    )
    
    # Index on registration (display format)
    db.tc_aircraft.create_index(
        [("registration", ASCENDING)],
        name="registration_idx"
    )
    
    # Index on manufacturer for filtering
    db.tc_aircraft.create_index(
        [("manufacturer", ASCENDING)],
        name="manufacturer_idx"
    )
    
    # Index on designator (type certificate)
    db.tc_aircraft.create_index(
        [("designator", ASCENDING)],
        name="designator_idx"
    )
    
    print("  Indexes created")


def verify_import(db):
    """Verify import by checking some records."""
    print("\nVerification:")
    
    total = db.tc_aircraft.count_documents({})
    print(f"  Total aircraft: {total}")
    
    # Check specific registrations
    test_regs = ["CFGSO", "CGAAA", "CFABC"]
    for reg in test_regs:
        doc = db.tc_aircraft.find_one({"registration_norm": reg})
        if doc:
            print(f"  {doc['registration']}: {doc['manufacturer']} {doc['model']} ({doc['designator']})")
        else:
            print(f"  {reg}: NOT FOUND")
    
    # Top manufacturers
    pipeline = [
        {"$group": {"_id": "$manufacturer", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 5}
    ]
    print("\n  Top manufacturers:")
    for doc in db.tc_aircraft.aggregate(pipeline):
        print(f"    - {doc['_id']}: {doc['count']}")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("TC Registry Import Script")
    print("=" * 60)
    
    # Check files exist
    if not os.path.exists(CARSCURR_FILE):
        print(f"ERROR: {CARSCURR_FILE} not found")
        sys.exit(1)
    if not os.path.exists(CARSOWNR_FILE):
        print(f"ERROR: {CARSOWNR_FILE} not found")
        sys.exit(1)
    
    # Connect to MongoDB
    print(f"\nConnecting to MongoDB: {MONGO_URL}")
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    
    # Load owners first
    owners = load_owners(CARSOWNR_FILE)
    
    # Import aircraft
    count = import_aircraft(db, CARSCURR_FILE, owners)
    
    # Create indexes
    create_indexes(db)
    
    # Verify
    verify_import(db)
    
    print("\n" + "=" * 60)
    print(f"Import complete: {count} aircraft")
    print("=" * 60)
    
    client.close()


if __name__ == "__main__":
    main()
