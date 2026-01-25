"""
TC PDF Import Service

Extracts AD/SB references from Transport Canada PDF documents.

TC-SAFE:
- Import only, no compliance decisions
- Source tracking for traceability
- Audit trail for all imports

PATCH MINIMAL: Does not modify existing AD/SB logic.
"""

import re
import fitz  # PyMuPDF
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field
from enum import Enum
import logging
import io

logger = logging.getLogger(__name__)


# ============================================================
# ENUMS & MODELS
# ============================================================

class ImportSource(str, Enum):
    """Source of AD/SB data import"""
    TC_SEED = "TC_SEED"           # Initial seed data
    TC_PDF_IMPORT = "TC_PDF_IMPORT"  # Manual PDF import
    TC_CAWIS = "TC_CAWIS"         # CAWIS web import (future)


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
    effective_date: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    scope: ADSBScope = ADSBScope.UNSPECIFIED
    raw_text: Optional[str] = None  # Original text snippet for audit


class PDFImportResult(BaseModel):
    """Result of PDF import operation"""
    success: bool
    filename: str
    pages_processed: int
    references_found: int
    references_inserted: int
    references_updated: int
    references_skipped: int
    references: List[ExtractedReference] = []
    errors: List[str] = []
    source: str = ImportSource.TC_PDF_IMPORT.value


class PDFImportAuditEntry(BaseModel):
    """Audit log entry for PDF import"""
    event_type: str = "TC_PDF_IMPORT"
    aircraft_id: str
    user_id: str
    filename: str
    pages_processed: int
    references_found: int
    references_inserted: int
    references_updated: int
    source: str = ImportSource.TC_PDF_IMPORT.value
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================
# PDF EXTRACTION SERVICE
# ============================================================

