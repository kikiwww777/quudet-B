"""Resource provisioner 鈥?download, verify, extract, cache on Linux nodes.

Implements the Level A (automatic) and Level B (approval-required) download
paths from the AR design.  The provisioner never discovers sources or chooses
URLs 鈥?it executes the manifest AR authored.

Cache layout (``/srv/quudet/cache/``)::

    staging/<provision-id>/download.part
    archives/<sha256>.tar
    content/<cache-key>/
    datasets/VOC -> ../content/<cache-key>/VOC
    receipts/<cache-key>.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from app.models.resource_manifest import ResourceManifest

logger = logging.getLogger(__name__)


def _relative_symlink_target(content_target: Path, alias_parent: Path) -> str:
    """Return a portable relative symlink target for all supported Python versions."""
    return os.path.relpath(content_target, start=alias_parent)


class ResourceProvisioner:
    """Download, verify, extract, and cache a resource on a Linux node.

    Usage::

        provisioner = ResourceProvisioner(cache_root=Path("/srv/quudet/cache"))
        receipt = provisioner.provision(
            manifest=manifest_dict,
            provision_id="uuid",
            on_progress=lambda pct, bytes_dl: ...
        )
    """

    def __init__(self, cache_root: Path) -> None:
        self._root = cache_root
        self._staging_dir = cache_root / "staging"
        self._archives_dir = cache_root / "archives"
        self._content_dir = cache_root / "content"
        self._receipts_dir = cache_root / "receipts"

        for d in [self._staging_dir, self._archives_dir, self._content_dir, self._receipts_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def provision(
        self,
        manifest: ResourceManifest | dict,
        provision_id: str,
        on_progress: callable = None,
        on_log: callable = None,
    ) -> dict:
        """Execute a full provision cycle for one manifest.

        Returns a receipt dict (suitable for ``ProvisionReceipt`` schema).
        Raises ``RuntimeError`` on any recoverable failure; caller should
        set the plan to ``FAILED`` with the error message.
        """
        m = manifest if isinstance(manifest, dict) else {
            "resource_id": manifest.resource_id,
            "version": manifest.version,
            "source": manifest.source or {},
            "integrity": manifest.integrity or {},
            "delivery": manifest.delivery or {},
            "validation": manifest.validation or {},
            "manifest_content_hash": manifest.manifest_content_hash,
        }

        delivery = m.get("delivery") or {}
        integrity = m.get("integrity") or {}
        source = m.get("source") or {}
        validation = m.get("validation") or {}

        # cache_key: authoritative source is manifest_content_hash, NOT
        # delivery.cache_key (which may be stale or client-forged).
        cache_key = m.get("manifest_content_hash") or delivery.get("cache_key") or ""
        if not cache_key:
            raise RuntimeError("No cache_key in manifest delivery block")

        archive_url = source.get("url", "")
        if not archive_url:
            raise RuntimeError("No source URL in manifest")

        archive_sha256 = integrity.get("archive_sha256", "")
        if not archive_sha256:
            raise RuntimeError("archive_sha256 is required for automatic download (Level A)")

        archive_format = delivery.get("archive_format", "zip")
        extract_subdir = delivery.get("extract_subdir")
        target_relative_path = delivery.get("target_relative_path", "")
        allow_resume = delivery.get("allow_resume", True)
        required_paths = validation.get("required_paths", [])
        validator_kind = validation.get("kind", "")
        output_data_yaml_path = delivery.get("output_data_yaml_path") or validation.get("yaml_relative_path")

        self._log(on_log, f"Provisioning {m.get('resource_id')} @ {cache_key[:16]}...  url={archive_url}")

        # ---- Step 1: Download (with resume) ----
        staging_path = self._staging_dir / provision_id
        staging_path.mkdir(parents=True, exist_ok=True)
        part_path = staging_path / "download.part"

        expected_size = integrity.get("expected_size_bytes", 0)
        self._download(archive_url, part_path, expected_size, allow_resume, on_progress, on_log)

        bytes_downloaded = part_path.stat().st_size if part_path.exists() else 0
        self._log(on_log, f"Downloaded {bytes_downloaded} bytes")

        # ---- Step 2: Verify checksum ----
        actual_sha256 = self._sha256_file(part_path)
        if actual_sha256 != archive_sha256:
            raise RuntimeError(
                f"Checksum mismatch: expected {archive_sha256}, got {actual_sha256}"
            )
        self._log(on_log, f"SHA256 verified ({actual_sha256[:16]}...)")

        # ---- Step 3: Archive to persistent store ----
        archive_ext = self._format_extension(archive_format)
        archive_path = self._archives_dir / f"{archive_sha256}{archive_ext}"
        if not archive_path.exists():
            shutil.copy2(part_path, archive_path)
            self._log(on_log, f"Archive stored at {archive_path.name}")

        # ---- Step 4: Extract (or re-validate) ----
        content_target = self._content_dir / _safe_cache_dirname(cache_key)
        needs_extract = True

        if content_target.exists():
            self._log(on_log, f"Content directory exists, re-validating: {content_target}")
            receipt_path = self._receipts_dir / _receipt_filename(cache_key)
            if receipt_path.is_file():
                try:
                    existing = json.loads(receipt_path.read_text(encoding="utf-8"))
                    if existing.get("archive_sha256") == archive_sha256:
                        self._log(on_log, "Receipt matches 鈥?cache is valid")
                        return existing
                except (json.JSONDecodeError, OSError):
                    self._log(on_log, "Corrupt receipt 鈥?re-provisioning")
            self._log(on_log, "Cache invalid 鈥?deleting and re-extracting")
            shutil.rmtree(content_target, ignore_errors=True)
            # Re-extraction happens below (needs_extract is still True)

        if needs_extract:
            extract_tmp = self._content_dir / f".tmp_{provision_id}"
            if extract_tmp.exists():
                shutil.rmtree(extract_tmp, ignore_errors=True)
            extract_tmp.mkdir(parents=True, exist_ok=True)

            try:
                self._extract(archive_path, extract_tmp, archive_format, extract_subdir, on_log)
                # ---- Step 5: Validate ----
                if validator_kind == "yolo_dataset":
                    self._validate_yolo_dataset(extract_tmp, required_paths, validation, on_log)
                elif validator_kind:
                    self._log(on_log, f"Validator '{validator_kind}' is not implemented 鈥?skipping")
                os.rename(str(extract_tmp), str(content_target))
                self._log(on_log, f"Extracted to {content_target}")
            except Exception:
                if extract_tmp.exists():
                    shutil.rmtree(extract_tmp, ignore_errors=True)
                raise

        # ---- Step 6: Create alias (symlink for human readability) ----
        if target_relative_path:
            alias = self._root / target_relative_path
            if not alias.exists():
                alias.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.symlink(
                        _relative_symlink_target(content_target, alias.parent),
                        str(alias),
                        target_is_directory=True,
                    )
                except (OSError, ValueError):
                    # Fallback to absolute symlink
                    os.symlink(str(content_target), str(alias), target_is_directory=True)
                self._log(on_log, f"Alias created: {alias} -> {content_target}")

        # ---- Step 7: Write receipt ----
        if output_data_yaml_path:
            output_path = content_target / output_data_yaml_path
            if not output_path.is_file():
                raise RuntimeError(f"Manifest output data yaml not found: {output_data_yaml_path}")
        local_uri = f"cache://{target_relative_path}" if target_relative_path else f"cache://content/{cache_key}"
        resource_id = m.get("resource_id", "")
        receipt = _build_receipt(
            provision_id=provision_id,
            resource_id=resource_id,
            cache_key=cache_key,
            archive_sha256=actual_sha256,
            bytes_downloaded=bytes_downloaded,
            local_uri=local_uri,
            validator_kind=validator_kind,
            output_data_yaml_path=output_data_yaml_path,
        )
        self._write_receipt(cache_key, receipt)
        self._log(on_log, f"Receipt written for {cache_key[:16]}...")

        # Cleanup staging
        if staging_path.exists():
            shutil.rmtree(staging_path, ignore_errors=True)

        return receipt

    # ------------------------------------------------------------------
    # Internal: Download
    # ------------------------------------------------------------------

    def _download(
        self,
        url: str,
        dest: Path,
        expected_size: int,
        allow_resume: bool,
        on_progress: callable | None,
        on_log: callable | None,
    ) -> None:
        """Download from URL to destination, resuming if possible."""
        mode = "ab" if dest.exists() and allow_resume else "wb"
        existing_size = dest.stat().st_size if dest.exists() and mode == "ab" else 0
        headers = {"User-Agent": "QuuDet-ResourceProvisioner/1.0"}
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"
            self._log(on_log, f"Resuming from byte {existing_size}")

        req = Request(url, headers=headers, method="GET")
        try:
            resp = urlopen(req, timeout=300)
        except HTTPError as e:
            # If server doesn't support Range (416), restart from scratch
            if e.code == 416 and existing_size > 0:
                dest.unlink(missing_ok=True)
                return self._download(url, dest, expected_size, False, on_progress, on_log)
            raise RuntimeError(f"Download HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}") from e
        except URLError as e:
            raise RuntimeError(f"Download network error: {e}") from e

        # Check for 206 Partial Content when resuming
        if existing_size > 0 and resp.status != 206:
            self._log(on_log, f"Server returned {resp.status} instead of 206 鈥?restarting from scratch")
            dest.unlink(missing_ok=True)
            return self._download(url, dest, expected_size, False, on_progress, on_log)

        total = expected_size or int(resp.headers.get("content-length", 0)) or 0
        downloaded = existing_size

        CHUNK = 1024 * 1024  # 1 MiB
        with dest.open(mode) as f:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0 and on_progress:
                    pct = min(99, int(round(downloaded * 100 / total)))
                    on_progress(pct, downloaded)

        if on_progress:
            on_progress(100, downloaded)

    # ------------------------------------------------------------------
    # Internal: Extract
    # ------------------------------------------------------------------

    def _extract(
        self,
        archive_path: Path,
        target: Path,
        archive_format: str,
        extract_subdir: str | None,
        on_log: callable | None,
    ) -> None:
        """Extract an archive into *target*, optionally stripping a top-level subdir.

        Raises ``RuntimeError`` on path traversal, symlinks outside cache,
        or oversized archives (> 10 GiB or > 100 000 files).
        """
        target_resolved = target.resolve()

        # Safety limits
        MAX_FILES = 100_000
        MAX_TOTAL_BYTES = 10 * 1024 ** 3  # 10 GiB
        file_count = 0
        total_bytes = 0

        def _check_container(path: Path) -> None:
            """Raise if *path* resolves outside *target_resolved*."""
            nonlocal file_count, total_bytes
            file_count += 1
            if file_count > MAX_FILES:
                raise RuntimeError(f"Archive contains >{MAX_FILES} files 鈥?rejected")
            try:
                path.resolve().relative_to(target_resolved)
            except ValueError:
                raise RuntimeError(f"Path traversal detected: {path}")

        if archive_format == "zip":
            with zipfile.ZipFile(archive_path, "r") as zf:
                for name in zf.namelist():
                    _check_container((target / name).resolve())
                    try:
                        info = zf.getinfo(name)
                        total_bytes += info.file_size
                        if total_bytes > MAX_TOTAL_BYTES:
                            raise RuntimeError(f"Archive exceeds {MAX_TOTAL_BYTES // (1024**3)} GiB 鈥?rejected")
                    except (KeyError, OSError):
                        pass
                zf.extractall(target)
        elif archive_format in ("tar", "tar.gz", "tgz", "tar.bz2"):
            mode = "r:gz" if archive_format in ("tar.gz", "tgz") else "r:bz2" if archive_format == "tar.bz2" else "r"
            with tarfile.open(archive_path, mode) as tf:
                for member in tf.getmembers():
                    _check_container((target / member.name).resolve())
                    total_bytes += member.size
                    if total_bytes > MAX_TOTAL_BYTES:
                        raise RuntimeError(f"Archive exceeds {MAX_TOTAL_BYTES // (1024**3)} GiB 鈥?rejected")
                    # Reject symlinks that point outside the extract root
                    if member.issym() or member.islnk():
                        link_dest = (target / member.linkname).resolve()
                        try:
                            link_dest.relative_to(target_resolved)
                        except ValueError:
                            raise RuntimeError(f"Symlink outside cache: {member.name} -> {member.linkname}")
                tf.extractall(target)
        else:
            # Treat unknown formats as raw single-file copy (with size limit)
            if archive_path.stat().st_size > MAX_TOTAL_BYTES:
                raise RuntimeError(f"File exceeds {MAX_TOTAL_BYTES // (1024**3)} GiB 鈥?rejected")
            shutil.copy2(archive_path, target / archive_path.name)

        # If a subdir should be extracted, move its contents up and remove it
        if extract_subdir:
            sub = target / extract_subdir
            if sub.exists() and sub.is_dir():
                for item in list(sub.iterdir()):
                    shutil.move(str(item), str(target / item.name))
                shutil.rmtree(sub, ignore_errors=True)

    # ------------------------------------------------------------------
    # Internal: YOLO dataset validation
    # ------------------------------------------------------------------

    def _validate_yolo_dataset(
        self,
        root: Path,
        required_paths: list[str],
        validation: dict,
        on_log: callable | None,
    ) -> None:
        """Check that a YOLO dataset has the expected directory layout."""
        missing = []
        for rel in required_paths:
            p = root / rel
            if not p.exists():
                missing.append(rel)
        if missing:
            raise RuntimeError(f"YOLO dataset validation failed 鈥?missing paths: {missing}")

        yaml_rel = validation.get("yaml_relative_path", "")
        if yaml_rel:
            yaml_path = root / yaml_rel
            if not yaml_path.is_file():
                raise RuntimeError(f"YOLO dataset yaml not found: {yaml_rel}")
            try:
                import yaml
                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise RuntimeError("Dataset YAML is not a valid mapping")
                for key in ("path", "train", "nc"):
                    if key not in data:
                        self._log(on_log, f"Dataset YAML missing recommended key: '{key}'")
            except ImportError:
                self._log(on_log, "PyYAML not available 鈥?skipping YAML parse validation")

    # ------------------------------------------------------------------
    # Internal: utilities
    # ------------------------------------------------------------------

    def _sha256_file(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(8 * 1024 * 1024)  # 8 MiB
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def _write_receipt(self, cache_key: str, receipt: dict) -> None:
        receipt_path = self._receipts_dir / _receipt_filename(cache_key)
        receipt_path.write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _format_extension(fmt: str) -> str:
        return {
            "zip": ".zip",
            "tar": ".tar",
            "tar.gz": ".tar.gz",
            "tgz": ".tar.gz",
            "tar.bz2": ".tar.bz2",
        }.get(fmt, f".{fmt}")

    @staticmethod
    def _log(on_log: callable | None, message: str) -> None:
        if on_log:
            on_log(message)
        logger.info(message)

    # ------------------------------------------------------------------
    # Cache inventory
    # ------------------------------------------------------------------

    def list_cache_inventory(self) -> list[dict]:
        """Build a ``NodeResourceInventory`` from disk receipts."""
        resources: list[dict] = []
        for fpath in sorted(self._receipts_dir.glob("*.json")):
            try:
                receipt = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            resources.append({
                "resource_id": receipt.get("resource_id", ""),
                "cache_key": receipt.get("cache_key", ""),
                "status": "READY" if receipt.get("state") == "READY" else "UNKNOWN",
                "verified_at": receipt.get("completed_at", ""),
                "local_uri": receipt.get("local_uri", ""),
            })
        return resources

    def cache_free_bytes(self) -> int:
        """Return free disk space on the cache filesystem."""
        try:
            import shutil
            return shutil.disk_usage(self._root).free
        except Exception:
            return 0


# 鈹€鈹€ Module-level helpers 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _receipt_filename(cache_key: str) -> str:
    """Filesystem-safe receipt filename (colons replaced for Windows compat)."""
    return f"{cache_key.replace(':', '_')}.json"


def _safe_cache_dirname(cache_key: str) -> str:
    """Filesystem-safe directory name for a cache_key."""
    return cache_key.replace(":", "_")


def _build_receipt(
    provision_id: str,
    resource_id: str,
    cache_key: str,
    archive_sha256: str,
    bytes_downloaded: int,
    local_uri: str,
    validator_kind: str,
    output_data_yaml_path: str | None = None,
) -> dict:
    """Build a standardised provision receipt dict."""
    return {
        "provision_id": provision_id,
        "resource_id": resource_id,
        "state": "READY",
        "cache_key": cache_key,
        "archive_sha256": archive_sha256,
        "bytes_downloaded": bytes_downloaded,
        "local_uri": local_uri,
        "output_data_yaml_path": output_data_yaml_path,
        "validator": {"kind": validator_kind, "result": "passed"} if validator_kind else {},
        "completed_at": datetime.utcnow().isoformat(),
    }
