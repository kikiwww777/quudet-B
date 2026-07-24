from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.job_record import JobRecord
from app.models.user import User

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats")
def dashboard_stats(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    base = db.query(JobRecord).filter(JobRecord.owner_id == user.id)
    total = base.count()
    running = base.filter(JobRecord.status == "RUNNING").count()
    success = base.filter(JobRecord.status == "SUCCESS").count()
    failed = base.filter(JobRecord.status == "FAILED").count()
    return {
        "total": total,
        "running": running,
        "success": success,
        "failed": failed,
        "trained_models": 0,
    }
