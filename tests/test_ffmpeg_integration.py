from __future__ import annotations

import shutil
import subprocess
from array import array
from copy import deepcopy

import pytest

from video_editor.ffmpeg import build_ffmpeg_command, build_smart_render_commands
from video_editor.hardware import encoder_for
from video_editor.media import probe_media
from video_editor.models import Clip, ExportProfile, HardwareBackend, Project, RenderRoute, TextOverlay, VideoCodec
from video_editor.render_planner import build_render_plan, plan_smart_segments


pytestmark = pytest.mark.ffmpeg


def _require_tools() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required")


def _run(arguments: list[str], timeout: float = 60) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return completed


def _source_project(tmp_path, *, fps: int = 25, duration: float = 0.4):
    _require_tools()
    source = tmp_path / "source.mp4"
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate={fps}",
        "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ])
    asset = probe_media(source)
    clip = Clip(asset_id=asset.id, source_out_ms=asset.duration_ms)
    project = Project(media=[asset])
    project.timeline.width, project.timeline.height, project.timeline.fps = asset.width, asset.height, asset.fps
    project.timeline.tracks[0].clips.append(clip)
    return project, asset


def test_untouched_60_fps_source_is_stream_copied(tmp_path):
    project, asset = _source_project(tmp_path, fps=60)
    output = tmp_path / "untouched-60fps.mp4"
    profile = ExportProfile(
        output_path=str(output),
        width=asset.width,
        height=asset.height,
        fps=asset.fps,
        bitrate_kbps=500,
    )

    plan = build_render_plan(project, profile, HardwareBackend.CPU)
    command = build_ffmpeg_command(plan, profile)

    assert plan.route == RenderRoute.STREAM_COPY
    assert "copy" in command.arguments
    try:
        _run(command.argv)
    finally:
        command.cleanup()
    assert probe_media(output).fps == pytest.approx(60, abs=0.01)


@pytest.mark.parametrize(
    ("codec", "expected"),
    [(VideoCodec.H265, "hevc"), (VideoCodec.AV1, "av1")],
)
def test_selected_codec_is_present_in_real_output(tmp_path, codec, expected):
    project, asset = _source_project(tmp_path)
    encoder = encoder_for(codec, HardwareBackend.CPU)
    encoders = _run(["ffmpeg", "-hide_banner", "-encoders"]).stdout
    if encoder not in encoders:
        pytest.skip(f"{encoder} is unavailable")
    output = tmp_path / f"output-{codec.value}.mp4"
    profile = ExportProfile(
        output_path=str(output), codec=codec, width=asset.width, height=asset.height, fps=asset.fps, bitrate_kbps=500
    )
    plan = build_render_plan(project, profile, HardwareBackend.CPU)
    command = build_ffmpeg_command(plan, profile)
    try:
        _run(command.argv)
    finally:
        command.cleanup()

    assert probe_media(output).video_codec == expected


def test_requested_fps_is_present_in_real_output(tmp_path):
    project, asset = _source_project(tmp_path, fps=25)
    output = tmp_path / "output-30fps.mp4"
    profile = ExportProfile(
        output_path=str(output), codec=VideoCodec.H264, width=asset.width, height=asset.height, fps=30, bitrate_kbps=500
    )
    plan = build_render_plan(project, profile, HardwareBackend.CPU)
    command = build_ffmpeg_command(plan, profile)
    try:
        _run(command.argv)
    finally:
        command.cleanup()

    assert probe_media(output).fps == pytest.approx(30, abs=0.01)


def test_audio_only_clip_exports_black_video_with_audio(tmp_path):
    _require_tools()
    source = tmp_path / "tone.wav"
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4", str(source),
    ])
    asset = probe_media(source)
    clip = Clip(asset_id=asset.id, source_out_ms=asset.duration_ms)
    project = Project(media=[asset])
    project.timeline.width, project.timeline.height, project.timeline.fps = 160, 90, 25
    project.timeline.tracks[0].clips.append(clip)
    output = tmp_path / "audio-segment.mp4"
    profile = ExportProfile(output_path=str(output), width=160, height=90, fps=25, bitrate_kbps=500)
    plan = build_render_plan(project, profile, HardwareBackend.CPU)
    command = build_ffmpeg_command(plan, profile)
    try:
        _run(command.argv)
    finally:
        command.cleanup()

    rendered = probe_media(output)
    assert rendered.has_video and rendered.has_audio


