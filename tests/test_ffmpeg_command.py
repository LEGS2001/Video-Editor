from video_editor.ffmpeg import (
    FfmpegCommand,
    _append_concat_demuxer_input,
    _atempo_filters,
    _concat_file_path,
    _concat_filter,
    _drawtext_filters,
    build_ffmpeg_command,
    build_smart_render_commands,
)
from video_editor.models import Clip, Crop, ExportProfile, HardwareBackend, MediaAsset, RenderPlan, RenderRoute, TextOverlay, Transform, VideoCodec
from video_editor.render_planner import RenderSegment


def _reencode_plan(asset: MediaAsset, clip: Clip, backend: HardwareBackend = HardwareBackend.CPU) -> RenderPlan:
    return RenderPlan(route=RenderRoute.REENCODE, backend=backend, clip=clip, asset=asset, clips=[clip], assets=[asset])


def test_builds_stream_copy_command():
    asset = MediaAsset(id="a", path="/tmp/input.mp4", video_codec="h264", duration_ms=3000)
    clip = Clip(asset_id=asset.id, source_out_ms=3000)
    plan = RenderPlan(route=RenderRoute.STREAM_COPY, backend=HardwareBackend.CPU, clip=clip, asset=asset, clips=[clip], assets=[asset])

    command = build_ffmpeg_command(plan, ExportProfile(output_path="/tmp/out.mp4"))

    assert command.argv[:4] == ["ffmpeg", "-hide_banner", "-y", "-i"]
    assert "-c" in command.arguments
    assert "copy" in command.arguments
    assert command.arguments[-1] == "/tmp/out.mp4"


def test_builds_reencode_scale_command():
    asset = MediaAsset(id="a", path="/tmp/input.mp4", video_codec="h264", width=1280, height=720, duration_ms=3000)
    clip = Clip(asset_id=asset.id, source_out_ms=3000)
    profile = ExportProfile(output_path="/tmp/out.mp4", width=1920, height=1080)
    plan = RenderPlan(route=RenderRoute.REENCODE, backend=HardwareBackend.CPU, clip=clip, asset=asset, clips=[clip], assets=[asset])

    command = build_ffmpeg_command(plan, profile)

    assert "-vf" in command.arguments
    assert "scale=1920:1080" in _vf_of(command)
    assert "libx264" in command.arguments
    assert "yuv420p" in command.arguments
    assert command.arguments[command.arguments.index("-r") + 1] == "60"


def _vf_of(command: FfmpegCommand) -> str:
    return command.arguments[command.arguments.index("-vf") + 1]


def test_scale_applies_to_cropped_size_not_full_frame():
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=1920, height=1080, duration_ms=3000)
    clip = Clip(
        asset_id="a",
        source_out_ms=3000,
        crop=Crop(left=100, right=100, enabled=True),
        transform=Transform(scale_x=2.0, scale_y=2.0),
    )

    command = build_ffmpeg_command(_reencode_plan(asset, clip), ExportProfile(output_path="/tmp/out.mp4"))

    vf = _vf_of(command)
    assert "crop=1720:1080:100:0" in vf
    assert "scale=3440:2160" in vf  # 2x the cropped 1720x1080, not the full frame


def test_crop_without_transform_still_fits_output_canvas():
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=1920, height=1080, duration_ms=3000)
    clip = Clip(asset_id="a", source_out_ms=3000, crop=Crop(left=200, enabled=True))

    command = build_ffmpeg_command(_reencode_plan(asset, clip), ExportProfile(output_path="/tmp/out.mp4"))

    vf = _vf_of(command)
    assert "crop=1720:1080:200:0" in vf
    assert "scale=1920:1080" in vf  # cropped frame is scaled back onto the canvas


def test_negative_position_never_produces_negative_pad_offsets():
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=1920, height=1080, duration_ms=3000)
    clip = Clip(asset_id="a", source_out_ms=3000, transform=Transform(x=-50))

    command = build_ffmpeg_command(_reencode_plan(asset, clip), ExportProfile(output_path="/tmp/out.mp4"))

    vf = _vf_of(command)
    assert "crop=1870:1080:50:0" in vf
    assert "pad=1920:1080:0:0" in vf
    assert "-50" not in vf


