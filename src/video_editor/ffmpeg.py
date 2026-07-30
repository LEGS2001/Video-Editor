from __future__ import annotations

import os
import shlex
import tempfile
from math import ceil, cos, radians, sin
from dataclasses import dataclass, field
from pathlib import Path

from .hardware import HardwareCapabilities, detect_hardware_cached, encoder_for
from .models import ExportProfile, HardwareBackend, RenderPlan, RenderRoute, VideoCodec
from .render_planner import _audio_compatible, _codec_matches, _output_container, _video_compatible


@dataclass
class FfmpegCommand:
    program: str = "ffmpeg"
    arguments: list[str] = field(default_factory=list)
    temporary_files: list[str] = field(default_factory=list)

    @property
    def argv(self) -> list[str]:
        return [self.program, *self.arguments]

    def to_shell_string(self) -> str:
        return " ".join(shlex.quote(part) for part in self.argv)

    def cleanup(self) -> None:
        for path in self.temporary_files:
            Path(path).unlink(missing_ok=True)


def _concat_file_path(path: str) -> str:
    return Path(path).resolve().as_posix().replace("'", "'\\''")


def _scale_to_canvas(src_w: int, src_h: int, out_w: int, out_h: int) -> str | None:
    """Filter that fits a source onto the output canvas. Same aspect ratio just
    scales; a different aspect (e.g. landscape footage in a portrait TikTok
    canvas) is fit and letterboxed with black bars rather than stretched."""
    if out_w <= 0 or out_h <= 0:
        return None
    if src_w == out_w and src_h == out_h:
        return None
    src_ar = (src_w / src_h) if src_h else 0.0
    out_ar = out_w / out_h
    if src_w > 0 and src_h > 0 and abs(src_ar - out_ar) > 0.01:
        return (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    return f"scale={out_w}:{out_h}"


def _crop_filter(clip, asset) -> tuple[list[str], int, int]:
    """Optional crop filter plus the frame size it produces."""
    if not clip.crop.enabled or asset.width < 2 or asset.height < 2:
        return [], asset.width, asset.height
    left = min(max(0, int(clip.crop.left)), asset.width - 2)
    top = min(max(0, int(clip.crop.top)), asset.height - 2)
    right = min(max(0, int(clip.crop.right)), asset.width - left - 2)
    bottom = min(max(0, int(clip.crop.bottom)), asset.height - top - 2)
    width, height = asset.width - left - right, asset.height - top - bottom
    return [f"crop={width}:{height}:{left}:{top}"], width, height


def _rotate_filter(clip, width: int, height: int) -> tuple[list[str], int, int]:
    angle = float(clip.transform.rotation_deg)
    if abs(angle) <= 1e-6:
        return [], width, height
    theta = radians(angle)
    rotated_w = max(2, ceil(abs(width * cos(theta)) + abs(height * sin(theta))))
    rotated_h = max(2, ceil(abs(width * sin(theta)) + abs(height * cos(theta))))
    return [f"rotate={angle:g}*PI/180:ow=rotw(iw):oh=roth(ih):c=black"], rotated_w, rotated_h


def _canvas_place_filters(src_w: int, src_h: int, clip, out_w: int, out_h: int) -> list[str]:
    """Scale the (already cropped) frame by the clip transform and place it at
    (x, y) on the output canvas. Anything falling outside the canvas is cropped
    away first, because pad errors out on negative offsets or inputs larger
    than the pad area."""
    scaled_w = max(2, int(round(src_w * max(0.0, clip.transform.scale_x))))
    scaled_h = max(2, int(round(src_h * max(0.0, clip.transform.scale_y))))
    x, y = int(clip.transform.x), int(clip.transform.y)
    filters: list[str] = []
    if (scaled_w, scaled_h) != (src_w, src_h):
        filters.append(f"scale={scaled_w}:{scaled_h}")

    if x >= out_w or y >= out_h or x + scaled_w <= 0 or y + scaled_h <= 0:
        return [*filters, f"scale={out_w}:{out_h}", "drawbox=color=black:t=fill"]

    crop_x, crop_y = max(0, -x), max(0, -y)
    pad_x, pad_y = max(0, x), max(0, y)
    visible_w = min(scaled_w - crop_x, out_w - pad_x, scaled_w)
    visible_h = min(scaled_h - crop_y, out_h - pad_y, scaled_h)

    if (visible_w, visible_h) != (scaled_w, scaled_h):
        filters.append(f"crop={visible_w}:{visible_h}:{crop_x}:{crop_y}")
    if (visible_w, visible_h) != (out_w, out_h) or pad_x or pad_y:
        filters.append(f"pad={out_w}:{out_h}:{pad_x}:{pad_y}:color=black")
    return filters


def _fade_filters(clip, video: bool) -> list[str]:
    """Fade in from / out to black (or silence), in the clip's own timebase.

    Durations come from Clip.fade_bounds so the preview and the export agree on
    what an over-long fade means."""
    duration_ms = clip.duration_ms
    fade_in, fade_out = clip.fade_bounds()
    name = "fade" if video else "afade"
    filters = []
    if fade_in > 0:
        filters.append(f"{name}=t=in:st=0:d={fade_in / 1000:g}")
    if fade_out > 0:
        filters.append(f"{name}=t=out:st={(duration_ms - fade_out) / 1000:g}:d={fade_out / 1000:g}")
    return filters


def _clip_software_filters(clip, asset, profile: ExportProfile) -> list[str]:
    """Canonical crop -> rotate -> scale/place -> opacity -> FPS/SAR chain."""
    filters, width, height = _crop_filter(clip, asset)
    rotation, width, height = _rotate_filter(clip, width, height)
    filters.extend(rotation)
    if profile.width <= 0 or profile.height <= 0:
        return filters
    if clip.has_canvas_transform:
        filters.extend(_canvas_place_filters(width, height, clip, profile.width, profile.height))
    else:
        fit = _scale_to_canvas(width, height, profile.width, profile.height)
        if fit:
            filters.append(fit)
    opacity = min(1.0, max(0.0, float(clip.opacity)))
    if opacity < 1.0:
        filters.append(f"colorchannelmixer=rr={opacity:g}:gg={opacity:g}:bb={opacity:g}")
    if abs(clip.speed - 1.0) > 1e-6:
        filters.append(f"setpts=(PTS-STARTPTS)/{clip.speed:g}")
    # After setpts: the fade keys off timestamps at its position in the chain, so
    # it has to see post-speed time for clip.duration_ms to be the right basis.
    filters.extend(_fade_filters(clip, video=True))
    filters.extend([f"fps={profile.fps:g}", "setsar=1"])
    return filters


FONT_FILES = {
    "Arial": "arial.ttf",
    "Arial Bold": "arialbd.ttf",
    "Impact": "impact.ttf",
    "Segoe UI": "segoeui.ttf",
    "Verdana": "verdana.ttf",
    "Times New Roman": "times.ttf",
    "Courier New": "cour.ttf",
    "Comic Sans MS": "comic.ttf",
}


def _escape_drawtext(value: str) -> str:
    """Escape a value for a drawtext option.

    FFmpeg unescapes filter option values twice: once splitting the graph on
    , ; [ ] ' and once splitting a filter's arguments on ':'. A character that
    is special at both levels therefore needs escaping twice. Verified against
    ffmpeg by rendering text= and comparing it pixel-for-pixel with the
    equivalent textfile=, which needs no escaping at all.

    '%' is deliberately NOT escaped: '\\%' makes drawtext render nothing. The
    caption is kept literal with expansion=none instead.
    """
    out = value.replace("\\", "\\\\\\\\")
    out = out.replace(":", "\\\\:")
    for char in "',;[]":
        out = out.replace(char, "\\\\\\" + char)
    return out.replace("\n", " ")


def _font_argument(name: str) -> str:
    """Prefer an explicit font file — Windows FFmpeg builds often lack the
    fontconfig lookup that `font=` needs. Falls back to the family name."""
    fonts_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    path = fonts_dir / FONT_FILES.get(name, "")
    if FONT_FILES.get(name) and path.is_file():
        return "fontfile=" + _escape_drawtext(path.as_posix())
    return f"font={_escape_drawtext(name)}"


def visible_texts(texts) -> list:
    return [text for text in texts if text.text.strip() and text.end_ms > text.start_ms]


def _drawtext_filters(texts) -> list[str]:
    """One drawtext per overlay, gated to its timeline window. Timeline ms map
    straight onto output seconds because the timeline is packed from zero."""
    filters = []
    for text in visible_texts(texts):
        filters.append(
            f"drawtext={_font_argument(text.font)}"
            f":text={_escape_drawtext(text.text)}"
            f":expansion=none"
            f":fontsize={max(1, int(text.size_px))}"
            f":fontcolor={text.color}"
            f":borderw={max(0, int(text.outline_px))}"
            f":bordercolor={text.outline_color}"
            f":x={int(text.x_px)}:y={int(text.y_px)}"
            f":enable='between(t,{text.start_ms / 1000.0:.3f},{text.end_ms / 1000.0:.3f})'"
        )
    return filters


def _atempo_filters(speed: float) -> list[str]:
    """Decompose a rate into FFmpeg's supported 0.5..2.0 atempo range."""
    filters: list[str] = []
    remaining = speed
    while remaining < 0.5 - 1e-9:
        filters.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0 + 1e-9:
        filters.append("atempo=2")
        remaining /= 2.0
    if abs(remaining - 1.0) > 1e-6:
        filters.append(f"atempo={remaining:g}")
    return filters


def _audio_filters(clip, profile: ExportProfile) -> list[str]:
    if clip.speed >= 4.0:
        return ["volume=0"]
    filters = _atempo_filters(clip.speed)
    gain = profile.master_volume * clip.volume
    if abs(gain - 1.0) > 1e-6:
        filters.append(f"volume={gain:.4f}")
    filters.extend(_fade_filters(clip, video=False))
    return filters


def _video_filter(plan: RenderPlan, profile: ExportProfile) -> str:
    clip, asset = plan.clip, plan.asset
    filters = _clip_software_filters(clip, asset, profile)
    text_filters = _drawtext_filters(plan.texts)
    # Captions are drawn in software, so they must go in before the VAAPI upload.
    filters.extend(text_filters)
    if plan.backend == HardwareBackend.VAAPI:
        filters.extend(["format=nv12", "hwupload"])
        return ",".join(filters)
    only_normalization = not text_filters and filters == [f"fps={profile.fps:g}", "setsar=1"]
    source_fps_matches = asset.fps <= 0 or abs(asset.fps - profile.fps) <= 0.01
    return "" if only_normalization and source_fps_matches else ",".join(filters)


def _concat_filter(plan: RenderPlan, profile: ExportProfile, include_audio: bool) -> str:
    filters: list[str] = []
    concat_inputs: list[str] = []

    for index, (clip, asset) in enumerate(zip(plan.clips, plan.assets, strict=True)):
        start = clip.source_in_ms / 1000.0
        source_duration = clip.source_duration_ms / 1000.0
        duration = clip.duration_ms / 1000.0
        if asset.has_video or asset.video_codec or asset.width > 0:
            video_filters = [
                f"[{index}:v]trim=start={start:.3f}:duration={source_duration:.3f}",
                "setpts=PTS-STARTPTS",
            ]
            video_filters.extend(_clip_software_filters(clip, asset, profile))
            filters.append(",".join(video_filters) + f"[v{index}]")
        else:
            filters.append(
                f"color=c=black:s={profile.width}x{profile.height}:r={profile.fps:g}:"
                f"d={duration:.3f},setsar=1[v{index}]"
            )
        concat_inputs.append(f"[v{index}]")

        if include_audio:
            audio_chain = (
                f"[{index}:a]atrim=start={start:.3f}:duration={source_duration:.3f},asetpts=PTS-STARTPTS"
                if asset.has_audio and clip.speed < 4.0
                else f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.3f},asetpts=PTS-STARTPTS"
            )
            if asset.has_audio and clip.speed < 4.0:
                tempo = _atempo_filters(clip.speed)
                if tempo:
                    audio_chain += "," + ",".join(tempo)
            audio_chain += ",aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
            gain = profile.master_volume * clip.volume
            if abs(gain - 1.0) > 1e-6 and clip.speed < 4.0:
                audio_chain += f",volume={gain:.4f}"
            # This chain is built inline rather than through _audio_filters, so the
            # fade has to be appended here too.
            if asset.has_audio and clip.speed < 4.0:
                fades = _fade_filters(clip, video=False)
                if fades:
                    audio_chain += "," + ",".join(fades)
            audio_chain += f"[a{index}]"
            filters.append(audio_chain)
            concat_inputs.append(f"[a{index}]")

    audio_label = "[a]" if include_audio else ""
    text_filters = _drawtext_filters(plan.texts)
    # Captions span the whole timeline, so they are drawn once on the joined
    # stream — and before any VAAPI upload, since drawtext runs in software.
    software_tail = ",".join(text_filters + ["format=nv12", "hwupload"]) if plan.backend == HardwareBackend.VAAPI else ",".join(text_filters)
    concat = f"{''.join(concat_inputs)}concat=n={len(plan.clips)}:v=1:a={1 if include_audio else 0}"
    if software_tail:
        filters.append(f"{concat}[vsw]{audio_label}")
        filters.append(f"[vsw]{software_tail}[v]")
    else:
        filters.append(f"{concat}[v]{audio_label}")
    return ";".join(filters)


