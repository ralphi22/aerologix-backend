"""Invoice Routes for AeroLogix AI"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from datetime import datetime
from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.invoice import InvoiceCreate, InvoiceResponse
from models.user import User
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def parse_date_string(date_str: Optional[str]) -> Optional[datetime]:
    """Parse date string to datetime object"""
    if not date_str or date_str == '':
        return None
    try:
        if 'T' in date_str:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return datetime.strptime(date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None


@router.get("/aircraft/{aircraft_id}", response_model=List[InvoiceResponse])
async def get_aircraft_invoices(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get all invoices for an aircraft"""
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    # Get invoices
    cursor = db.invoices.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    }).sort("invoice_date", -1)
    
    invoices = []
    async for invoice in cursor:
        invoices.append(InvoiceResponse(
            _id=str(invoice["_id"]),
            user_id=invoice["user_id"],
            aircraft_id=invoice["aircraft_id"],
            invoice_number=invoice.get("invoice_number"),
            invoice_date=invoice.get("invoice_date"),
            supplier=invoice.get("supplier"),
            parts=invoice.get("parts", []),
            subtotal=invoice.get("subtotal"),
            tax=invoice.get("tax"),
            total=invoice.get("total"),
            currency=invoice.get("currency", "CAD"),
            source=invoice.get("source", "ocr"),
            ocr_scan_id=invoice.get("ocr_scan_id"),
            remarks=invoice.get("remarks"),
            created_at=invoice["created_at"],
            updated_at=invoice["updated_at"]
        ))
    
    return invoices


@router.post("/", response_model=InvoiceResponse)
async def create_invoice(
    invoice_data: InvoiceCreate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Create invoice record for an aircraft"""
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": invoice_data.aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    now = datetime.utcnow()
    invoice_doc = {
        "user_id": current_user.id,
        "aircraft_id": invoice_data.aircraft_id,
        "invoice_number": invoice_data.invoice_number,
        "invoice_date": parse_date_string(invoice_data.invoice_date),
        "supplier": invoice_data.supplier,
        "parts": [p.dict() for p in invoice_data.parts],
        "subtotal": invoice_data.subtotal,
        "tax": invoice_data.tax,
        "total": invoice_data.total,
        "currency": invoice_data.currency,
        "source": invoice_data.source,
        "ocr_scan_id": invoice_data.ocr_scan_id,
        "remarks": invoice_data.remarks,
        "created_at": now,
        "updated_at": now
    }
    
    result = await db.invoices.insert_one(invoice_doc)
    
    logger.info(f"Created invoice for aircraft {invoice_data.aircraft_id}")
    
    return InvoiceResponse(
        _id=str(result.inserted_id),
        user_id=current_user.id,
        aircraft_id=invoice_data.aircraft_id,
        invoice_number=invoice_data.invoice_number,
        invoice_date=invoice_doc["invoice_date"],
        supplier=invoice_data.supplier,
        parts=invoice_data.parts,
        subtotal=invoice_data.subtotal,
        tax=invoice_data.tax,
        total=invoice_data.total,
        currency=invoice_data.currency,
        source=invoice_data.source,
        ocr_scan_id=invoice_data.ocr_scan_id,
        remarks=invoice_data.remarks,
        created_at=now,
        updated_at=now
    )


@router.delete("/{invoice_id}")
async def delete_invoice(
    invoice_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Delete invoice record"""
    from bson import ObjectId
    
    try:
        query_id = ObjectId(invoice_id)
    except Exception:
        query_id = invoice_id
    
    result = await db.invoices.delete_one({
        "_id": query_id,
        "user_id": current_user.id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found"
        )
    
    logger.info(f"Deleted invoice {invoice_id}")
    
    return {"message": "Invoice deleted successfully"}