def test_upscaled_clip_is_cropped_to_canvas_instead_of_failing_pad():
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=1920, height=1080, duration_ms=3000)
    clip = Clip(asset_id="a", source_out_ms=3000, transform=Transform(scale_x=1.5, scale_y=1.5))

    command = build_ffmpeg_command(_reencode_plan(asset, clip), ExportProfile(output_path="/tmp/out.mp4"))

    vf = _vf_of(command)
    assert "scale=2880:1620" in vf
    assert "crop=1920:1080:0:0" in vf  # pad would reject an oversized input


def test_filter_order_and_defensive_crop():
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=100, height=80, duration_ms=3000)
    clip = Clip(
        asset_id="a",
        source_out_ms=3000,
        crop=Crop(left=-10, right=200, enabled=True),
        transform=Transform(x=4, rotation_deg=15),
        opacity=0.5,
    )

    vf = _vf_of(build_ffmpeg_command(_reencode_plan(asset, clip), ExportProfile(output_path="/tmp/out.mp4")))

    assert "crop=2:80:0:0" in vf
    assert vf.index("crop=") < vf.index("rotate=") < vf.index("pad=") < vf.index("colorchannelmixer=") < vf.index("fps=") < vf.index("setsar=")


def test_fully_off_canvas_clip_renders_black():
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=640, height=360, duration_ms=3000)
    clip = Clip(asset_id="a", source_out_ms=3000, transform=Transform(x=2000))

    vf = _vf_of(build_ffmpeg_command(_reencode_plan(asset, clip), ExportProfile(output_path="/tmp/out.mp4")))

    assert "drawbox=color=black:t=fill" in vf


def test_stream_copy_route_with_wrong_codec_is_defensively_reencoded():
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=1920, height=1080, fps=30, duration_ms=3000)
    clip = Clip(asset_id="a", source_out_ms=3000)
    plan = RenderPlan(route=RenderRoute.STREAM_COPY, backend=HardwareBackend.CPU, clip=clip, asset=asset, clips=[clip], assets=[asset])

    command = build_ffmpeg_command(plan, ExportProfile(output_path="/tmp/out.mp4", codec=VideoCodec.AV1))

    assert "copy" not in command.arguments
    assert "libsvtav1" in command.arguments


def test_faststart_is_only_added_to_mov_family_outputs():
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=1920, height=1080, duration_ms=3000)
    clip = Clip(asset_id="a", source_out_ms=3000)

    command = build_ffmpeg_command(_reencode_plan(asset, clip), ExportProfile(output_path="/tmp/out.mkv"))

    assert "-movflags" not in command.arguments


def test_concat_keeps_audio_by_inserting_silence_for_silent_clips():
    audible = MediaAsset(id="a", path="/tmp/a.mp4", video_codec="h264", width=1920, height=1080, has_video=True, has_audio=True)
    silent = MediaAsset(id="b", path="/tmp/b.mp4", video_codec="h264", width=1920, height=1080, has_video=True)
    clips = [Clip(asset_id="a", source_out_ms=1000), Clip(asset_id="b", source_out_ms=1000)]
    plan = RenderPlan(clips=clips, assets=[audible, silent])

    filter_complex = _concat_filter(plan, ExportProfile(), include_audio=True)

    assert "[0:a]atrim" in filter_complex
    assert "anullsrc=r=48000:cl=stereo" in filter_complex
    assert "concat=n=2:v=1:a=1" in filter_complex


def test_audio_only_concat_segment_gets_black_video():
    asset = MediaAsset(id="a", path="/tmp/a.mp3", audio_codec="mp3", has_audio=True)
    clip = Clip(asset_id="a", source_out_ms=1000)

    filter_complex = _concat_filter(RenderPlan(clips=[clip], assets=[asset]), ExportProfile(), include_audio=True)

    assert "color=c=black:s=1920x1080:r=60:d=1.000" in filter_complex