def _append_concat_demuxer_input(command: FfmpegCommand, plan: RenderPlan) -> None:
    concat_file = tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="video-editor-concat-", suffix=".txt", delete=False)
    with concat_file:
        for clip, asset in zip(plan.clips, plan.assets, strict=True):
            concat_file.write(f"file '{_concat_file_path(asset.path)}'\n")
            if clip.source_in_ms > 0:
                concat_file.write(f"inpoint {clip.source_in_ms / 1000.0:.3f}\n")
            if 0 < clip.source_out_ms < asset.duration_ms:
                concat_file.write(f"outpoint {clip.source_out_ms / 1000.0:.3f}\n")
    command.temporary_files.append(concat_file.name)
    command.arguments.extend(["-f", "concat", "-safe", "0", "-i", concat_file.name])


def _append_encoder_args(command: FfmpegCommand, plan: RenderPlan, profile: ExportProfile) -> None:
    command.arguments.extend(["-c:v", encoder_for(profile.codec, plan.backend)])
    if plan.backend == HardwareBackend.NVENC:
        command.arguments.extend(["-preset", "p2" if profile.prefer_speed_over_quality else "p5"])
        if profile.prefer_speed_over_quality:
            command.arguments.extend(["-tune", "ll"])
    elif plan.backend == HardwareBackend.CPU and profile.codec == VideoCodec.H264:
        command.arguments.extend(["-preset", "veryfast" if profile.prefer_speed_over_quality else "medium"])
        command.arguments.extend(["-pix_fmt", "yuv420p"])
    if profile.bitrate_kbps > 0:
        command.arguments.extend(["-b:v", f"{profile.bitrate_kbps}k"])
    command.arguments.extend(["-r", f"{profile.fps:g}"])


