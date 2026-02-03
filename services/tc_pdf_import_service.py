"""
TC PDF Import Service (V3)

Extracts AD/SB references from Transport Canada PDF documents.
Uses dedicated collections: tc_pdf_imports and tc_imported_references.

TC-SAFE:
- Import only, no compliance decisions
- Source tracking for traceability
- Audit trail for all imports

SUPPORTED FORMATS:
- Canada (CF): CF-2024-01, CF-1987-15R4
- US (FAA): 2022-03-15, 83-17-06, 80-11-04R3
- EU (EASA): 2009-0278, 2008-0183-E
- France (DGAC): F-2005-023, F-2001-139R1
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
# CONSTANTS - INTERNATIONAL AD PATTERNS
# ============================================================

# Canadian format: CF-YYYY-NN (with optional revision R#)
PATTERN_CANADA = re.compile(r'CF[-\s]?\d{2,4}[-\s]?\d{2,4}(?:R\d+)?', re.IGNORECASE)

# US FAA format: YY-NN-NN or YYYY-NN-NN (with optional revision R#)
# Examples: 83-17-06, 80-11-04R3, 2022-03-15
PATTERN_US = re.compile(r'\b(\d{2,4})[-\s](\d{2})[-\s](\d{2})(?:R\d+)?\b')

# EU EASA format: YYYY-NNNN (with optional -E suffix)
# Examples: 2009-0278, 2008-0183-E
PATTERN_EU = re.compile(r'\b20\d{2}[-\s]?\d{4}(?:-?E)?\b')

# France DGAC format: F-YYYY-NNN (with optional R#)
# Examples: F-2005-023, F-2001-139R1
PATTERN_FRANCE = re.compile(r'F[-\s]?\d{4}[-\s]?\d{3,4}(?:R\d+)?', re.IGNORECASE)


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
    
    def extract_title_from_text(self, text: str) -> Optional[str]:
        """
        Extract title/subject from TC PDF text.
        
        TC PDFs typically have a "Subject:" line with the AD description.
        Example: "Subject: Cessna 150/152 — Rudder Stop"
        
        Returns:
            Title string or None if not found
        """
        if not text:
            return None
        
        # Pattern 1: Subject: line (TC format)
        subject_match = re.search(r'Subject:\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
        if subject_match:
            title = subject_match.group(1).strip()
            if title:
                return title
        
        # Pattern 2: Look for "AIRWORTHINESS DIRECTIVE" followed by a title line
        ad_match = re.search(r'AIRWORTHINESS\s+DIRECTIVE[^\n]*\n+(?:.*?\n)*?Subject:\s*([^\n]+)', text, re.IGNORECASE)
        if ad_match:
            title = ad_match.group(1).strip()
            if title:
                return title
        
        # Pattern 3: Look for Number and Subject pattern
        num_subject = re.search(r'Number:\s*CF-[\d-]+\s*\n\s*Subject:\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
        if num_subject:
            title = num_subject.group(1).strip()
            if title:
                return title
        
        return None
    
    # --------------------------------------------------------
    # REFERENCE EXTRACTION - INTERNATIONAL AD FORMATS
    # --------------------------------------------------------
    
    def detect_reference_type(self, ref: str) -> str:
        """
        Detect the type of AD reference.
        
        Returns: "CF" | "US" | "EU" | "FR" | "UNKNOWN"
        """
        ref_upper = ref.upper().strip()
        
        if ref_upper.startswith('CF'):
            return "CF"
        if ref_upper.startswith('F-') or ref_upper.startswith('F '):
            return "FR"
        # EU format: 20XX-NNNN
        if re.match(r'^20\d{2}[-\s]?\d{4}', ref_upper):
            return "EU"
        # US format: YY-NN-NN or YYYY-NN-NN
        if re.match(r'^\d{2,4}[-\s]\d{2}[-\s]\d{2}', ref_upper):
            return "US"
        
        return "UNKNOWN"
    
    def normalize_reference(self, raw_ref: str) -> Optional[str]:
        """
        Normalize an AD reference to standard format.
        
        Supports:
        - Canada: CF-YYYY-NN → CF-YYYY-NN
        - US: YY-NN-NN → YY-NN-NN
        - EU: YYYY-NNNN → YYYY-NNNN
        - France: F-YYYY-NNN → F-YYYY-NNN
        
        Returns:
            Normalized reference or None if invalid
        """
        if not raw_ref:
            return None
        
        # Clean up
        ref = raw_ref.strip().upper()
        
        # Canadian format: CF-YYYY-NN
        if ref.startswith('CF'):
            ref = ref.replace(' ', '')
            match = re.match(r'^CF-?(\d{2,4})-?(\d{2,4})(R\d+)?$', ref)
            if match:
                year = match.group(1)
                num = match.group(2)
                rev = match.group(3) or ""
                return f"CF-{year}-{num}{rev}"
        
        # France format: F-YYYY-NNN
        if ref.startswith('F'):
            ref = ref.replace(' ', '')
            match = re.match(r'^F-?(\d{4})-?(\d{3,4})(R\d+)?$', ref)
            if match:
                year = match.group(1)
                num = match.group(2)
                rev = match.group(3) or ""
                return f"F-{year}-{num}{rev}"
        
        # EU format: YYYY-NNNN
        match = re.match(r'^(20\d{2})-?(\d{4})(-?E)?$', ref)
        if match:
            year = match.group(1)
            num = match.group(2)
            suffix = match.group(3) or ""
            return f"{year}-{num}{suffix}"
        
        # US format: YY-NN-NN or YYYY-NN-NN
        ref_clean = re.sub(r'[\s]+', '-', ref)
        match = re.match(r'^(\d{2,4})-(\d{2})-(\d{2})(R\d+)?$', ref_clean)
        if match:
            year = match.group(1)
            mid = match.group(2)
            num = match.group(3)
            rev = match.group(4) or ""
            return f"{year}-{mid}-{num}{rev}"
        
        return None
    
    def extract_references(self, text: str) -> List[str]:
        """
        Extract all valid AD references from text.
        
        Supports multiple international formats:
        - Canada: CF-YYYY-NN
        - US: YY-NN-NN, YYYY-NN-NN
        - EU: YYYY-NNNN
        - France: F-YYYY-NNN
        
        Returns:
            List of normalized, unique, valid references
        """
        valid_refs = set()
        
        # Extract Canadian references
        for match in PATTERN_CANADA.findall(text):
            normalized = self.normalize_reference(match)
            if normalized:
                valid_refs.add(normalized)
        
        # Extract French references
        for match in PATTERN_FRANCE.findall(text):
            normalized = self.normalize_reference(match)
            if normalized:
                valid_refs.add(normalized)
        
        # Extract EU references
        for match in PATTERN_EU.findall(text):
            normalized = self.normalize_reference(match)
            if normalized:
                valid_refs.add(normalized)
        
        # Extract US references (YY-NN-NN format)
        for match in PATTERN_US.findall(text):
            if isinstance(match, tuple):
                raw = '-'.join(match)
            else:
                raw = match
            normalized = self.normalize_reference(raw)
            if normalized:
                # Filter out false positives (dates, etc.)
                # US AD years are typically 60-99 or 2000+
                year_str = normalized.split('-')[0]
                try:
                    year = int(year_str)
                    # Valid US AD years: 60-99 or 2000-2030
                    if (60 <= year <= 99) or (2000 <= year <= 2030):
                        valid_refs.add(normalized)
                except:
                    pass
        
        result = sorted(valid_refs)
        logger.info(f"[TC PDF IMPORT] Extracted {len(result)} valid CF-xxxx references from {len(raw_matches)} candidates")
        
        return result
    
    # --------------------------------------------------------
    # PDF STORAGE - Uses /tmp for Render compatibility
    # --------------------------------------------------------
    
    # A) Base directory for PDF storage (writable on Render)
    PDF_STORAGE_DIR = "/tmp/tc_pdfs"
    
    async def store_pdf(
        self,
        pdf_bytes: bytes,
        filename: str,
        user_id: str
    ) -> Tuple[str, str]:
        """
        Store PDF file and create tc_pdf_imports document.
        
        Storage: /tmp/tc_pdfs/{tc_pdf_id}_{original_filename}
        
        Returns:
            Tuple[str, str]: (tc_pdf_id, storage_path)
        """
        # A) Generate tc_pdf_id (UUID v4)
        tc_pdf_id = str(uuid.uuid4())
        
        # B) Create storage path in /tmp (writable on Render)
        safe_filename = "".join(c if c.isalnum() or c in ".-_" else "_" for c in filename)
        storage_path = f"/tmp/tc_pdfs/{tc_pdf_id}_{safe_filename}"
        
        # Ensure directory exists
        os.makedirs(self.PDF_STORAGE_DIR, exist_ok=True)
        
        # C) Write file
        with open(storage_path, "wb") as f:
            f.write(pdf_bytes)
        
        file_size = len(pdf_bytes)
        
        # Verify file exists
        if not os.path.exists(storage_path):
            raise ValueError(f"Failed to write PDF to {storage_path}")
        
        # Log storage
        logger.info(f"[TC PDF STORE] path={storage_path} size={file_size}")
        
        # D) Create tc_pdf_imports document with EXACT storage_path
        pdf_doc = {
            "tc_pdf_id": tc_pdf_id,
            "filename": filename,
            "storage_path": storage_path,  # Exact path as stored
            "content_type": "application/pdf",
            "file_size_bytes": file_size,
            "source": "TRANSPORT_CANADA",
            "imported_by": user_id,
            "imported_at": datetime.now(timezone.utc)
        }
        
        await self.db.tc_pdf_imports.insert_one(pdf_doc)
        
        logger.info(f"[TC PDF IMPORT] tc_pdf_id={tc_pdf_id}")
        
        return tc_pdf_id, storage_path
    
    # --------------------------------------------------------
    # REFERENCE CREATION
    # --------------------------------------------------------
    
    async def create_references(
        self,
        references: List[str],
        aircraft_id: str,
        tc_pdf_id: str,
        user_id: str,
        title: Optional[str] = None
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
            
            # Create document with title
            ref_doc = {
                "aircraft_id": aircraft_id,
                "identifier": identifier,
                "type": "AD",
                "tc_pdf_id": tc_pdf_id,
                "title": title,  # Include title from PDF
                "source": "TC_PDF_IMPORT",
                "created_by": user_id,
                "created_at": now
            }
            
            try:
                result = await self.db.tc_imported_references.insert_one(ref_doc)
                created_count += 1
                logger.debug(f"[TC PDF IMPORT] Created reference: _id={result.inserted_id} identifier={identifier} title={title}")
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
        2. Extract title/subject from PDF
        3. Find valid CF-XXXX-XX references
        4. Store PDF (tc_pdf_imports)
        5. Create references (tc_imported_references)
        6. Collaborative detection (notify other users)
        7. Audit log
        
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
            
            # Step 2: Extract title/subject from PDF
            title = self.extract_title_from_text(text)
            logger.info(f"[TC PDF IMPORT] Extracted title: {title}")
            
            # Step 3: Extract valid references (STRICT CF-XXXX-XX only)
            references = self.extract_references(text)
            
            # Step 4: Store PDF (include title in the document)
            tc_pdf_id, storage_path = await self.store_pdf(
                pdf_bytes=pdf_bytes,
                filename=filename,
                user_id=user_id
            )
            
            # Update PDF document with title
            if title:
                await self.db.tc_pdf_imports.update_one(
                    {"tc_pdf_id": tc_pdf_id},
                    {"$set": {"title": title}}
                )
            
            # Step 5: Create references (if any found) - include title
            refs_created = 0
            if references:
                refs_created = await self.create_references(
                    references=references,
                    aircraft_id=aircraft_id,
                    tc_pdf_id=tc_pdf_id,
                    user_id=user_id,
                    title=title
                )
            
            # Step 6: Collaborative detection - notify other users with same aircraft type
            # Uses CANONICAL key: aircraft_type_key = manufacturer::model
            if references and refs_created > 0:
                try:
                    from services.collaborative_adsb_service import CollaborativeADSBService
                    
                    # Get aircraft manufacturer and model for collaborative detection
                    aircraft = await self.db.aircrafts.find_one({"_id": aircraft_id})
                    manufacturer = aircraft.get("manufacturer", "") if aircraft else ""
                    model = aircraft.get("model", "") if aircraft else ""
                    
                    if manufacturer and model:
                        collab_service = CollaborativeADSBService(self.db)
                        collab_result = await collab_service.process_imported_references(
                            references=references,
                            reference_type="AD",  # TC PDF imports are always AD
                            aircraft_id=aircraft_id,
                            user_id=user_id,
                            manufacturer=manufacturer,
                            model=model
                        )
                        
                        if collab_result.new_references_count > 0:
                            logger.info(
                                f"[TC PDF IMPORT] Collaborative: type_key={collab_result.aircraft_type_key} | "
                                f"{collab_result.new_references_count} new refs, "
                                f"{collab_result.alerts_created_count} alerts, "
                                f"{collab_result.users_notified} users notified"
                            )
                except Exception as e:
                    # Don't fail import if collaborative detection fails
                    logger.warning(f"[TC PDF IMPORT] Collaborative detection failed (non-blocking): {e}")
            
            # Step 6: Audit log
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
