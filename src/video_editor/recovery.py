from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .models import Project
from .project_io import project_from_dict, project_to_dict, validate_project


@dataclass(frozen=True)
class RecoveryRecord:
    path: Path
    project_path: str
    created_at: float
    project_id: str


class RecoveryService:
    def __init__(self, directory: str | Path, keep: int = 3) -> None:
        self.directory = Path(directory)
        self.keep = max(1, keep)

    def snapshot(self, project: Project, project_path: str = "") -> RecoveryRecord:
        validate_project(project)
        self.directory.mkdir(parents=True, exist_ok=True)
        created_at = time.time()
        target = self.directory / f"{project.id}-{time.time_ns()}.json"
        payload = project_to_dict(project)
        payload["_recovery"] = {
            "projectPath": project_path,
            "createdAt": created_at,
            "projectId": project.id,
        }
        temporary = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.directory, prefix=f".{target.name}.", suffix=".tmp", delete=False
            ) as handle:
                temporary = handle.name
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            raise
        self._prune(project.id)
        return RecoveryRecord(target, project_path, created_at, project.id)

    def records(self, project_id: str = "") -> list[RecoveryRecord]:
        if not self.directory.is_dir():
            return []
        records: list[RecoveryRecord] = []
        for path in self.directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                metadata = payload.get("_recovery") or {}
                record = RecoveryRecord(
                    path=path,
                    project_path=str(metadata.get("projectPath", "")),
                    created_at=float(metadata.get("createdAt", path.stat().st_mtime)),
                    project_id=str(metadata.get("projectId", "")),
                )
                if record.project_id and (not project_id or record.project_id == project_id):
                    records.append(record)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def load(self, record: RecoveryRecord) -> Project:
        return project_from_dict(json.loads(record.path.read_text(encoding="utf-8")))

    def clear(self, project_id: str) -> None:
        for record in self.records(project_id):
            record.path.unlink(missing_ok=True)

    def latest_newer_than_saved(self) -> RecoveryRecord | None:
        for record in self.records():
            if not record.project_path:
                return record
            try:
                if record.created_at > Path(record.project_path).stat().st_mtime:
                    return record
            except OSError:
                return record
        return None

    def _prune(self, project_id: str) -> None:
        for record in self.records(project_id)[self.keep:]:
            record.path.unlink(missing_ok=True)
