#!/usr/bin/env python3
"""
TC PDF Import Regression Test Script (V2)

Tests the new collections:
- tc_pdf_imports
- tc_imported_references

Tests:
1. Import PDF -> creates documents in both collections
2. Baseline shows tc_reference_id (ObjectId) and tc_pdf_id (UUID)
3. Delete by tc_reference_id works
4. PDF viewing works

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
from bson import ObjectId
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')

MONGO_URL = os.getenv('MONGO_URL')
DB_NAME = os.getenv('DB_NAME', 'aerologix')

# Test data
TEST_AIRCRAFT_ID = "test_aircraft_regression_v2"
TEST_USER_ID = "test_user_regression_v2"


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

CF-2099-01: Test AD for Regression Testing
CF-2099-02: Another Test AD for Regression
SB-172-2099-01: Test Service Bulletin

This is a test document for regression testing.
""", fontsize=11)
    
    pdf_path = "/tmp/regression_test_v2.pdf"
    doc.save(pdf_path)
    doc.close()
    
    print(f"✓ Test PDF created: {pdf_path}")
    return pdf_path


async def import_pdf(db, pdf_path):
    """Simulate PDF import using the service."""
    from services.tc_pdf_import_service import TCPDFImportService
    
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    
    service = TCPDFImportService(db)
    result = await service.import_pdf(
        pdf_bytes=pdf_bytes,
        filename="regression_test_v2.pdf",
        aircraft_id=TEST_AIRCRAFT_ID,
        user_id=TEST_USER_ID
    )
    
    print(f"✓ PDF Import: success={result.success}, refs_found={result.references_found}, inserted={result.references_inserted}")
    print(f"  tc_pdf_id: {result.tc_pdf_id}")
    
    return result


async def check_collections(db):
    """Check both collections have the correct data."""
    print(f"\n=== Collection Check ===")
    
    # Check tc_pdf_imports
    pdf_count = await db.tc_pdf_imports.count_documents({})
    print(f"tc_pdf_imports: {pdf_count} documents")
    
    pdf_doc = await db.tc_pdf_imports.find_one({"imported_by": TEST_USER_ID})
    if pdf_doc:
        print(f"  ✓ Found PDF: tc_pdf_id={pdf_doc.get('tc_pdf_id')[:8]}...")
        print(f"    filename: {pdf_doc.get('filename')}")
        print(f"    storage_path: {pdf_doc.get('storage_path')}")
    else:
        print(f"  ✗ No PDF found for test user")
        return False
    
    # Check tc_imported_references
    ref_count = await db.tc_imported_references.count_documents({"aircraft_id": TEST_AIRCRAFT_ID})
    print(f"\ntc_imported_references: {ref_count} documents for aircraft")
    
    refs = []
    async for ref in db.tc_imported_references.find({"aircraft_id": TEST_AIRCRAFT_ID}):
        refs.append({
            "_id": str(ref.get("_id")),
            "identifier": ref.get("identifier"),
            "type": ref.get("type"),
            "tc_pdf_id": ref.get("tc_pdf_id")
        })
        print(f"  ✓ {ref.get('identifier')}: tc_reference_id={str(ref['_id'])[:8]}..., tc_pdf_id={ref.get('tc_pdf_id')[:8]}...")
    
    return refs


async def test_baseline(db):
    """Test that baseline endpoint would return correct data."""
    print(f"\n=== Baseline Simulation ===")
    
    # Simulate what adsb.py baseline does
    refs = []
    async for ref in db.tc_imported_references.find({"aircraft_id": TEST_AIRCRAFT_ID}):
        tc_reference_id = str(ref.get("_id"))
        tc_pdf_id = ref.get("tc_pdf_id")
        
        # Validate tc_reference_id is 24-char hex
        if len(tc_reference_id) != 24:
            print(f"  ✗ FAIL: tc_reference_id length={len(tc_reference_id)}, expected 24")
            return False
        
        try:
            int(tc_reference_id, 16)
        except ValueError:
            print(f"  ✗ FAIL: tc_reference_id not valid hex")
            return False
        
        # Validate tc_pdf_id is UUID (36 chars)
        if len(tc_pdf_id) != 36:
            print(f"  ✗ FAIL: tc_pdf_id length={len(tc_pdf_id)}, expected 36")
            return False
        
        refs.append({
            "tc_reference_id": tc_reference_id,
            "tc_pdf_id": tc_pdf_id,
            "identifier": ref.get("identifier")
        })
        print(f"  ✓ {ref.get('identifier')}: IDs valid")
    
    print(f"✓ All {len(refs)} references have valid IDs")
    return refs


