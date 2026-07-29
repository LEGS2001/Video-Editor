from video_editor.models import (
    Clip,
    ExportProfile,
    HardwareBackend,
    MediaAsset,
    Project,
    RenderRoute,
    TextOverlay,
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
        fps=60.0,
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


def test_stream_copy_can_remux_between_compatible_containers():
    asset = compatible_asset()
    asset.container = "matroska,webm"

    plan = build_render_plan(project_with_clip(asset), ExportProfile(output_path="/tmp/out.mp4"), HardwareBackend.CPU)

    assert plan.route == RenderRoute.STREAM_COPY


def test_output_fps_change_forces_reencode():
    asset = compatible_asset()
    profile = ExportProfile(output_path="/tmp/out.mp4", fps=30.0)

    plan = build_render_plan(project_with_clip(asset), profile, HardwareBackend.CPU)

    assert plan.route == RenderRoute.REENCODE
    assert "fps" in plan.reason.lower()


def test_plain_split_is_coalesced_and_stream_copied():
    asset = compatible_asset()
    project = Project(media=[asset])
    project.timeline.tracks[0].clips = [
        Clip(asset_id=asset.id, source_out_ms=2000),
        Clip(asset_id=asset.id, source_in_ms=2000, source_out_ms=4000, timeline_start_ms=2000),
    ]

    plan = build_render_plan(project, ExportProfile(output_path="/tmp/out.mp4"), HardwareBackend.CPU)

    assert plan.route == RenderRoute.STREAM_COPY
    assert len(plan.clips) == 1


def test_smart_render_only_encodes_changed_clip_on_silent_timeline():
    first, second = compatible_asset("first"), compatible_asset("second")
    for asset in (first, second):
        asset.audio_codec = ""
        asset.has_audio = False
        asset.pixel_format = "yuv420p"
    clips = [
        Clip(asset_id=first.id, source_out_ms=first.duration_ms),
        Clip(
            asset_id=second.id,
            source_out_ms=second.duration_ms,
            timeline_start_ms=first.duration_ms,
            transform=Transform(x=20),
        ),
    ]
    project = Project(media=[first, second])
    project.timeline.tracks[0].clips = clips
    profile = ExportProfile(output_path="/tmp/out.mp4")
    plan = build_render_plan(project, profile, HardwareBackend.CPU)

    segments = plan_smart_segments(project, profile, plan.clips, plan.assets)

    assert segments is not None
    assert [segment.encode for segment in segments] == [False, True]


def test_smart_render_supports_hevc_segments():
    first, second = compatible_asset("first"), compatible_asset("second")
    for asset in (first, second):
        asset.video_codec = "hevc"
        asset.pixel_format = "yuv420p"
    clips = [
        Clip(asset_id=first.id, source_out_ms=first.duration_ms),
        Clip(
            asset_id=second.id,
            source_out_ms=second.duration_ms,
            timeline_start_ms=first.duration_ms,
            speed=2.0,
        ),
    ]
    project = Project(media=[first, second])
    project.timeline.tracks[0].clips = clips
    profile = ExportProfile(output_path="/tmp/out.mp4", codec=VideoCodec.H265)
    plan = build_render_plan(project, profile, HardwareBackend.CPU)

    segments = plan_smart_segments(project, profile, plan.clips, plan.assets)

    assert segments is not None
    assert [segment.encode for segment in segments] == [False, True]


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


def project_with_caption(asset: MediaAsset, **overlay_fields) -> Project:
    project = project_with_clip(asset)
    project.timeline.texts.append(TextOverlay(**{"text": "Hello", "end_ms": 2000, **overlay_fields}))
    return project


def test_caption_forces_reencode_and_reaches_the_plan():
    asset = compatible_asset()
    project = project_with_caption(asset)

    plan = build_render_plan(project, ExportProfile(output_path="/tmp/out.mp4"), HardwareBackend.CPU)

    assert plan.route == RenderRoute.REENCODE
    assert "text overlay" in plan.reason.lower()
    assert [text.text for text in plan.texts] == ["Hello"]


def test_blank_or_zero_length_captions_do_not_block_stream_copy():
    asset = compatible_asset()
    blank = project_with_caption(asset, text="   ")
    empty_range = project_with_caption(asset, start_ms=1000, end_ms=1000)

    for project in (blank, empty_range):
        plan = build_render_plan(project, ExportProfile(output_path="/tmp/out.mp4"), HardwareBackend.CPU)
        assert plan.route == RenderRoute.STREAM_COPY
        assert plan.texts == []


def test_captions_disable_smart_render_segments():
    first, second = compatible_asset("first"), compatible_asset("second")
    for asset in (first, second):
        asset.audio_codec = ""
        asset.has_audio = False
        asset.pixel_format = "yuv420p"
    clips = [
        Clip(asset_id=first.id, source_out_ms=first.duration_ms),
        Clip(
            asset_id=second.id,
            source_out_ms=second.duration_ms,
            timeline_start_ms=first.duration_ms,
            transform=Transform(x=20),
        ),
    ]
    project = Project(media=[first, second])
    project.timeline.tracks[0].clips = clips
    profile = ExportProfile(output_path="/tmp/out.mp4")

    assert plan_smart_segments(project, profile, clips, [first, second]) is not None

    project.timeline.texts.append(TextOverlay(text="Hello", end_ms=2000))
    assert plan_smart_segments(project, profile, clips, [first, second]) is None
