import json

import pytest

from video_editor.models import Clip, MediaAsset, Project, TextOverlay
from video_editor.project_io import (
    load_project,
    project_from_dict,
    project_to_dict,
    save_project,
    validate_project,
)
from video_editor.project_service import ProjectService


def test_project_json_round_trip_preserves_core_fields():
    project = Project(name="Demo")
    asset = MediaAsset(
        id="asset-1",
        path="/tmp/a.mp4",
        container="mov,mp4,m4a,3gp,3g2,mj2",
        video_codec="h264",
        audio_codec="aac",
        width=1920,
        height=1080,
        fps=30.0,
        duration_ms=5000,
        has_audio=True,
        has_video=True,
    )
    project.media.append(asset)
    project.timeline.tracks[0].clips.append(Clip(id="clip-1", asset_id=asset.id, source_out_ms=5000))
    project.export_defaults.fps = 59.94

    loaded = project_from_dict(project_to_dict(project))

    assert loaded.name == "Demo"
    assert loaded.media[0].video_codec == "h264"
    assert loaded.timeline.tracks[0].clips[0].duration_ms == 5000
    assert loaded.export_defaults.fps == 59.94


def test_service_adds_clips_contiguously():
    service = ProjectService()
    service.add_media(MediaAsset(id="a", path="/tmp/a.mp4", duration_ms=1000, has_video=True))
    service.add_media(MediaAsset(id="b", path="/tmp/b.mp4", duration_ms=2000, has_video=True))

    first = service.add_asset_to_timeline("a")
    second = service.add_asset_to_timeline("b")

    assert first.timeline_start_ms == 0
    assert second.timeline_start_ms == 1000
    assert service.timeline_duration_ms() == 3000


def test_first_video_clip_uses_its_fps_and_codec_as_export_defaults():
    service = ProjectService()
    asset = MediaAsset(
        id="source",
        path="/tmp/source.mov",
        video_codec="hevc",
        fps=59.94,
        duration_ms=1000,
        has_video=True,
    )
    service.add_media(asset)

    service.add_asset_to_timeline(asset.id)

    assert service.project.export_defaults.fps == 59.94
    assert service.project.export_defaults.codec.value == "h265"


def test_service_finds_clip_at_timeline_and_next_clip():
    service = ProjectService()
    service.add_media(MediaAsset(id="a", path="/tmp/a.mp4", duration_ms=1000, has_video=True))
    service.add_media(MediaAsset(id="b", path="/tmp/b.mp4", duration_ms=2000, has_video=True))
    first = service.add_asset_to_timeline("a")
    second = service.add_asset_to_timeline("b")

    assert service.clip_at_timeline(500).id == first.id
    assert service.clip_at_timeline(1500).id == second.id
    assert service.next_clip_after(first.id).id == second.id
    assert service.next_clip_after(second.id) is None


def test_service_batch_and_visible_clip_indexes():
    service = ProjectService()
    service.add_media_batch(
        [
            MediaAsset(id="a", path="/tmp/a.mp4", duration_ms=1000, has_video=True),
            MediaAsset(id="b", path="/tmp/b.mp4", duration_ms=2000, has_video=True),
        ]
    )
    first = service.add_asset_to_timeline("a")
    second = service.add_asset_to_timeline("b")

    assert service.asset_by_id("b").duration_ms == 2000
    assert service.clip_by_id(first.id) is first
    assert service.visible_video_clips(900, 1100) == [first, second]


def test_project_validation_rejects_missing_asset_reference():
    payload = project_to_dict(Project())
    payload["project"]["timeline"]["tracks"][0]["clips"] = [
        {"id": "clip", "assetId": "missing", "sourceInMs": "0", "sourceOutMs": "1000", "timelineStartMs": "0"}
    ]

    with pytest.raises(ValueError, match="missing media"):
        project_from_dict(payload)


