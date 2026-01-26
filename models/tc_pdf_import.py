"""
TC PDF Import Models

Collections pour Phase 1 TC AD/SB PDF Import.

SÉPARATION STRICTE:
- tc_pdf_imports: Métadonnées des PDF TC importés physiquement
- tc_imported_references: Références TC liées à un avion et un PDF

Ces collections sont INDÉPENDANTES de tc_ad/tc_sb (données canoniques TC).
TC-SAFE: Import uniquement, aucune logique de conformité.
"""

from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field
from bson import ObjectId
import uuid


# ============================================================
# COLLECTION: tc_pdf_imports
# ============================================================
# 1 document = 1 PDF TC importé physiquement sur le serveur

class TCPDFImportDocument(BaseModel):
    """
    Document MongoDB pour tc_pdf_imports.
    
    Représente un fichier PDF TC stocké physiquement.
    """
    id: Optional[str] = Field(None, alias="_id")
    tc_pdf_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID v4 unique pour identifier le PDF (UNIQUE INDEX)"
    )
    filename: str = Field(..., description="Nom original du fichier")
    storage_path: str = Field(..., description="Chemin relatif sur disque")
    content_type: str = Field(default="application/pdf")
    file_size_bytes: int = Field(default=0, description="Taille du fichier")
    source: str = Field(default="TRANSPORT_CANADA")
    imported_by: str = Field(..., description="user_id de l'importateur")
    imported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        populate_by_name = True
        json_encoders = {
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        }


class TCPDFImportCreate(BaseModel):
    """Payload pour créer un tc_pdf_imports document."""
    filename: str
    storage_path: str
    file_size_bytes: int = 0
    imported_by: str


class TCPDFImportResponse(BaseModel):
    """Réponse API pour un PDF importé."""
    tc_pdf_id: str
    filename: str
    storage_path: str
    content_type: str = "application/pdf"
    file_size_bytes: int
    source: str = "TRANSPORT_CANADA"
    imported_by: str
    imported_at: str


# ============================================================
# COLLECTION: tc_imported_references
# ============================================================
# 1 document = 1 référence TC liée à un avion et un PDF

class TCImportedReferenceDocument(BaseModel):
    """
    Document MongoDB pour tc_imported_references.
    
    Représente une référence AD/SB extraite d'un PDF et liée à un avion.
    
    IMPORTANT:
    - _id (ObjectId) = tc_reference_id canonique pour DELETE
    - identifier (CF-xxxx) = affichage humain uniquement, JAMAIS clé DB
    - tc_pdf_id = lien vers tc_pdf_imports
    """
    id: Optional[str] = Field(None, alias="_id")
    aircraft_id: str = Field(..., description="ID de l'avion lié")
    identifier: str = Field(..., description="Référence TC (ex: CF-1987-15R) - affichage uniquement")
    type: Literal["AD", "SB"] = Field(..., description="Type de référence")
    title: Optional[str] = Field(None, description="Titre extrait du PDF")
    tc_pdf_id: str = Field(..., description="UUID du PDF source (lien vers tc_pdf_imports)")
    source: str = Field(default="TC_PDF_IMPORT")
    scope: Optional[str] = Field(None, description="airframe, engine, propeller, etc.")
    created_by: str = Field(..., description="user_id du créateur")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        populate_by_name = True
        json_encoders = {
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        }


class TCImportedReferenceCreate(BaseModel):
    """Payload pour créer une référence importée."""
    aircraft_id: str
    identifier: str
    type: Literal["AD", "SB"]
    title: Optional[str] = None
    tc_pdf_id: str
    scope: Optional[str] = None
    created_by: str


class TCImportedReferenceResponse(BaseModel):
    """
    Réponse API pour une référence importée.
    
    tc_reference_id = MongoDB _id (24-char hex) → utiliser pour DELETE
    tc_pdf_id = UUID du PDF → utiliser pour GET PDF
    """
    tc_reference_id: str = Field(..., description="MongoDB ObjectId (24-char hex) - utiliser pour DELETE")
    aircraft_id: str
    identifier: str = Field(..., description="Référence TC (affichage humain)")
    type: str
    title: Optional[str] = None
    tc_pdf_id: str = Field(..., description="UUID du PDF - utiliser pour GET PDF")
    source: str = "TC_PDF_IMPORT"
    scope: Optional[str] = None
    created_by: str
    created_at: str


# ============================================================
# INDEX DEFINITIONS (pour création via ensure_indexes)
# ============================================================

TC_PDF_IMPORTS_INDEXES = [
    {
        "keys": [("tc_pdf_id", 1)],
        "unique": True,
        "name": "tc_pdf_id_unique"
    },
    {
        "keys": [("imported_by", 1)],
        "name": "imported_by_idx"
    }
]

TC_IMPORTED_REFERENCES_INDEXES = [
    {
        "keys": [("aircraft_id", 1)],
        "name": "aircraft_id_idx"
    },
    {
        "keys": [("tc_pdf_id", 1)],
        "name": "tc_pdf_id_idx"
    },
    {
        "keys": [("aircraft_id", 1), ("identifier", 1)],
        "name": "aircraft_identifier_idx"
    },
    {
        "keys": [("created_by", 1)],
        "name": "created_by_idx"
    }
]
