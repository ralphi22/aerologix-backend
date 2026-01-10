#!/usr/bin/env python3
"""
Transport Canada CARS (Canadian Civil Aircraft Register) Parser

Parses the official Transport Canada aircraft registry files:
- carscurr.txt: Current aircraft registrations
- carsownr.txt: Owner information
- carslayout.txt: Field definitions (for reference)

Produces normalized JSON ready for MongoDB import.

Usage:
    python scripts/parse_tc_cars.py [--limit N] [--output FILE]

Author: AeroLogix AI
"""

import csv
import json
import sys
import argparse
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime


# ============================================================
# FIELD DEFINITIONS (from carslayout.txt)
# ============================================================

# CARSCURR.TXT fields (0-indexed)
CARSCURR_FIELDS = [
    "MARK",                          # 0  - Aircraft Mark (e.g., "AAC")
    "REGISTRATION_SUB_TYPE_E",       # 1  - Activity Sub-type (English)
    "REGISTRATION_SUB_TYPE_F",       # 2  - Activity Sub-type (French)
    "COMMON_NAME",                   # 3  - Common Name / Manufacturer
    "MODEL_NAME",                    # 4  - Model Name
    "MANUFACTURERS_SERIAL_NUMBER",   # 5  - Serial Number
    "MANUFACTURER_SERIAL_COMPRESSED",# 6  - Serial Number, Compressed
    "ID_PLATE_MANUFACTURERS_NAME",   # 7  - Manufacturer on ID Plate
    "BASIS_FOR_REGISTRATION",        # 8  - Basis for Registration (English)
    "BASIS_FOR_REGISTRATION_F",      # 9  - Basis for Registration (French)
    "AIRCRAFT_CATEGORY_E",           # 10 - Aircraft Category (English)
    "AIRCRAFT_CATEGORY_F",           # 11 - Aircraft Category (French)
    "DATE_OF_IMPORT",                # 12 - Date of Import
    "ENGINE_MANUF_E",                # 13 - Engine Manufacturer
    "POWERGLIDER_FLAG",              # 14 - Powerglider (Y/N)
    "ENGINE_CATEGORY_E",             # 15 - Engine Category (English)
    "ENGINE_CATEGORY_F",             # 16 - Engine Category (French)
    "NUMBER_OF_ENGINES",             # 17 - Number of Engines
    "NUMBER_OF_SEATS",               # 18 - Number of Seats
    "AIR_WEIGHT_KILOS",              # 19 - Takeoff Weight (kg)
    "SALE_REPORTED",                 # 20 - Sale Reported (Y/N)
    "ISSUE_DATE",                    # 21 - Certificate Issue Date
    "EFFECTIVE_DATE",                # 22 - Registration Effective Date
    "INEFFECTIVE_DATE",              # 23 - Registration Expiry Date
    "REGISTERED_PURPOSE_E",          # 24 - Purpose (English)
    "REGISTERED_PURPOSE_F",          # 25 - Purpose (French)
    "FLIGHT_AUTHORITY_E",            # 26 - Flight Authority (English)
    "FLIGHT_AUTHORITY_F",            # 27 - Flight Authority (French)
    "MANUFACTURE_OR_ASSEMBLY",       # 28 - M=Manufactured, A=Assembled
    "COUNTRY_MANUFACTURE_ASS_E",     # 29 - Country of Manufacture (English)
    "COUNTRY_MANUFACTURE_ASS_F",     # 30 - Country of Manufacture (French)
    "DATE_MANUFACTURE_ASSEMBLY",     # 31 - Date Manufactured
    "BASE_OF_OPERATIONS_CTRY_E",     # 32 - Base Country (English)
    "BASE_OF_OPERATIONS_CTRY_F",     # 33 - Base Country (French)
    "BASE_PROVINCE_OR_STATE_E",      # 34 - Base Province (English)
    "BASE_PROVINCE_OR_STATE_F",      # 35 - Base Province (French)
    "CITY_AIRPORT",                  # 36 - City/Airport
    "TYPE_CERTIFICATE_NUMBER",       # 37 - Type Certificate
    "REGISTRATION_AUTH_STATUS_E",    # 38 - Status (English)
    "REGISTRATION_AUTH_STATUS_F",    # 39 - Status (French)
    "MULTIPLE_OWNER_FLAG",           # 40 - Multiple Owners (Y/N)
    "MODIFIED_DATE",                 # 41 - Last Modified Date
    "MODE_S_TRANSPONDER_BINARY",     # 42 - Mode S Transponder
    "PHYSICAL_FILE_REGION_E",        # 43 - File Region (English)
    "PHYSICAL_FILE_REGION_F",        # 44 - File Region (French)
    "EX_MILITARY_MARK",              # 45 - Former Military Mark
    "TRIMMED_MARK",                  # 46 - Mark without spaces
]

