from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QGraphicsRectItem, QMessageBox, QScrollArea
import pytest

from video_editor import ui
from video_editor.models import Clip, MediaAsset, Project, Transform, VideoCodec
from video_editor.recovery import RecoveryService
from video_editor.ui import MainWindow, _format_ms


class _PlayerStub(QObject):
    positionChanged = Signal(int)
    mediaStatusChanged = Signal(object)

    class PlaybackState:
        StoppedState = 0
        PlayingState = 1
        PausedState = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._position = 0
        self._rate = 1.0
        self._state = self.PlaybackState.StoppedState

    def setAudioOutput(self, _output):
        pass

    def setVideoOutput(self, _output):
        pass

    def stop(self):
        self._state = self.PlaybackState.StoppedState

    def setSource(self, source):
        self._source = source

    def source(self):
        return self._source

    def setPosition(self, position):
        self._position = position

    def position(self):
        return self._position

    def setPlaybackRate(self, rate):
        self._rate = rate

    def playbackRate(self):
        return self._rate

    def playbackState(self):
        return self._state

    def play(self):
        self._state = self.PlaybackState.PlayingState

    def pause(self):
        self._state = self.PlaybackState.PausedState


class _AudioStub(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.volume = 1.0

    def setVolume(self, value):
        self.volume = value


class _VideoItemStub(QGraphicsRectItem):
    def setAspectRatioMode(self, _mode):
        pass

    def setSize(self, size):
        self.setRect(0, 0, size.width(), size.height())


class _CloseEventStub:
    accepted = False
    ignored = False

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "QMediaPlayer", _PlayerStub)
    monkeypatch.setattr(ui, "QAudioOutput", _AudioStub)
    monkeypatch.setattr(ui, "QGraphicsVideoItem", _VideoItemStub)
    monkeypatch.setattr(ui, "RecoveryService", lambda *_args, **_kwargs: RecoveryService(tmp_path / "recovery"))
    editor = MainWindow()
    editor.show()
    yield editor
    editor._set_dirty(False)
    QApplication.instance().removeEventFilter(editor)
    editor.hide()
    editor.deleteLater()


def _project_with_two_clips() -> Project:
    first = MediaAsset(path="first.mp4", width=1920, height=1080, duration_ms=1000, has_video=True)
    second = MediaAsset(path="second.mp4", width=1280, height=720, duration_ms=2000, has_video=True)
    project = Project(media=[first, second])
    project.timeline.tracks[0].clips = [
        Clip(asset_id=first.id, source_out_ms=1000),
        Clip(
            asset_id=second.id,
            source_out_ms=2000,
            timeline_start_ms=1000,
            transform=Transform(scale_x=0.5, scale_y=0.5),
        ),
    ]
    return project


def test_selection_is_synchronized_across_editor(window, qtbot):
    project = _project_with_two_clips()
    clip = project.timeline.tracks[0].clips[1]
    window.service.set_project(project)
    window.refresh()

    window._set_selection(clip_id=clip.id)
    qtbot.wait(10)

    assert window.selected_asset_id == clip.asset_id
    assert window.selected_clip_id == clip.id
    assert window.timeline_table.currentRow() == 1
    assert window.timeline_canvas.selected_clip_id == clip.id
    assert window.scale.value() == 50
    assert window.inspector_card.isEnabled()


def test_space_keeps_native_button_behavior(window, qtbot):
    window.advanced_toggle.setFocus()
    qtbot.keyClick(window.advanced_toggle, Qt.Key.Key_Space)

    assert window.advanced_toggle.isChecked()
    assert not window.command_box.isHidden()


def test_export_defaults_persist_in_project(window):
    window.codec.setCurrentIndex(window.codec.findData(VideoCodec.H265))
    window.bitrate.setValue(18000)
    window.allow_stream_copy.setChecked(False)

    defaults = window.service.project.export_defaults
    assert defaults.codec == VideoCodec.H265
    assert defaults.bitrate_kbps == 18000
    assert not defaults.allow_stream_copy
    assert window.dirty


def test_dirty_new_project_can_discard(window, monkeypatch):
    original_id = window.service.project.id
    window._set_dirty()
    window._write_recovery()
    assert window.recovery_service.records(original_id)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard,
    )

    window.new_project()

    assert window.service.project.id != original_id
    assert not window.dirty
    assert window.current_project_path is None
    assert not window.recovery_service.records(original_id)


def test_dirty_close_can_be_cancelled(window, monkeypatch):
    event = _CloseEventStub()
    window._set_dirty()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )

    window.closeEvent(event)

    assert event.ignored
    assert not event.accepted
    assert window.dirty