@pytest.mark.parametrize("speed", [0.25, 2.0, 4.0, 10.0, 100.0])
def test_real_output_duration_matches_clip_speed_within_one_frame(tmp_path, speed):
    project, asset = _source_project(tmp_path, duration=1.0)
    clip = project.timeline.tracks[0].clips[0]
    clip.speed = speed
    output = tmp_path / f"speed-{speed:g}.mp4"
    profile = ExportProfile(
        output_path=str(output), width=asset.width, height=asset.height, fps=25, bitrate_kbps=500
    )
    plan = build_render_plan(project, profile, HardwareBackend.CPU)
    command = build_ffmpeg_command(plan, profile)
    try:
        _run(command.argv)
    finally:
        command.cleanup()

    expected = clip.duration_ms / 1000.0
    actual = probe_media(output).duration_ms / 1000.0
    assert actual == pytest.approx(expected, abs=(1 / profile.fps) + 0.01)


def test_audio_is_preserved_below_four_x_and_silent_from_four_x(tmp_path):
    _require_tools()
    source = tmp_path / "source-with-tone.mp4"
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    ])
    asset = probe_media(source)

    peaks = {}
    for speed in (2.0, 4.0):
        clip = Clip(asset_id=asset.id, source_out_ms=asset.duration_ms, speed=speed)
        project = Project(media=[asset])
        project.timeline.width, project.timeline.height, project.timeline.fps = 160, 90, 25
        project.timeline.tracks[0].clips.append(clip)
        output = tmp_path / f"tone-{speed:g}.mp4"
        profile = ExportProfile(output_path=str(output), width=160, height=90, fps=25, bitrate_kbps=500)
        command = build_ffmpeg_command(build_render_plan(project, profile, HardwareBackend.CPU), profile)
        try:
            _run(command.argv)
        finally:
            command.cleanup()
        decoded = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output), "-map", "0:a:0", "-f", "s16le", "-"],
            capture_output=True, timeout=60, check=True,
        ).stdout
        samples = array("h")
        samples.frombytes(decoded)
        peaks[speed] = max(map(abs, samples), default=0)

    assert peaks[2.0] > 100
    assert peaks[4.0] == 0


def test_smart_render_copies_untouched_segment_and_reencodes_speed_segment(tmp_path):
    _require_tools()
    source = tmp_path / "smart-source.mp4"
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    ])
    first_asset = probe_media(source)
    second_asset = deepcopy(first_asset)
    second_asset.id = "second"
    clips = [
        Clip(asset_id=first_asset.id, source_out_ms=first_asset.duration_ms),
        Clip(
            asset_id=second_asset.id, source_out_ms=second_asset.duration_ms,
            timeline_start_ms=first_asset.duration_ms, speed=10.0,
        ),
    ]
    project = Project(media=[first_asset, second_asset])
    project.timeline.width, project.timeline.height, project.timeline.fps = 160, 90, 25
    project.timeline.tracks[0].clips = clips
    output = tmp_path / "smart-speed.mp4"
    profile = ExportProfile(output_path=str(output), width=160, height=90, fps=25, bitrate_kbps=500)
    plan = build_render_plan(project, profile, HardwareBackend.CPU)
    segments = plan_smart_segments(project, profile, plan.clips, plan.assets)

    assert segments is not None
    assert [segment.encode for segment in segments] == [False, True]
    jobs = build_smart_render_commands(segments, profile)
    try:
        for command, _weight in jobs:
            _run(command.argv)
    finally:
        jobs[-1][0].cleanup()

    rendered = probe_media(output)
    expected_ms = sum(clip.duration_ms for clip in plan.clips)
    assert rendered.duration_ms == pytest.approx(expected_ms, abs=40)
    assert rendered.has_audio


def test_smart_render_copies_untouched_silent_segment(tmp_path):
    project, first_asset = _source_project(tmp_path, fps=25, duration=1.0)
    second_asset = deepcopy(first_asset)
    second_asset.id = "second-silent"
    clips = [
        Clip(asset_id=first_asset.id, source_out_ms=first_asset.duration_ms),
        Clip(
            asset_id=second_asset.id,
            source_out_ms=second_asset.duration_ms,
            timeline_start_ms=first_asset.duration_ms,
            speed=2.0,
        ),
    ]
    project.media.append(second_asset)
    project.timeline.tracks[0].clips = clips
    output = tmp_path / "smart-silent.mp4"
    profile = ExportProfile(
        output_path=str(output),
        width=first_asset.width,
        height=first_asset.height,
        fps=first_asset.fps,
        bitrate_kbps=500,
    )
    plan = build_render_plan(project, profile, HardwareBackend.CPU)
    segments = plan_smart_segments(project, profile, plan.clips, plan.assets)

    assert segments is not None
    assert [segment.encode for segment in segments] == [False, True]
    jobs = build_smart_render_commands(segments, profile)
    try:
        for command, _weight in jobs:
            _run(command.argv)
    finally:
        jobs[-1][0].cleanup()

    rendered = probe_media(output)
    assert rendered.duration_ms == pytest.approx(sum(clip.duration_ms for clip in clips), abs=40)
    assert rendered.has_video and not rendered.has_audio


