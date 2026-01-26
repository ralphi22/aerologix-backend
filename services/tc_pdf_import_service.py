"""
TC PDF Import Service (V3)

Extracts TC AD references from Transport Canada PDF documents.
Uses dedicated collections: tc_pdf_imports and tc_imported_references.

TC-SAFE:
- Import only, no compliance decisions
- Source tracking for traceability
- Audit trail for all imports

STRICT EXTRACTION:
- Only CF-XXXX-XX pattern (^CF-\d{4}-\d{2,4}$)
- Normalized: trim, uppercase, no spaces
- Duplicates prevented by aircraft_id + identifier + tc_pdf_id
"""

import re
import fitz  # PyMuPDF
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)


# ============================================================
# CONSTANTS
# ============================================================

# STRICT pattern for TC AD references: CF-YYYY-NN or CF-YYYY-NNN or CF-YYYY-NNNN
TC_AD_PATTERN = re.compile(r'CF[-\s]?\d{4}[-\s]?\d{2,4}', re.IGNORECASE)
TC_AD_STRICT_PATTERN = re.compile(r'^CF-\d{4}-\d{2,4}$')


# ============================================================
# MODELS
# ============================================================

class PDFImportResult(BaseModel):
    """Result of PDF import operation"""
    success: bool
    tc_pdf_id: Optional[str] = None
    filename: str
    imported_references_count: int = 0
    errors: List[str] = []


# ============================================================
# SERVICE
# ============================================================

