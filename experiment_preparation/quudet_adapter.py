"""QuuDet adapter — gate that checks resource readiness before experiment creation.

This module is discovered and imported dynamically by ``app.api.routes.experiments._get_prep_gate()``.

Interface::

    def check_experiment_group(body: dict) -> dict:
        '''Returns {"allowed": bool, "status": str, "actions": list}'''
        ...

    def handle_preparation_result(result: dict, db: Session) -> dict:
        '''Create provisioning plans for any resources that need them.'''
        ...

The ``body`` is the deserialised ``ExperimentGroupCreate`` dict (``model_dump()``
output from the request).

Gate conditions (from the design doc §9):

    | Condition                                              | Result            |
    |--------------------------------------------------------|-------------------|
    | AR cannot resolve a usable source                      | blocked           |
    | Manifest requires human approval                       | manual_required   |
    | Compatible node has a valid cache receipt              | ready             |
    | Compatible node lacks resource but auto-provisionable  | provisioning      |
    | Provision fails                                        | blocked/required  |
    | No compatible node has disk/GPU capability             | blocked           |
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

# Lazy-import SQLAlchemy/DB to avoid forcing the package to depend on the
# backend — the gate is imported dynamically and may run in a different
# environment during development.

logger = logging.getLogger(__name__)


def check_experiment_group(body: dict) -> dict[str, Any]:
    """Evaluate resource readiness before creating an experiment group.

    This is the primary gate function called by experiments.py.
    Returns a dict with keys: ``allowed``, ``status``, ``actions``, ``details``.
    """
    # Default: allow if no resource constraints are involved
    dataset_name = (body.get("dataset_name") or "").strip()
    runs = body.get("runs") or []
    sweep = body.get("sweep")

    # Extract resource hints from the experiment spec
    explicit_resources = [item for item in (body.get("resources") or []) if isinstance(item, dict)]
    resource_ids = _collect_resource_ids(dataset_name, runs, sweep, explicit_resources)
    required_gpu = _requires_gpu(runs, sweep)

    if not resource_ids:
        return {"allowed": True, "status": "ready", "actions": []}

    requested_node_ids = _collect_target_node_ids(runs)

    if len(requested_node_ids) > 1:
        return {
            "allowed": False,
            "status": "blocked",
            "actions": [{
                "type": "blocked",
                "reason": "All runs in a resource-managed group must target the same node.",
                "node_ids": sorted(requested_node_ids),
            }],
            "provision_plan_ids": [],
        }

    # Delegate to the QuuDet backend for resource checking
    return _check_resources_via_backend(
        resource_ids,
        required_gpu=required_gpu,
        requested_node_id=next(iter(requested_node_ids), None),
        explicit_resources=explicit_resources,
    )


def handle_preparation_result(result: dict, db_session: Any) -> dict[str, Any]:
    """(Deprecated) Legacy stub — plans are now created inside check_experiment_group."""
    return result


# ---------------------------------------------------------------------------
# Internal: resource extraction
# ---------------------------------------------------------------------------


def _collect_resource_ids(
    dataset_name: str,
    runs: list[dict],
    sweep: dict | None,
    explicit_resources: list[dict] | None = None,
) -> set[str]:
    """Collect all AR-managed resource IDs referenced by the experiment spec.

    Maps group datasets plus ``payload.data`` and ``payload.model`` references
    to known resource IDs.  As AR matures, this can consume explicit manifest
    IDs directly instead of maintaining the compatibility map below.
    """
    ids: set[str] = set()

    for resource in explicit_resources or []:
        resource_id = str(resource.get("resource_id") or "").strip()
        if resource_id:
            ids.add(resource_id)

    if dataset_name:
        # Map well-known dataset names to AR resource IDs.
        mapped = _map_dataset_to_resource(dataset_name)
        if mapped:
            ids.add(mapped)

    def add_reference(value: Any) -> None:
        if not isinstance(value, str) or value.startswith("cache://"):
            return
        mapped = _map_dataset_to_resource(value)
        if mapped:
            ids.add(mapped)

    add_reference(dataset_name)

    # Scan run payloads for dataset and weight references.
    for run in runs:
        payload = run.get("payload") or {}
        add_reference(payload.get("data"))
        add_reference(payload.get("model"))

    # Scan sweep base_payload
    if sweep:
        base_payload = sweep.get("base_payload") or {}
        add_reference(base_payload.get("data"))
        add_reference(base_payload.get("model"))

    return ids


def _requires_gpu(runs: list[dict], sweep: dict | None) -> bool:
    """Return whether any requested run requires a GPU-capable node."""
    for run in runs:
        payload = run.get("payload") or {}
        if (
            run.get("required_gpu", False)
            or bool(payload.get("required_gpu"))
            or str(payload.get("device") or "").lower().startswith("cuda")
        ):
            return True
    if sweep:
        payload = sweep.get("base_payload") or {}
        return bool(payload.get("required_gpu")) or str(payload.get("device") or "").lower().startswith("cuda")
    return False


def _collect_target_node_ids(runs: list[dict]) -> set[str]:
    """Collect explicit node constraints from individual runs."""
    return {
        str(run.get("target_node_id")).strip()
        for run in runs
        if str(run.get("target_node_id") or "").strip()
    }


def _map_dataset_to_resource(dataset_name: str) -> str | None:
    """Map a dataset name or filename to a known AR resource ID.

    Extensible lookup — add entries as new resources are registered.
    """
    name_lower = dataset_name.lower().strip()

    mapping: dict[str, str] = {
        # YOLO built-in datasets
        "voc": "dataset:voc:2012-yolo",
        "voc.yaml": "dataset:voc:2012-yolo",
        "voc2012": "dataset:voc:2012-yolo",
        "coco8": "dataset:coco:coco8-yolo",
        "coco8.yaml": "dataset:coco:coco8-yolo",
        "coco128": "dataset:coco:coco128-yolo",
        "coco128.yaml": "dataset:coco:coco128-yolo",
        "coco": "dataset:coco:2017-yolo",
        "coco.yaml": "dataset:coco:2017-yolo",
        "visdrone": "dataset:visdrone:2019-yolo",
        "visdrone.yaml": "dataset:visdrone:2019-yolo",
        # Weights
        "yolo11n.pt": "weight:ultralytics:yolo11n",
        "yolo11s.pt": "weight:ultralytics:yolo11s",
        "yolo11m.pt": "weight:ultralytics:yolo11m",
        "yolo11l.pt": "weight:ultralytics:yolo11l",
        "yolo11x.pt": "weight:ultralytics:yolo11x",
    }

    for key, resource_id in mapping.items():
        if key in name_lower or name_lower in key:
            return resource_id
    return None


# ---------------------------------------------------------------------------
# Internal: QuuDet backend integration
# ---------------------------------------------------------------------------


def _get_backend_session():
    """Import and return a QuuDet database session.

    May return None if the backend modules are not available (e.g., during
    early development or standalone testing).
    """
    try:
        from app.config import get_settings
        from app.database import SessionLocal

        _ = get_settings()  # ensure settings are loaded first
        return SessionLocal()
    except Exception:
        return None


def _check_resources_via_backend(
    resource_ids: set[str],
    *,
    required_gpu: bool,
    requested_node_id: str | None,
    explicit_resources: list[dict] | None = None,
) -> dict[str, Any]:
    """Query the QuuDet database for manifests and node inventory."""
    db = _get_backend_session()
    if db is None:
        # Backend not available — allow through (preparation runs later)
        return {
            "allowed": False,
            "status": "blocked",
            "actions": [{
                "type": "blocked",
                "reason": "QuuDet resource gate backend is unavailable; refusing to bypass preparation.",
            }],
            "provision_plan_ids": [],
        }

    try:
        return _do_check(
            db,
            resource_ids,
            required_gpu=required_gpu,
            requested_node_id=requested_node_id,
            explicit_resources=explicit_resources or [],
        )
    finally:
        db.close()


def _do_check(
    db_session,
    resource_ids: set[str],
    *,
    required_gpu: bool,
    requested_node_id: str | None,
    explicit_resources: list[dict],
) -> dict[str, Any]:
    """Core check logic with an active DB session."""
    from app.models.compute_node import ComputeNode
    from app.models.resource_manifest import ResourceManifest

    actions: list[dict] = []
    resource_plans: list[dict] = []
    explicit_source_urls = {
        str(resource.get("resource_id") or "").strip(): str((resource.get("source") or {}).get("url") or resource.get("url") or "")
        for resource in explicit_resources
        if str(resource.get("resource_id") or "").strip()
    }

    _upsert_explicit_manifests(db_session, explicit_resources)

    for rid in resource_ids:
        # 1. Find active manifests for this resource
        manifests = (
            db_session.query(ResourceManifest)
            .filter(
                ResourceManifest.resource_id == rid,
                ResourceManifest.status == "active",
            )
            .order_by(ResourceManifest.created_at.desc())
            .all()
        )

        if not manifests:
            # AR has not published a manifest for this resource
            actions.append({
                "type": "blocked",
                "resource_id": rid,
                "reason": "No active manifest found. Run AR resource discovery first.",
            })
            return _blocked(actions)

        manifest = _select_manifest(manifests, explicit_source_urls.get(rid, ""))
        integrity = manifest.integrity or {}

        # 2. Check if this manifest needs human approval
        needs_approval = (
            (integrity.get("archive_sha256") or "") == ""
            and (manifest.manual_fallback or {}).get("allowed", False)
        )
        if needs_approval and not manifest.integrity_approved:
            actions.append({
                "type": "manual_required",
                "resource_id": rid,
                "manifest_id": manifest.id,
                "reason": "Manifest integrity requires human approval",
                "instructions": (manifest.manual_fallback or {}).get(
                    "instructions", "Approve via POST /api/v1/resources/manifests/{id}/approve"
                ),
            })
            return {
                "allowed": False,
                "status": "manual_required",
                "actions": actions,
                "provision_plan_ids": [],
            }

        # 3. Find compatible nodes that have this resource cached
        cache_key = manifest.manifest_content_hash or ""
        compatible_nodes = _find_compatible_nodes(
            db_session,
            cache_key,
            required_gpu=required_gpu,
            requested_node_id=requested_node_id,
        )

        if not compatible_nodes:
            actions.append({
                "type": "blocked",
                "resource_id": rid,
                "manifest_id": manifest.id,
                "reason": "No compatible node with capacity available",
            })
            return _blocked(actions)

        resource_plans.append({
            "resource_id": rid,
            "manifest_id": manifest.id,
            "cache_key": cache_key,
            "nodes": compatible_nodes,
        })

    target_node_id = _select_target_node(resource_plans)
    if target_node_id is None:
        actions.append({
            "type": "blocked",
            "reason": "No single compatible node can host every required resource.",
        })
        return _blocked(actions)

    provision_ids: list[str] = []
    for plan in resource_plans:
        node = next(item for item in plan["nodes"] if item["node_id"] == target_node_id)
        if node["cache_hit"]:
            actions.append({
                "type": "cache_hit",
                "resource_id": plan["resource_id"],
                "manifest_id": plan["manifest_id"],
                "node_id": target_node_id,
                "cache_key": plan["cache_key"],
            })
            continue

        provision_id = _create_provision_plan(
            db_session,
            {
                "resource_id": plan["resource_id"],
                "manifest_id": plan["manifest_id"],
                "cache_key": plan["cache_key"],
                "node_id": target_node_id,
                "requested_by": "experiment_preparation",
            },
        )
        if provision_id is None:
            actions.append({
                "type": "blocked",
                "resource_id": plan["resource_id"],
                "reason": "Failed to create a provisioning plan.",
            })
            return _blocked(actions)
        provision_ids.append(provision_id)
        actions.append({
            "type": "provision",
            "resource_id": plan["resource_id"],
            "manifest_id": plan["manifest_id"],
            "cache_key": plan["cache_key"],
            "node_id": target_node_id,
            "provision_id": provision_id,
        })

    status = "provisioning" if provision_ids else "ready"
    return {
        "allowed": status == "ready",
        "status": status,
        "actions": actions,
        "provision_plan_ids": provision_ids,
        "target_node_id": target_node_id,
        "details": {
            "resource_ids": sorted(resource_ids),
            "checked_at": datetime.utcnow().isoformat(),
        },
    }


def _upsert_explicit_manifests(db_session, resources: list[dict]) -> None:
    """Persist AR-provided manifests without any dataset-name lookup.

    A resource is keyed by its semantic manifest hash, so repeated AR polling
    is idempotent.  URLs without a checksum remain manual-approval-only;
    the agent never downloads an unverified artifact automatically.
    """
    if not resources:
        return
    from app.models.resource_manifest import ResourceManifest

    changed = False
    for raw in resources:
        resource_id = str(raw.get("resource_id") or "").strip()
        source = dict(raw.get("source") or {})
        if not source.get("url") and raw.get("url"):
            source["url"] = str(raw["url"])
        if not resource_id or not source.get("url"):
            continue
        integrity = dict(raw.get("integrity") or {})
        delivery = dict(raw.get("delivery") or {})
        validation = dict(raw.get("validation") or {})
        manual_fallback = dict(raw.get("manual_fallback") or {})
        if not integrity.get("archive_sha256"):
            manual_fallback.setdefault("allowed", True)
            manual_fallback.setdefault("instructions", "Provide the archive SHA256, then resubmit this manifest.")
        manifest_data = {
            "resource_id": resource_id,
            "resource_type": raw.get("resource_type", "dataset"),
            "version": str(raw.get("version") or "discovered"),
            "source": source,
            "integrity": integrity,
            "delivery": delivery,
            "validation": validation,
            "manual_fallback": manual_fallback,
        }
        content_hash = ResourceManifest.compute_cache_key(manifest_data)
        exists = db_session.query(ResourceManifest).filter(
            ResourceManifest.manifest_content_hash == content_hash
        ).first()
        if exists is not None:
            continue
        db_session.add(ResourceManifest(
            id=str(uuid.uuid4()),
            resource_id=resource_id,
            resource_type=str(manifest_data["resource_type"]),
            version=manifest_data["version"],
            display_name=str(raw.get("display_name") or resource_id),
            source=source,
            integrity=integrity,
            delivery=delivery,
            validation=validation,
            manual_fallback=manual_fallback,
            provenance=dict(raw.get("provenance") or {}),
            manifest_content_hash=content_hash,
        ))
        changed = True
    if changed:
        db_session.commit()


def _select_manifest(manifests: list[Any], source_url: str) -> Any:
    """Prefer an approved manifest for the requested source URL."""
    matching_source = [
        manifest
        for manifest in manifests
        if source_url and str((manifest.source or {}).get("url") or "") == source_url
    ]
    candidates = matching_source or manifests
    return next(
        (
            manifest
            for manifest in candidates
            if str((manifest.integrity or {}).get("archive_sha256") or "")
        ),
        candidates[0],
    )


def _blocked(actions: list[dict]) -> dict[str, Any]:
    """Return a consistent fail-closed gate response."""
    return {
        "allowed": False,
        "status": "blocked",
        "actions": actions,
        "provision_plan_ids": [],
    }


def _select_target_node(resource_plans: list[dict]) -> str | None:
    """Choose one node that can host all resources, preferring existing caches."""
    if not resource_plans:
        return None
    candidates = {node["node_id"] for node in resource_plans[0]["nodes"]}
    for plan in resource_plans[1:]:
        candidates.intersection_update(node["node_id"] for node in plan["nodes"])
    if not candidates:
        return None

    scored: list[tuple[int, int, str]] = []
    for node_id in candidates:
        node_entries = [
            next(node for node in plan["nodes"] if node["node_id"] == node_id)
            for plan in resource_plans
        ]
        cache_hits = sum(1 for node in node_entries if node["cache_hit"])
        scored.append((-cache_hits, -min(node["free_capacity"] for node in node_entries), node_id))
    return min(scored)[2]


def _find_compatible_nodes(
    db_session,
    cache_key: str,
    *,
    required_gpu: bool,
    requested_node_id: str | None,
) -> list[dict]:
    """Find ONLINE nodes that can host the given resource.

    Returns a list with node id, capability metadata, and cache hit status.
    """
    from app.models.compute_node import ComputeNode

    nodes = (
        db_session.query(ComputeNode)
        .filter(ComputeNode.status == "ONLINE")
        .all()
    )

    result: list[dict] = []
    for node in nodes:
        if requested_node_id and node.id != requested_node_id:
            continue
        caps = node.capabilities or {}
        cache = node.resource_cache or []

        # Check cache hit
        cache_hit = any(
            isinstance(entry, dict)
            and entry.get("cache_key") == cache_key
            and entry.get("status") == "READY"
            for entry in cache
        )

        free_capacity = max(0, node.max_concurrent_jobs - node.running_jobs)
        if free_capacity <= 0:
            continue
        if required_gpu and not caps.get("has_gpu", False):
            continue

        result.append({
            "node_id": node.id,
            "display_name": node.display_name,
            "node_kind": caps.get("node_kind", "local"),
            "has_gpu": caps.get("has_gpu", False),
            "cache_hit": cache_hit,
            "free_capacity": free_capacity,
        })

    # Prefer cache-hit nodes first
    result.sort(key=lambda n: (not n["cache_hit"], -n["free_capacity"]))
    return result


def _create_provision_plan(db_session, action: dict) -> str | None:
    """Create a ProvisionPlan.  Returns ID of new or existing active plan."""
    try:
        from app.models.provision_plan import ProvisionPlan

        plan = ProvisionPlan(
            node_id=action["node_id"],
            manifest_id=action["manifest_id"],
            cache_key=action["cache_key"],
            state="PENDING",
            requested_by=action.get("requested_by", "experiment_preparation"),
            expires_at=datetime.utcnow() + timedelta(hours=24),
        )
        db_session.add(plan)
        db_session.commit()
        plan_id = plan.id
        logger.info("ProvisionPlan %s created for node=%s", plan_id, action["node_id"])
        return plan_id
    except Exception as error:
        db_session.rollback()
        if not _is_integrity_error(error):
            logger.error("Failed to create ProvisionPlan: %s", error)
            return None
        try:
            from app.models.provision_plan import ProvisionPlan
            existing = (
                db_session.query(ProvisionPlan)
                .filter(
                    ProvisionPlan.node_id == action["node_id"],
                    ProvisionPlan.cache_key == action["cache_key"],
                    ProvisionPlan.state.in_(["PENDING", "DOWNLOADING", "VERIFYING"]),
                )
                .order_by(ProvisionPlan.created_at.asc())
                .first()
            )
            return existing.id if existing else None
        except Exception:
            return None


def _is_integrity_error(error: Exception) -> bool:
    """Avoid importing SQLAlchemy when this adapter is unit-tested standalone."""
    try:
        from sqlalchemy.exc import IntegrityError
    except ImportError:
        return False
    return isinstance(error, IntegrityError)
