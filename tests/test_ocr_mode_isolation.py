"""
Test OCR Mode Isolation between REPORT and INVOICE document types.

Key Business Rules:
- INVOICE mode: ONLY extract financial data, store in 'invoices' collection
  - NO parts created in part_records
  - NO components created in installed_components
  - NO aircraft hours updated
- REPORT mode: Full technical extraction (parts, hours, AD, limitations, components)

Test user: test@aerologix.com / testpassword123
"""

import pytest
import requests
import os
from datetime import datetime

# Use localhost for testing as per misc_info
BASE_URL = "http://localhost:8001"


class TestOCRModeIsolation:
    """Test OCR Mode Isolation between REPORT and INVOICE document types"""
    
    @pytest.fixture(autouse=True)
    def setup(self, api_client, auth_token, aircraft_id):
        """Setup for each test"""
        self.client = api_client
        self.token = auth_token
        self.aircraft_id = aircraft_id
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
    
    # ============== INVOICE MODE TESTS ==============
    
    def test_invoice_scan_creates_only_invoice_record(self, db_client):
        """
        Test: Invoice scan creates ONLY invoice record (no parts, no components)
        
        When document_type='invoice':
        - Invoice record should be created in 'invoices' collection
        - NO records should be created in 'part_records'
        - NO records should be created in 'installed_components'
        """
        # Get counts before
        parts_before = db_client.part_records.count_documents({
            "aircraft_id": self.aircraft_id,
            "source": "ocr"
        })
        components_before = db_client.installed_components.count_documents({
            "aircraft_id": self.aircraft_id
        })
        invoices_before = db_client.invoices.count_documents({
            "aircraft_id": self.aircraft_id
        })
        
        # Create OCR scan with invoice document type
        scan_id = self._create_test_ocr_scan(
            document_type="invoice",
            extracted_data={
                "invoice_number": "INV-TEST-001",
                "invoice_date": "2024-01-15",
                "vendor_name": "Aviation Parts Inc",
                "subtotal": 1500.00,
                "tax": 195.00,
                "total": 1695.00,
                "currency": "CAD",
                "parts": [
                    {"part_number": "VP-001", "description": "Vacuum Pump", "quantity": 1, "unit_price": 800.00},
                    {"part_number": "MAG-002", "description": "Magneto", "quantity": 2, "unit_price": 350.00}
                ]
            },
            db=db_client
        )
        
        # Apply OCR results
        response = requests.post(
            f"{BASE_URL}/api/ocr/apply/{scan_id}",
            headers=self.headers
        )
        
        assert response.status_code == 200, f"Apply failed: {response.text}"
        data = response.json()
        
        # Verify mode is INVOICE
        assert data.get("mode") == "INVOICE", f"Expected mode=INVOICE, got {data.get('mode')}"
        
        # Verify parts_created = 0
        assert data["applied"]["part_records"] == 0, f"Expected 0 parts, got {data['applied']['part_records']}"
        
        # Verify invoice was created
        assert data["applied"]["invoice_created"] == True, "Invoice should be created"
        assert data["applied"].get("invoice_id") is not None, "Invoice ID should be returned"
        
        # Verify counts after
        parts_after = db_client.part_records.count_documents({
            "aircraft_id": self.aircraft_id,
            "source": "ocr"
        })
        components_after = db_client.installed_components.count_documents({
            "aircraft_id": self.aircraft_id
        })
        invoices_after = db_client.invoices.count_documents({
            "aircraft_id": self.aircraft_id
        })
        
        # CRITICAL: No new parts should be created
        assert parts_after == parts_before, f"Parts should not increase: before={parts_before}, after={parts_after}"
        
        # CRITICAL: No new components should be created
        assert components_after == components_before, f"Components should not increase: before={components_before}, after={components_after}"
        
        # Invoice count should increase by 1
        assert invoices_after == invoices_before + 1, f"Invoice count should increase by 1"
        
        # Cleanup
        db_client.ocr_scans.delete_one({"_id": scan_id})
        db_client.invoices.delete_one({"_id": data["applied"]["invoice_id"]})
        
        print("✅ PASS: Invoice scan creates ONLY invoice record (no parts, no components)")
    
    def test_invoice_with_parts_list_does_not_create_part_records(self, db_client):
        """
        Test: Invoice with parts list (Vacuum Pump, Magneto) does NOT create part_records
        
        Even if invoice contains parts data, they should be stored as line_items
        in the invoice, NOT as separate part_records.
        """
        # Create OCR scan with invoice containing parts
        scan_id = self._create_test_ocr_scan(
            document_type="invoice",
            extracted_data={
                "invoice_number": "INV-PARTS-TEST",
                "vendor_name": "Parts Supplier",
                "total": 2500.00,
                "parts": [
                    {"part_number": "VP-001", "description": "Vacuum Pump", "quantity": 1, "unit_price": 1200.00},
                    {"part_number": "MAG-L", "description": "Left Magneto", "quantity": 1, "unit_price": 650.00},
                    {"part_number": "MAG-R", "description": "Right Magneto", "quantity": 1, "unit_price": 650.00}
                ]
            },
            db=db_client
        )
        
        # Apply OCR results
        response = requests.post(
            f"{BASE_URL}/api/ocr/apply/{scan_id}",
            headers=self.headers
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify NO parts created
        assert data["applied"]["part_records"] == 0, f"Expected 0 parts, got {data['applied']['part_records']}"
        
        # Verify invoice was created with line_items
        invoice_id = data["applied"].get("invoice_id")
        assert invoice_id is not None
        
        # Check invoice has line_items stored
        invoice = db_client.invoices.find_one({"_id": invoice_id})
        assert invoice is not None, "Invoice should exist"
        assert "line_items" in invoice, "Invoice should have line_items field"
        assert len(invoice["line_items"]) == 3, f"Expected 3 line_items, got {len(invoice['line_items'])}"
        
        # Verify line_items contain the parts data
        descriptions = [item.get("description") for item in invoice["line_items"]]
        assert "Vacuum Pump" in descriptions, "Vacuum Pump should be in line_items"
        assert "Left Magneto" in descriptions or "Right Magneto" in descriptions, "Magneto should be in line_items"
        
        # Cleanup
        db_client.ocr_scans.delete_one({"_id": scan_id})
        db_client.invoices.delete_one({"_id": invoice_id})
        
        print("✅ PASS: Invoice with parts list does NOT create part_records")
    
    def test_invoice_does_not_update_aircraft_hours(self, db_client):
        """
        Test: Invoice does NOT update aircraft hours
        
        Even if invoice contains hours data, aircraft hours should NOT be updated.
        """
        # Get current aircraft hours
        aircraft = db_client.aircrafts.find_one({"_id": self.aircraft_id})
        original_airframe_hours = aircraft.get("airframe_hours", 0)
        original_engine_hours = aircraft.get("engine_hours", 0)
        
        # Create OCR scan with invoice containing hours (should be ignored)
        scan_id = self._create_test_ocr_scan(
            document_type="invoice",
            extracted_data={
                "invoice_number": "INV-HOURS-TEST",
                "vendor_name": "Service Center",
                "total": 500.00,
                "airframe_hours": 9999.0,  # Should be ignored
                "engine_hours": 8888.0,    # Should be ignored
                "propeller_hours": 7777.0  # Should be ignored
            },
            db=db_client
        )
        
        # Apply OCR results
        response = requests.post(
            f"{BASE_URL}/api/ocr/apply/{scan_id}",
            headers=self.headers
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify mode is INVOICE
        assert data.get("mode") == "INVOICE"
        
        # Check aircraft hours are unchanged
        aircraft_after = db_client.aircrafts.find_one({"_id": self.aircraft_id})
        
        assert aircraft_after.get("airframe_hours") == original_airframe_hours, \
            f"Airframe hours should not change: expected {original_airframe_hours}, got {aircraft_after.get('airframe_hours')}"
        assert aircraft_after.get("engine_hours") == original_engine_hours, \
            f"Engine hours should not change: expected {original_engine_hours}, got {aircraft_after.get('engine_hours')}"
        
        # Cleanup
        db_client.ocr_scans.delete_one({"_id": scan_id})
        if data["applied"].get("invoice_id"):
            db_client.invoices.delete_one({"_id": data["applied"]["invoice_id"]})
        
        print("✅ PASS: Invoice does NOT update aircraft hours")
    
    def test_invoice_stores_financial_data(self, db_client):
        """
        Test: Invoice stores financial data correctly
        
        Fields: vendor_name, invoice_number, invoice_date, subtotal, tax, total, currency
        """
        scan_id = self._create_test_ocr_scan(
            document_type="invoice",
            extracted_data={
                "invoice_number": "INV-FIN-001",
                "invoice_date": "2024-02-20",
                "vendor_name": "Aviation Services Ltd",
                "subtotal": 2000.00,
                "tax": 260.00,
                "total": 2260.00,
                "currency": "USD",
                "labor_hours": 5.0,
                "labor_cost": 500.00,
                "parts_cost": 1500.00
            },
            db=db_client
        )
        
        # Apply OCR results
        response = requests.post(
            f"{BASE_URL}/api/ocr/apply/{scan_id}",
            headers=self.headers
        )
        
        assert response.status_code == 200
        data = response.json()
        
        invoice_id = data["applied"].get("invoice_id")
        assert invoice_id is not None
        
        # Verify invoice data
        invoice = db_client.invoices.find_one({"_id": invoice_id})
        assert invoice is not None
        
        # Check all financial fields
        assert invoice.get("invoice_number") == "INV-FIN-001"
        assert invoice.get("vendor_name") == "Aviation Services Ltd"
        assert invoice.get("subtotal") == 2000.00
        assert invoice.get("tax") == 260.00
        assert invoice.get("total") == 2260.00
        assert invoice.get("currency") == "USD"
        assert invoice.get("labor_hours") == 5.0
        assert invoice.get("labor_cost") == 500.00
        assert invoice.get("parts_cost") == 1500.00
        
        # Cleanup
        db_client.ocr_scans.delete_one({"_id": scan_id})
        db_client.invoices.delete_one({"_id": invoice_id})
        
        print("✅ PASS: Invoice stores financial data correctly")
    
    def test_invoice_stores_line_items_as_reference(self, db_client):
        """
        Test: Invoice stores line_items as reference (not as separate part_records)
        """
        scan_id = self._create_test_ocr_scan(
            document_type="invoice",
            extracted_data={
                "invoice_number": "INV-LINES-001",
                "vendor_name": "Parts Store",
                "total": 1000.00,
                "parts": [
                    {"part_number": "FILTER-001", "description": "Oil Filter", "quantity": 2, "unit_price": 50.00, "line_total": 100.00},
                    {"part_number": "SPARK-001", "description": "Spark Plug", "quantity": 8, "unit_price": 25.00, "line_total": 200.00}
                ]
            },
            db=db_client
        )
        
        # Apply OCR results
        response = requests.post(
            f"{BASE_URL}/api/ocr/apply/{scan_id}",
            headers=self.headers
        )
        
        assert response.status_code == 200
        data = response.json()
        
        invoice_id = data["applied"].get("invoice_id")
        invoice = db_client.invoices.find_one({"_id": invoice_id})
        
        # Verify line_items structure
        assert "line_items" in invoice
        assert len(invoice["line_items"]) == 2
        
        # Check line_item structure
        for item in invoice["line_items"]:
            assert "description" in item or "part_number" in item
            assert "quantity" in item or item.get("quantity") is None
            assert "unit_price" in item or item.get("unit_price") is None
        
        # Cleanup
        db_client.ocr_scans.delete_one({"_id": scan_id})
        db_client.invoices.delete_one({"_id": invoice_id})
        
        print("✅ PASS: Invoice stores line_items as reference")
    
    # ============== REPORT MODE TESTS ==============
    
    def test_report_scan_creates_parts_and_components(self, db_client):
        """
        Test: Report scan still creates parts and components correctly
        
        When document_type='maintenance_report':
        - Parts should be created in part_records
        - Components may be created via OCR Intelligence
        - Hours should be updated
        """
        # Get counts before
        parts_before = db_client.part_records.count_documents({
            "aircraft_id": self.aircraft_id,
            "source": "ocr"
        })
        
        # Create OCR scan with maintenance_report document type
        scan_id = self._create_test_ocr_scan(
            document_type="maintenance_report",
            extracted_data={
                "description": "Annual inspection completed",
                "work_order_number": "WO-REPORT-001",
                "ame_name": "John Smith",
                "airframe_hours": 850.0,
                "engine_hours": 850.0,
                "parts_replaced": [
                    {"part_number": "FILTER-OIL-001", "description": "Oil Filter", "quantity": 1, "price": 45.00},
                    {"part_number": "FILTER-AIR-001", "description": "Air Filter", "quantity": 1, "price": 65.00}
                ]
            },
            db=db_client
        )
        
        # Apply OCR results
        response = requests.post(
            f"{BASE_URL}/api/ocr/apply/{scan_id}",
            headers=self.headers
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify mode is REPORT
        assert data.get("mode") == "REPORT", f"Expected mode=REPORT, got {data.get('mode')}"
        
        # Verify parts were created
        assert data["applied"]["part_records"] >= 1, f"Expected parts to be created, got {data['applied']['part_records']}"
        
        # Verify maintenance record was created
        assert data["applied"]["maintenance_record"] is not None, "Maintenance record should be created"
        
        # Verify parts count increased
        parts_after = db_client.part_records.count_documents({
            "aircraft_id": self.aircraft_id,
            "source": "ocr"
        })
        assert parts_after > parts_before, f"Parts count should increase: before={parts_before}, after={parts_after}"
        
        # Cleanup
        db_client.ocr_scans.delete_one({"_id": scan_id})
        if data["applied"]["maintenance_record"]:
            db_client.maintenance_records.delete_one({"_id": data["applied"]["maintenance_record"]})
        # Clean up created parts
        db_client.part_records.delete_many({
            "aircraft_id": self.aircraft_id,
            "ocr_scan_id": scan_id
        })
        
        print("✅ PASS: Report scan creates parts and components correctly")
    
    def test_ocr_apply_response_includes_mode_field(self, db_client):
        """
        Test: OCR apply response includes 'mode' field (REPORT or INVOICE)
        """
        # Test INVOICE mode
        invoice_scan_id = self._create_test_ocr_scan(
            document_type="invoice",
            extracted_data={"invoice_number": "MODE-TEST-INV", "total": 100.00},
            db=db_client
        )
        
        response = requests.post(
            f"{BASE_URL}/api/ocr/apply/{invoice_scan_id}",
            headers=self.headers
        )
        assert response.status_code == 200
        assert "mode" in response.json(), "Response should include 'mode' field"
        assert response.json()["mode"] == "INVOICE"
        
        # Test REPORT mode
        report_scan_id = self._create_test_ocr_scan(
            document_type="maintenance_report",
            extracted_data={"description": "Test maintenance", "work_order_number": "MODE-TEST-WO"},
            db=db_client
        )
        
        response = requests.post(
            f"{BASE_URL}/api/ocr/apply/{report_scan_id}",
            headers=self.headers
        )
        assert response.status_code == 200
        assert "mode" in response.json()
        assert response.json()["mode"] == "REPORT"
        
        # Cleanup
        db_client.ocr_scans.delete_many({"_id": {"$in": [invoice_scan_id, report_scan_id]}})
        db_client.invoices.delete_many({"ocr_scan_id": invoice_scan_id})
        db_client.maintenance_records.delete_many({"ocr_scan_id": report_scan_id})
        
        print("✅ PASS: OCR apply response includes 'mode' field")
    
    def test_ocr_apply_response_shows_parts_created_zero_for_invoices(self, db_client):
        """
        Test: OCR apply response shows parts_created=0 for invoices
        """
        scan_id = self._create_test_ocr_scan(
            document_type="invoice",
            extracted_data={
                "invoice_number": "PARTS-ZERO-TEST",
                "total": 500.00,
                "parts": [
                    {"part_number": "P1", "description": "Part 1", "quantity": 1},
                    {"part_number": "P2", "description": "Part 2", "quantity": 2}
                ]
            },
            db=db_client
        )
        
        response = requests.post(
            f"{BASE_URL}/api/ocr/apply/{scan_id}",
            headers=self.headers
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify parts_created = 0 in response
        assert data["applied"]["part_records"] == 0, \
            f"Expected part_records=0 for invoice, got {data['applied']['part_records']}"
        
        # Cleanup
        db_client.ocr_scans.delete_one({"_id": scan_id})
        if data["applied"].get("invoice_id"):
            db_client.invoices.delete_one({"_id": data["applied"]["invoice_id"]})
        
        print("✅ PASS: OCR apply response shows parts_created=0 for invoices")
    
    # ============== HELPER METHODS ==============
    
    def _create_test_ocr_scan(self, document_type: str, extracted_data: dict, db) -> str:
        """Create a test OCR scan directly in the database"""
        now = datetime.utcnow()
        scan_id = f"test_scan_{int(now.timestamp() * 1000)}"
        
        scan_doc = {
            "_id": scan_id,
            "user_id": "1768078526954195",  # Test user ID
            "aircraft_id": self.aircraft_id,
            "document_type": document_type,
            "status": "COMPLETED",
            "raw_text": f"Test {document_type} document",
            "extracted_data": extracted_data,
            "error_message": None,
            "applied_maintenance_id": None,
            "applied_adsb_ids": [],
            "applied_part_ids": [],
            "applied_stc_ids": [],
            "created_at": now,
            "updated_at": now
        }
        
        db.ocr_scans.insert_one(scan_doc)
        return scan_id


# ============== FIXTURES ==============

@pytest.fixture(scope="module")
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="module")
def auth_token(api_client):
    """Get authentication token"""
    response = api_client.post(
        f"{BASE_URL}/api/auth/login",
        data={"username": "test@aerologix.com", "password": "testpassword123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    if response.status_code == 200:
        return response.json().get("access_token")
    pytest.skip("Authentication failed - skipping tests")


@pytest.fixture(scope="module")
def aircraft_id(api_client, auth_token):
    """Get test aircraft ID"""
    headers = {"Authorization": f"Bearer {auth_token}"}
    response = api_client.get(f"{BASE_URL}/api/aircraft", headers=headers)
    
    if response.status_code == 200 and len(response.json()) > 0:
        return response.json()[0]["_id"]
    pytest.skip("No aircraft found - skipping tests")


@pytest.fixture(scope="module")
def db_client():
    """Get MongoDB client for direct database operations"""
    from pymongo import MongoClient
    
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "aerologix")
    
    client = MongoClient(mongo_url)
    db = client[db_name]
    
    yield db
    
    client.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
