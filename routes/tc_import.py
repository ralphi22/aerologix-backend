"""
TC PDF Import Routes

Endpoints for importing AD/SB references from Transport Canada PDF documents.

TC-SAFE:
- Import only, no compliance decisions
- Full audit trail
- Source tracking

PATCH MINIMAL: New routes only, no modification to existing endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

from database.mongodb import get_database
from services.auth_deps import get_current_user
from services.tc_pdf_import_service import (
    TCPDFImportService,
    PDFImportResult,
    ImportSource,
    ADSBScope,
)
from models.user import User
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/adsb/tc", tags=["tc-import"])


# ============================================================
# RESPONSE MODELS
# ============================================================

class ImportResponse(BaseModel):
    """Response for TC PDF import"""
    success: bool
    message: str
    filename: str
    pages_processed: int
    references_found: int
    references_inserted: int
    references_updated: int
    references_skipped: int
    references: list = Field(default_factory=list)
    errors: list = Field(default_factory=list)
    source: str = ImportSource.TC_PDF_IMPORT.value
    disclaimer: str = (
        "This import is for INFORMATIONAL PURPOSES ONLY. "
        "Imported references are not validated against Transport Canada. "
        "All airworthiness decisions must be made by a licensed AME/TEA."
    )


# ============================================================
# ENDPOINTS
# ============================================================

@router.post(
    "/import-pdf/{aircraft_id}",
    response_model=ImportResponse,
    summary="Import AD/SB references from TC PDF",
    description="""
    **TC PDF Import Endpoint**
    
    Receives a PDF file containing AD/SB references from Transport Canada
    and extracts the references for storage.
    
    **Process:**
    1. Extract text from PDF
    2. Identify AD/SB reference patterns (CF-XXXX-XX, SB-XXX-XXX, etc.)
    3. Upsert references to MongoDB with source="TC_PDF_IMPORT"
    4. Log import event for audit trail
    
    **TC-SAFE:**
    - Import only, no compliance decisions
    - Does NOT overwrite authoritative TC data
    - Full audit trail
    
    **Accepted formats:** PDF only
    **Max file size:** 10MB
    """
)
async def import_pdf_tc(
    aircraft_id: str,
    file: UploadFile = File(..., description="TC PDF document"),
    scope: Optional[ADSBScope] = None,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Import AD/SB references from a Transport Canada PDF document.
    
    Extracts references and stores them with source tracking.
    TC-SAFE: Import only, no compliance logic.
    """
    logger.info(f"TC PDF Import | aircraft={aircraft_id} | user={current_user.id} | file={file.filename}")
    
    # Validate aircraft ownership
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found or not authorized"
        )
    
    # Validate file type
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required"
        )
    
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted"
        )
    
    # Check content type
    if file.content_type and 'pdf' not in file.content_type.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid content type. Expected PDF."
        )
    
    # Read file content
    try:
        pdf_bytes = await file.read()
    except Exception as e:
        logger.error(f"Failed to read uploaded file: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to read uploaded file"
        )
    
    # Check file size (10MB limit)
    if len(pdf_bytes) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File too large. Maximum size is 10MB."
        )
    
    # Check minimum size (likely corrupt if too small)
    if len(pdf_bytes) < 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File too small. May be corrupted."
        )
    
    # Perform import
    service = TCPDFImportService(db)
    
    try:
        result = await service.import_pdf(
            pdf_bytes=pdf_bytes,
            filename=file.filename,
            aircraft_id=aircraft_id,
            user_id=current_user.id
        )
    except Exception as e:
        logger.error(f"PDF import service failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}"
        )
    
    # Build response message
    if result.success:
        if result.references_found == 0:
            message = "PDF processed successfully. No AD/SB references found."
        else:
            message = (
                f"Import complete. Found {result.references_found} references: "
                f"{result.references_inserted} inserted, "
                f"{result.references_updated} updated, "
                f"{result.references_skipped} skipped."
            )
    else:
        message = f"Import failed: {', '.join(result.errors)}"
    
    logger.info(f"TC PDF Import complete | {message}")
    
    return ImportResponse(
        success=result.success,
        message=message,
        filename=result.filename,
        pages_processed=result.pages_processed,
        references_found=result.references_found,
        references_inserted=result.references_inserted,
        references_updated=result.references_updated,
        references_skipped=result.references_skipped,
        references=[r.model_dump() for r in result.references],
        errors=result.errors,
        source=result.source,
    )


@router.get(
    "/import-history/{aircraft_id}",
    summary="Get TC PDF import history",
    description="Returns audit log of TC PDF imports for an aircraft."
)
async def get_import_history(
    aircraft_id: str,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Get import history for an aircraft.
    
    Returns audit log entries for TC PDF imports.
    """
    # Validate aircraft ownership
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found or not authorized"
        )
    
    # Query audit log
    cursor = db.tc_adsb_audit_log.find(
        {
            "aircraft_id": aircraft_id,
            "event_type": "TC_PDF_IMPORT"
        },
        {"_id": 0}
    ).sort("created_at", -1).limit(limit)
    
    history = await cursor.to_list(length=limit)
    
    # Format dates
    for entry in history:
        if "created_at" in entry and hasattr(entry["created_at"], "isoformat"):
            entry["created_at"] = entry["created_at"].isoformat()
    
    return {
        "aircraft_id": aircraft_id,
        "total_imports": len(history),
        "history": history
    }