def test_smart_render_reuses_canonical_transform_chain():
    asset = MediaAsset(id="a", path="/tmp/a.mp4", video_codec="h264", width=1920, height=1080, has_video=True, has_audio=True)
    clip = Clip(asset_id="a", source_out_ms=1000, transform=Transform(rotation_deg=10), opacity=0.5)
    jobs = build_smart_render_commands([RenderSegment(clip, asset, True)], ExportProfile(output_path="/tmp/out.mp4"))

    try:
        vf = _vf_of(jobs[0][0])
        assert vf.index("rotate=") < vf.index("colorchannelmixer=") < vf.index("fps=") < vf.index("setsar=")
    finally:
        jobs[-1][0].cleanup()


def test_nvenc_keeps_frames_on_gpu_only_when_no_software_filters():
    scaled_asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=1280, height=720, duration_ms=3000)
    scaled_clip = Clip(asset_id="a", source_out_ms=3000)
    profile = ExportProfile(output_path="/tmp/out.mp4")

    scaled = build_ffmpeg_command(_reencode_plan(scaled_asset, scaled_clip, HardwareBackend.NVENC), profile)
    assert "-hwaccel" in scaled.arguments
    assert "-hwaccel_output_format" not in scaled.arguments  # -vf needs frames in RAM

    direct_asset = MediaAsset(id="b", path="/tmp/in2.mp4", video_codec="h264", width=1920, height=1080, duration_ms=3000)
    direct_clip = Clip(asset_id="b", source_out_ms=3000)
    direct = build_ffmpeg_command(_reencode_plan(direct_asset, direct_clip, HardwareBackend.NVENC), profile)
    assert "-hwaccel_output_format" in direct.arguments


def test_concat_demuxer_paths_are_ffmpeg_friendly():
    path = _concat_file_path(r"C:\Videos\clip one.mp4")

    assert "\\" not in path
    assert path.endswith("/Videos/clip one.mp4")


def test_multi_clip_stream_copy_uses_concat_file_with_normalized_paths():
    first = MediaAsset(id="a", path=r"C:\Videos\a.mp4", video_codec="h264", duration_ms=3000)
    second = MediaAsset(id="b", path=r"C:\Videos\b.mp4", video_codec="h264", duration_ms=2000)
    first_clip = Clip(asset_id=first.id, source_out_ms=3000)
    second_clip = Clip(asset_id=second.id, source_out_ms=2000)
    plan = RenderPlan(
        route=RenderRoute.STREAM_COPY,
        backend=HardwareBackend.CPU,
        clip=first_clip,
        asset=first,
        clips=[first_clip, second_clip],
        assets=[first, second],
    )
    command = FfmpegCommand()

    try:
        _append_concat_demuxer_input(command, plan)
        with open(command.temporary_files[0], encoding="utf-8") as concat_file:
            content = concat_file.read()
    finally:
        command.cleanup()

    assert "C:/Videos/a.mp4" in content
    assert "C:/Videos/b.mp4" in content


def test_speed_filter_and_effective_duration_are_used_for_video():
    asset = MediaAsset(
        id="a", path="/tmp/in.mp4", video_codec="h264", width=1920, height=1080,
        duration_ms=4000, has_video=True, has_audio=True,
    )
    clip = Clip(asset_id=asset.id, source_out_ms=4000, speed=10.0)

    command = build_ffmpeg_command(_reencode_plan(asset, clip), ExportProfile(output_path="/tmp/out.mp4"))

    assert "setpts=(PTS-STARTPTS)/10" in _vf_of(command)
    assert command.arguments[command.arguments.index("-t") + 1] == "0.400"
    assert command.arguments[command.arguments.index("-af") + 1] == "volume=0"


def test_atempo_chain_supports_the_full_pitch_preserving_range():
    assert _atempo_filters(0.25) == ["atempo=0.5", "atempo=0.5"]
    assert _atempo_filters(2.0) == ["atempo=2"]
    assert _atempo_filters(3.0) == ["atempo=2", "atempo=1.5"]