# CARSOWNR.TXT fields (0-indexed)
CARSOWNR_FIELDS = [
    "MARK_LINK",                     # 0  - Mark (link to CARSCURR)
    "FULL_NAME",                     # 1  - Owner Full Name
    "TRADE_NAME",                    # 2  - Trade Name
    "STREET_NAME",                   # 3  - Street Address Line 1
    "STREET_NAME2",                  # 4  - Street Address Line 2
    "CITY",                          # 5  - City
    "PROVINCE_OR_STATE_E",           # 6  - Province (English)
    "PROVINCE_OR_STATE_F",           # 7  - Province (French)
    "POSTAL_CODE",                   # 8  - Postal Code
    "COUNTRY_E",                     # 9  - Country (English)
    "COUNTRY_F",                     # 10 - Country (French)
    "TYPE_OF_OWNER_E",               # 11 - Owner Type (Individual/Entity)
    "TYPE_OF_OWNER_F",               # 12 - Owner Type (French)
    "ACTIVE_FLAG",                   # 13 - A=Active, I=Inactive
    "CARE_OF",                       # 14 - Care Of
    "REGION_E",                      # 15 - Region (English)
    "REGION_F",                      # 16 - Region (French)
    "OWNER_NAME_OLD_FORMAT",         # 17 - "Lastname,Firstname" format
    "MAIL_RECIPIENT",                # 18 - Mail Recipient (Y/N)
    "TRIMMED_MARK",                  # 19 - Mark without spaces
]


# ============================================================
# PARSING FUNCTIONS
# ============================================================

def clean_value(value: str) -> str:
    """Clean a field value: strip whitespace and quotes"""
    if not value:
        return ""
    return value.strip().strip('"').strip()