def test_close_stops_export_and_removes_temporary(window, monkeypatch, tmp_path):
    class Worker:
        stopped = False

        def isRunning(self):
            return True

        def force_stop(self, timeout_ms):
            assert timeout_ms == 2000
            self.stopped = True

    event = _CloseEventStub()
    worker = Worker()
    temporary = tmp_path / ".partial.mp4"
    temporary.write_bytes(b"partial")
    window.export_worker = worker
    window.export_temp_path = str(temporary)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    window.closeEvent(event)

    assert worker.stopped
    assert not temporary.exists()
    assert event.accepted


def test_export_panel_scrolls_and_hides_advanced_details(window):
    scroll_areas = window.findChildren(QScrollArea)

    assert scroll_areas
    assert scroll_areas[-1].widgetResizable()
    assert window.command_box.isHidden()
    assert window.advanced_toggle.accessibleName()


def test_export_sidebar_fits_viewport_and_dropdowns_have_icons(window, qtbot):
    sidebar = window.findChildren(QScrollArea)[-1]
    dropdown_icon = ui._ASSETS_DIR / "spin-down.svg"
    window.resize(1600, 900)
    qtbot.wait(10)

    assert sidebar.widget().width() == sidebar.viewport().width()
    assert "QComboBox::down-arrow" in window.styleSheet()
    assert dropdown_icon.as_posix() in window.styleSheet()
    assert dropdown_icon.is_file()


def test_ui_remains_responsive_during_fifty_file_import(window, qtbot, monkeypatch):
    def fake_probe(path, **_kwargs):
        time.sleep(0.005)
        return MediaAsset(path=path, duration_ms=1000, has_video=False)

    monkeypatch.setattr(ui, "probe_media", fake_probe)
    monkeypatch.setattr(ui, "create_thumbnail", lambda *_args, **_kwargs: None)
    responsive = []
    QTimer.singleShot(10, lambda: responsive.append(True))

    window.import_paths([f"clip-{index}.wav" for index in range(50)])

    qtbot.waitUntil(lambda: bool(responsive), timeout=1000)
    qtbot.waitUntil(lambda: len(window.service.project.media) == 50, timeout=3000)
    assert window.import_worker is not None and not window.import_worker.isRunning()


def test_successful_export_is_validated_before_atomic_replace(window, monkeypatch, tmp_path):
    destination = tmp_path / "output.mp4"
    temporary = tmp_path / ".output.partial.mp4"
    destination.write_bytes(b"previous")
    temporary.write_bytes(b"new")
    window.export_output_path = str(destination)
    window.export_temp_path = str(temporary)
    window._export_expected_fps = 30
    window._export_expected_codec = VideoCodec.H264
    monkeypatch.setattr(
        "video_editor.ui.probe_media",
        lambda *_args, **_kwargs: MediaAsset(has_video=True, fps=30, video_codec="h264"),
    )

    window.on_export_finished(0)

    assert destination.read_bytes() == b"new"
    assert not temporary.exists()
    assert not window.export_temp_path


def test_failed_export_validation_preserves_existing_output(window, monkeypatch, tmp_path):
    destination = tmp_path / "output.mp4"
    temporary = tmp_path / ".output.partial.mp4"
    destination.write_bytes(b"previous")
    temporary.write_bytes(b"bad")
    window.export_output_path = str(destination)
    window.export_temp_path = str(temporary)
    window._export_expected_fps = 30
    window._export_expected_codec = VideoCodec.H264
    monkeypatch.setattr(
        "video_editor.ui.probe_media",
        lambda *_args, **_kwargs: MediaAsset(has_video=True, fps=25, video_codec="h264"),
    )

    window.on_export_finished(0)

    assert destination.read_bytes() == b"previous"
    assert not temporary.exists()


def test_subsecond_time_format_is_available():
    assert _format_ms(1234, show_ms=True) == "00:00:01.234"


def test_speed_control_updates_duration_and_supports_undo_redo(window):
    project = _project_with_two_clips()
    clip = project.timeline.tracks[0].clips[0]
    window.service.set_project(project)
    window.refresh()
    window._set_selection(clip_id=clip.id)
    window._saved_fingerprint = window._project_fingerprint()
    window._set_dirty(False)

    assert window.speed.minimum() == 0.25
    assert window.speed.maximum() == 100.0
    assert window.speed.singleStep() == 0.25
    assert [window.speed_presets.itemData(index) for index in range(1, window.speed_presets.count())] == [
        0.25, 0.5, 1.0, 2.0, 4.0, 10.0, 25.0, 50.0, 100.0,
    ]

    window.speed.setValue(10.0)
    window.apply_clip_speed()

    assert window.service.clip_by_id(clip.id).speed == 10.0
    assert window.service.clip_by_id(clip.id).duration_ms == 100
    assert "00:00:00.100" in window.speed_duration.text()
    assert window.dirty
    window.undo()
    assert window.service.clip_by_id(clip.id).speed == 1.0
    assert not window.dirty
    window.redo()
    assert window.service.clip_by_id(clip.id).speed == 10.0
    assert window.dirty


