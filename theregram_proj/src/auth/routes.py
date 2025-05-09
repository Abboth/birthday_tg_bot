from fastapi import Security, Depends, HTTPException, status, APIRouter, Request
from fastapi.security import HTTPAuthorizationCredentials, OAuth2PasswordRequestForm
from fastapi_limiter.depends import RateLimiter
from sqlalchemy.ext.asyncio import AsyncSession

from theregram_proj.src.auth.services import get_user_device
from theregram_proj.src.core.connection import get_db
from theregram_proj.src.auth import repository as auth_repository
from theregram_proj.src.auth.security import auth_security, get_refresh_token
from theregram_proj.src.mail_services.service import verification_letter
from theregram_proj.src.users import repository as user_repository
from theregram_proj.src.users.models import User
from theregram_proj.src.users.repository import get_user_by_email
from theregram_proj.src.users.schemas import UserResponseSchema, UserSchema

router = APIRouter(tags=["Authorization"])


@router.post("/signup", response_model=UserResponseSchema,
             status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RateLimiter(times=4, seconds=60))]
             )
async def signup(body: UserSchema,
           request: Request,
           db: AsyncSession = Depends(get_db)):
    exist_user = await user_repository.get_user_by_email(str(body.email), db)
    if exist_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already exists")
    body.password = auth_security.get_password_hash(body.password)
    new_user = await user_repository.create_new_user(body, db)

    await verification_letter(new_user, str(request.base_url))
    await db.refresh(new_user)

    return new_user


@router.post("/login")
async def login(body: OAuth2PasswordRequestForm = Depends(),
                request: Request = None,
                db: AsyncSession = Depends(get_db)):
    user = await user_repository.get_user_by_email(str(body.username), db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email")
    if not user.is_verified:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email isn't confirmed yet")
    if not auth_security.verify_password(body.password, user.hashed_pwd):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")

    access_token = await auth_security.create_access_token(data={"sub": user.email})
    refresh_token_data = await auth_security.create_refresh_token(data={"sub": user.email})

    user_device = await get_user_device(request)
    refresh_token = refresh_token_data["token"]
    expires_at = refresh_token_data["expires_at"]

    await auth_repository.update_token(user, user_device, refresh_token, expires_at, db)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "device_type": user_device
    }


@router.get('/refresh_token')
async def refresh_token(credentials: HTTPAuthorizationCredentials = Security(get_refresh_token),
                        request: Request = None,
                        db: AsyncSession = Depends(get_db)):
    token = credentials.credentials
    email = await auth_security.decode_refresh_token(token)
    user: User = await get_user_by_email(email, db)
    session = next((s for s in user.auth_session if s.refresh_token == token), None)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    access_token = await auth_security.create_access_token(data={"sub": email})
    refresh_token_data = await auth_security.create_refresh_token(data={"sub": email})

    user_device = await get_user_device(request)
    refresh_token = refresh_token_data["token"]
    expires_at = refresh_token_data["expires_at"]

    await auth_repository.update_token(user, user_device, refresh_token, expires_at, db)
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}
