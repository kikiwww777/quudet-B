"""Admin operations — manual reconciliation, system diagnostics."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_superuser
from app.database import get_db
from app.models.user import User
from app.services.reconciliation import reconcile_all

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reconcile")
def run_reconciliation(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_superuser)],
):
    """Manually trigger all reconciliation steps.

    Superuser only.  Useful for debugging stuck jobs.
    """
    results = reconcile_all(db)
    return {"ok": True, "steps": results}
