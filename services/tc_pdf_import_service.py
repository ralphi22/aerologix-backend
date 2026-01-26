"""
TC PDF Import Service (V2)

Extracts AD/SB references from Transport Canada PDF documents.
Utilise les collections dédiées: tc_pdf_imports et tc_imported_references.

TC-SAFE:
- Import only, no compliance decisions
- Source tracking for traceability
- Audit trail for all imports

SÉPARATION STRICTE:
- Ne touche PAS aux collections tc_ad/tc_sb (données canoniques TC)
- Utilise tc_pdf_imports pour les PDF
- Utilise tc_imported_references pour les références
"""

import re
import fitz  # PyMuPDF
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field
from enum import Enum
from bson import ObjectId
import logging

from services.tc_pdf_db_service import TCPDFDatabaseService

logger = logging.getLogger(__name__)


# ============================================================
# ENUMS & MODELS
# ============================================================

class ImportSource(str, Enum):
    """Source of AD/SB data import"""
    TC_SEED = "TC_SEED"
    TC_PDF_IMPORT = "TC_PDF_IMPORT"
    TC_CAWIS = "TC_CAWIS"


class ADSBScope(str, Enum):
    """Scope of AD/SB applicability"""
    AIRFRAME = "airframe"
    ENGINE = "engine"
    PROPELLER = "propeller"
    APPLIANCE = "appliance"
    UNSPECIFIED = "unspecified"


class ExtractedReference(BaseModel):
    """Single extracted AD/SB reference from PDF"""
    ref: str
    type: str  # "AD" or "SB"
    title: Optional[str] = None
    scope: ADSBScope = ADSBScope.UNSPECIFIED
    raw_text: Optional[str] = None


class PDFImportResult(BaseModel):
    """Result of PDF import operation"""
    success: bool
    filename: str
    pages_processed: int
    references_found: int
    references_inserted: int
    references_skipped: int
    tc_pdf_id: Optional[str] = None
    references: List[ExtractedReference] = []
    errors: List[str] = []
    source: str = ImportSource.TC_PDF_IMPORT.value


class ImportedReferenceInfo(BaseModel):
    """Info about an imported reference for API response"""
    tc_reference_id: str  # MongoDB ObjectId (24-char hex)
    identifier: str       # CF-xxxx (display only)
    type: str            # AD or SB
    title: Optional[str] = None
    tc_pdf_id: str       # UUID for PDF access


# ============================================================
# PDF EXTRACTION SERVICE
# ============================================================

