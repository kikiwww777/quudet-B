from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.security import hash_password
from app.models.user import User


def ensure_superuser(db: Session) -> None:
    settings = get_settings()
    exists = db.query(User).filter(User.email == settings.FIRST_SUPERUSER_EMAIL).first()
    if exists:
        return
    user = User(
        email=settings.FIRST_SUPERUSER_EMAIL,
        hashed_password=hash_password(settings.FIRST_SUPERUSER_PASSWORD),
        is_superuser=True,
        is_active=True,
    )
    db.add(user)
    db.commit()


def ensure_guest_user(db: Session) -> None:
    """Anonymous mode user (created when DISABLE_AUTH is True)."""
    settings = get_settings()
    email = settings.GUEST_USER_EMAIL
    exists = db.query(User).filter(User.email == email).first()
    if exists:
        return
    user = User(
        email=email,
        hashed_password=hash_password("not-used-disable-auth"),
        is_superuser=True,
        is_active=True,
    )
    db.add(user)
    db.commit()
