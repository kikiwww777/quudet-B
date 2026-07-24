import uuid
from typing import Annotated
from zipfile import ZipFile, is_zipfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.uploaded_dataset import UploadedDataset
from app.models.user import User
from app.services.uploaded_dataset_yaml import resolve_train_yaml_path

router = APIRouter(prefix="/datasets", tags=["datasets"])


def resolve_uploaded_dataset_data_yaml(ds: UploadedDataset) -> str | None:
    """Absolute path to a YAML suitable for Ultralytics ``data=`` (forward slashes)."""
    p = resolve_train_yaml_path(ds)
    return p.replace("\\", "/") if p else None


@router.post("/upload")
def upload_dataset(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
):
    safe_name = (file.filename or "upload.bin").replace("..", "_").replace("/", "_").replace("\\", "_")

    dest_dir = user_upload_dir(user.id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    uid = uuid.uuid4().hex
    path = dest_dir / f"{uid}_{safe_name}"
    
    # 增加上传大小限制到 4GB，并使用流式写入
    max_bytes = 4 * 1024 * 1024 * 1024  # 4GB
    chunk_size = 10 * 1024 * 1024  # 10MB per chunk
    total_bytes = 0
    
    with open(path, "wb") as f:
        while True:
            chunk = file.file.read(chunk_size)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                path.unlink(missing_ok=True)
                raise HTTPException(413, f"File too large (max {max_bytes // (1024 * 1024)}MB)")
            f.write(chunk)

    ds = UploadedDataset(
        filename=safe_name,
        stored_path=str(path.resolve()),
        owner_id=user.id,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)

    if is_zipfile(path):
        extract_to = dest_dir / f"{ds.id}_extracted"
        extract_to.mkdir(parents=True, exist_ok=True)
        try:
            with ZipFile(path) as zf:
                zf.extractall(extract_to)
            ds.extracted_path = str(extract_to.resolve())
        except Exception as exc:  # noqa: BLE001
            ds.extracted_path = f"(extract failed: {exc})"

    db.commit()
    db.refresh(ds)
    return {
        "id": ds.id,
        "filename": ds.filename,
        "stored_path": ds.stored_path,
        "extracted_path": ds.extracted_path,
        "data_yaml": resolve_uploaded_dataset_data_yaml(ds),
    }


@router.get("")
def list_datasets(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    rows = db.query(UploadedDataset).filter(UploadedDataset.owner_id == user.id).order_by(UploadedDataset.id.desc()).all()
    return [
        {
            "id": r.id,
            "filename": r.filename,
            "stored_path": r.stored_path,
            "extracted_path": r.extracted_path,
            "created_at": r.created_at.isoformat(),
            "data_yaml": resolve_uploaded_dataset_data_yaml(r),
        }
        for r in rows
    ]


def user_upload_dir(user_id: int):
    from app.config import get_settings

    return get_settings().uploads_dir / "datasets" / str(user_id)
