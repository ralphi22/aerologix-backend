"""
TC PDF Import Routes (V3)

Endpoints pour importer des AD depuis des PDF Transport Canada.
Utilise les collections dédiées: tc_pdf_imports et tc_imported_references.

TC-SAFE:
- Import only, no compliance decisions
- Full audit trail
- Source tracking

SÉPARATION STRICTE:
- Ne touche PAS aux collections tc_ad/tc_sb
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import FileResponse
from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from bson import ObjectId
import os
import glob
import logging

from database.mongodb import get_database
from services.auth_deps import get_current_user
from services.tc_pdf_import_service import TCPDFImportService
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/adsb/tc", tags=["tc-import"])


# ============================================================
# RESPONSE MODELS
# ============================================================

class ImportResponse(BaseModel):
    """Response for TC PDF import - D) Réponse API"""
    tc_pdf_id: str
    filename: str
    imported_references_count: int


class ImportedReferenceItem(BaseModel):
    """Single imported reference in list response"""
    tc_reference_id: str = Field(..., description="MongoDB ObjectId - use for DELETE")
    identifier: str = Field(..., description="TC reference (e.g., CF-2024-01)")
    type: str
    tc_pdf_id: str = Field(..., description="UUID - use for GET PDF")
    created_at: str


class ImportedReferencesResponse(BaseModel):
    """Response for listing imported references"""
    aircraft_id: str
    total_count: int
    references: List[ImportedReferenceItem]


class DeleteReferenceResponse(BaseModel):
    """Response for DELETE reference"""
    ok: bool
    tc_reference_id: str
    identifier: str
    deleted: bool


# ============================================================
# ENDPOINT 1: IMPORT PDF
# ============================================================

@router.post(
    "/import-pdf/{aircraft_id}",
    response_model=ImportResponse,
    summary="Import AD references from TC PDF",
    description="""
    **TC PDF Import Endpoint**
    
    - Extracts only valid CF-XXXX-XX references (^CF-\\d{4}-\\d{2,4}$)
    - Stores PDF in tc_pdf_imports
    - Creates references in tc_imported_references
    
    **TC-SAFE:** Import only, no compliance decisions.
    """
)
async def import_pdf_tc(
    aircraft_id: str,
    file: UploadFile = File(..., description="TC PDF document"),
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Import AD references from a Transport Canada PDF document."""
    logger.info(f"[TC PDF IMPORT] aircraft={aircraft_id} user={current_user.id} file={file.filename}")
    
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
    
    # Validate file
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
    
    # Read file content
    try:
        pdf_bytes = await file.read()
    except Exception as e:
        logger.error(f"[TC PDF IMPORT] Failed to read file: {e}")
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
        logger.error(f"[TC PDF IMPORT] Service failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}"
        )
    
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.errors[0] if result.errors else "Import failed"
        )
    
    # D) Response API
    return ImportResponse(
        tc_pdf_id=result.tc_pdf_id,
        filename=result.filename,
        imported_references_count=result.imported_references_count
    )


# ============================================================
# ENDPOINT 2: LIST IMPORTED REFERENCES
# ============================================================

@router.get(
    "/references/{aircraft_id}",
    response_model=ImportedReferencesResponse,
    summary="List imported TC references for aircraft"
)
async def list_imported_references(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """List all imported references for an aircraft."""
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
    
    # Query tc_imported_references
    cursor = db.tc_imported_references.find({"aircraft_id": aircraft_id})
    docs = await cursor.to_list(length=1000)
    
    references = []
    for doc in docs:
        created_at = doc.get("created_at")
        created_at_str = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at)
        
        references.append(ImportedReferenceItem(
            tc_reference_id=str(doc["_id"]),
            identifier=doc.get("identifier", ""),
            type=doc.get("type", "AD"),
            tc_pdf_id=doc.get("tc_pdf_id", ""),
            created_at=created_at_str
        ))
    
    return ImportedReferencesResponse(
        aircraft_id=aircraft_id,
        total_count=len(references),
        references=references
    )


# ============================================================
# ENDPOINT 3: VIEW PDF BY tc_pdf_id
# ============================================================

