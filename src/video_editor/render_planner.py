from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from .models import (
    Clip,
    ExportProfile,
    HardwareBackend,
    MediaAsset,
    Project,
    RenderPlan,
    RenderRoute,
    TrackType,
    VideoCodec,
)


@dataclass
class RenderSegment:
    """One piece of a smart-render export: either stream-copied verbatim or
    re-encoded. Re-encoded pieces use the selected H.264/HEVC codec with
    yuv420p and consistent AAC audio so every segment concatenates cleanly."""

    clip: Clip
    asset: MediaAsset
    encode: bool


def _can_stream_copy_clip(clip, asset: MediaAsset) -> bool:
    return (
        abs(clip.speed - 1.0) <= 1e-6
        and not clip.has_visual_transform
        and bool(asset.path and asset.has_video and asset.video_codec)
    )


def _spans_full_source(clip, asset: MediaAsset) -> bool:
    """True when the clip uses its whole source file (no trim). Trimmed clips
    can't be cut cleanly by stream copy — the boundary lands on a keyframe and
    the AAC audio priming desyncs — so those must be reencoded."""
    return clip.source_in_ms <= 0 and clip.source_out_ms >= asset.duration_ms


def _coalesce_clips(clips, assets):
    """Fuse runs of clips that come from the same source and join seamlessly
    (e.g. the two halves of a split: clip[n].source_in == clip[n-1].source_out).
    Returned clips are copies, so the project is never mutated."""
    out_clips: list = []
    out_assets: list = []
    for clip, asset in zip(clips, assets, strict=True):
        if out_clips:
            prev = out_clips[-1]
            contiguous = abs(clip.source_in_ms - prev.source_out_ms) <= 1
            same_treatment = (
                prev.asset_id == clip.asset_id
                and not prev.has_visual_transform
                and not clip.has_visual_transform
                and abs(prev.volume - clip.volume) <= 1e-6
                and abs(prev.speed - clip.speed) <= 1e-6
            )
            if contiguous and same_treatment:
                prev.source_out_ms = clip.source_out_ms
                continue
        out_clips.append(deepcopy(clip))
        out_assets.append(asset)
    return out_clips, out_assets


def _can_stream_copy_concat(clips, assets: list[MediaAsset]) -> bool:
    if not clips or len(clips) != len(assets):
        return False
    first = assets[0]
    for clip, asset in zip(clips, assets, strict=True):
        if not _can_stream_copy_clip(clip, asset):
            return False
        if not _spans_full_source(clip, asset):
            return False
        if asset.container != first.container:
            return False
        if asset.video_codec != first.video_codec:
            return False
        if asset.audio_codec != first.audio_codec:
            return False
        if asset.width != first.width or asset.height != first.height:
            return False
        if abs(asset.fps - first.fps) > 0.01:
            return False
        if asset.has_audio != first.has_audio:
            return False
    return True


def _codec_matches(asset: MediaAsset, codec: VideoCodec) -> bool:
    aliases = {
        VideoCodec.H264: {"h264", "avc", "avc1"},
        VideoCodec.H265: {"h265", "hevc", "hev1", "hvc1"},
        VideoCodec.AV1: {"av1", "av01"},
    }
    return asset.video_codec.lower() in aliases[codec]


def _output_container(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".mp4": "mov", ".m4v": "mov", ".mov": "mov",
        ".mkv": "matroska", ".webm": "webm",
    }.get(suffix, suffix.removeprefix("."))


def _audio_compatible(asset: MediaAsset, output_container: str) -> bool:
    codecs = {
        "mov": {"aac"},
        "matroska": {"aac", "mp3", "opus", "vorbis", "flac"},
        "webm": {"opus", "vorbis"},
    }
    return not asset.has_audio or asset.audio_codec.lower() in codecs.get(output_container, set())


def _video_compatible(codec: VideoCodec, output_container: str) -> bool:
    codecs = {
        "mov": {VideoCodec.H264, VideoCodec.H265, VideoCodec.AV1},
        "matroska": {VideoCodec.H264, VideoCodec.H265, VideoCodec.AV1},
        "webm": {VideoCodec.AV1},
    }
    return codec in codecs.get(output_container, set())


def can_stream_copy_profile(
    profile: ExportProfile,
    clips: list[Clip],
    assets: list[MediaAsset],
) -> bool:
    """Prove that copying cannot silently ignore any export setting."""
    if not _can_stream_copy_concat(clips, assets):
        return False
    output_container = _output_container(profile.output_path)
    return _video_compatible(profile.codec, output_container) and all(
        _codec_matches(asset, profile.codec)
        and asset.width == profile.width
        and asset.height == profile.height
        and abs(asset.fps - profile.fps) <= 0.01
        and _audio_compatible(asset, output_container)
        for asset in assets
    )


def _visible_texts(project: Project) -> list:
    return [
        text for text in project.timeline.texts
        if text.text.strip() and text.end_ms > text.start_ms
    ]