def parse_date(date_str: str) -> Optional[str]:
    """
    Parse TC date format (YYYY/MM/DD) to ISO format (YYYY-MM-DD).
    Returns None if invalid or empty.
    """
    date_str = clean_value(date_str)
    if not date_str:
        return None
    
    try:
        # TC format: YYYY/MM/DD
        dt = datetime.strptime(date_str, "%Y/%m/%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_owner_name(full_name: str, old_format: str, owner_type: str = "") -> Dict[str, str]:
    """
    Parse owner name into given_name and family_name.
    
    Uses OWNER_NAME_OLD_FORMAT which is "Lastname,Firstname Initials"
    Falls back to FULL_NAME if old_format is not usable.
    
    For Entity/Company owners, family_name contains the company name.
    """
    result = {
        "given_name": "",
        "family_name": "",
        "full_name": clean_value(full_name),
        "is_company": False
    }
    
    owner_type = clean_value(owner_type).lower()
    old_format = clean_value(old_format)
    
    # Check if this is a company/entity
    if owner_type in ["entity", "une personne morale", "company", "manufacturer"]:
        result["is_company"] = True
        result["family_name"] = result["full_name"]  # Company name goes in family_name
        return result
    
    if old_format and "," in old_format:
        # Format: "Lastname,Firstname Initials"
        parts = old_format.split(",", 1)
        result["family_name"] = parts[0].strip()
        if len(parts) > 1:
            # Take first word as given name
            given_parts = parts[1].strip().split()
            if given_parts:
                result["given_name"] = given_parts[0]
    elif result["full_name"]:
        # Fallback: try to split full name
        parts = result["full_name"].split()
        if len(parts) >= 2:
            result["given_name"] = parts[0]
            result["family_name"] = parts[-1]
        elif len(parts) == 1:
            result["family_name"] = parts[0]
    
    return result


def parse_csv_line(line: str) -> List[str]:
    """
    Parse a TC CSV line with proper handling of quoted fields.
    Format: "value1","value2","value3",...
    """
    # Use csv module for proper parsing
    try:
        reader = csv.reader([line], quotechar='"', skipinitialspace=True)
        return list(reader)[0]
    except Exception:
        # Fallback: simple split
        return [f.strip().strip('"') for f in line.split('","')]


def parse_carscurr(filepath: Path, limit: Optional[int] = None) -> Dict[str, Dict]:
    """
    Parse carscurr.txt and return dict keyed by MARK.
    
    Returns:
        Dict[mark, aircraft_data]
    """
    aircraft = {}
    count = 0
    
    print(f"Parsing {filepath}...")
    
    with open(filepath, 'r', encoding='latin-1') as f:
        for line in f:
            if limit and count >= limit:
                break
            
            line = line.strip()
            if not line:
                continue
            
            fields = parse_csv_line(line)
            
            if len(fields) < 47:
                # Pad with empty strings if needed
                fields.extend([""] * (47 - len(fields)))
            
            mark = clean_value(fields[0])
            if not mark:
                continue
            
            # Build aircraft record
            aircraft[mark] = {
                "mark": mark,
                "registration": f"C-{mark}",
                "manufacturer": clean_value(fields[3]),  # COMMON_NAME
                "model": clean_value(fields[4]),         # MODEL_NAME
                "serial_number": clean_value(fields[5]), # MANUFACTURERS_SERIAL_NUMBER
                "manufacturer_full": clean_value(fields[7]),  # ID_PLATE_MANUFACTURERS_NAME
                "category": clean_value(fields[10]),     # AIRCRAFT_CATEGORY_E
                "engine_type": clean_value(fields[15]),  # ENGINE_CATEGORY_E
                "num_engines": clean_value(fields[17]),  # NUMBER_OF_ENGINES
                "num_seats": clean_value(fields[18]),    # NUMBER_OF_SEATS
                "weight_kg": clean_value(fields[19]),    # AIR_WEIGHT_KILOS
                "issue_date": parse_date(fields[21]),    # ISSUE_DATE
                "effective_date": parse_date(fields[22]),# EFFECTIVE_DATE
                "ineffective_date": parse_date(fields[23]), # INEFFECTIVE_DATE
                "purpose": clean_value(fields[24]),      # REGISTERED_PURPOSE_E
                "flight_authority": clean_value(fields[26]), # FLIGHT_AUTHORITY_E
                "country_manufactured": clean_value(fields[29]), # COUNTRY_MANUFACTURE_ASS_E
                "date_manufactured": parse_date(fields[31]), # DATE_MANUFACTURE_ASSEMBLY
                "base_province": clean_value(fields[34]), # BASE_PROVINCE_OR_STATE_E
                "base_city": clean_value(fields[36]),    # CITY_AIRPORT
                "type_certificate": clean_value(fields[37]), # TYPE_CERTIFICATE_NUMBER
                "status": clean_value(fields[38]),       # REGISTRATION_AUTH_STATUS_E
                "mode_s_code": clean_value(fields[42]),  # MODE_S_TRANSPONDER_BINARY
            }
            
            count += 1
    
    print(f"  Parsed {count} aircraft records")
    return aircraft


def parse_carsownr(filepath: Path, limit: Optional[int] = None) -> Dict[str, List[Dict]]:
    """
    Parse carsownr.txt and return dict keyed by MARK.
    
    Note: Multiple owners possible per aircraft, so returns List.
    
    Returns:
        Dict[mark, List[owner_data]]
    """
    owners = {}
    count = 0
    
    print(f"Parsing {filepath}...")
    
    with open(filepath, 'r', encoding='latin-1') as f:
        for line in f:
            if limit and count >= limit:
                break
            
            line = line.strip()
            if not line:
                continue
            
            fields = parse_csv_line(line)
            
            if len(fields) < 20:
                fields.extend([""] * (20 - len(fields)))
            
            mark = clean_value(fields[0])
            if not mark:
                continue
            
            # Parse owner name (pass owner_type for company detection)
            name_info = parse_owner_name(fields[1], fields[17], fields[11])
            
            owner = {
                "full_name": name_info["full_name"],
                "given_name": name_info["given_name"],
                "family_name": name_info["family_name"],
                "is_company": name_info["is_company"],
                "trade_name": clean_value(fields[2]),
                "street": clean_value(fields[3]),
                "city": clean_value(fields[5]),
                "province": clean_value(fields[6]),
                "postal_code": clean_value(fields[8]),
                "country": clean_value(fields[9]),
                "owner_type": clean_value(fields[11]),  # Individual / Entity
                "is_active": clean_value(fields[13]) == "A",
            }
            
            if mark not in owners:
                owners[mark] = []
            owners[mark].append(owner)
            
            count += 1
    
    print(f"  Parsed {count} owner records")
    return owners


def join_aircraft_owners(
    aircraft: Dict[str, Dict], 
    owners: Dict[str, List[Dict]]
) -> List[Dict]:
    """
    Join aircraft and owner data by MARK.
    
    Returns list of normalized aircraft records.
    """
    results = []
    
    for mark, ac_data in aircraft.items():
        # Get first owner (primary)
        owner_list = owners.get(mark, [])
        first_owner = owner_list[0] if owner_list else {}
        
        # Build normalized output
        record = {
            "registration": ac_data["registration"],
            "manufacturer": ac_data["manufacturer"],
            "model": ac_data["model"],
            "designator": ac_data["type_certificate"],
            "serial_number": ac_data["serial_number"],
            "category": ac_data["category"],
            "first_owner_given_name": first_owner.get("given_name", ""),
            "first_owner_family_name": first_owner.get("family_name", ""),
            "first_owner_full_name": first_owner.get("full_name", ""),
            "first_owner_city": first_owner.get("city", ""),
            "first_owner_province": first_owner.get("province", ""),
            "validity_start": ac_data["effective_date"],
            "validity_end": ac_data["ineffective_date"],
            "status": ac_data["status"],
            # Additional fields for completeness
            "_tc_data": {
                "mark": mark,
                "manufacturer_full": ac_data["manufacturer_full"],
                "engine_type": ac_data["engine_type"],
                "num_engines": ac_data["num_engines"],
                "num_seats": ac_data["num_seats"],
                "weight_kg": ac_data["weight_kg"],
                "purpose": ac_data["purpose"],
                "flight_authority": ac_data["flight_authority"],
                "country_manufactured": ac_data["country_manufactured"],
                "date_manufactured": ac_data["date_manufactured"],
                "base_city": ac_data["base_city"],
                "base_province": ac_data["base_province"],
                "mode_s_code": ac_data["mode_s_code"],
                "owner_count": len(owner_list),
            }
        }
        
        results.append(record)
    
    return results


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Parse Transport Canada CARS aircraft registry files"
    )
    parser.add_argument(
        "--data-dir", 
        type=str, 
        default="tc_data",
        help="Directory containing TC data files"
    )
    parser.add_argument(
        "--limit", 
        type=int, 
        default=None,
        help="Limit number of records to parse (for testing)"
    )
    parser.add_argument(
        "--output", 
        type=str, 
        default=None,
        help="Output JSON file path"
    )
    parser.add_argument(
        "--sample", 
        type=int, 
        default=3,
        help="Number of sample records to display"
    )
    
    args = parser.parse_args()
    
    # Resolve paths
    base_dir = Path(args.data_dir)
    if not base_dir.is_absolute():
        # Try relative to script location
        script_dir = Path(__file__).parent.parent
        base_dir = script_dir / args.data_dir
    
    carscurr_path = base_dir / "carscurr.txt"
    carsownr_path = base_dir / "carsownr.txt"
    
    # Verify files exist
    if not carscurr_path.exists():
        print(f"ERROR: {carscurr_path} not found")
        sys.exit(1)
    if not carsownr_path.exists():
        print(f"ERROR: {carsownr_path} not found")
        sys.exit(1)
    
    print("=" * 60)
    print("TRANSPORT CANADA CARS PARSER")
    print("=" * 60)
    print(f"Data directory: {base_dir}")
    print()
    
    # Parse files
    aircraft = parse_carscurr(carscurr_path, args.limit)
    owners = parse_carsownr(carsownr_path, args.limit)
    
    # Join data
    print("\nJoining aircraft and owner data...")
    results = join_aircraft_owners(aircraft, owners)
    print(f"  Produced {len(results)} normalized records")
    
    # Display sample
    print("\n" + "=" * 60)
    print(f"SAMPLE OUTPUT ({args.sample} records)")
    print("=" * 60)
    
    for i, record in enumerate(results[:args.sample]):
        print(f"\n--- Record {i+1} ---")
        # Print main fields (excluding _tc_data)
        output = {k: v for k, v in record.items() if k != "_tc_data"}
        print(json.dumps(output, indent=2, ensure_ascii=False))
    
    # Save to file if requested
    if args.output:
        output_path = Path(args.output)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n✅ Saved {len(results)} records to {output_path}")
    
    # Summary statistics
    print("\n" + "=" * 60)
    print("SUMMARY STATISTICS")
    print("=" * 60)
    
    categories = {}
    manufacturers = {}
    provinces = {}
    
    for r in results:
        cat = r.get("category") or "Unknown"
        mfr = r.get("manufacturer") or "Unknown"
        prov = r.get("_tc_data", {}).get("base_province") or "Unknown"
        
        categories[cat] = categories.get(cat, 0) + 1
        manufacturers[mfr] = manufacturers.get(mfr, 0) + 1
        provinces[prov] = provinces.get(prov, 0) + 1
    
    print("\nTop 10 Manufacturers:")
    for mfr, count in sorted(manufacturers.items(), key=lambda x: -x[1])[:10]:
        print(f"  {mfr}: {count}")
    
    print("\nAircraft Categories:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")
    
    print("\nTop 10 Provinces:")
    for prov, count in sorted(provinces.items(), key=lambda x: -x[1])[:10]:
        print(f"  {prov}: {count}")
    
    return results


if __name__ == "__main__":
    main()
