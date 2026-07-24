import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/uploads", tags=["uploads"])


def _safe_filename(name: str) -> str:
    if not name:
        return "upload.bin"
    # basic path traversal prevention
    return name.replace("..", "_").replace("/", "_").replace("\\", "_")


@router.post("/detect-file")
def upload_detect_file(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    file: UploadFile = File(...),
):
    settings = get_settings()
    safe = _safe_filename(file.filename or "upload.bin")

    # Upload size limit: 500MB (adjust if needed)
    data = file.file.read()
    max_bytes = 500 * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(413, f"File too large (max {max_bytes // (1024 * 1024)}MB)")

    dest_dir = settings.uploads_dir / "detect" / str(user.id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    uid = uuid.uuid4().hex
    stored_path = dest_dir / f"{uid}_{safe}"
    stored_path.write_bytes(data)

    # Return absolute path since YOLO runner uses subprocess with repo root cwd.
    return {"stored_path": str(stored_path.resolve())}