def _known_stream_copy_conflict(plan: RenderPlan, profile: ExportProfile) -> bool:
    if visible_texts(plan.texts):
        return True
    output_container = _output_container(profile.output_path)
    if output_container and not _video_compatible(profile.codec, output_container):
        return True
    for clip, asset in zip(plan.clips, plan.assets, strict=True):
        if abs(clip.speed - 1.0) > 1e-6 or clip.has_visual_transform or clip.has_audio_adjustment:
            return True
        if asset.duration_ms > 0 and (clip.source_in_ms > 0 or clip.source_out_ms < asset.duration_ms):
            return True
        if asset.video_codec and not _codec_matches(asset, profile.codec):
            return True
        if asset.width > 0 and asset.width != profile.width:
            return True
        if asset.height > 0 and asset.height != profile.height:
            return True
        if asset.fps > 0 and abs(asset.fps - profile.fps) > 0.01:
            return True
        if asset.has_audio and asset.audio_codec and not _audio_compatible(asset, output_container):
            return True
    return False


def _append_faststart(arguments: list[str], output_path: str) -> None:
    if Path(output_path).suffix.lower() in {".mp4", ".m4v", ".mov"}:
        arguments.extend(["-movflags", "+faststart"])


def build_ffmpeg_command(
    plan: RenderPlan,
    profile: ExportProfile,
    *,
    capabilities: HardwareCapabilities | None = None,
) -> FfmpegCommand:
    command = FfmpegCommand(arguments=["-hide_banner", "-y"])
    route = plan.route
    if route == RenderRoute.STREAM_COPY and (
        not profile.allow_stream_copy or _known_stream_copy_conflict(plan, profile)
    ):
        route = RenderRoute.REENCODE
    multi_clip = len(plan.clips) > 1
    filter_arg = ""
    if route == RenderRoute.REENCODE and not multi_clip:
        filter_arg = _video_filter(plan, profile)

    if route == RenderRoute.REENCODE and plan.backend == HardwareBackend.VAAPI:
        resolved_capabilities = capabilities or detect_hardware_cached()
        command.arguments.extend(["-vaapi_device", resolved_capabilities.vaapi_device])
    elif route == RenderRoute.REENCODE and plan.backend == HardwareBackend.NVENC and not multi_clip:
        command.arguments.extend(["-hwaccel", "cuda"])
        # Frames may only stay on the GPU when no software filter needs them
        # in system memory — a -vf chain on CUDA frames fails the export.
        if not filter_arg:
            command.arguments.extend(["-hwaccel_output_format", "cuda"])
    if multi_clip and route == RenderRoute.STREAM_COPY:
        _append_concat_demuxer_input(command, plan)
    elif multi_clip:
        for asset in plan.assets:
            command.arguments.extend(["-i", asset.path])
    else:
        start = plan.clip.source_in_ms / 1000.0
        duration = plan.clip.duration_ms / 1000.0
        if start > 0.0:
            command.arguments.extend(["-ss", f"{start:.3f}"])
        command.arguments.extend(["-i", plan.asset.path])
        if duration > 0.0:
            command.arguments.extend(["-t", f"{duration:.3f}"])

    if route == RenderRoute.STREAM_COPY:
        command.arguments.extend(["-c", "copy"])
    elif multi_clip:
        include_audio = any(asset.has_audio for asset in plan.assets)
        command.arguments.extend(["-filter_complex", _concat_filter(plan, profile, include_audio), "-map", "[v]"])
        if include_audio:
            command.arguments.extend(["-map", "[a]"])
        _append_encoder_args(command, plan, profile)
        if include_audio:
            command.arguments.extend(["-c:a", "aac", "-b:a", f"{profile.audio_bitrate_kbps}k"])
        else:
            command.arguments.append("-an")
    elif plan.asset.has_video or plan.asset.video_codec or plan.asset.width > 0:
        if filter_arg:
            command.arguments.extend(["-vf", filter_arg])
        _append_encoder_args(command, plan, profile)
        audio_filters = _audio_filters(plan.clip, profile)
        if plan.asset.has_audio and audio_filters:
            command.arguments.extend(["-af", ",".join(audio_filters)])
        command.arguments.extend(["-c:a", "aac", "-b:a", f"{profile.audio_bitrate_kbps}k"])
    else:
        duration = max(0.001, plan.clip.duration_ms / 1000.0)
        color = ",".join([
            f"color=c=black:s={profile.width}x{profile.height}:r={profile.fps:g}:d={duration:.3f}",
            "setsar=1",
            *_drawtext_filters(plan.texts),
        ]) + "[v]"
        command.arguments.extend(["-filter_complex", color, "-map", "[v]", "-map", "0:a:0?"])
        _append_encoder_args(command, plan, profile)
        audio_filters = _audio_filters(plan.clip, profile)
        if plan.asset.has_audio and audio_filters:
            command.arguments.extend(["-af", ",".join(audio_filters)])
        command.arguments.extend(["-c:a", "aac", "-b:a", f"{profile.audio_bitrate_kbps}k", "-shortest"])

    _append_faststart(command.arguments, profile.output_path)
    command.arguments.extend(["-progress", "pipe:1", "-nostats", profile.output_path])
    return command


