"""
TC PDF Import Routes (V3)

Endpoints pour importer des AD depuis des PDF Transport Canada.
Collections: tc_pdf_imports, tc_imported_references

TC-SAFE: Import only, no compliance decisions

ENDPOINTS CANONIQUES:
- POST   /api/adsb/tc/import-pdf/{aircraft_id}
- GET    /api/adsb/tc/references/{aircraft_id}
- DELETE /api/adsb/tc/reference-by-id/{tc_reference_id}  ← ObjectId
- GET    /api/adsb/tc/pdf-by-id/{tc_pdf_id}              ← UUID (canonical)
- GET    /api/adsb/tc/pdf/{tc_pdf_id}                    ← UUID (alias)
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import FileResponse, StreamingResponse
from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from bson import ObjectId
import os
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
    """Response for TC PDF import"""
    tc_pdf_id: str
    filename: str
    imported_references_count: int


class ImportedReferenceItem(BaseModel):
    """Single imported reference"""
    tc_reference_id: str = Field(..., description="ObjectId (24-char hex) - use for DELETE")
    identifier: str = Field(..., description="TC reference (e.g., CF-2024-01) - display only")
    type: str
    tc_pdf_id: str = Field(..., description="UUID - use for GET PDF")
    pdf_available: bool = True
    created_at: str


class ImportedReferencesResponse(BaseModel):
    """Response for listing imported references"""
    aircraft_id: str
    total_count: int
    references: List[ImportedReferenceItem]


class DeleteResponse(BaseModel):
    """Response for DELETE - B) format exact"""
    ok: bool
    tc_reference_id: str
    deleted_count: int


# ============================================================
# ENDPOINT 1: IMPORT PDF
# ============================================================

@router.post(
    "/import-pdf/{aircraft_id}",
    response_model=ImportResponse,
    summary="Import AD references from TC PDF"
)
async def import_pdf_tc(
    aircraft_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Import AD references from a Transport Canada PDF."""
    logger.info(f"[TC PDF IMPORT] aircraft={aircraft_id} user={current_user.id} file={file.filename}")
    
    # Validate aircraft ownership
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    # Validate file
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="PDF file required")
    
    # Read file
    try:
        pdf_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read file")
    
    if len(pdf_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")
    
    if len(pdf_bytes) < 100:
        raise HTTPException(status_code=400, detail="File too small")
    
    # Import
    service = TCPDFImportService(db)
    result = await service.import_pdf(
        pdf_bytes=pdf_bytes,
        filename=file.filename,
        aircraft_id=aircraft_id,
        user_id=current_user.id
    )
    
    if not result.success:
        raise HTTPException(status_code=400, detail=result.errors[0] if result.errors else "Import failed")
    
    return ImportResponse(
        tc_pdf_id=result.tc_pdf_id,
        filename=result.filename,
        imported_references_count=result.imported_references_count
    )


# ============================================================
# ENDPOINT 2: LIST REFERENCES
# ============================================================

@router.get(
    "/references/{aircraft_id}",
    response_model=ImportedReferencesResponse,
    summary="List imported TC references"
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
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    # Query tc_imported_references
    cursor = db.tc_imported_references.find({"aircraft_id": aircraft_id})
    docs = await cursor.to_list(length=1000)
    
    references = []
    for doc in docs:
        created_at = doc.get("created_at")
        created_at_str = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at)
        
        references.append(ImportedReferenceItem(
            tc_reference_id=str(doc["_id"]),  # ObjectId → string
            identifier=doc.get("identifier", ""),
            type=doc.get("type", "AD"),
            tc_pdf_id=doc.get("tc_pdf_id", ""),  # UUID
            pdf_available=bool(doc.get("tc_pdf_id")),
            created_at=created_at_str
        ))
    
    return ImportedReferencesResponse(
        aircraft_id=aircraft_id,
        total_count=len(references),
        references=references
    )


# ============================================================
# ENDPOINT 3: DELETE BY tc_reference_id (ObjectId)
# ============================================================

@router.delete(
    "/reference-by-id/{tc_reference_id}",
    response_model=DeleteResponse,
    summary="Delete imported reference by ObjectId"
)
async def delete_reference_by_id(
    tc_reference_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    DELETE canonique par tc_reference_id (ObjectId string).
    
    B) tc_reference_id = ObjectId string (24-char hex)
    """
    # Convert to ObjectId
    try:
        obj_id = ObjectId(tc_reference_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid tc_reference_id (expected 24-char hex ObjectId)")
    
    # Find reference in tc_imported_references
    reference = await db.tc_imported_references.find_one({"_id": obj_id})
    
    if not reference:
        raise HTTPException(status_code=404, detail="Reference not found")
    
    # Verify user ownership via aircraft
    aircraft_id = reference.get("aircraft_id")
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # DELETE from tc_imported_references
    result = await db.tc_imported_references.delete_one({"_id": obj_id})
    
    # Log B) format
    logger.info(f"[TC REF DELETE] tc_reference_id={tc_reference_id} deleted_count={result.deleted_count}")
    
    # Check if PDF is orphaned and clean up
    tc_pdf_id = reference.get("tc_pdf_id")
    if tc_pdf_id and result.deleted_count > 0:
        remaining = await db.tc_imported_references.count_documents({"tc_pdf_id": tc_pdf_id})
        if remaining == 0:
            # Delete orphaned PDF
            pdf_doc = await db.tc_pdf_imports.find_one({"tc_pdf_id": tc_pdf_id})
            if pdf_doc:
                storage_path = pdf_doc.get("storage_path")
                if storage_path and os.path.exists(storage_path):
                    try:
                        os.remove(storage_path)
                        logger.info(f"[TC REF DELETE] Orphan PDF deleted: {tc_pdf_id}")
                    except Exception as e:
                        logger.warning(f"[TC REF DELETE] Failed to delete file: {e}")
                await db.tc_pdf_imports.delete_one({"tc_pdf_id": tc_pdf_id})
    
    # B) Response format
    return DeleteResponse(
        ok=True,
        tc_reference_id=tc_reference_id,
        deleted_count=result.deleted_count
    )


# ============================================================
# ENDPOINT 4: GET PDF BY tc_pdf_id (UUID) - CANONICAL
# ============================================================

@router.get(
    "/pdf-by-id/{tc_pdf_id}",
    summary="Get PDF by UUID (canonical)"
)
async def get_pdf_by_id(
    tc_pdf_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    GET PDF canonique par tc_pdf_id (UUID).
    
    C) tc_pdf_id = UUID string
    """
    return await _stream_pdf(tc_pdf_id, current_user, db)


# ============================================================
# ENDPOINT 4b: GET PDF - ALIAS
# ============================================================

@router.get(
    "/pdf/{tc_pdf_id}",
    summary="Get PDF by UUID (alias)"
)
async def get_pdf_alias(
    tc_pdf_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Alias pour GET PDF."""
    return await _stream_pdf(tc_pdf_id, current_user, db)


async def _stream_pdf(
    tc_pdf_id: str,
    current_user: User,
    db
):
    """
    Internal: Stream PDF file.
    
    C) Cherche dans tc_pdf_imports par tc_pdf_id
       Lit le fichier via storage_path
       StreamingResponse avec headers corrects
    """
    # Find PDF in tc_pdf_imports by tc_pdf_id (UUID)
    pdf_doc = await db.tc_pdf_imports.find_one({"tc_pdf_id": tc_pdf_id})
    
    if not pdf_doc:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    # Verify access via aircraft ownership
    ref = await db.tc_imported_references.find_one({"tc_pdf_id": tc_pdf_id})
    if ref:
        aircraft = await db.aircrafts.find_one({
            "_id": ref.get("aircraft_id"),
            "user_id": current_user.id
        })
        if not aircraft:
            raise HTTPException(status_code=403, detail="Not authorized")
    elif pdf_doc.get("imported_by") != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # E) Get storage path - read directly (storage_path is absolute)
    storage_path = pdf_doc.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=404, detail="PDF_NOT_FOUND")
    
    # storage_path is now absolute (/tmp/tc_pdfs/...)
    if not os.path.exists(storage_path):
        raise HTTPException(status_code=404, detail="PDF_NOT_FOUND")
    
    # Get file size
    file_size = os.path.getsize(storage_path)
    
    # Log C) format
    logger.info(f"[TC PDF VIEW] tc_pdf_id={tc_pdf_id} bytes={file_size}")
    
    # C) StreamingResponse avec headers
    def file_iterator():
        with open(storage_path, "rb") as f:
            while chunk := f.read(65536):  # 64KB chunks
                yield chunk
    
    return StreamingResponse(
        file_iterator(),
        media_type="application/pdf",
        headers={
            "Content-Type": "application/pdf",
            "Content-Disposition": f'inline; filename="tc_{tc_pdf_id}.pdf"',
            "Content-Length": str(file_size)
        }
    )


# ============================================================
# ENDPOINT 5: IMPORT HISTORY
# ============================================================

@router.get(
    "/import-history/{aircraft_id}",
    summary="Get import history"
)
async def get_import_history(
    aircraft_id: str,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get import history for an aircraft."""
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    cursor = db.tc_adsb_audit_log.find(
        {"aircraft_id": aircraft_id, "event_type": "TC_PDF_IMPORT"},
        {"_id": 0}
    ).sort("created_at", -1).limit(limit)
    
    history = await cursor.to_list(length=limit)
    
    for entry in history:
        if "created_at" in entry and hasattr(entry["created_at"], "isoformat"):
            entry["created_at"] = entry["created_at"].isoformat()
    
    return {"aircraft_id": aircraft_id, "history": history}
