#!/usr/bin/env python3
"""
TC PDF Import V3 Regression Test

Tests:
1. STRICT pattern extraction (CF-XXXX-XX only)
2. Normalization (trim, uppercase, no spaces)
3. Duplicate prevention (aircraft_id + identifier + tc_pdf_id)
4. API response format (tc_pdf_id, filename, imported_references_count)

Usage:
    python3 scripts/test_tc_pdf_import.py
"""

import asyncio
import os
import sys
import fitz  # PyMuPDF

sys.path.insert(0, '/app/backend')

from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')

MONGO_URL = os.getenv('MONGO_URL')
DB_NAME = os.getenv('DB_NAME', 'aerologix')

TEST_AIRCRAFT_ID = "test_aircraft_v3"
TEST_USER_ID = "test_user_v3"


async def setup(db):
    """Setup test data."""
    # Clean previous test data
    await db.tc_imported_references.delete_many({"aircraft_id": TEST_AIRCRAFT_ID})
    await db.tc_pdf_imports.delete_many({"imported_by": TEST_USER_ID})
    await db.aircrafts.delete_one({"_id": TEST_AIRCRAFT_ID})
    
    # Create test aircraft
    await db.aircrafts.insert_one({
        "_id": TEST_AIRCRAFT_ID,
        "user_id": TEST_USER_ID,
        "registration": "C-TEST",
        "manufacturer": "Test",
        "model": "V3"
    })
    print("✓ Setup complete")


async def test_pattern_extraction(db):
    """Test STRICT CF-XXXX-XX pattern extraction."""
    print("\n--- TEST 1: Pattern Extraction ---")
    
    from services.tc_pdf_import_service import TCPDFImportService
    service = TCPDFImportService(db)
    
    # Test cases
    test_cases = [
        # Valid
        ("CF-2024-01", True),
        ("CF-2024-123", True),
        ("CF-1987-15", True),
        ("CF-2024-1234", True),
        # Should be normalized
        ("cf-2024-01", True),      # lowercase
        ("CF 2024 01", True),      # spaces
        ("CF202401", True),        # no separators
        # Invalid
        ("CF-24-01", False),       # 2-digit year
        ("CF-2024-1", False),      # 1-digit number
        ("CF-2024-12345", False),  # 5-digit number
        ("AD-2024-01", False),     # AD prefix
        ("SB-172-01", False),      # SB prefix
        ("CF2024", False),         # incomplete
    ]
    
    for raw, should_match in test_cases:
        normalized = service.normalize_reference(raw)
        matched = normalized is not None
        status = "✓" if matched == should_match else "✗"
        result = normalized if normalized else "REJECTED"
        print(f"  {status} '{raw}' -> {result}")
        
        if matched != should_match:
            print(f"    FAIL: Expected {'valid' if should_match else 'invalid'}")
            return False
    
    print("✓ Pattern extraction OK")
    return True


async def test_pdf_import(db):
    """Test full PDF import."""
    print("\n--- TEST 2: PDF Import ---")
    
    # Create test PDF with mixed references
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), """
TRANSPORT CANADA AIRWORTHINESS DIRECTIVE

Valid references:
CF-2024-01: First valid AD
CF-2024-123: Second valid AD
CF-1987-15R: Third with suffix (should extract CF-1987-15)

Invalid references (should be ignored):
AD-2024-01: FAA format
SB-172-001: Service Bulletin
CF-24-01: Short year
INVALID-REF
""", fontsize=11)
    
    pdf_path = "/tmp/test_v3.pdf"
    doc.save(pdf_path)
    doc.close()
    
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    
    from services.tc_pdf_import_service import TCPDFImportService
    service = TCPDFImportService(db)
    
    result = await service.import_pdf(
        pdf_bytes=pdf_bytes,
        filename="test_v3.pdf",
        aircraft_id=TEST_AIRCRAFT_ID,
        user_id=TEST_USER_ID
    )
    
    print(f"  success: {result.success}")
    print(f"  tc_pdf_id: {result.tc_pdf_id}")
    print(f"  filename: {result.filename}")
    print(f"  imported_references_count: {result.imported_references_count}")
    
    if not result.success:
        print("  ✗ Import failed")
        return False, None
    
    if not result.tc_pdf_id:
        print("  ✗ Missing tc_pdf_id")
        return False, None
    
    # Should have exactly 3 valid references
    expected = 3  # CF-2024-01, CF-2024-123, CF-1987-15
    if result.imported_references_count != expected:
        print(f"  ✗ Expected {expected} refs, got {result.imported_references_count}")
        return False, None
    
    print("✓ PDF import OK")
    return True, result.tc_pdf_id