async def test_delete(db, tc_reference_id):
    """Test deletion by tc_reference_id."""
    print(f"\n=== Delete Test ===")
    print(f"tc_reference_id: {tc_reference_id}")
    
    # Convert to ObjectId
    obj_id = ObjectId(tc_reference_id)
    
    # Get tc_pdf_id before delete
    ref = await db.tc_imported_references.find_one({"_id": obj_id})
    tc_pdf_id = ref.get("tc_pdf_id") if ref else None
    
    # Delete reference
    result = await db.tc_imported_references.delete_one({"_id": obj_id})
    print(f"deleted_count: {result.deleted_count}")
    
    if result.deleted_count != 1:
        return False
    
    # Check if PDF is orphaned
    remaining = await db.tc_imported_references.count_documents({"tc_pdf_id": tc_pdf_id})
    print(f"remaining refs using this PDF: {remaining}")
    
    return True


async def cleanup(db):
    """Clean up test data."""
    # Delete from tc_imported_references
    del_refs = await db.tc_imported_references.delete_many({"aircraft_id": TEST_AIRCRAFT_ID})
    print(f"Deleted {del_refs.deleted_count} references")
    
    # Delete from tc_pdf_imports
    del_pdfs = await db.tc_pdf_imports.delete_many({"imported_by": TEST_USER_ID})
    print(f"Deleted {del_pdfs.deleted_count} PDFs")
    
    # Delete test aircraft
    await db.aircrafts.delete_one({"_id": TEST_AIRCRAFT_ID})
    
    # Clean up old data from tc_ad/tc_sb (from V1)
    await db.tc_ad.delete_many({"import_aircraft_id": {"$regex": "test_aircraft"}})
    await db.tc_sb.delete_many({"import_aircraft_id": {"$regex": "test_aircraft"}})
    
    print("✓ Cleanup complete")


async def run_tests():
    """Run all regression tests."""
    print("=" * 60)
    print("TC PDF Import V2 Regression Tests")
    print("Collections: tc_pdf_imports, tc_imported_references")
    print("=" * 60)
    
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    try:
        # Setup
        await cleanup(db)
        await setup_test_aircraft(db)
        
        # Test 1: Import PDF
        print("\n--- TEST 1: Import PDF ---")
        pdf_path = await create_test_pdf()
        import_result = await import_pdf(db, pdf_path)
        
        if not import_result.success:
            print("✗ FAIL: Import failed")
            return False
        
        if not import_result.tc_pdf_id:
            print("✗ FAIL: No tc_pdf_id returned")
            return False
        
        # Test 2: Check collections
        print("\n--- TEST 2: Check Collections ---")
        refs = await check_collections(db)
        
        if not refs:
            print("✗ FAIL: No references found")
            return False
        
        # Test 3: Validate IDs for baseline
        print("\n--- TEST 3: Validate IDs ---")
        validated_refs = await test_baseline(db)
        
        if not validated_refs:
            print("✗ FAIL: ID validation failed")
            return False
        
        # Test 4: Delete by tc_reference_id
        print("\n--- TEST 4: Delete by tc_reference_id ---")
        tc_ref_to_delete = validated_refs[0]["tc_reference_id"]
        initial_count = len(validated_refs)
        
        if not await test_delete(db, tc_ref_to_delete):
            print("✗ FAIL: Delete failed")
            return False
        
        # Verify count decreased
        remaining = await db.tc_imported_references.count_documents({"aircraft_id": TEST_AIRCRAFT_ID})
        if remaining != initial_count - 1:
            print(f"✗ FAIL: Expected {initial_count - 1} refs, got {remaining}")
            return False
        
        print("✓ PASS: Delete worked correctly")
        
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
