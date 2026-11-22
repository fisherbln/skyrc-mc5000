"""Runs and charge curves rebuilt from Home Assistant's recorded history.

Home Assistant records every slot sensor. A *run* is a stretch where a slot's
status is charging, discharging or resting; it ends when the status settles on
anything else (an `unavailable` gap, e.g. an HA restart, doesn't end it).
Finished runs are copied to a local archive so they outlive HA's recorder
retention (10 days by default).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .charger import UNAVAILABLE, ChargerEntities, error_label

ACTIVE = {"charging", "discharging", "resting"}
RUN_KEYS = ["status", "error", "capacity", "elapsed", "chemistry", "operation"]
CURVE_KEYS = ["voltage", "current", "capacity", "temperature"]

Points = list[tuple[float, str]]  # (unix time, state)


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "mc5000-bench"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def parse_history(raw: dict[str, list[dict[str, Any]]]) -> dict[str, Points]:
    """HA's compressed history (``{"s", "lu"}`` items) -> sorted (time, state) points."""
    out: dict[str, Points] = {}
    for eid, items in (raw or {}).items():
        pts: Points = []
        for item in items:
            ts = item.get("lu", item.get("lc"))
            if ts is None:
                continue
            pts.append((float(ts), item.get("s")))
        pts.sort(key=lambda p: p[0])
        out[eid] = pts
    return out


def _value_at(points: Points, ts: float) -> str | None:
    """Last known (available) state at or before ts."""
    value = None
    for t, s in points:
        if t > ts:
            break
        if s not in UNAVAILABLE:
            value = s
    return value


def numeric(points: Points, start: float, end: float | None = None) -> list[tuple[float, float]]:
    out = []
    for t, s in points:
        if t < start - 1 or (end is not None and t > end + 1):
            continue
        try:
            out.append((t, float(s)))
        except (TypeError, ValueError):
            continue
    return out


def _last_nonzero(points: Points, start: float, end: float) -> float | None:
    best = None
    for _t, v in numeric(points, start, end):
        if v:
            best = v
    return best


@dataclass
class Run:
    slot: int
    start: float
    end: float | None  # None while still running
    chemistry: str | None = None
    operation: str | None = None
    outcome: str = "Running"
    capacity: float | None = None
    elapsed_s: float | None = None
    curve: dict[str, list[tuple[float, float]]] = field(default_factory=dict)

    @property
    def run_id(self) -> str:
        return f"{self.slot}-{int(self.start)}"

    @property
    def duration_s(self) -> float | None:
        if self.elapsed_s:
            return self.elapsed_s
        return (self.end - self.start) if self.end else None


def find_runs(slot: int, charger: ChargerEntities, history: dict[str, Points],
              now: float, elapsed_unit_s: float = 60) -> list[Run]:
    """Split a slot's history into runs.

    History carries no units; elapsed_unit_s is seconds per unit of the elapsed
    sensor (the integration reports minutes by default).
    """
    keys = charger.slots.get(slot, {})

    def pts(key: str) -> Points:
        return history.get(keys.get(key, ""), [])

    status = pts("status")
    runs: list[Run] = []
    current: Run | None = None
    for t, s in status:
        if s in UNAVAILABLE:
            continue
        if s in ACTIVE:
            if current is None:
                current = Run(slot, t, None)
        elif current is not None:
            current.end = t
            err = None
            for et, es in pts("error"):
                if current.start <= et <= t + 30 and es not in UNAVAILABLE and es != "none":
                    err = es
            current.outcome = error_label(err) if err else ("Done" if s == "done" else "Stopped")
            runs.append(current)
            current = None
    if current is not None:
        runs.append(current)
    for run in runs:
        end = run.end if run.end is not None else now
        run.chemistry = _value_at(pts("chemistry"), end)
        run.operation = _value_at(pts("operation"), end)
        run.capacity = _last_nonzero(pts("capacity"), run.start, end)
        elapsed = _last_nonzero(pts("elapsed"), run.start, end)
        if elapsed is not None:
            run.elapsed_s = elapsed * elapsed_unit_s
    return runs


def curve_for(run: Run, charger: ChargerEntities, history: dict[str, Points]) -> dict[str, list]:
    keys = charger.slots.get(run.slot, {})
    out = {}
    for key in CURVE_KEYS:
        pts = numeric(history.get(keys.get(key, ""), []), run.start, run.end)
        if run.end is not None:
            # Readings stamped at the end belong to the idle slot afterwards
            # (0 V, counter zeroed); leave them out of the run's curve.
            pts = [p for p in pts if p[0] < run.end - 0.5]
        out[key] = pts
    return out


class Archive:
    """Finished runs saved as one JSON file each."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (data_dir() / "runs")

    def _path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    def has(self, run_id: str) -> bool:
        return self._path(run_id).exists()

    def save(self, run: Run) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(run.run_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(run)))
        tmp.replace(self._path(run.run_id))

    def load(self, run_id: str) -> Run | None:
        try:
            raw = json.loads(self._path(run_id).read_text())
        except (OSError, ValueError):
            return None
        raw["curve"] = {k: [tuple(p) for p in v] for k, v in raw.get("curve", {}).items()}
        return Run(**raw)

    def all(self) -> list[Run]:
        if not self.root.exists():
            return []
        runs = []
        for path in self.root.glob("*.json"):
            run = self.load(path.stem)
            if run:
                run.curve = {}  # summaries only; load() again for the curve
                runs.append(run)
        return runs

    def delete(self, run_id: str) -> None:
        try:
            self._path(run_id).unlink()
        except OSError:
            pass


def merge_runs(from_ha: list[Run], archived: list[Run]) -> list[Run]:
    """Archive and HA history together, newest first, HA winning on duplicates."""
    by_id = {r.run_id: r for r in archived}
    for run in from_ha:
        by_id[run.run_id] = run
    return sorted(by_id.values(), key=lambda r: r.start, reverse=True)
