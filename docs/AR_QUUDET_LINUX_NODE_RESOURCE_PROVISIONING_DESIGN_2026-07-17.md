# AR → QuuDet Linux Node Resource Provisioning Design

## 1. Objective

Enable a Linux execution node to obtain and cache datasets, weights, and small reproducibility bundles before an experiment is submitted. The design prevents the Windows master from relaying large datasets for every job and prevents training code from performing implicit downloads.

This document is a design only. No Linux node or acceptance run is required at this stage.

## 2. Boundaries

| Component | Owns | Must not do |
|---|---|---|
| AR resource discovery | Find official sources, licenses, versions, download level, integrity metadata, and manual fallback instructions | Execute arbitrary commands on a node |
| Experiment Preparation | Convert an AR resource decision into a readiness decision and a node provisioning request | Treat a resource as ready without a validated node receipt |
| QuuDet master | Persist manifests, select compatible nodes, issue provisioning work, and gate experiment submission | Search the web or substitute a download source |
| Linux QuuDet agent | Download only the approved manifest, resume, verify, extract, cache, and report receipt | Discover resources or choose an alternative URL |
| Human | Approve an unverified source or manually place a restricted resource | Bypass hash/structure validation |

The Linux node downloads a resource, but AR remains the sole authority deciding **what** to download and **from where**.

## 3. Current-State Gap

Current AR preparation already performs discovery, registry lookup, download-level selection, download, and validation. The current QuuDet node path can also fetch a `job-dataset` ZIP and a `job-bundle` ZIP after a job is claimed.

That ZIP-per-job pattern is retained only as a small compatibility path. It is unsuitable for VOC-scale datasets because it repeatedly packages, transfers, extracts, and consumes disk for data that should be cached once per node.

## 4. Target Flow

```text
ExperimentSpec
  -> AR discovery / registry / validator
  -> ResourceManifest(s)
  -> Preparation selects a candidate Linux node
  -> node inventory cache hit?
       yes -> node-ready receipt
       no  -> independent provisioning request
  -> Linux agent downloads from AR-approved source
  -> checksum + archive + dataset-structure validation
  -> node-ready receipt
  -> Preparation API returns allowed
  -> create experiment group and PENDING_ASSIGN jobs
  -> Linux agent claims training job using local cache paths
```

Provisioning occurs **before** `POST /api/v1/experiments` creates an experiment group or a training job. The API returns a non-terminal preparation state while provisioning is running; AR polls that state and retries submission only after every required resource is node-ready.

## 5. Canonical Contracts

### 5.1 `ResourceManifest`

AR emits one immutable manifest per concrete resource version.

```json
{
  "resource_id": "dataset:voc:2012-yolo",
  "resource_type": "dataset",
  "version": "2012-yolo-v1",
  "display_name": "PASCAL VOC 2012",
  "source": {
    "kind": "official_direct",
    "url": "https://approved.example/VOCtrainval_11-May-2012.tar",
    "official_page": "https://official.example/voc",
    "license": "source-declared"
  },
  "integrity": {
    "archive_sha256": "required-for-automatic-download",
    "expected_size_bytes": 0,
    "manifest_schema": "resource-manifest/v1"
  },
  "delivery": {
    "archive_format": "tar",
    "extract_subdir": "VOC",
    "cache_key": "sha256:<manifest-content-hash>",
    "target_relative_path": "datasets/VOC",
    "allow_resume": true
  },
  "validation": {
    "kind": "yolo_dataset",
    "yaml_relative_path": "VOC.yaml",
    "required_paths": ["images/train", "images/val", "labels/train", "labels/val"]
  },
  "manual_fallback": {
    "allowed": true,
    "instructions": "Place the verified archive under the node staging path and request validation."
  },
  "provenance": {
    "discovery_id": "ar-discovery-id",
    "selected_by": "AR resource discovery",
    "created_at": "ISO-8601"
  }
}
```

`archive_sha256` is mandatory for unattended downloads. If the official source has no trustworthy checksum, AR creates a `needs_human_integrity_approval` manifest rather than silently trusting a first download.

### 5.2 `NodeResourceInventory`

Each node sends a compact cache inventory during registration and heartbeat.

```json
{
  "node_id": "linux-3090-01",
  "cache_root": "/srv/quudet/cache",
  "free_bytes": 0,
  "resources": [
    {
      "resource_id": "dataset:voc:2012-yolo",
      "cache_key": "sha256:<manifest-content-hash>",
      "status": "READY",
      "verified_at": "ISO-8601",
      "local_uri": "cache://datasets/VOC"
    }
  ]
}
```

The master stores only `local_uri`, cache key, validation status, and capacity metadata. It never stores an arbitrary Linux absolute path in an ExperimentSpec.

### 5.3 `NodeProvisionPlan`

Preparation creates a provisioning plan independently of a training job.

```json
{
  "provision_id": "uuid",
  "node_id": "linux-3090-01",
  "manifest": { "resource_id": "dataset:voc:2012-yolo" },
  "state": "PENDING",
  "requested_by": "preparation-request-id",
  "expires_at": "ISO-8601"
}
```

Allowed states are `PENDING`, `DOWNLOADING`, `VERIFYING`, `READY`, `FAILED`, and `MANUAL_REQUIRED`.

### 5.4 `ProvisionReceipt`

The Linux agent reports a receipt only after successful validation.

```json
{
  "provision_id": "uuid",
  "state": "READY",
  "cache_key": "sha256:<manifest-content-hash>",
  "archive_sha256": "verified digest",
  "bytes_downloaded": 0,
  "local_uri": "cache://datasets/VOC",
  "validator": { "kind": "yolo_dataset", "result": "passed" },
  "completed_at": "ISO-8601"
}
```