def test_save_project_is_atomic_and_round_trips(tmp_path):
    target = tmp_path / "project.json"
    project = Project(name="Atomic")

    save_project(project, target)

    assert load_project(target).name == "Atomic"
    assert json.loads(target.read_text(encoding="utf-8"))["version"] == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_save_failure_preserves_existing_file(monkeypatch, tmp_path):
    target = tmp_path / "project.json"
    target.write_text("original", encoding="utf-8")

    def fail_replace(*_args):
        raise OSError("disk failure")

    monkeypatch.setattr("video_editor.project_io.os.replace", fail_replace)

    with pytest.raises(OSError, match="disk failure"):
        save_project(Project(), target)
    assert target.read_text(encoding="utf-8") == "original"
    assert not list(tmp_path.glob("*.tmp"))


def test_clip_speed_is_optional_in_json_v1_and_changes_timeline_duration():
    asset = MediaAsset(
        id="asset", path="/tmp/a.mp4", duration_ms=4000, width=1920, height=1080, has_video=True
    )
    project = Project(media=[asset])
    project.timeline.tracks[0].clips.append(Clip(id="clip", asset_id=asset.id, source_out_ms=4000, speed=4.0))

    payload = project_to_dict(project)
    loaded = project_from_dict(payload)

    assert payload["version"] == 1
    assert payload["project"]["timeline"]["tracks"][0]["clips"][0]["speed"] == 4.0
    assert loaded.timeline.tracks[0].clips[0].source_duration_ms == 4000
    assert loaded.timeline.tracks[0].clips[0].duration_ms == 1000

    del payload["project"]["timeline"]["tracks"][0]["clips"][0]["speed"]
    legacy = project_from_dict(payload).timeline.tracks[0].clips[0]
    assert legacy.speed == 1.0
    assert legacy.duration_ms == 4000


def test_legacy_project_without_export_fps_uses_common_clip_fps():
    asset = MediaAsset(
        id="asset",
        path="/tmp/a.mp4",
        width=1920,
        height=1080,
        video_codec="hevc",
        fps=59.94,
        duration_ms=4000,
        has_video=True,
    )
    project = Project(media=[asset])
    project.timeline.tracks[0].clips.append(Clip(id="clip", asset_id=asset.id, source_out_ms=4000))
    payload = project_to_dict(project)
    del payload["project"]["exportDefaults"]["fps"]

    loaded = project_from_dict(payload)

    assert loaded.export_defaults.fps == 59.94
    assert loaded.export_defaults.codec.value == "h265"


def test_speed_is_undoable_redoable_and_a_new_edit_invalidates_redo():
    asset = MediaAsset(id="asset", path="/tmp/a.mp4", duration_ms=4000, has_video=True)
    project = Project(media=[asset])
    project.timeline.tracks[0].clips.append(Clip(id="clip", asset_id=asset.id, source_out_ms=4000))
    service = ProjectService(project)

    assert service.set_clip_speed("clip", 2.0)
    assert service.clip_by_id("clip").duration_ms == 2000
    assert service.undo()
    assert service.clip_by_id("clip").speed == 1.0
    assert service.redo()
    assert service.clip_by_id("clip").speed == 2.0
    assert service.undo()
    assert service.set_clip_speed("clip", 4.0)
    assert not service.redo()


def test_undo_history_is_limited_to_thirty_states():
    asset = MediaAsset(id="asset", path="/tmp/a.mp4", duration_ms=4000, has_video=True)
    project = Project(media=[asset])
    project.timeline.tracks[0].clips.append(Clip(id="clip", asset_id=asset.id, source_out_ms=4000))
    service = ProjectService(project)

    for index in range(35):
        service.set_clip_speed("clip", 1.0 if index % 2 else 2.0)

    assert len(service.undo_stack) == 30


def test_split_uses_source_time_for_a_fast_clip():
    asset = MediaAsset(id="asset", path="/tmp/a.mp4", duration_ms=4000, has_video=True)
    project = Project(media=[asset])
    project.timeline.tracks[0].clips.append(
        Clip(id="clip", asset_id=asset.id, source_out_ms=4000, speed=4.0)
    )
    service = ProjectService(project)

    assert service.split_clip("clip", 500)
    left, right = service.video_track.clips
    assert left.source_out_ms == 2000
    assert right.source_in_ms == 2000
    assert left.duration_ms == right.duration_ms == 500


