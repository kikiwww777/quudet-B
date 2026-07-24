"""Artifact store abstraction — pluggable storage backend for experiment outputs.

Current implementation: ``LocalArtifactStore`` (local filesystem).
Future: ``S3ArtifactStore`` (MinIO / S3-compatible object storage).

Usage::

    from app.services.artifact_store import get_artifact_store

    store = get_artifact_store()
    uri = store.write_text(f"jobs/{job_id}/run.log", log_content)
    text = store.read_text(uri)
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Protocol


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class ArtifactStore(ABC):
    """Pluggable storage backend for experiment artifacts."""

    @abstractmethod
    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> str:
        """Write text content to *path* (relative to store root).

        Returns the canonical URI for the written artifact.
        """
        ...

    @abstractmethod
    def read_text(self, path: str, encoding: str = "utf-8") -> str | None:
        """Read text content from *path*.

        Returns ``None`` if the artifact does not exist.
        """
        ...

    @abstractmethod
    def write_bytes(self, path: str, content: bytes) -> str:
        """Write binary content to *path*.

        Returns the canonical URI.
        """
        ...

    @abstractmethod
    def read_bytes(self, path: str) -> bytes | None:
        """Read binary content from *path*.

        Returns ``None`` if the artifact does not exist.
        """
        ...

    @abstractmethod
    def write_json(self, path: str, data: Any) -> str:
        """Write JSON-serialisable *data* to *path*.

        Returns the canonical URI.
        """
        ...

    @abstractmethod
    def read_json(self, path: str) -> Any | None:
        """Read and deserialise JSON from *path*.

        Returns ``None`` if the artifact does not exist.
        """
        ...

    @abstractmethod
    def delete(self, path: str) -> bool:
        """Remove the artifact at *path*.

        Returns ``True`` if something was deleted, ``False`` otherwise.
        """
        ...

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Return ``True`` if an artifact exists at *path*."""
        ...

    @abstractmethod
    def list_files(self, directory: str, pattern: str = "*") -> list[str]:
        """List artifact paths under *directory* matching *pattern*.

        Returns relative paths (not full URIs).
        """
        ...

    @abstractmethod
    def job_dir(self, job_id: str) -> str:
        """Return the root directory/prefix for a job's artifacts."""
        ...


# ---------------------------------------------------------------------------
# Local filesystem implementation
# ---------------------------------------------------------------------------


class LocalArtifactStore(ArtifactStore):
    """Artifact store backed by the local filesystem.

    All paths are resolved relative to ``root``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    # -- helpers ---------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        """Convert a store-relative path to an absolute filesystem path.

        Safety: prevent directory traversal outside root.
        """
        # Resolve the full path and ensure it's under root
        resolved = (self._root / path).resolve()
        if not str(resolved).startswith(str(self._root)):
            msg = f"Path traversal detected: {path}"
            raise PermissionError(msg)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    # -- read / write ----------------------------------------------------

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> str:
        p = self._resolve(path)
        p.write_text(content, encoding=encoding)
        return str(p)

    def read_text(self, path: str, encoding: str = "utf-8") -> str | None:
        p = self._resolve(path)
        if not p.is_file():
            return None
        return p.read_text(encoding=encoding)

    def write_bytes(self, path: str, content: bytes) -> str:
        p = self._resolve(path)
        p.write_bytes(content)
        return str(p)

    def read_bytes(self, path: str) -> bytes | None:
        p = self._resolve(path)
        if not p.is_file():
            return None
        return p.read_bytes()

    def write_json(self, path: str, data: Any) -> str:
        content = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        return self.write_text(path, content)

    def read_json(self, path: str) -> Any | None:
        raw = self.read_text(path)
        if raw is None:
            return None
        return json.loads(raw)

    def delete(self, path: str) -> bool:
        p = self._resolve(path)
        if not p.exists():
            return False
        if p.is_dir():
            import shutil
            shutil.rmtree(p)
        else:
            p.unlink()
        return True

    def exists(self, path: str) -> bool:
        p = self._resolve(path)
        return p.exists()

    def list_files(self, directory: str, pattern: str = "*") -> list[str]:
        p = self._resolve(directory)
        if not p.is_dir():
            return []
        files: list[str] = []
        for f in p.rglob(pattern):
            if f.is_file():
                files.append(str(f.relative_to(self._root)).replace("\\", "/"))
        return sorted(files)

    def job_dir(self, job_id: str) -> str:
        return f"jobs/{job_id}"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_STORE: ArtifactStore | None = None


def get_artifact_store() -> ArtifactStore:
    """Return the application-wide artifact store instance.

    The store backend is determined by ``settings.ARTIFACT_STORE_BACKEND``.
    Currently only ``local`` is supported.
    """
    global _STORE
    if _STORE is not None:
        return _STORE

    from app.config import get_settings

    settings = get_settings()
    backend = getattr(settings, "ARTIFACT_STORE_BACKEND", "local")

    if backend == "local":
        _STORE = LocalArtifactStore(root=settings.artifacts_dir)
    else:
        msg = f"Unknown ARTIFACT_STORE_BACKEND: {backend!r}"
        raise ValueError(msg)

    return _STORE


def reset_artifact_store() -> None:
    """Reset the cached store instance (useful in tests)."""
    global _STORE
    _STORE = None