## 6. API and Agent Additions

The following routes are additive; the current `job-dataset` ZIP route remains unchanged during migration.

| Route | Caller | Purpose |
|---|---|---|
| `POST /api/v1/resources/manifests` | Preparation service | Persist an AR-approved immutable manifest |
| `POST /api/v1/nodes/{node_id}/resources/inventory` | Linux agent | Upsert node cache inventory |
| `POST /api/v1/provisioning` | Preparation service | Request resource preparation on a selected node |
| `POST /api/v1/provisioning/claim-next` | Linux agent | Atomically claim one pending provision plan |
| `POST /api/v1/provisioning/{id}/events` | Linux agent | Report progress, logs, failure, or receipt |
| `GET /api/v1/provisioning/{id}` | AR / Preparation | Poll state before experiment submission |

`app.agent.runner` gains a provisioning loop that runs before `claim-next` for training jobs. It uses a dedicated `ResourceProvisioner`; it must not reuse the training subprocess or call `yolo train` to obtain data.

## 7. Linux Cache Layout and Atomicity

```text
/srv/quudet/cache/
  staging/<provision-id>/download.part
  archives/<sha256>.tar
  content/<cache-key>/
  datasets/VOC -> ../content/<cache-key>/VOC
  weights/yolo11s.pt -> ../content/<cache-key>/yolo11s.pt
  receipts/<cache-key>.json
```

Rules:

1. Download to `staging`, using HTTP range requests when the source supports resume.
2. Verify the complete archive checksum before extracting.
3. Extract into a temporary content directory; reject path traversal, symlinks outside the cache, and oversized archives.
4. Run the manifest validator against the extracted directory.
5. Atomically rename the verified directory into `content/<cache-key>` and write its receipt.
6. Only a `READY` receipt may create/update a human-readable dataset or weight alias.
7. A failed or interrupted staging directory is resumable or removable without touching a valid cache entry.

## 8. Download Levels and Human Fallback

| AR level | Node behavior | Submission result |
|---|---|---|
| A: official direct URL + trusted hash | Download, resume, verify, extract, validate automatically | `READY` on success |
| B: source URL but integrity requires approval | Download only after explicit human approval of the manifest/hash | `MANUAL_REQUIRED` until approved |
| C: restricted/manual source | Do not download | `MANUAL_REQUIRED` with exact node destination and validation command |

For human delivery, the operator places the archive or extracted directory under the node staging path. The agent then validates it against the same manifest and emits a normal `READY` receipt. Humans never need to edit a database row or fabricate a cache hit.

## 9. Scheduling and Preparation Gate

Preparation must evaluate both resource correctness and target-node readiness:

| Condition | Result |
|---|---|
| AR cannot resolve a usable source | `blocked`; no provision and no experiment group |
| Manifest requires human approval | `manual_required`; no group/job |
| Compatible node has a valid cache receipt | `ready`; normal experiment submission is allowed |
| Compatible node lacks resource but automatic provision is possible | `provisioning`; no group/job until receipt is `READY` |
| Provision fails | `blocked` or `manual_required`; preserve failure evidence |
| No compatible node has disk/GPU capability | `blocked`; AR may select another node or revise spec |

The scheduler may prefer a cache hit over a nominally faster node. A resource miss can be provisioned proactively, but it cannot be hidden behind a `PENDING_ASSIGN` training job.

## 10. Delivery Sources

Priority order is fixed by AR policy:

1. Official direct source with checksum.
2. Approved public mirror with checksum and explicit provenance.
3. Private MinIO/S3 resource mirror controlled by the project.
4. Human manual placement.

For the first Linux nodes, direct official downloads are sufficient. When several nodes repeatedly need VOC or larger resources, introduce MinIO/S3 so the master does not repackage data and each node downloads a stable object by digest.

## 11. Security and Reliability Rules

- The agent accepts only HTTPS URLs and manifest schemas signed/authorized by the master API.
- No shell command, arbitrary extraction command, credential, or host path may appear in a manifest.
- Node tokens authorize provisioning events but do not grant web-search authority.
- Cache limits and minimum free space are checked before download.
- Download progress and receipt timestamps update the provisioning heartbeat; stalled plans fail visibly rather than waiting forever.
- Resource eviction is explicit and reference-aware. A resource used by queued/running work cannot be evicted.
- Training uses a cache alias resolved from a validated receipt, never a raw external URL.

## 12. Migration Plan

### Phase 0 — Design Freeze

Approve this contract and choose a node cache root such as `/srv/quudet/cache`.

### Phase 1 — AR Manifest Output

Extend `experiment_preparation` to serialize discovery/registry decisions as `ResourceManifest`; retain the current local downloader for Windows-only execution.

### Phase 2 — Node Cache and Provisioning API

Add manifest, inventory, provisioning-plan, receipt persistence, and the Linux agent `ResourceProvisioner`. Implement direct official-source downloads and manual receipt validation first.

### Phase 3 — Preparation Gate Integration

Make Preparation select a target node and return `provisioning`/`manual_required` before experiment creation. Add cache-hit scheduling preference.

### Phase 4 — Storage and Operations

Add MinIO/S3 mirror support, cache quotas, eviction policy, and dashboard visibility for resource state per node.

### Phase 5 — Future Acceptance

Run cache miss, resume, checksum failure, manual placement, cache hit, and two-node scheduling acceptance tests only after a Linux node is available.

## 13. Explicit Non-Goals

- Do not move AnySearch or resource discovery onto Linux nodes.
- Do not stream datasets through PostgreSQL, Redis, chat sessions, or Celery task payloads.
- Do not let Ultralytics silently download a missing dataset during training.
- Do not claim a multi-node DDP implementation; nodes execute independent scheduled jobs.
