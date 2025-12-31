"""
Auth dependencies for AeroLogix AI
Contains shared authentication dependencies to avoid circular imports
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from motor.motor_asyncio import AsyncIOMotorDatabase
from database.mongodb import get_database
from models.user import User, UserSubscription, UserLimits
from services.auth_service import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

async def get_current_user(
    token: str = Depends(oauth2_scheme), 
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> User:
    """Get current authenticated user from token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
    
    user_id: str = payload.get("sub")
    if user_id is None:
        raise credentials_exception
    
    user_doc = await db.users.find_one({"_id": user_id})
    if user_doc is None:
        raise credentials_exception
    
    return User(
        id=user_doc["_id"],
        email=user_doc["email"],
        name=user_doc["name"],
        created_at=user_doc["created_at"],
        subscription=UserSubscription(**user_doc.get("subscription", {})),
        limits=UserLimits(**user_doc.get("limits", {}))
    )