class TCPDFImportService:
    """
    Service for importing AD references from TC PDF documents.
    
    Collections used:
    - tc_pdf_imports: PDF file metadata
    - tc_imported_references: Extracted references linked to aircraft
    
    TC-SAFE: Import only, no compliance logic.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
    
    # --------------------------------------------------------
    # PDF TEXT EXTRACTION
    # --------------------------------------------------------
    
    def extract_text_from_pdf(self, pdf_bytes: bytes) -> Tuple[str, int]:
        """
        Extract all text from PDF file.
        
        Returns:
            Tuple[str, int]: (extracted_text, page_count)
        """
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            text_parts = []
            
            for page in doc:
                text_parts.append(page.get_text())
            
            page_count = len(doc)
            doc.close()
            
            return "\n".join(text_parts), page_count
            
        except Exception as e:
            logger.error(f"[TC PDF IMPORT] PDF extraction failed: {e}")
            raise ValueError(f"Failed to extract text from PDF: {e}")
    
    # --------------------------------------------------------
    # REFERENCE EXTRACTION - STRICT CF-XXXX-XX ONLY
    # --------------------------------------------------------
    
    def normalize_reference(self, raw_ref: str) -> Optional[str]:
        """
        Normalize a reference to CF-YYYY-NN format.
        
        Steps:
        1. Trim whitespace
        2. Uppercase
        3. Remove internal spaces
        4. Ensure CF-YYYY-NN format with hyphens
        
        Returns:
            Normalized reference or None if invalid
        """
        if not raw_ref:
            return None
        
        # Step 1: Trim
        ref = raw_ref.strip()
        
        # Step 2: Uppercase
        ref = ref.upper()
        
        # Step 3: Remove spaces
        ref = ref.replace(" ", "")
        
        # Step 4: Normalize separators to hyphens
        # Handle cases like "CF2024-01" or "CF-202401"
        ref = re.sub(r'[-\s]+', '-', ref)
        
        # Ensure format: CF-YYYY-NN
        # Try to fix common variations
        match = re.match(r'^CF-?(\d{4})-?(\d{2,4})$', ref)
        if match:
            year = match.group(1)
            num = match.group(2)
            normalized = f"CF-{year}-{num}"
            
            # Final validation against strict pattern
            if TC_AD_STRICT_PATTERN.match(normalized):
                return normalized
        
        return None
    
    def extract_references(self, text: str) -> List[str]:
        """
        Extract valid TC AD references from text.
        
        STRICT: Only CF-YYYY-NN pattern (^CF-\d{4}-\d{2,4}$)
        
        Returns:
            List of normalized, unique, valid references
        """
        # Find all potential matches
        raw_matches = TC_AD_PATTERN.findall(text)
        
        # Normalize and filter
        valid_refs = set()
        for raw in raw_matches:
            normalized = self.normalize_reference(raw)
            if normalized:
                valid_refs.add(normalized)
        
        result = sorted(valid_refs)
        logger.info(f"[TC PDF IMPORT] Extracted {len(result)} valid CF-xxxx references from {len(raw_matches)} candidates")
        
        return result
    
    # --------------------------------------------------------
    # PDF STORAGE
    # --------------------------------------------------------
    
    async def store_pdf(
        self,
        pdf_bytes: bytes,
        filename: str,
        user_id: str
    ) -> Tuple[str, str]:
        """
        Store PDF file and create tc_pdf_imports document.
        
        Storage path: tc_pdfs/{tc_pdf_id}_{original_filename}
        
        Returns:
            Tuple[str, str]: (tc_pdf_id, storage_path)
        """
        # A) Generate tc_pdf_id (UUID v4)
        tc_pdf_id = str(uuid.uuid4())
        
        # B) Create storage path
        safe_filename = "".join(c if c.isalnum() or c in ".-_" else "_" for c in filename)
        storage_path = f"tc_pdfs/{tc_pdf_id}_{safe_filename}"
        full_path = f"/app/backend/storage/{storage_path}"
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        # Write file
        with open(full_path, "wb") as f:
            f.write(pdf_bytes)
        
        file_size = len(pdf_bytes)
        
        # C) Create tc_pdf_imports document
        pdf_doc = {
            "tc_pdf_id": tc_pdf_id,
            "filename": filename,
            "storage_path": f"storage/{storage_path}",
            "content_type": "application/pdf",
            "file_size_bytes": file_size,
            "source": "TRANSPORT_CANADA",
            "imported_by": user_id,
            "imported_at": datetime.now(timezone.utc)
        }
        
        await self.db.tc_pdf_imports.insert_one(pdf_doc)
        
        logger.info(f"[TC PDF IMPORT] PDF stored: tc_pdf_id={tc_pdf_id} path={storage_path} size={file_size}")
        
        return tc_pdf_id, f"storage/{storage_path}"
    
    # --------------------------------------------------------
    # REFERENCE CREATION
    # --------------------------------------------------------
    
    async def create_references(
        self,
        references: List[str],
        aircraft_id: str,
        tc_pdf_id: str,
        user_id: str
    ) -> int:
        """
        Create tc_imported_references documents.
        
        Prevents duplicates by: aircraft_id + identifier + tc_pdf_id
        
        Returns:
            Number of references created
        """
        created_count = 0
        now = datetime.now(timezone.utc)
        
        for identifier in references:
            # Check for duplicate (same aircraft + identifier + pdf)
            existing = await self.db.tc_imported_references.find_one({
                "aircraft_id": aircraft_id,
                "identifier": identifier,
                "tc_pdf_id": tc_pdf_id
            })
            
            if existing:
                logger.debug(f"[TC PDF IMPORT] Skip duplicate: {identifier} for aircraft={aircraft_id}")
                continue
            
            # Create document
            ref_doc = {
                "aircraft_id": aircraft_id,
                "identifier": identifier,
                "type": "AD",
                "tc_pdf_id": tc_pdf_id,
                "source": "TC_PDF_IMPORT",
                "created_by": user_id,
                "created_at": now
            }
            
            try:
                result = await self.db.tc_imported_references.insert_one(ref_doc)
                created_count += 1
                logger.debug(f"[TC PDF IMPORT] Created reference: _id={result.inserted_id} identifier={identifier}")
            except Exception as e:
                logger.warning(f"[TC PDF IMPORT] Failed to create {identifier}: {e}")
        
        return created_count
    
    # --------------------------------------------------------
    # AUDIT LOGGING
    # --------------------------------------------------------
    
    async def log_audit(
        self,
        aircraft_id: str,
        user_id: str,
        tc_pdf_id: str,
        filename: str,
        refs_created: int
    ):
        """Log import event for audit trail."""
        audit_doc = {
            "event_type": "TC_PDF_IMPORT",
            "aircraft_id": aircraft_id,
            "user_id": user_id,
            "tc_pdf_id": tc_pdf_id,
            "filename": filename,
            "refs_created": refs_created,
            "created_at": datetime.now(timezone.utc)
        }
        
        try:
            await self.db.tc_adsb_audit_log.insert_one(audit_doc)
        except Exception as e:
            logger.error(f"[TC PDF IMPORT] Audit log failed: {e}")
    
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
        Import TC PDF and extract AD references.
        
        Process:
        1. Extract text from PDF
        2. Find valid CF-XXXX-XX references
        3. Store PDF (tc_pdf_imports)
        4. Create references (tc_imported_references)
        5. Audit log
        
        Returns:
            PDFImportResult with tc_pdf_id and imported_references_count
        """
        errors = []
        
        try:
            # Step 1: Extract text
            text, page_count = self.extract_text_from_pdf(pdf_bytes)
            
            if not text.strip():
                return PDFImportResult(
                    success=False,
                    filename=filename,
                    imported_references_count=0,
                    errors=["PDF contains no extractable text"]
                )
            
            # Step 2: Extract valid references (STRICT CF-XXXX-XX only)
            references = self.extract_references(text)
            
            # Step 3: Store PDF
            tc_pdf_id, storage_path = await self.store_pdf(
                pdf_bytes=pdf_bytes,
                filename=filename,
                user_id=user_id
            )
            
            # Step 4: Create references (if any found)
            refs_created = 0
            if references:
                refs_created = await self.create_references(
                    references=references,
                    aircraft_id=aircraft_id,
                    tc_pdf_id=tc_pdf_id,
                    user_id=user_id
                )
            
            # Step 5: Audit log
            await self.log_audit(
                aircraft_id=aircraft_id,
                user_id=user_id,
                tc_pdf_id=tc_pdf_id,
                filename=filename,
                refs_created=refs_created
            )
            
            # Log result
            logger.info(f"[TC PDF IMPORT] tc_pdf_id={tc_pdf_id} refs_created={refs_created}")
            
            return PDFImportResult(
                success=True,
                tc_pdf_id=tc_pdf_id,
                filename=filename,
                imported_references_count=refs_created
            )
            
        except ValueError as e:
            errors.append(str(e))
            return PDFImportResult(
                success=False,
                filename=filename,
                imported_references_count=0,
                errors=errors
            )
        except Exception as e:
            logger.error(f"[TC PDF IMPORT] Failed: {e}")
            errors.append(f"Import failed: {str(e)}")
            return PDFImportResult(
                success=False,
                filename=filename,
                imported_references_count=0,
                errors=errors
            )
