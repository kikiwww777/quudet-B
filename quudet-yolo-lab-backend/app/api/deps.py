from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.user import User

# Optional bearer: allows missing token when DISABLE_AUTH is True
security = HTTPBearer(auto_error=False)


def _guest_user(db: Session) -> User:
    settings = get_settings()
    user = db.query(User).filter(User.email == settings.GUEST_USER_EMAIL).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Guest user not initialized; restart the API after bootstrap.",
        )
    return user


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> User:
    settings = get_settings()

    if settings.DISABLE_AUTH:
        return _guest_user(db)

    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if creds is None or (creds.scheme or "").lower() != "bearer":
        raise credentials_exc

    token = creds.credentials
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        sub = payload.get("sub")
        if sub is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    user = db.query(User).filter(User.email == str(sub)).first()
    if user is None or not user.is_active:
        raise credentials_exc
    return user


def require_superuser(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Superuser required")
    return user