@router.get(
    "/pdf/{tc_pdf_id}",
    summary="View imported TC PDF"
)
async def view_tc_pdf(
    tc_pdf_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Stream the PDF file by its tc_pdf_id."""
    logger.info(f"[TC PDF VIEW] tc_pdf_id={tc_pdf_id} user={current_user.id}")
    
    # Find PDF in tc_pdf_imports
    pdf_doc = await db.tc_pdf_imports.find_one({"tc_pdf_id": tc_pdf_id})
    
    if not pdf_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PDF not found"
        )
    
    # Verify access via aircraft ownership
    ref = await db.tc_imported_references.find_one({"tc_pdf_id": tc_pdf_id})
    if ref:
        aircraft = await db.aircrafts.find_one({
            "_id": ref.get("aircraft_id"),
            "user_id": current_user.id
        })
        if not aircraft:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized"
            )
    elif pdf_doc.get("imported_by") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized"
        )
    
    # Get storage path
    storage_path = pdf_doc.get("storage_path")
    filename = pdf_doc.get("filename", f"tc_{tc_pdf_id}.pdf")
    
    if not storage_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PDF file not found"
        )
    
    full_path = f"/app/backend/{storage_path}"
    
    if not os.path.exists(full_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PDF file not found on disk"
        )
    
    file_size = os.path.getsize(full_path)
    
    logger.info(f"[TC PDF VIEW] tc_pdf_id={tc_pdf_id} size={file_size}")
    
    return FileResponse(
        path=full_path,
        media_type="application/pdf",
        filename=filename,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(file_size)
        }
    )


# ============================================================
# ENDPOINT 4: DELETE REFERENCE BY tc_reference_id
# ============================================================

@router.delete(
    "/reference-by-id/{tc_reference_id}",
    response_model=DeleteReferenceResponse,
    summary="Delete imported TC reference by ID"
)
async def delete_tc_reference_by_id(
    tc_reference_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Delete a user-imported reference by its tc_reference_id."""
    logger.info(f"[TC PDF DELETE] tc_reference_id={tc_reference_id} user={current_user.id}")
    
    # Convert string to ObjectId
    try:
        obj_id = ObjectId(tc_reference_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid tc_reference_id format"
        )
    
    # Find reference
    reference = await db.tc_imported_references.find_one({"_id": obj_id})
    
    if not reference:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reference not found"
        )
    
    # Verify ownership via aircraft
    aircraft_id = reference.get("aircraft_id")
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized"
        )
    
    identifier = reference.get("identifier", "")
    tc_pdf_id = reference.get("tc_pdf_id")
    
    # Delete the reference
    result = await db.tc_imported_references.delete_one({"_id": obj_id})
    
    # Check if PDF is orphaned
    if tc_pdf_id:
        remaining = await db.tc_imported_references.count_documents({"tc_pdf_id": tc_pdf_id})
        if remaining == 0:
            # Delete orphaned PDF
            pdf_doc = await db.tc_pdf_imports.find_one({"tc_pdf_id": tc_pdf_id})
            if pdf_doc:
                storage_path = pdf_doc.get("storage_path")
                if storage_path:
                    full_path = f"/app/backend/{storage_path}"
                    if os.path.exists(full_path):
                        try:
                            os.remove(full_path)
                            logger.info(f"[TC PDF DELETE] Orphan PDF deleted: {tc_pdf_id}")
                        except Exception as e:
                            logger.warning(f"[TC PDF DELETE] Failed to delete file: {e}")
                
                await db.tc_pdf_imports.delete_one({"tc_pdf_id": tc_pdf_id})
    
    logger.info(f"[TC PDF DELETE] tc_reference_id={tc_reference_id} identifier={identifier}")
    
    return DeleteReferenceResponse(
        ok=True,
        tc_reference_id=tc_reference_id,
        identifier=identifier,
        deleted=result.deleted_count > 0
    )


# ============================================================
# ENDPOINT 5: IMPORT HISTORY
# ============================================================

@router.get(
    "/import-history/{aircraft_id}",
    summary="Get TC PDF import history"
)
async def get_import_history(
    aircraft_id: str,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get import history for an aircraft."""
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
    
    cursor = db.tc_adsb_audit_log.find(
        {"aircraft_id": aircraft_id, "event_type": "TC_PDF_IMPORT"},
        {"_id": 0}
    ).sort("created_at", -1).limit(limit)
    
    history = await cursor.to_list(length=limit)
    
    for entry in history:
        if "created_at" in entry and hasattr(entry["created_at"], "isoformat"):
            entry["created_at"] = entry["created_at"].isoformat()
    
    return {
        "aircraft_id": aircraft_id,
        "history": history
    }