def _segment_copy_command(asset, ts_path: str) -> FfmpegCommand:
    """Remux an untouched clip into an MPEG-TS segment without re-encoding."""
    return FfmpegCommand(
        arguments=[
            "-hide_banner", "-y", "-i", asset.path,
            "-c", "copy", "-f", "mpegts",
            "-progress", "pipe:1", "-nostats", ts_path,
        ]
    )


def _segment_encode_command(clip, asset, profile: ExportProfile, ts_path: str) -> FfmpegCommand:
    """Re-encode one normalized smart-render segment on the CPU."""
    args = ["-hide_banner", "-y"]
    start = clip.source_in_ms / 1000.0
    duration = clip.duration_ms / 1000.0
    if start > 0.0:
        args.extend(["-ss", f"{start:.3f}"])
    args.extend(["-i", asset.path])
    if duration > 0.0:
        args.extend(["-t", f"{duration:.3f}"])
    if asset.has_video or asset.video_codec or asset.width > 0:
        args.extend(["-vf", ",".join(_clip_software_filters(clip, asset, profile))])
    else:
        args.extend([
            "-filter_complex",
            f"color=c=black:s={profile.width}x{profile.height}:r={profile.fps:g}:d={duration:.3f},setsar=1[v]",
            "-map", "[v]", "-map", "0:a:0?",
        ])
    args.extend(["-r", f"{profile.fps:g}"])
    args.extend([
        "-c:v", encoder_for(profile.codec, HardwareBackend.CPU),
        "-preset", "veryfast" if profile.prefer_speed_over_quality else "medium",
        "-pix_fmt", "yuv420p",
    ])
    if profile.bitrate_kbps > 0:
        args.extend(["-b:v", f"{profile.bitrate_kbps}k"])
    audio_filters = _audio_filters(clip, profile)
    if asset.has_audio and audio_filters:
        args.extend(["-af", ",".join(audio_filters)])
    args.extend(["-c:a", "aac", "-b:a", f"{profile.audio_bitrate_kbps}k"])
    if asset.audio_sample_rate > 0:
        args.extend(["-ar", str(asset.audio_sample_rate)])
    if asset.audio_channels > 0:
        args.extend(["-ac", str(asset.audio_channels)])
    args.extend(["-f", "mpegts", "-progress", "pipe:1", "-nostats", ts_path])
    return FfmpegCommand(arguments=args)


