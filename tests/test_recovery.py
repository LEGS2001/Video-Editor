import os

import pytest

from video_editor.models import Project
from video_editor.recovery import RecoveryService


def test_recovery_retains_three_atomic_snapshots_and_loads_latest(tmp_path):
    service = RecoveryService(tmp_path / "recovery")
    project = Project(name="v0")

    for version in range(4):
        project.name = f"v{version}"
        service.snapshot(project)

    records = service.records(project.id)
    assert len(records) == 3
    assert service.load(records[0]).name == "v3"
    assert not list((tmp_path / "recovery").glob("*.tmp"))


def test_recovery_is_offered_only_when_newer_and_can_be_cleared(tmp_path):
    project_file = tmp_path / "project.json"
    project_file.write_text("saved", encoding="utf-8")
    os.utime(project_file, (1, 1))
    service = RecoveryService(tmp_path / "recovery")
    project = Project(name="Recovered")

    record = service.snapshot(project, str(project_file))

    assert service.latest_newer_than_saved() == record
    service.clear(project.id)
    assert service.records(project.id) == []


def test_failed_atomic_recovery_keeps_previous_snapshot(monkeypatch, tmp_path):
    service = RecoveryService(tmp_path / "recovery")
    project = Project(name="Previous")
    previous = service.snapshot(project)
    project.name = "Unsaved edit"

    monkeypatch.setattr("video_editor.recovery.os.replace", lambda *_args: (_ for _ in ()).throw(OSError("disk")))

    with pytest.raises(OSError, match="disk"):
        service.snapshot(project)
    assert service.load(previous).name == "Previous"
    assert not list((tmp_path / "recovery").glob("*.tmp"))
