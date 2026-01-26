"""
TC PDF Import Database Service

Gère les collections tc_pdf_imports et tc_imported_references.
Création lazy des indexes à la première utilisation.

TC-SAFE: Aucune logique de conformité, import uniquement.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
import uuid
import os

from models.tc_pdf_import import (
    TC_PDF_IMPORTS_INDEXES,
    TC_IMPORTED_REFERENCES_INDEXES,
)

logger = logging.getLogger(__name__)

# Flag pour éviter de créer les indexes plusieurs fois
_indexes_ensured = False


async def ensure_tc_pdf_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Crée les indexes pour les collections tc_pdf_imports et tc_imported_references.
    
    Appelé une seule fois au démarrage ou à la première opération.
    """
    global _indexes_ensured
    
    if _indexes_ensured:
        return
    
    try:
        # Collection tc_pdf_imports
        for idx_spec in TC_PDF_IMPORTS_INDEXES:
            try:
                await db.tc_pdf_imports.create_index(
                    idx_spec["keys"],
                    unique=idx_spec.get("unique", False),
                    name=idx_spec["name"],
                    background=True
                )
            except Exception as e:
                # Index exists or other non-fatal error
                logger.debug(f"Index {idx_spec['name']} skip: {e}")
        
        # Collection tc_imported_references
        for idx_spec in TC_IMPORTED_REFERENCES_INDEXES:
            try:
                await db.tc_imported_references.create_index(
                    idx_spec["keys"],
                    unique=idx_spec.get("unique", False),
                    name=idx_spec["name"],
                    background=True
                )
            except Exception as e:
                logger.debug(f"Index {idx_spec['name']} skip: {e}")
        
        _indexes_ensured = True
        logger.info("[TC PDF] Indexes ensured for tc_pdf_imports and tc_imported_references")
        
    except Exception as e:
        logger.error(f"[TC PDF] Failed to ensure indexes: {e}")