def build_smart_render_commands(segments, profile: ExportProfile) -> list[tuple[FfmpegCommand, int]]:
    """Build the command sequence for a segment-level smart-render export:
    one command per clip (copy or encode) writing a TS segment, then a final
    concat that stream-copies them together. Returns (command, weight_ms) jobs;
    the concat command owns all intermediates so they are cleaned up last."""
    ts_paths: list[str] = []
    jobs: list[tuple[FfmpegCommand, int]] = []
    for index, segment in enumerate(segments):
        handle = tempfile.NamedTemporaryFile(
            prefix=f"video-editor-seg{index:02d}-", suffix=".ts", delete=False
        )
        handle.close()
        ts_paths.append(handle.name)
        command = (
            _segment_encode_command(segment.clip, segment.asset, profile, handle.name)
            if segment.encode
            else _segment_copy_command(segment.asset, handle.name)
        )
        jobs.append((command, max(1, segment.clip.duration_ms)))

    list_handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix="video-editor-segments-", suffix=".txt", delete=False
    )
    with list_handle:
        for ts_path in ts_paths:
            list_handle.write(f"file '{_concat_file_path(ts_path)}'\n")

    concat_arguments = [
        "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", list_handle.name,
        "-c", "copy",
    ]
    _append_faststart(concat_arguments, profile.output_path)
    concat_arguments.extend(["-progress", "pipe:1", "-nostats", profile.output_path])
    concat = FfmpegCommand(
        arguments=concat_arguments,
        temporary_files=[list_handle.name, *ts_paths],
    )
    total = sum(weight for _, weight in jobs) or 1
    jobs.append((concat, total))
    return jobs