def test_frame_navigation_and_snapping_setting(window, qtbot):
    window.service.set_project(_project_with_two_clips())
    window.service.project.timeline.fps = 30.0
    window.refresh()

    window.step_frame(1)
    assert window.playhead_ms == 33
    window.step_frame(-1)
    assert window.playhead_ms == 0

    window.setFocus()
    qtbot.keyClick(window, Qt.Key.Key_Period)
    assert window.playhead_ms == 33
    qtbot.keyClick(window, Qt.Key.Key_End)
    assert window.playhead_ms == window.service.timeline_duration_ms()
    qtbot.keyClick(window, Qt.Key.Key_Home)
    assert window.playhead_ms == 0

    window.set_snapping(False)
    assert not window.snap_action.isChecked()
    assert not window.timeline_canvas.snap_enabled
    assert not window.settings.value("timeline/snapping", True, type=bool)


def test_speed_above_four_uses_sampled_silent_preview(window):
    project = _project_with_two_clips()
    clip = project.timeline.tracks[0].clips[0]
    clip.speed = 10.0
    project.timeline.tracks[0].clips[1].timeline_start_ms = clip.duration_ms
    window.service.set_project(project)
    window.refresh()

    window.play_timeline_from(0)

    assert window._sampled_timer.isActive()
    assert window.player.playbackState() == _PlayerStub.PlaybackState.PausedState
    assert window.player.playbackRate() == 1.0
    assert window.audio_output.volume == 0.0


def test_dirty_project_writes_and_clears_recovery(window):
    window._set_dirty()
    window._write_recovery()

    project_id = window.service.project.id
    assert len(window.recovery_service.records(project_id)) == 1
    window._set_dirty(False)
    window.recovery_service.clear(project_id)
    assert window.recovery_service.records(project_id) == []


def test_speed_seek_converts_between_timeline_and_source(window):
    project = _project_with_two_clips()
    clip = project.timeline.tracks[0].clips[0]
    clip.speed = 10.0
    window.service.set_project(project)
    window.refresh()

    window.seek_timeline(50)
    assert window.player.position() == 500

    window.playing_clip_id = clip.id
    window.on_player_position_changed(800)
    assert window.playhead_ms == 80


def test_snapping_threshold_stays_eight_visual_pixels_at_different_zooms(window):
    window.service.set_project(_project_with_two_clips())
    canvas = window.timeline_canvas
    canvas.resize(1000, canvas.height())
    canvas.set_playhead(1500)
    canvas.set_snapping(True)

    canvas.zoom_factor = 1.0
    assert canvas._snap_time(1020, window.service.video_track.clips[0].id) == 1000

    canvas.zoom_factor = 10.0
    assert canvas._snap_time(1020, window.service.video_track.clips[0].id) == 1020
    assert canvas._snap_time(1002, window.service.video_track.clips[0].id) == 1000


