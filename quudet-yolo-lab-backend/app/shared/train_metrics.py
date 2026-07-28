from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def parse_results_csv(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open('r', encoding='utf-8', errors='replace', newline='') as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return None
    series: dict[str, list[float]] = {}
    x: list[int] = []
    for index, row in enumerate(rows):
        try:
            x.append(int(float(row.get('epoch') or row.get('Epoch') or row.get('epochs'))))
        except (TypeError, ValueError):
            x.append(index)
        for key, value in row.items():
            if not key or key.strip().lower() == 'epoch' or value is None or not value.strip():
                continue
            try:
                series.setdefault(key.strip(), []).append(float(value))
            except ValueError:
                series.setdefault(key.strip(), []).append(float('nan'))
    return {'x': x, 'series': series}


def epoch_progress(x: list[Any], total_epochs: int, *, status: str = '') -> dict[str, int]:
    total = max(0, int(total_epochs or 0))
    if not x:
        return {'epochs_done': 0, 'epochs_total': total, 'progress_percent': 100 if status.upper() == 'SUCCESS' and total else 0}
    try:
        done = max(0, max(int(float(value)) for value in x) + 1)
    except (TypeError, ValueError):
        done = len(x)
    percent = 100 if status.upper() == 'SUCCESS' else (min(99, min(100, max(0, int(round(done * 100 / total))))) if total and status.upper() == 'RUNNING' else (min(100, max(0, int(round(done * 100 / total)))) if total else 0))
    return {'epochs_done': done, 'epochs_total': total, 'progress_percent': percent}


def resolve_results_csv_for_train(
    *,
    payload: dict[str, Any],
    work_dir: Path,
    job_dir: Path,
    started_at=None,
    log_text=None,
    job_type=None,
    allow_fallback: bool = True,
) -> Path | None:
    direct = Path(str(payload.get('_metrics_source_path') or ''))
    if str(direct) and direct.is_file():
        return direct
    local = job_dir / 'results.csv'
    if local.is_file():
        return local
    project = str(payload.get('project') or '').strip().replace('\\', '/')
    name = str(payload.get('name') or '').strip()
    if project and name:
        candidate = work_dir / project / name / 'results.csv'
        if candidate.is_file():
            return candidate
    if not allow_fallback:
        return None
    candidates = list(work_dir.glob('runs/**/results.csv'))
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None