class TCPDFImportService:
    """
    Service for importing AD/SB references from TC PDF documents.
    
    TC-SAFE: Import only, no compliance logic.
    """
    
    # AD/SB reference patterns (Transport Canada formats)
    AD_PATTERNS = [
        r'CF[-\s]?\d{4}[-\s]?\d{1,3}[A-Z]?',      # CF-2020-01, CF-2024-12R
        r'AD[-\s]?\d{4}[-\s]?\d{1,4}',             # AD-2020-0001
        r'CAR[-\s]?\d{4}[-\s]?\d{1,3}',            # CAR-2020-01 (older format)
        r'FAA[-\s]AD[-\s]?\d{4}[-\s]?\d{1,4}[-\s]?\d*',  # FAA AD refs adopted by TC
    ]
    
    SB_PATTERNS = [
        r'SB[-\s]?\d{2,4}[-\s]?\d{1,4}[-\s]?\d{0,2}',  # SB-172-001, SB-2020-01
        r'SIL[-\s]?\d{2,4}[-\s]?\d{1,4}',              # Service Information Letters
        r'SEL[-\s]?\d{2,4}[-\s]?\d{1,4}',              # Service Engineering Letters
    ]
    
    # Scope detection keywords
    SCOPE_KEYWORDS = {
        ADSBScope.ENGINE: ['engine', 'moteur', 'powerplant', 'turbine', 'piston'],
        ADSBScope.PROPELLER: ['propeller', 'hélice', 'prop', 'blade'],
        ADSBScope.AIRFRAME: ['airframe', 'fuselage', 'wing', 'aile', 'structure', 'landing gear'],
        ADSBScope.APPLIANCE: ['appliance', 'equipment', 'instrument', 'avionics'],
    }
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
    
    # --------------------------------------------------------
    # PDF TEXT EXTRACTION
    # --------------------------------------------------------
    
    def extract_text_from_pdf(self, pdf_bytes: bytes) -> Tuple[str, int]:
        """
        Extract all text from PDF file.
        
        Args:
            pdf_bytes: PDF file content as bytes
            
        Returns:
            Tuple of (extracted_text, page_count)
        """
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
        """
        Extract AD/SB references from text.
        
        Args:
            text: Full text from PDF
            
        Returns:
            List of ExtractedReference objects
        """
        references = []
        seen_refs = set()
        
        # Extract ADs
        for pattern in self.AD_PATTERNS:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                ref = self._normalize_reference(match.group())
                if ref and ref not in seen_refs:
                    seen_refs.add(ref)
                    
                    # Get context (surrounding text)
                    start = max(0, match.start() - 100)
                    end = min(len(text), match.end() + 200)
                    context = text[start:end]
                    
                    references.append(ExtractedReference(
                        ref=ref,
                        type="AD",
                        title=self._extract_title(context, ref),
                        scope=self._detect_scope(context),
                        raw_text=context[:300],  # Limit for storage
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
        """Normalize reference format (uppercase, consistent separators)."""
        if not ref:
            return ""
        
        # Uppercase and clean
        ref = ref.strip().upper()
        
        # Standardize separators
        ref = re.sub(r'\s+', '-', ref)
        ref = re.sub(r'-+', '-', ref)
        
        return ref
    
    def _extract_title(self, context: str, ref: str) -> Optional[str]:
        """Try to extract title from context around reference."""
        # Look for common patterns like "CF-2020-01: Title Here" or "CF-2020-01 - Title"
        patterns = [
            rf'{re.escape(ref)}[\s:–-]+([A-Za-z][^.\n]{{10,100}})',
            rf'(?:Subject|Title|Objet)[\s:]+([A-Za-z][^.\n]{{10,100}})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, context, re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                # Clean up title
                title = re.sub(r'\s+', ' ', title)
                return title[:200]  # Limit length
        
        return None
    
    def _detect_scope(self, context: str) -> ADSBScope:
        """Detect scope (airframe/engine/propeller) from context."""
        context_lower = context.lower()
        
        for scope, keywords in self.SCOPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in context_lower:
                    return scope
        
        return ADSBScope.UNSPECIFIED
    
    # --------------------------------------------------------
    # MONGODB UPSERT
    # --------------------------------------------------------
    
    async def upsert_references(
        self,
        references: List[ExtractedReference],
        aircraft_id: str,
        user_id: str,
        filename: str
    ) -> Tuple[int, int, int]:
        """
        Upsert extracted references into MongoDB.
        
        Uses ref as unique key. Does NOT overwrite existing data
        from other sources - only updates if source is TC_PDF_IMPORT.
        
        Args:
            references: List of extracted references
            aircraft_id: Aircraft ID for context
            user_id: User performing import
            filename: Original filename for audit
            
        Returns:
            Tuple of (inserted, updated, skipped)
        """
        inserted = 0
        updated = 0
        skipped = 0
        
        now = datetime.now(timezone.utc)
        
        for ref_data in references:
            collection = self.db.tc_ad if ref_data.type == "AD" else self.db.tc_sb
            
            # Check if exists
            existing = await collection.find_one({"ref": ref_data.ref})
            
            if existing:
                # Only update if source is TC_PDF_IMPORT (don't overwrite authoritative data)
                existing_source = existing.get("source")
                
                if existing_source == ImportSource.TC_PDF_IMPORT.value or existing_source is None:
                    # Safe to update
                    update_data = {
                        "source": ImportSource.TC_PDF_IMPORT.value,
                        "scope": ref_data.scope.value,
                        "updated_at": now,
                        "last_import_filename": filename,
                        "last_import_user": user_id,
                        "import_aircraft_id": aircraft_id,  # CRITICAL: Link to aircraft for baseline query
                    }
                    
                    # Only update title if we found one and existing is empty
                    if ref_data.title and not existing.get("title"):
                        update_data["title"] = ref_data.title
                    
                    await collection.update_one(
                        {"ref": ref_data.ref},
                        {"$set": update_data}
                    )
                    updated += 1
                    logger.debug(f"Updated {ref_data.type} {ref_data.ref}")
                else:
                    # Don't overwrite authoritative sources
                    skipped += 1
                    logger.debug(f"Skipped {ref_data.type} {ref_data.ref} (source={existing_source})")
            else:
                # Insert new
                doc = {
                    "_id": ref_data.ref,  # Use ref as _id for uniqueness
                    "ref": ref_data.ref,
                    "type": ref_data.type,
                    "title": ref_data.title,
                    "scope": ref_data.scope.value,
                    "source": ImportSource.TC_PDF_IMPORT.value,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                    "import_filename": filename,
                    "import_user": user_id,
                    "import_aircraft_id": aircraft_id,
                }
                
                # Add manufacturer/model if provided
                if ref_data.manufacturer:
                    doc["manufacturer"] = ref_data.manufacturer
                if ref_data.model:
                    doc["model"] = ref_data.model
                
                try:
                    await collection.insert_one(doc)
                    inserted += 1
                    logger.info(f"Inserted {ref_data.type} {ref_data.ref}")
                except Exception as e:
                    # Duplicate key - race condition, skip
                    logger.warning(f"Insert failed for {ref_data.ref}: {e}")
                    skipped += 1
        
        return inserted, updated, skipped
    
    # --------------------------------------------------------
    # AUDIT LOGGING
    # --------------------------------------------------------
    
    async def log_import_audit(
        self,
        aircraft_id: str,
        user_id: str,
        filename: str,
        pages_processed: int,
        references_found: int,
        references_inserted: int,
        references_updated: int,
        errors: List[str] = None
    ):
        """
        Log import event to audit collection.
        
        TC-SAFE: Full traceability of all imports.
        """
        doc = {
            "event_type": "TC_PDF_IMPORT",
            "aircraft_id": aircraft_id,
            "user_id": user_id,
            "filename": filename,
            "pages_processed": pages_processed,
            "references_found": references_found,
            "references_inserted": references_inserted,
            "references_updated": references_updated,
            "source": ImportSource.TC_PDF_IMPORT.value,
            "errors": errors or [],
            "created_at": datetime.now(timezone.utc),
        }
        
        try:
            await self.db.tc_adsb_audit_log.insert_one(doc)
            logger.info(f"Audit log: TC_PDF_IMPORT | aircraft={aircraft_id} | refs={references_found}")
        except Exception as e:
            # Never fail import due to audit error
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
        
        Args:
            pdf_bytes: PDF file content
            filename: Original filename
            aircraft_id: Aircraft context
            user_id: User performing import
            
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
                    references_updated=0,
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
                    references_updated=0,
                    references_skipped=0,
                    errors=["No AD/SB references found in PDF"],
                )
            
            # Step 3: Upsert to MongoDB
            inserted, updated, skipped = await self.upsert_references(
                references=references,
                aircraft_id=aircraft_id,
                user_id=user_id,
                filename=filename
            )
            
            # Step 4: Audit log
            await self.log_import_audit(
                aircraft_id=aircraft_id,
                user_id=user_id,
                filename=filename,
                pages_processed=page_count,
                references_found=len(references),
                references_inserted=inserted,
                references_updated=updated,
                errors=errors
            )
            
            return PDFImportResult(
                success=True,
                filename=filename,
                pages_processed=page_count,
                references_found=len(references),
                references_inserted=inserted,
                references_updated=updated,
                references_skipped=skipped,
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
                references_updated=0,
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
                references_updated=0,
                references_skipped=0,
                errors=errors,
            )