class TCPDFImportService:
    """
    Service for importing AD/SB references from TC PDF documents.
    
    Uses dedicated collections:
    - tc_pdf_imports: PDF file metadata
    - tc_imported_references: Extracted references linked to aircraft
    
    TC-SAFE: Import only, no compliance logic.
    """
    
    # AD/SB reference patterns (Transport Canada formats)
    AD_PATTERNS = [
        r'CF[-\s]?\d{4}[-\s]?\d{1,3}[A-Z]?',
        r'AD[-\s]?\d{4}[-\s]?\d{1,4}',
        r'CAR[-\s]?\d{4}[-\s]?\d{1,3}',
        r'FAA[-\s]AD[-\s]?\d{4}[-\s]?\d{1,4}[-\s]?\d*',
    ]
    
    SB_PATTERNS = [
        r'SB[-\s]?\d{2,4}[-\s]?\d{1,4}[-\s]?\d{0,2}',
        r'SIL[-\s]?\d{2,4}[-\s]?\d{1,4}',
        r'SEL[-\s]?\d{2,4}[-\s]?\d{1,4}',
    ]
    
    SCOPE_KEYWORDS = {
        ADSBScope.ENGINE: ['engine', 'moteur', 'powerplant', 'turbine', 'piston'],
        ADSBScope.PROPELLER: ['propeller', 'hélice', 'prop', 'blade'],
        ADSBScope.AIRFRAME: ['airframe', 'fuselage', 'wing', 'aile', 'structure', 'landing gear'],
        ADSBScope.APPLIANCE: ['appliance', 'equipment', 'instrument', 'avionics'],
    }
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.db_service = TCPDFDatabaseService(db)
    
    # --------------------------------------------------------
    # PDF TEXT EXTRACTION
    # --------------------------------------------------------
    
    def extract_text_from_pdf(self, pdf_bytes: bytes) -> Tuple[str, int]:
        """Extract all text from PDF file."""
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            text_parts = []
            
            for page in doc:
                text_parts.append(page.get_text())
            
            page_count = len(doc)
            doc.close()
            
            full_text = "\n".join(text_parts)
            logger.info(f"Extracted {len(full_text)} chars from {page_count} pages")
            
            return full_text, page_count
            
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            raise ValueError(f"Failed to extract text from PDF: {e}")
    
    # --------------------------------------------------------
    # REFERENCE EXTRACTION
    # --------------------------------------------------------
    
    def extract_references(self, text: str) -> List[ExtractedReference]:
        """Extract AD/SB references from text."""
        references = []
        seen_refs = set()
        
        # Extract ADs
        for pattern in self.AD_PATTERNS:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                ref = self._normalize_reference(match.group())
                if ref and ref not in seen_refs:
                    seen_refs.add(ref)
                    
                    start = max(0, match.start() - 100)
                    end = min(len(text), match.end() + 200)
                    context = text[start:end]
                    
                    references.append(ExtractedReference(
                        ref=ref,
                        type="AD",
                        title=self._extract_title(context, ref),
                        scope=self._detect_scope(context),
                        raw_text=context[:300],
                    ))
        
        # Extract SBs
        for pattern in self.SB_PATTERNS:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                ref = self._normalize_reference(match.group())
                if ref and ref not in seen_refs:
                    seen_refs.add(ref)
                    
                    start = max(0, match.start() - 100)
                    end = min(len(text), match.end() + 200)
                    context = text[start:end]
                    
                    references.append(ExtractedReference(
                        ref=ref,
                        type="SB",
                        title=self._extract_title(context, ref),
                        scope=self._detect_scope(context),
                        raw_text=context[:300],
                    ))
        
        logger.info(f"Extracted {len(references)} unique references")
        return references
    
    def _normalize_reference(self, ref: str) -> str:
        """Normalize reference format."""
        if not ref:
            return ""
        ref = ref.strip().upper()
        ref = re.sub(r'\s+', '-', ref)
        ref = re.sub(r'-+', '-', ref)
        return ref
    
    def _extract_title(self, context: str, ref: str) -> Optional[str]:
        """Try to extract title from context."""
        patterns = [
            rf'{re.escape(ref)}[\s:–-]+([A-Za-z][^.\n]{{10,100}})',
            rf'(?:Subject|Title|Objet)[\s:]+([A-Za-z][^.\n]{{10,100}})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, context, re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                title = re.sub(r'\s+', ' ', title)
                return title[:200]
        
        return None
    
    def _detect_scope(self, context: str) -> ADSBScope:
        """Detect scope from context."""
        context_lower = context.lower()
        
        for scope, keywords in self.SCOPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in context_lower:
                    return scope
        
        return ADSBScope.UNSPECIFIED
    
    # --------------------------------------------------------
    # PDF FILE STORAGE
    # --------------------------------------------------------
    
    async def store_pdf_file(
        self,
        pdf_bytes: bytes,
        filename: str,
        user_id: str
    ) -> Tuple[str, str]:
        """
        Store PDF file to local filesystem and create tc_pdf_imports record.
        
        Returns:
            Tuple[str, str]: (storage_path, tc_pdf_id)
        """
        # Generate unique PDF ID
        tc_pdf_id = str(uuid.uuid4())
        
        # Create storage path
        safe_filename = "".join(c if c.isalnum() or c in ".-_" else "_" for c in filename)
        storage_filename = f"{tc_pdf_id}_{safe_filename}"
        storage_path = f"storage/tc_pdfs/{storage_filename}"
        full_path = f"/app/backend/{storage_path}"
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        # Write file
        with open(full_path, "wb") as f:
            f.write(pdf_bytes)
        
        file_size = len(pdf_bytes)
        
        # Create tc_pdf_imports document
        doc = {
            "tc_pdf_id": tc_pdf_id,
            "filename": filename,
            "storage_path": storage_path,
            "content_type": "application/pdf",
            "file_size_bytes": file_size,
            "source": "TRANSPORT_CANADA",
            "imported_by": user_id,
            "imported_at": datetime.now(timezone.utc)
        }
        
        await self.db.tc_pdf_imports.insert_one(doc)
        
        logger.info(f"[TC PDF] Stored: tc_pdf_id={tc_pdf_id}, path={storage_path}, size={file_size}")
        
        return storage_path, tc_pdf_id
    
    # --------------------------------------------------------
    # REFERENCE INSERTION
    # --------------------------------------------------------
    
    async def insert_references(
        self,
        references: List[ExtractedReference],
        aircraft_id: str,
        user_id: str,
        tc_pdf_id: str
    ) -> Tuple[int, int]:
        """
        Insert extracted references into tc_imported_references.
        
        Skips duplicates (same aircraft_id + identifier).
        
        Returns:
            Tuple[int, int]: (inserted_count, skipped_count)
        """
        inserted = 0
        skipped = 0
        now = datetime.now(timezone.utc)
        
        for ref_data in references:
            # Check for existing reference (same aircraft + identifier)
            existing = await self.db.tc_imported_references.find_one({
                "aircraft_id": aircraft_id,
                "identifier": ref_data.ref.upper()
            })
            
            if existing:
                skipped += 1
                logger.debug(f"Skipped duplicate: {ref_data.ref} for aircraft {aircraft_id}")
                continue
            
            # Insert new reference
            doc = {
                "aircraft_id": aircraft_id,
                "identifier": ref_data.ref.upper(),
                "type": ref_data.type,
                "title": ref_data.title,
                "tc_pdf_id": tc_pdf_id,
                "source": "TC_PDF_IMPORT",
                "scope": ref_data.scope.value if ref_data.scope else None,
                "created_by": user_id,
                "created_at": now
            }
            
            try:
                result = await self.db.tc_imported_references.insert_one(doc)
                inserted += 1
                logger.info(f"[TC PDF] Inserted reference: _id={result.inserted_id}, identifier={ref_data.ref}")
            except Exception as e:
                skipped += 1
                logger.warning(f"Insert failed for {ref_data.ref}: {e}")
        
        return inserted, skipped
    
    # --------------------------------------------------------
    # AUDIT LOGGING
    # --------------------------------------------------------
    
    async def log_import_audit(
        self,
        aircraft_id: str,
        user_id: str,
        filename: str,
        tc_pdf_id: str,
        pages_processed: int,
        references_found: int,
        references_inserted: int,
        errors: List[str] = None
    ):
        """Log import event to audit collection."""
        doc = {
            "event_type": "TC_PDF_IMPORT",
            "aircraft_id": aircraft_id,
            "user_id": user_id,
            "filename": filename,
            "tc_pdf_id": tc_pdf_id,
            "pages_processed": pages_processed,
            "references_found": references_found,
            "references_inserted": references_inserted,
            "source": ImportSource.TC_PDF_IMPORT.value,
            "errors": errors or [],
            "created_at": datetime.now(timezone.utc),
        }
        
        try:
            await self.db.tc_adsb_audit_log.insert_one(doc)
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
    
    # --------------------------------------------------------
    # MAIN IMPORT METHOD
    # --------------------------------------------------------
    
    async def import_pdf(
        self,
        pdf_bytes: bytes,
        filename: str,
        aircraft_id: str,
        user_id: str
    ) -> PDFImportResult:
        """
        Main method to import AD/SB references from TC PDF.
        
        Process:
        1. Extract text from PDF
        2. Find AD/SB reference patterns
        3. Store PDF file + create tc_pdf_imports record
        4. Create tc_imported_references for each ref
        5. Audit log
        
        Returns:
            PDFImportResult with summary
        """
        errors = []
        
        try:
            # Step 1: Extract text
            text, page_count = self.extract_text_from_pdf(pdf_bytes)
            
            if not text.strip():
                return PDFImportResult(
                    success=False,
                    filename=filename,
                    pages_processed=page_count,
                    references_found=0,
                    references_inserted=0,
                    references_skipped=0,
                    errors=["PDF contains no extractable text"],
                )
            
            # Step 2: Extract references
            references = self.extract_references(text)
            
            if not references:
                return PDFImportResult(
                    success=True,
                    filename=filename,
                    pages_processed=page_count,
                    references_found=0,
                    references_inserted=0,
                    references_skipped=0,
                    errors=["No AD/SB references found in PDF"],
                )
            
            # Step 3: Store PDF file
            storage_path, tc_pdf_id = await self.store_pdf_file(
                pdf_bytes=pdf_bytes,
                filename=filename,
                user_id=user_id
            )
            
            # Step 4: Insert references into tc_imported_references
            inserted, skipped = await self.insert_references(
                references=references,
                aircraft_id=aircraft_id,
                user_id=user_id,
                tc_pdf_id=tc_pdf_id
            )
            
            # Step 5: Audit log
            await self.log_import_audit(
                aircraft_id=aircraft_id,
                user_id=user_id,
                filename=filename,
                tc_pdf_id=tc_pdf_id,
                pages_processed=page_count,
                references_found=len(references),
                references_inserted=inserted,
                errors=errors
            )
            
            return PDFImportResult(
                success=True,
                filename=filename,
                pages_processed=page_count,
                references_found=len(references),
                references_inserted=inserted,
                references_skipped=skipped,
                tc_pdf_id=tc_pdf_id,
                references=references,
            )
            
        except ValueError as e:
            errors.append(str(e))
            return PDFImportResult(
                success=False,
                filename=filename,
                pages_processed=0,
                references_found=0,
                references_inserted=0,
                references_skipped=0,
                errors=errors,
            )
        except Exception as e:
            logger.error(f"PDF import failed: {e}")
            errors.append(f"Import failed: {str(e)}")
            return PDFImportResult(
                success=False,
                filename=filename,
                pages_processed=0,
                references_found=0,
                references_inserted=0,
                references_skipped=0,
                errors=errors,
            )