def test_hevc_smart_render_copies_untouched_segment(tmp_path):
    _require_tools()
    encoder = encoder_for(VideoCodec.H265, HardwareBackend.CPU)
    if encoder not in _run(["ffmpeg", "-hide_banner", "-encoders"]).stdout:
        pytest.skip(f"{encoder} is unavailable")
    source = tmp_path / "hevc-source.mp4"
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=0.4",
        "-c:v", encoder, "-pix_fmt", "yuv420p", str(source),
    ])
    first_asset = probe_media(source)
    second_asset = deepcopy(first_asset)
    second_asset.id = "second-hevc"
    clips = [
        Clip(asset_id=first_asset.id, source_out_ms=first_asset.duration_ms),
        Clip(
            asset_id=second_asset.id,
            source_out_ms=second_asset.duration_ms,
            timeline_start_ms=first_asset.duration_ms,
            speed=2.0,
        ),
    ]
    project = Project(media=[first_asset, second_asset])
    project.timeline.width, project.timeline.height, project.timeline.fps = 160, 90, 25
    project.timeline.tracks[0].clips = clips
    output = tmp_path / "smart-hevc.mp4"
    profile = ExportProfile(
        output_path=str(output),
        codec=VideoCodec.H265,
        width=160,
        height=90,
        fps=25,
        bitrate_kbps=500,
    )
    plan = build_render_plan(project, profile, HardwareBackend.CPU)
    segments = plan_smart_segments(project, profile, plan.clips, plan.assets)

    assert segments is not None
    assert [segment.encode for segment in segments] == [False, True]
    jobs = build_smart_render_commands(segments, profile)
    try:
        for command, _weight in jobs:
            _run(command.argv)
    finally:
        jobs[-1][0].cleanup()

    rendered = probe_media(output)
    assert rendered.video_codec == "hevc"
    assert rendered.duration_ms == pytest.approx(sum(clip.duration_ms for clip in clips), abs=80)


def _mean_luma(path, at_seconds: float) -> float:
    """Average brightness of one frame, via ffmpeg's signalstats."""
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-y", "-ss", f"{at_seconds:.3f}",
         "-i", str(path), "-frames:v", "1", "-vf", "signalstats,metadata=print",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    for line in completed.stderr.splitlines():
        if "lavfi.signalstats.YAVG" in line:
            return float(line.rsplit("=", 1)[1])
    raise AssertionError(completed.stderr)


def test_caption_is_burned_in_only_between_its_start_and_end(tmp_path):
    """White text on a black canvas: the frames inside the caption's window are
    measurably brighter than the ones outside it."""
    _require_tools()
    source = tmp_path / "black.mp4"
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:size=320x240:rate=25",
        "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ])
    asset = probe_media(source)
    project = Project(media=[asset])
    project.timeline.width, project.timeline.height, project.timeline.fps = 320, 240, 25.0
    project.timeline.tracks[0].clips.append(Clip(asset_id=asset.id, source_out_ms=asset.duration_ms))
    project.timeline.texts.append(
        TextOverlay(
            text="HELLO: it's 100% on", start_ms=500, end_ms=1500,
            size_px=40, color="#ffffff", outline_px=2, x_px=10, y_px=100,
        )
    )

    output = tmp_path / "captioned.mp4"
    profile = ExportProfile(output_path=str(output), width=320, height=240, fps=25.0, bitrate_kbps=800)
    plan = build_render_plan(project, profile, HardwareBackend.CPU)
    command = build_ffmpeg_command(plan, profile)

    assert plan.route == RenderRoute.REENCODE
    try:
        _run(command.argv)
    finally:
        command.cleanup()

    before, during, after = (_mean_luma(output, t) for t in (0.2, 1.0, 1.8))
    assert during > before + 1.0, (before, during, after)
    assert during > after + 1.0, (before, during, after)
