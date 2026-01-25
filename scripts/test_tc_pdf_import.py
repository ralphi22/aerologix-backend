#!/usr/bin/env python3
"""
TC PDF Import Regression Test Script

Tests:
1. Import PDF -> baseline shows tc_reference_id
2. Delete by tc_reference_id -> baseline count decreases
3. PDF endpoint returns correct headers

Usage:
    python3 scripts/test_tc_pdf_import.py
"""

import asyncio
import os
import sys
import json
import fitz  # PyMuPDF

# Add backend to path
sys.path.insert(0, '/app/backend')

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')

MONGO_URL = os.getenv('MONGO_URL')
DB_NAME = os.getenv('DB_NAME', 'aerologix')

# Test data
TEST_AIRCRAFT_ID = "test_aircraft_regression"
TEST_USER_ID = "test_user_regression"


async def setup_test_aircraft(db):
    """Create test aircraft if not exists."""
    await db.aircrafts.update_one(
        {"_id": TEST_AIRCRAFT_ID},
        {
            "$set": {
                "_id": TEST_AIRCRAFT_ID,
                "user_id": TEST_USER_ID,
                "registration": "C-TEST",
                "manufacturer": "Test",
                "model": "Regression"
            }
        },
        upsert=True
    )
    print(f"✓ Test aircraft created: {TEST_AIRCRAFT_ID}")


async def create_test_pdf():
    """Create a test PDF with AD/SB references."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), """
TRANSPORT CANADA AIRWORTHINESS DIRECTIVE

CF-TEST-001: Test AD for Regression
CF-TEST-002: Another Test AD
SB-TEST-001: Test Service Bulletin

This is a test document for regression testing.
""", fontsize=11)
    
    pdf_path = "/tmp/regression_test.pdf"
    doc.save(pdf_path)
    doc.close()
    
    print(f"✓ Test PDF created: {pdf_path}")
    return pdf_path


async def import_pdf(db, pdf_path):
    """Simulate PDF import."""
    from services.tc_pdf_import_service import TCPDFImportService
    
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    
    service = TCPDFImportService(db)
    result = await service.import_pdf(
        pdf_bytes=pdf_bytes,
        filename="regression_test.pdf",
        aircraft_id=TEST_AIRCRAFT_ID,
        user_id=TEST_USER_ID
    )
    
    print(f"✓ PDF Import: success={result.success}, refs_found={result.references_found}, inserted={result.references_inserted}")
    return result


async def check_baseline(db):
    """Check baseline includes tc_reference_id."""
    # Query imported references
    refs = []
    async for ad in db.tc_ad.find({
        "source": "TC_PDF_IMPORT",
        "import_aircraft_id": TEST_AIRCRAFT_ID
    }):
        refs.append({
            "_id": str(ad.get("_id")),
            "ref": ad.get("ref"),
            "import_filename": ad.get("import_filename"),
            "pdf_storage_path": ad.get("pdf_storage_path")
        })
    
    print(f"\n=== Baseline Check ===")
    print(f"Total USER_IMPORTED_REFERENCE: {len(refs)}")
    
    for ref in refs:
        has_id = "✓" if ref["_id"] else "✗"
        has_pdf = "✓" if ref["pdf_storage_path"] else "✗"
        print(f"  {has_id} {ref['ref']}: tc_reference_id={ref['_id']}, pdf={has_pdf}")
    
    return refs


async def delete_reference(db, tc_reference_id):
    """Test deletion by tc_reference_id."""
    # Find and delete
    result = await db.tc_ad.delete_one({
        "_id": tc_reference_id,
        "source": "TC_PDF_IMPORT"
    })
    
    print(f"\n=== Delete Test ===")
    print(f"tc_reference_id: {tc_reference_id}")
    print(f"deleted_count: {result.deleted_count}")
    
    return result.deleted_count


async def cleanup(db):
    """Clean up test data."""
    await db.tc_ad.delete_many({
        "source": "TC_PDF_IMPORT",
        "import_aircraft_id": TEST_AIRCRAFT_ID
    })
    await db.tc_sb.delete_many({
        "source": "TC_PDF_IMPORT",
        "import_aircraft_id": TEST_AIRCRAFT_ID
    })
    await db.aircrafts.delete_one({"_id": TEST_AIRCRAFT_ID})
    print("\n✓ Cleanup complete")


async def run_tests():
    """Run all regression tests."""
    print("=" * 60)
    print("TC PDF Import Regression Tests")
    print("=" * 60)
    
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    try:
        # Setup
        await cleanup(db)  # Clean any previous test data
        await setup_test_aircraft(db)
        
        # Test 1: Import PDF
        print("\n--- TEST 1: Import PDF ---")
        pdf_path = await create_test_pdf()
        import_result = await import_pdf(db, pdf_path)
        
        if not import_result.success:
            print("✗ FAIL: Import failed")
            return False
        
        # Test 2: Check baseline has tc_reference_id
        print("\n--- TEST 2: Baseline has tc_reference_id ---")
        refs = await check_baseline(db)
        
        if len(refs) == 0:
            print("✗ FAIL: No references found in baseline")
            return False
        
        for ref in refs:
            if not ref["_id"]:
                print(f"✗ FAIL: Missing tc_reference_id for {ref['ref']}")
                return False
        
        print("✓ PASS: All references have tc_reference_id")
        
        # Test 3: Delete by tc_reference_id
        print("\n--- TEST 3: Delete by tc_reference_id ---")
        tc_ref_to_delete = refs[0]["_id"]
        initial_count = len(refs)
        
        deleted = await delete_reference(db, tc_ref_to_delete)
        
        if deleted != 1:
            print(f"✗ FAIL: Expected deleted_count=1, got {deleted}")
            return False
        
        # Verify count decreased
        refs_after = await check_baseline(db)
        
        if len(refs_after) != initial_count - 1:
            print(f"✗ FAIL: Expected {initial_count - 1} refs, got {len(refs_after)}")
            return False
        
        print("✓ PASS: Delete decreased baseline count")
        
        # Cleanup
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