def build_render_plan(project: Project, profile: ExportProfile, resolved_backend: HardwareBackend) -> RenderPlan:
    plan = RenderPlan(backend=resolved_backend, texts=_visible_texts(project))
    video_clips = [
        clip
        for track in project.timeline.tracks
        if track.type == TrackType.VIDEO and not track.muted
        for clip in track.clips
        if not clip.muted
    ]
    for clip in video_clips:
        clip.set_timeline_fps(project.timeline.fps)
    video_clips.sort(key=lambda clip: clip.timeline_start_ms)

    if not video_clips:
        plan.reason = "No video clips to export"
        return plan

    assets = []
    for clip in video_clips:
        asset = next((asset for asset in project.media if asset.id == clip.asset_id), None)
        if asset is None:
            plan.reason = "Clip media asset is missing"
            return plan
        assets.append(asset)

    # Merge adjacent clips that share a source and join exactly (the two halves
    # of a split). They collapse into one continuous segment, so the export has
    # no cut to glitch on and can still stream-copy when the source is untrimmed.
    plan.clips, plan.assets = _coalesce_clips(video_clips, assets)
    plan.clip = plan.clips[0]
    plan.asset = plan.assets[0]
    clip_count = len(plan.clips)

    if not profile.allow_stream_copy:
        plan.reason = "Stream copy disabled in profile"
        return plan

    # Captions are burned in by a drawtext filter, which a stream copy would
    # silently drop.
    if plan.texts:
        if clip_count == 1 and plan.clip.has_canvas_transform:
            plan.backend = HardwareBackend.CPU
        plan.reason = "Text overlay requires reencode"
        return plan

    # A non-default master or per-clip volume needs an audio filter, which a
    # stream copy cannot apply. Force a reencode so the gain lands in the output.
    gain_active = abs(project.timeline.master_volume - 1.0) > 1e-6 or any(
        clip.has_audio_adjustment for clip in plan.clips
    )
    if gain_active:
        if clip_count == 1 and plan.clip.has_canvas_transform:
            plan.backend = HardwareBackend.CPU
        plan.reason = "Volume adjustment requires reencode"
        return plan

    if any(abs(clip.speed - 1.0) > 1e-6 for clip in plan.clips):
        plan.reason = "Clip speed requires reencode"
        return plan

    # Stream copy keeps the source resolution/fps, so it's only valid when the
    # output canvas already matches every source (otherwise the mode's target
    # resolution would be silently ignored).
    if can_stream_copy_profile(profile, plan.clips, plan.assets):
        plan.route = RenderRoute.STREAM_COPY
        plan.backend = HardwareBackend.CPU
        plan.reason = (
            "Single compatible clip without visual transforms"
            if clip_count == 1
            else "Compatible clips can be concatenated with stream copy"
        )
        return plan

    if clip_count > 1:
        plan.reason = "Multiple clips need reencode because they are trimmed, differ in media, or have transforms"
        return plan
    if plan.clip.has_visual_transform:
        if plan.clip.has_canvas_transform:
            plan.backend = HardwareBackend.CPU
        plan.reason = "Crop, scale, position, rotation, or opacity requires reencode"
        return plan
    if not _codec_matches(plan.asset, profile.codec):
        plan.reason = "Output codec differs from source"
        return plan
    if profile.width != plan.asset.width or profile.height != plan.asset.height:
        plan.reason = "Output resolution differs from source"
        return plan
    if abs(profile.fps - plan.asset.fps) > 0.01:
        plan.reason = "Output fps differs from source"
        return plan

    plan.reason = "Source container or audio is incompatible with the output"
    return plan


def _segment_needs_encode(clip, asset: MediaAsset, profile: ExportProfile) -> bool:
    """A clip can be copied verbatim only if it is the whole source, untouched,
    and already in the output's format. Anything else must be reencoded."""
    return (
        abs(clip.speed - 1.0) > 1e-6
        or clip.has_visual_transform
        or clip.has_audio_adjustment
        or not _spans_full_source(clip, asset)
        or not _codec_matches(asset, profile.codec)
        or asset.pixel_format != "yuv420p"
        or (asset.has_audio and asset.audio_codec != "aac")
        or asset.width != profile.width
        or asset.height != profile.height
        or abs(asset.fps - profile.fps) > 0.01
    )


def plan_smart_segments(
    project: Project, profile: ExportProfile, clips, assets
) -> list[RenderSegment] | None:
    """Decide per clip whether to stream-copy or reencode, so an export only
    reencodes the pieces that actually changed and copies the rest.

    Returns the segment list when this is worthwhile and provably clean to
    concatenate, otherwise None (the caller falls back to the single-command
    plan). Scoped to H.264/HEVC yuv420p timelines with either consistent AAC
    audio or no audio, where every segment ends up
    identical in format — the case where a copy+encode concat is reliable."""
    if not profile.allow_stream_copy or profile.codec not in {VideoCodec.H264, VideoCodec.H265}:
        return None
    # Segments are encoded independently, each with its own timebase, so a
    # timeline-wide between(t,…) caption window cannot be expressed per segment.
    if _visible_texts(project):
        return None
    if abs(project.timeline.master_volume - 1.0) > 1e-6:
        return None
    if len(clips) < 2 or len(clips) != len(assets):
        return None
    # Audio presence and layout must match so copied and reencoded streams line
    # up across the concat. Entirely silent timelines are safe too.
    if len({asset.has_audio for asset in assets}) > 1:
        return None
    if assets[0].has_audio and (
        len({asset.audio_sample_rate for asset in assets}) > 1
        or len({asset.audio_channels for asset in assets}) > 1
    ):
        return None

    segments = [
        RenderSegment(clip, asset, _segment_needs_encode(clip, asset, profile))
        for clip, asset in zip(clips, assets, strict=True)
    ]
    has_copy = any(not seg.encode for seg in segments)
    has_encode = any(seg.encode for seg in segments)
    # Only worthwhile when some clips copy and some encode. All-copy already
    # stream-copies; all-encode is handled better by the single concat filter.
    if not (has_copy and has_encode):
        return None
    return segments
