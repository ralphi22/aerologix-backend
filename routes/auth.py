from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from motor.motor_asyncio import AsyncIOMotorDatabase
from database.mongodb import get_database
from models.user import UserCreate, User, UserInDB, Token, PlanTier, UserSubscription, UserLimits
from models.subscription_plan import SUBSCRIPTION_PLANS
from services.auth_service import verify_password, get_password_hash, create_access_token, decode_access_token
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncIOMotorDatabase = Depends(get_database)) -> User:
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

@router.post("/signup", response_model=Token)
async def signup(user: UserCreate, db: AsyncIOMotorDatabase = Depends(get_database)):
    """Create a new user account"""
    # Check if user already exists
    existing_user = await db.users.find_one({"email": user.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Get BASIC plan limits
    basic_plan = SUBSCRIPTION_PLANS[PlanTier.BASIC]
    
    # Create new user with BASIC plan
    user_dict = {
        "_id": str(datetime.utcnow().timestamp()).replace(".", ""),
        "email": user.email,
        "name": user.name,
        "hashed_password": get_password_hash(user.password),
        "created_at": datetime.utcnow(),
        "subscription": {
            "plan": PlanTier.BASIC,
            "status": "active",
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "trial_end": None,
            "current_period_end": None
        },
        "limits": {
            "max_aircrafts": basic_plan.features.max_aircrafts,
            "ocr_per_month": basic_plan.features.ocr_per_month,
            "logbook_entries_per_month": basic_plan.features.logbook_entries_per_month
        }
    }
    
    result = await db.users.insert_one(user_dict)
    logger.info(f"New user created: {user.email}")
    
    # Create access token
    access_token = create_access_token(data={"sub": user_dict["_id"]})
    
    # Return token and user info
    return Token(
        access_token=access_token,
        user=User(
            id=user_dict["_id"],
            email=user.email,
            name=user.name,
            created_at=user_dict["created_at"],
            subscription=UserSubscription(**user_dict["subscription"]),
            limits=UserLimits(**user_dict["limits"])
        )
    )

@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncIOMotorDatabase = Depends(get_database)):
    """Login with email and password"""
    # Find user
    user_doc = await db.users.find_one({"email": form_data.username})
    if not user_doc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Verify password
    if not verify_password(form_data.password, user_doc["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create access token
    access_token = create_access_token(data={"sub": user_doc["_id"]})
    
    logger.info(f"User logged in: {user_doc['email']}")
    
    return Token(
        access_token=access_token,
        user=User(
            id=user_doc["_id"],
            email=user_doc["email"],
            name=user_doc["name"],
            created_at=user_doc["created_at"],
            subscription=UserSubscription(**user_doc.get("subscription", {})),
            limits=UserLimits(**user_doc.get("limits", {}))
        )
    )

@router.get("/me", response_model=User)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user info"""
    return current_user