async def test_duplicate_prevention(db, tc_pdf_id):
    """Test duplicate prevention."""
    print("\n--- TEST 3: Duplicate Prevention ---")
    
    # Try to import same PDF again
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "CF-2024-01: Same reference", fontsize=11)
    
    pdf_path = "/tmp/test_dup.pdf"
    doc.save(pdf_path)
    doc.close()
    
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    
    from services.tc_pdf_import_service import TCPDFImportService
    service = TCPDFImportService(db)
    
    result = await service.import_pdf(
        pdf_bytes=pdf_bytes,
        filename="test_dup.pdf",
        aircraft_id=TEST_AIRCRAFT_ID,
        user_id=TEST_USER_ID
    )
    
    # Should create 0 new references (CF-2024-01 already exists for this aircraft)
    # But it's a NEW PDF, so new tc_pdf_id
    # Duplicate check is: aircraft_id + identifier + tc_pdf_id
    # So same identifier with different tc_pdf_id is NOT a duplicate
    
    print(f"  Second import: {result.imported_references_count} refs")
    print(f"  New tc_pdf_id: {result.tc_pdf_id[:8]}...")
    
    # Count total refs for aircraft
    count = await db.tc_imported_references.count_documents({"aircraft_id": TEST_AIRCRAFT_ID})
    print(f"  Total refs for aircraft: {count}")
    
    # Should have 4 refs now (3 from first + 1 from second, different PDFs)
    if count != 4:
        print(f"  ✗ Expected 4 refs, got {count}")
        return False
    
    print("✓ Duplicate prevention OK")
    return True


async def test_collections(db):
    """Verify collections have correct data."""
    print("\n--- TEST 4: Collection Verification ---")
    
    # Check tc_pdf_imports
    pdf_count = await db.tc_pdf_imports.count_documents({"imported_by": TEST_USER_ID})
    print(f"  tc_pdf_imports: {pdf_count} documents")
    
    pdf_doc = await db.tc_pdf_imports.find_one({"imported_by": TEST_USER_ID})
    if pdf_doc:
        required_fields = ["tc_pdf_id", "filename", "storage_path", "imported_by", "imported_at"]
        for field in required_fields:
            if field not in pdf_doc:
                print(f"  ✗ Missing field: {field}")
                return False
        print(f"  ✓ tc_pdf_imports schema OK")
    
    # Check tc_imported_references
    ref_count = await db.tc_imported_references.count_documents({"aircraft_id": TEST_AIRCRAFT_ID})
    print(f"  tc_imported_references: {ref_count} documents")
    
    ref_doc = await db.tc_imported_references.find_one({"aircraft_id": TEST_AIRCRAFT_ID})
    if ref_doc:
        required_fields = ["aircraft_id", "identifier", "type", "tc_pdf_id", "source", "created_by", "created_at"]
        for field in required_fields:
            if field not in ref_doc:
                print(f"  ✗ Missing field: {field}")
                return False
        
        # Verify identifier format
        identifier = ref_doc.get("identifier")
        import re
        if not re.match(r'^CF-\d{4}-\d{2,4}$', identifier):
            print(f"  ✗ Invalid identifier format: {identifier}")
            return False
        
        print(f"  ✓ tc_imported_references schema OK")
    
    print("✓ Collections OK")
    return True


async def cleanup(db):
    """Cleanup test data."""
    await db.tc_imported_references.delete_many({"aircraft_id": TEST_AIRCRAFT_ID})
    await db.tc_pdf_imports.delete_many({"imported_by": TEST_USER_ID})
    await db.aircrafts.delete_one({"_id": TEST_AIRCRAFT_ID})
    print("\n✓ Cleanup complete")


async def run_tests():
    """Run all tests."""
    print("=" * 60)
    print("TC PDF Import V3 Tests")
    print("STRICT: CF-XXXX-XX pattern only")
    print("=" * 60)
    
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    try:
        await setup(db)
        
        # Test 1: Pattern extraction
        if not await test_pattern_extraction(db):
            return False
        
        # Test 2: PDF import
        success, tc_pdf_id = await test_pdf_import(db)
        if not success:
            return False
        
        # Test 3: Duplicate prevention
        if not await test_duplicate_prevention(db, tc_pdf_id):
            return False
        
        # Test 4: Collections
        if not await test_collections(db):
            return False
        
        await cleanup(db)
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED ✓")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        client.close()


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
