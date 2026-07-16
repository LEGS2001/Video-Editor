from video_editor.models import (
    Clip,
    ExportProfile,
    HardwareBackend,
    MediaAsset,
    Project,
    RenderRoute,
    Transform,
    VideoCodec,
)
from video_editor.render_planner import build_render_plan, plan_smart_segments


def project_with_clip(asset: MediaAsset, clip: Clip | None = None) -> Project:
    project = Project()
    project.media.append(asset)
    project.timeline.tracks[0].clips.append(clip or Clip(asset_id=asset.id, source_out_ms=asset.duration_ms))
    return project


def compatible_asset(asset_id: str = "asset") -> MediaAsset:
    return MediaAsset(
        id=asset_id,
        path=f"/tmp/{asset_id}.mp4",
        container="mov",
        video_codec="h264",
        audio_codec="aac",
        width=1920,
        height=1080,
        fps=30.0,
        duration_ms=4000,
        has_audio=True,
        has_video=True,
    )


def test_single_compatible_clip_uses_stream_copy():
    asset = compatible_asset()
    plan = build_render_plan(project_with_clip(asset), ExportProfile(output_path="/tmp/out.mp4"), HardwareBackend.NVENC)

    assert plan.route == RenderRoute.STREAM_COPY
    assert plan.backend == HardwareBackend.CPU
    assert "Single compatible clip" in plan.reason


def test_transform_forces_reencode_and_cpu_for_canvas_transform():
    asset = compatible_asset()
    clip = Clip(asset_id=asset.id, source_out_ms=asset.duration_ms, transform=Transform(x=20))

    plan = build_render_plan(project_with_clip(asset, clip), ExportProfile(output_path="/tmp/out.mp4"), HardwareBackend.NVENC)

    assert plan.route == RenderRoute.REENCODE
    assert plan.backend == HardwareBackend.CPU
    assert "requires reencode" in plan.reason


def test_stream_copy_never_ignores_selected_codec():
    profile = ExportProfile(output_path="/tmp/out.mp4", codec=VideoCodec.AV1)

    plan = build_render_plan(project_with_clip(compatible_asset()), profile, HardwareBackend.CPU)

    assert plan.route == RenderRoute.REENCODE
    assert "codec" in plan.reason.lower()


def test_trimmed_single_clip_is_reencoded():
    asset = compatible_asset()
    clip = Clip(asset_id=asset.id, source_in_ms=500, source_out_ms=asset.duration_ms)

    plan = build_render_plan(project_with_clip(asset, clip), ExportProfile(output_path="/tmp/out.mp4"), HardwareBackend.CPU)

    assert plan.route == RenderRoute.REENCODE


def test_output_container_and_audio_must_support_copy():
    asset = compatible_asset()
    asset.audio_codec = "mp3"

    plan = build_render_plan(project_with_clip(asset), ExportProfile(output_path="/tmp/out.mp4"), HardwareBackend.CPU)

    assert plan.route == RenderRoute.REENCODE
    assert "container or audio" in plan.reason


def test_stream_copy_requires_the_same_container_family():
    asset = compatible_asset()
    asset.container = "matroska,webm"

    plan = build_render_plan(project_with_clip(asset), ExportProfile(output_path="/tmp/out.mp4"), HardwareBackend.CPU)

    assert plan.route == RenderRoute.REENCODE


def test_disabling_stream_copy_also_disables_smart_copy_segments():
    first, second = compatible_asset("first"), compatible_asset("second")
    clips = [
        Clip(asset_id=first.id, source_out_ms=first.duration_ms),
        Clip(asset_id=second.id, source_out_ms=second.duration_ms, transform=Transform(x=20)),
    ]
    project = Project(media=[first, second])
    profile = ExportProfile(output_path="/tmp/out.mp4", allow_stream_copy=False)

    assert plan_smart_segments(project, profile, clips, [first, second]) is None


def test_non_default_speed_always_forces_reencode():
    asset = compatible_asset()
    clip = Clip(asset_id=asset.id, source_out_ms=asset.duration_ms, speed=2.0)

    plan = build_render_plan(project_with_clip(asset, clip), ExportProfile(output_path="/tmp/out.mp4"), HardwareBackend.CPU)

    assert plan.route == RenderRoute.REENCODE
    assert "speed" in plan.reason.lower()