def test_concat_uses_silence_at_four_x_and_effective_duration():
    asset = MediaAsset(
        id="a", path="/tmp/in.mp4", video_codec="h264", width=1920, height=1080,
        duration_ms=4000, has_video=True, has_audio=True,
    )
    clip = Clip(asset_id=asset.id, source_out_ms=4000, speed=4.0)

    filter_complex = _concat_filter(RenderPlan(clips=[clip], assets=[asset]), ExportProfile(), include_audio=True)

    assert "setpts=(PTS-STARTPTS)/4" in filter_complex
    assert "anullsrc=r=48000:cl=stereo,atrim=duration=1.000" in filter_complex
    assert "[0:a]atrim" not in filter_complex


def _overlay(**kwargs) -> TextOverlay:
    defaults = dict(text="Hello", start_ms=500, end_ms=2500, outline_px=3, x_px=100, y_px=800)
    return TextOverlay(**{**defaults, **kwargs})


def test_single_clip_burns_captions_into_the_video_filter():
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=1920, height=1080, duration_ms=3000, has_video=True)
    clip = Clip(asset_id=asset.id, source_out_ms=3000)
    plan = _reencode_plan(asset, clip)
    plan.texts = [_overlay()]

    chain = _vf_of(build_ffmpeg_command(plan, ExportProfile(output_path="/tmp/out.mp4")))

    assert "drawtext=" in chain
    assert ":text=Hello:" in chain
    assert ":fontsize=48:fontcolor=#ffffff:borderw=3:bordercolor=#000000:x=100:y=800" in chain
    assert "enable='between(t,0.500,2.500)'" in chain


def test_caption_survives_a_chain_that_would_otherwise_be_skipped():
    """A clip needing no filters normally emits no -vf at all."""
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=1920, height=1080, fps=60.0, duration_ms=3000, has_video=True)
    clip = Clip(asset_id=asset.id, source_out_ms=3000)
    profile = ExportProfile(output_path="/tmp/out.mp4")

    assert "-vf" not in build_ffmpeg_command(_reencode_plan(asset, clip), profile).arguments

    plan = _reencode_plan(asset, clip)
    plan.texts = [_overlay()]
    assert "drawtext=" in _vf_of(build_ffmpeg_command(plan, profile))


def test_concat_draws_captions_once_after_the_join():
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", width=1920, height=1080, duration_ms=4000, has_video=True)
    clips = [Clip(asset_id=asset.id, source_out_ms=2000), Clip(asset_id=asset.id, source_in_ms=2000, source_out_ms=4000)]
    plan = RenderPlan(clips=clips, assets=[asset, asset], texts=[_overlay()])

    filter_complex = _concat_filter(plan, ExportProfile(), include_audio=False)

    assert "concat=n=2:v=1:a=0[vsw]" in filter_complex
    assert filter_complex.split(";")[-1].startswith("[vsw]drawtext=")
    assert filter_complex.endswith("[v]")
    assert filter_complex.count("drawtext=") == 1


def test_captions_block_stream_copy():
    asset = MediaAsset(id="a", path="/tmp/in.mp4", video_codec="h264", duration_ms=3000)
    clip = Clip(asset_id=asset.id, source_out_ms=3000)
    plan = RenderPlan(route=RenderRoute.STREAM_COPY, clip=clip, asset=asset, clips=[clip], assets=[asset], texts=[_overlay()])

    command = build_ffmpeg_command(plan, ExportProfile(output_path="/tmp/out.mp4"))

    assert "copy" not in command.arguments


def test_drawtext_values_are_escaped():
    """Escaping verified against ffmpeg by rendering text= and textfile= and
    comparing pixels; ':' is special once, "'" at both parser levels, and '%'
    must stay raw (expansion=none keeps it literal)."""
    chain = ",".join(_drawtext_filters([_overlay(text="50%: it's [fine]")]))

    assert r"text=50%\\: it\\\'s \\\[fine\\\]" in chain
    assert ":expansion=none:" in chain