class TCPDFDatabaseService:
    """
    Service de base de données pour les imports PDF TC.
    
    Collections:
    - tc_pdf_imports: Métadonnées des PDF stockés
    - tc_imported_references: Références extraites liées aux avions
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
    
    async def _ensure_indexes(self):
        """Ensure indexes exist (lazy initialization)."""
        await ensure_tc_pdf_indexes(self.db)
    
    # ========================================================
    # tc_pdf_imports OPERATIONS
    # ========================================================
    
    async def create_pdf_import(
        self,
        filename: str,
        storage_path: str,
        file_size_bytes: int,
        imported_by: str
    ) -> Tuple[ObjectId, str]:
        """
        Crée un document tc_pdf_imports.
        
        Returns:
            Tuple[ObjectId, str]: (_id, tc_pdf_id)
        """
        await self._ensure_indexes()
        
        tc_pdf_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        
        doc = {
            "tc_pdf_id": tc_pdf_id,
            "filename": filename,
            "storage_path": storage_path,
            "content_type": "application/pdf",
            "file_size_bytes": file_size_bytes,
            "source": "TRANSPORT_CANADA",
            "imported_by": imported_by,
            "imported_at": now
        }
        
        result = await self.db.tc_pdf_imports.insert_one(doc)
        logger.info(f"[TC PDF] Created tc_pdf_imports: _id={result.inserted_id}, tc_pdf_id={tc_pdf_id}")
        
        return result.inserted_id, tc_pdf_id
    
    async def get_pdf_by_tc_pdf_id(self, tc_pdf_id: str) -> Optional[dict]:
        """Récupère un PDF par son tc_pdf_id (UUID)."""
        await self._ensure_indexes()
        return await self.db.tc_pdf_imports.find_one({"tc_pdf_id": tc_pdf_id})
    
    async def delete_pdf_import(self, tc_pdf_id: str) -> bool:
        """
        Supprime un document tc_pdf_imports et le fichier physique.
        
        Returns:
            True si supprimé, False sinon
        """
        await self._ensure_indexes()
        
        doc = await self.db.tc_pdf_imports.find_one({"tc_pdf_id": tc_pdf_id})
        if not doc:
            return False
        
        # Supprimer le fichier physique
        storage_path = doc.get("storage_path")
        if storage_path:
            full_path = f"/app/backend/{storage_path}"
            if os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    logger.info(f"[TC PDF] Deleted file: {storage_path}")
                except Exception as e:
                    logger.warning(f"[TC PDF] Failed to delete file: {e}")
        
        # Supprimer le document
        result = await self.db.tc_pdf_imports.delete_one({"tc_pdf_id": tc_pdf_id})
        
        return result.deleted_count > 0
    
    # ========================================================
    # tc_imported_references OPERATIONS
    # ========================================================
    
    async def create_imported_reference(
        self,
        aircraft_id: str,
        identifier: str,
        ref_type: str,
        tc_pdf_id: str,
        created_by: str,
        title: Optional[str] = None,
        scope: Optional[str] = None
    ) -> ObjectId:
        """
        Crée un document tc_imported_references.
        
        Returns:
            ObjectId: _id du document créé (= tc_reference_id)
        """
        await self._ensure_indexes()
        
        now = datetime.now(timezone.utc)
        
        doc = {
            "aircraft_id": aircraft_id,
            "identifier": identifier.upper(),
            "type": ref_type.upper(),
            "title": title,
            "tc_pdf_id": tc_pdf_id,
            "source": "TC_PDF_IMPORT",
            "scope": scope,
            "created_by": created_by,
            "created_at": now
        }
        
        result = await self.db.tc_imported_references.insert_one(doc)
        logger.info(f"[TC PDF] Created tc_imported_references: _id={result.inserted_id}, identifier={identifier}")
        
        return result.inserted_id
    
    async def get_references_by_aircraft(self, aircraft_id: str) -> List[dict]:
        """Récupère toutes les références importées pour un avion."""
        await self._ensure_indexes()
        
        cursor = self.db.tc_imported_references.find({"aircraft_id": aircraft_id})
        return await cursor.to_list(length=1000)
    
    async def get_reference_by_id(self, tc_reference_id: str) -> Optional[dict]:
        """
        Récupère une référence par son _id (tc_reference_id).
        
        Args:
            tc_reference_id: ObjectId en string (24-char hex)
        """
        await self._ensure_indexes()
        
        try:
            obj_id = ObjectId(tc_reference_id)
        except Exception:
            return None
        
        return await self.db.tc_imported_references.find_one({"_id": obj_id})
    
    async def delete_reference_by_id(self, tc_reference_id: str) -> Tuple[bool, Optional[str]]:
        """
        Supprime une référence par son _id.
        
        Returns:
            Tuple[bool, Optional[str]]: (success, tc_pdf_id si orphelin)
        """
        await self._ensure_indexes()
        
        try:
            obj_id = ObjectId(tc_reference_id)
        except Exception:
            return False, None
        
        # Récupérer le document avant suppression
        doc = await self.db.tc_imported_references.find_one({"_id": obj_id})
        if not doc:
            return False, None
        
        tc_pdf_id = doc.get("tc_pdf_id")
        
        # Supprimer la référence
        result = await self.db.tc_imported_references.delete_one({"_id": obj_id})
        
        if result.deleted_count == 0:
            return False, None
        
        # Vérifier si le PDF est orphelin (aucune autre référence)
        remaining = await self.db.tc_imported_references.count_documents({"tc_pdf_id": tc_pdf_id})
        
        orphan_pdf_id = tc_pdf_id if remaining == 0 else None
        
        logger.info(f"[TC PDF] Deleted reference: _id={tc_reference_id}, orphan_pdf={orphan_pdf_id is not None}")
        
        return True, orphan_pdf_id
    
    async def get_references_by_pdf(self, tc_pdf_id: str) -> List[dict]:
        """Récupère toutes les références liées à un PDF."""
        await self._ensure_indexes()
        
        cursor = self.db.tc_imported_references.find({"tc_pdf_id": tc_pdf_id})
        return await cursor.to_list(length=1000)
    
    # ========================================================
    # UTILITY METHODS
    # ========================================================
    
    async def count_references_by_aircraft(self, aircraft_id: str) -> int:
        """Compte les références importées pour un avion."""
        await self._ensure_indexes()
        return await self.db.tc_imported_references.count_documents({"aircraft_id": aircraft_id})
    
    async def get_import_stats(self, user_id: str) -> dict:
        """Statistiques d'import pour un utilisateur."""
        await self._ensure_indexes()
        
        pdf_count = await self.db.tc_pdf_imports.count_documents({"imported_by": user_id})
        ref_count = await self.db.tc_imported_references.count_documents({"created_by": user_id})
        
        return {
            "total_pdfs": pdf_count,
            "total_references": ref_count
        }