def test_effective_duration_is_rounded_to_timeline_frames():
    asset = MediaAsset(id="asset", path="/tmp/a.mp4", duration_ms=1000, has_video=True)
    project = Project(media=[asset])
    project.timeline.fps = 30.0
    project.timeline.tracks[0].clips.append(
        Clip(id="clip", asset_id=asset.id, source_out_ms=1000, speed=4.0)
    )

    clip = ProjectService(project).clip_by_id("clip")

    assert clip.duration_ms == 267  # eight frames at 30 fps, not an arbitrary 250 ms

    clip.speed = 100.0
    assert clip.duration_ms == 33  # never shorter than one timeline frame


def test_relink_batch_is_atomic_and_preserves_asset_ids():
    first = MediaAsset(
        id="first", path="missing-a.mp4", duration_ms=5000, width=1920, height=1080,
        has_video=True, has_audio=True,
    )
    second = MediaAsset(
        id="second", path="missing-b.mp4", duration_ms=3000, width=1280, height=720,
        has_video=True,
    )
    service = ProjectService(Project(media=[first, second]))
    valid = MediaAsset(
        path="found-a.mp4", duration_ms=5050, width=1920, height=1080,
        has_video=True, has_audio=True,
    )
    invalid = MediaAsset(path="wrong-b.mp4", duration_ms=3000, width=640, height=360, has_video=True)

    with pytest.raises(ValueError, match="dimensions"):
        service.relink_assets({first.id: valid, second.id: invalid})

    assert service.asset_by_id(first.id).path == "missing-a.mp4"
    assert service.asset_by_id(second.id).path == "missing-b.mp4"

    replacement = MediaAsset(path="found-b.mp4", duration_ms=3800, width=1280, height=720, has_video=True)
    service.relink_assets({first.id: valid, second.id: replacement})
    assert service.asset_by_id(first.id).path == "found-a.mp4"
    assert service.asset_by_id(first.id).id == first.id
    assert service.asset_by_id(second.id).id == second.id


def test_text_overlays_round_trip_and_old_projects_load_without_them():
    project = Project(name="Captions")
    project.timeline.texts.append(
        TextOverlay(
            id="text-1", text="Hello: world", start_ms=500, end_ms=2500,
            font="Impact", size_px=72, color="#ff0000", outline_color="#101010",
            outline_px=4, x_px=120, y_px=880,
        )
    )

    payload = project_to_dict(project)
    loaded = project_from_dict(payload)

    assert [text.text for text in loaded.timeline.texts] == ["Hello: world"]
    overlay = loaded.timeline.texts[0]
    assert (overlay.start_ms, overlay.end_ms) == (500, 2500)
    assert (overlay.font, overlay.size_px, overlay.color) == ("Impact", 72, "#ff0000")
    assert (overlay.outline_color, overlay.outline_px) == ("#101010", 4)
    assert (overlay.x_px, overlay.y_px) == (120, 880)

    # Projects saved before captions existed have no "texts" key at all.
    del payload["project"]["timeline"]["texts"]
    assert project_from_dict(payload).timeline.texts == []


def test_invalid_text_overlay_is_rejected():
    project = Project()
    project.timeline.texts.append(TextOverlay(id="t", start_ms=1000, end_ms=1000))
    with pytest.raises(ValueError, match="timing"):
        validate_project(project)

    project.timeline.texts[0].end_ms = 2000
    project.timeline.texts[0].color = "red"
    with pytest.raises(ValueError, match="colour"):
        validate_project(project)


def test_service_text_edits_are_undoable():
    service = ProjectService()
    overlay = service.add_text(1000, 4000)

    assert service.set_text_range(overlay.id, 2000, 5000)
    assert (overlay.start_ms, overlay.end_ms) == (2000, 5000)
    assert not service.set_text_range(overlay.id, 5000, 5000)

    assert service.update_text(overlay.id, text="Hi", size_px=60)
    assert not service.update_text(overlay.id, text="Hi", size_px=60)

    service.undo()
    assert service.texts[0].text == "Caption"
    service.undo()
    assert (service.texts[0].start_ms, service.texts[0].end_ms) == (1000, 4000)

    assert service.remove_text(service.texts[0].id)
    assert service.texts == []