def test_timeline_ruler_scrubs_continuously_without_dragging_clip(window, qtbot):
    window.service.set_project(_project_with_two_clips())
    window.refresh()
    canvas = window.timeline_canvas
    canvas.resize(900, canvas.height())
    first_x = int(canvas._ms_to_x(500))
    second_x = int(canvas._ms_to_x(1500))
    ruler_y = canvas.RULER_BOTTOM - 4

    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(first_x, ruler_y))
    assert window.playhead_ms == pytest.approx(500, abs=5)
    assert canvas.mode == "scrub_playhead"

    qtbot.mouseMove(canvas, QPoint(second_x, ruler_y))
    assert window.playhead_ms == pytest.approx(1500, abs=5)
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(second_x, ruler_y))
    assert canvas.mode == "idle"

    clip_x = int(canvas._ms_to_x(500))
    qtbot.mousePress(
        canvas, Qt.MouseButton.LeftButton,
        pos=QPoint(clip_x, canvas.LANE_TOP + canvas.LANE_HEIGHT // 2),
    )
    assert canvas.mode == "drag_clip"
    qtbot.mouseRelease(
        canvas, Qt.MouseButton.LeftButton,
        pos=QPoint(clip_x, canvas.LANE_TOP + canvas.LANE_HEIGHT // 2),
    )


def test_table_single_click_selects_and_double_click_seeks_clip_start(window, qtbot):
    project = _project_with_two_clips()
    second = project.timeline.tracks[0].clips[1]
    window.service.set_project(project)
    window.refresh()
    item = window.timeline_table.item(1, 1)
    position = window.timeline_table.visualItemRect(item).center()

    qtbot.mouseClick(window.timeline_table.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert window.selected_clip_id == second.id
    assert window.playhead_ms == 0

    qtbot.mouseDClick(window.timeline_table.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert window.selected_clip_id == second.id
    assert window.playhead_ms == second.timeline_start_ms


def test_topbar_resets_timeline_and_selected_clip_properties_with_undo(window):
    project = _project_with_two_clips()
    clip = project.timeline.tracks[0].clips[1]
    original_range = (clip.source_in_ms, clip.source_out_ms)
    clip.opacity, clip.volume, clip.speed = 0.4, 1.5, 2.0
    project.timeline.width, project.timeline.height = 1080, 1920
    project.timeline.fps, project.timeline.master_volume = 60.0, 2.0
    window.service.set_project(project)
    window.refresh()
    window._set_selection(clip_id=clip.id)

    assert window.reset_timeline_action in window.findChildren(ui.QToolBar)[0].actions()
    assert window.reset_clip_action in window.findChildren(ui.QToolBar)[0].actions()
    window.reset_clip_action.trigger()
    reset_clip = window.service.clip_by_id(clip.id)
    assert reset_clip.transform == Transform()
    assert reset_clip.opacity == reset_clip.volume == reset_clip.speed == 1.0
    assert (reset_clip.source_in_ms, reset_clip.source_out_ms) == original_range

    window.undo()
    assert window.service.clip_by_id(clip.id).speed == 2.0
    window.reset_timeline_action.trigger()
    timeline = window.service.project.timeline
    assert (timeline.width, timeline.height, timeline.fps, timeline.master_volume) == (1920, 1080, 30.0, 1.0)

    window.undo()
    timeline = window.service.project.timeline
    assert (timeline.width, timeline.height, timeline.fps, timeline.master_volume) == (1080, 1920, 60.0, 2.0)


def test_recovery_timers_save_clears_and_restore_loads_snapshot(window, monkeypatch, tmp_path):
    window.service.project.name = "Recovered edit"
    window._set_dirty()
    assert window._recovery_debounce.interval() == 30000
    assert window._recovery_deadline.interval() == 60000
    assert window._recovery_debounce.isActive() and window._recovery_deadline.isActive()
    window._write_recovery()
    project_id = window.service.project.id

    window.service.set_project(Project(name="Different project"))
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
    )
    window._offer_startup_recovery()
    assert window.service.project.name == "Recovered edit"
    assert window.dirty

    window.current_project_path = tmp_path / "saved.json"
    assert window.save_project()
    assert not window.dirty
    assert not window.recovery_service.records(project_id)


def test_undo_redo_covers_trim_split_transform_and_volume(window):
    project = _project_with_two_clips()
    clip_id = project.timeline.tracks[0].clips[0].id
    window.service.set_project(project)
    window.refresh()
    window._set_selection(clip_id=clip_id)

    window.on_clip_trimmed(clip_id, 100, None)
    assert window.service.clip_by_id(clip_id).source_in_ms == 100
    window.undo()
    assert window.service.clip_by_id(clip_id).source_in_ms == 0
    window.redo()
    assert window.service.clip_by_id(clip_id).source_in_ms == 100

    window.scale.setValue(75)
    window._finish_continuous_edit()
    assert window.service.clip_by_id(clip_id).transform.scale_x == 0.75
    window.undo()
    assert window.service.clip_by_id(clip_id).transform.scale_x == 1.0
    window.redo()
    assert window.service.clip_by_id(clip_id).transform.scale_x == 0.75

    window.clip_volume_slider.setValue(150)
    window._finish_continuous_edit()
    assert window.service.clip_by_id(clip_id).volume == 1.5
    window.undo()
    assert window.service.clip_by_id(clip_id).volume == 1.0
    window.redo()
    assert window.service.clip_by_id(clip_id).volume == 1.5

    window._set_selection(clip_id=clip_id)
    window.seek_timeline(500)
    window.split_selected_clip()
    assert len(window.service.video_track.clips) == 3
    window.undo()
    assert len(window.service.video_track.clips) == 2
    window.redo()
    assert len(window.service.video_track.clips) == 3
