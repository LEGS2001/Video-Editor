from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import (
    Clip,
    Crop,
    ExportDefaults,
    HardwareBackend,
    MediaAsset,
    Project,
    TextOverlay,
    Timeline,
    Track,
    TrackType,
    Transform,
    VideoCodec,
)

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def hardware_backend_from_string(value: str) -> HardwareBackend:
    try:
        return HardwareBackend((value or "").lower())
    except ValueError:
        return HardwareBackend.AUTO


def video_codec_from_string(value: str) -> VideoCodec:
    return {
        "avc": VideoCodec.H264,
        "avc1": VideoCodec.H264,
        "h265": VideoCodec.H265,
        "hevc": VideoCodec.H265,
        "hev1": VideoCodec.H265,
        "hvc1": VideoCodec.H265,
        "av1": VideoCodec.AV1,
        "av01": VideoCodec.AV1,
    }.get((value or "").lower(), VideoCodec.H264)


def _int64(value: Any) -> int:
    return 0 if value is None else int(value)


def _clip_to_dict(clip: Clip) -> dict[str, Any]:
    return {
        "id": clip.id,
        "assetId": clip.asset_id,
        "sourceInMs": str(clip.source_in_ms),
        "sourceOutMs": str(clip.source_out_ms),
        "timelineStartMs": str(clip.timeline_start_ms),
        "transform": {
            "x": clip.transform.x,
            "y": clip.transform.y,
            "scaleX": clip.transform.scale_x,
            "scaleY": clip.transform.scale_y,
            "rotationDeg": clip.transform.rotation_deg,
        },
        "crop": asdict(clip.crop),
        "opacity": clip.opacity,
        "volume": clip.volume,
        "muted": clip.muted,
        "groupId": clip.group_id,
        "speed": clip.speed,
        "fadeInMs": str(clip.fade_in_ms),
        "fadeOutMs": str(clip.fade_out_ms),
    }


def _clip_from_dict(data: dict[str, Any]) -> Clip:
    transform = data.get("transform") or {}
    crop = data.get("crop") or {}
    return Clip(
        id=data.get("id", ""),
        asset_id=data.get("assetId", ""),
        source_in_ms=_int64(data.get("sourceInMs")),
        source_out_ms=_int64(data.get("sourceOutMs")),
        timeline_start_ms=_int64(data.get("timelineStartMs")),
        transform=Transform(
            x=float(transform.get("x", 0.0)),
            y=float(transform.get("y", 0.0)),
            scale_x=float(transform.get("scaleX", 1.0)),
            scale_y=float(transform.get("scaleY", 1.0)),
            rotation_deg=float(transform.get("rotationDeg", 0.0)),
        ),
        crop=Crop(
            left=int(crop.get("left", 0)),
            top=int(crop.get("top", 0)),
            right=int(crop.get("right", 0)),
            bottom=int(crop.get("bottom", 0)),
            enabled=bool(crop.get("enabled", False)),
        ),
        opacity=float(data.get("opacity", 1.0)),
        volume=float(data.get("volume", 1.0)),
        muted=bool(data.get("muted", False)),
        group_id=data.get("groupId", ""),
        speed=float(data.get("speed", 1.0)),
        fade_in_ms=_int64(data.get("fadeInMs")),
        fade_out_ms=_int64(data.get("fadeOutMs")),
    )


def _text_to_dict(text: TextOverlay) -> dict[str, Any]:
    return {
        "id": text.id,
        "text": text.text,
        "startMs": str(text.start_ms),
        "endMs": str(text.end_ms),
        "font": text.font,
        "sizePx": text.size_px,
        "color": text.color,
        "outlineColor": text.outline_color,
        "outlinePx": text.outline_px,
        "xPx": text.x_px,
        "yPx": text.y_px,
    }


def _text_from_dict(data: dict[str, Any]) -> TextOverlay:
    return TextOverlay(
        id=data.get("id", ""),
        text=data.get("text", ""),
        start_ms=_int64(data.get("startMs")),
        end_ms=_int64(data.get("endMs")),
        font=data.get("font", "Arial"),
        size_px=int(data.get("sizePx", 48)),
        color=data.get("color", "#ffffff"),
        outline_color=data.get("outlineColor", "#000000"),
        outline_px=int(data.get("outlinePx", 3)),
        x_px=int(data.get("xPx", 0)),
        y_px=int(data.get("yPx", 0)),
    )


def _asset_to_dict(asset: MediaAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "path": asset.path,
        "container": asset.container,
        "videoCodec": asset.video_codec,
        "audioCodec": asset.audio_codec,
        "pixelFormat": asset.pixel_format,
        "width": asset.width,
        "height": asset.height,
        "fps": asset.fps,
        "audioSampleRate": asset.audio_sample_rate,
        "audioChannels": asset.audio_channels,
        "durationMs": str(asset.duration_ms),
        "hasAudio": asset.has_audio,
        "hasVideo": asset.has_video,
    }


def _asset_from_dict(data: dict[str, Any]) -> MediaAsset:
    return MediaAsset(
        id=data.get("id", ""),
        path=data.get("path", ""),
        container=data.get("container", ""),
        video_codec=data.get("videoCodec", ""),
        audio_codec=data.get("audioCodec", ""),
        pixel_format=data.get("pixelFormat", ""),
        width=int(data.get("width", 0)),
        height=int(data.get("height", 0)),
        fps=float(data.get("fps", 0.0)),
        audio_sample_rate=int(data.get("audioSampleRate", 0)),
        audio_channels=int(data.get("audioChannels", 0)),
        duration_ms=_int64(data.get("durationMs")),
        has_audio=bool(data.get("hasAudio", False)),
        has_video=bool(data.get("hasVideo", False)),
    )


def project_to_dict(project: Project) -> dict[str, Any]:
    return {
        "version": 1,
        "project": {
            "id": project.id,
            "name": project.name,
            "timeline": {
                "width": project.timeline.width,
                "height": project.timeline.height,
                "fps": project.timeline.fps,
                "masterVolume": project.timeline.master_volume,
                "tracks": [
                    {
                        "id": track.id,
                        "type": track.type.value,
                        "muted": track.muted,
                        "locked": track.locked,
                        "clips": [_clip_to_dict(clip) for clip in track.clips],
                    }
                    for track in project.timeline.tracks
                ],
                "texts": [_text_to_dict(text) for text in project.timeline.texts],
            },
            "exportDefaults": {
                "codec": project.export_defaults.codec.value,
                "hardwareBackend": project.export_defaults.hardware_backend.value,
                "fps": project.export_defaults.fps,
                "bitrateKbps": project.export_defaults.bitrate_kbps,
                "audioBitrateKbps": project.export_defaults.audio_bitrate_kbps,
                "preferSpeedOverQuality": project.export_defaults.prefer_speed_over_quality,
                "allowStreamCopy": project.export_defaults.allow_stream_copy,
            },
            "media": [_asset_to_dict(asset) for asset in project.media],
        },
    }


def project_from_dict(root: dict[str, Any]) -> Project:
    if int(root.get("version", 0)) != 1:
        raise ValueError("Unsupported project version")

    data = root.get("project") or {}
    timeline_data = data.get("timeline") or {}
    tracks = [
        Track(
            id=track_data.get("id", ""),
            type=TrackType.AUDIO if track_data.get("type") == "audio" else TrackType.VIDEO,
            muted=bool(track_data.get("muted", False)),
            locked=bool(track_data.get("locked", False)),
            clips=[_clip_from_dict(item) for item in track_data.get("clips", [])],
        )
        for track_data in timeline_data.get("tracks", [])
    ]

    defaults = data.get("exportDefaults") or {}
    project = Project(
        id=data.get("id", ""),
        name=data.get("name", "Untitled"),
        timeline=Timeline(
            width=int(timeline_data.get("width", 1920)),
            height=int(timeline_data.get("height", 1080)),
            fps=float(timeline_data.get("fps", 30.0)),
            master_volume=float(timeline_data.get("masterVolume", 1.0)),
            tracks=tracks or [Track()],
            texts=[_text_from_dict(item) for item in timeline_data.get("texts", [])],
        ),
        export_defaults=ExportDefaults(
            codec=video_codec_from_string(defaults.get("codec", VideoCodec.H264.value)),
            hardware_backend=hardware_backend_from_string(
                defaults.get("hardwareBackend", HardwareBackend.AUTO.value)
            ),
            fps=float(defaults.get("fps", 60.0)),
            bitrate_kbps=int(defaults.get("bitrateKbps", 12000)),
            audio_bitrate_kbps=int(defaults.get("audioBitrateKbps", 192)),
            prefer_speed_over_quality=bool(defaults.get("preferSpeedOverQuality", True)),
            allow_stream_copy=bool(defaults.get("allowStreamCopy", True)),
        ),
        media=[_asset_from_dict(item) for item in data.get("media", [])],
    )
    if "fps" not in defaults:
        clip_asset_ids = {
            clip.asset_id
            for track in project.timeline.tracks
            if track.type == TrackType.VIDEO
            for clip in track.clips
        }
        source_assets = [
            asset for asset in project.media if asset.id in clip_asset_ids and asset.has_video
        ]
        source_fps = [asset.fps for asset in source_assets if asset.fps > 0]
        if source_fps and all(abs(fps - source_fps[0]) <= 0.01 for fps in source_fps[1:]):
            project.export_defaults.fps = source_fps[0]
        source_codecs = {asset.video_codec.lower() for asset in source_assets if asset.video_codec}
        if len(source_codecs) == 1:
            project.export_defaults.codec = video_codec_from_string(source_codecs.pop())
    for track in project.timeline.tracks:
        for clip in track.clips:
            clip.set_timeline_fps(project.timeline.fps)
    validate_project(project)
    return project


def validate_project(project: Project) -> None:
    """Reject corrupt project state before it reaches playback or FFmpeg."""
    if not project.id:
        raise ValueError("Project id is missing")
    timeline = project.timeline
    if timeline.width <= 0 or timeline.height <= 0 or not math.isfinite(timeline.fps) or timeline.fps <= 0:
        raise ValueError("Timeline dimensions and FPS must be positive")
    if not math.isfinite(timeline.master_volume) or timeline.master_volume < 0:
        raise ValueError("Timeline master volume must be non-negative")

    asset_ids: set[str] = set()
    asset_index: dict[str, MediaAsset] = {}
    for asset in project.media:
        if not asset.id or asset.id in asset_ids:
            raise ValueError(f"Media asset id is missing or duplicated: {asset.id!r}")
        asset_ids.add(asset.id)
        asset_index[asset.id] = asset
        if not asset.path:
            raise ValueError(f"Media asset {asset.id!r} has no path")
        if min(asset.width, asset.height, asset.duration_ms, asset.audio_sample_rate, asset.audio_channels) < 0:
            raise ValueError(f"Media asset {asset.id!r} contains negative metadata")
        if not math.isfinite(asset.fps) or asset.fps < 0:
            raise ValueError(f"Media asset {asset.id!r} has invalid FPS")
        if asset.has_video and (asset.width <= 0 or asset.height <= 0):
            raise ValueError(f"Video asset {asset.id!r} has invalid dimensions")

    track_ids: set[str] = set()
    clip_ids: set[str] = set()
    for track in timeline.tracks:
        if not track.id or track.id in track_ids:
            raise ValueError(f"Track id is missing or duplicated: {track.id!r}")
        track_ids.add(track.id)
        for clip in track.clips:
            if not clip.id or clip.id in clip_ids:
                raise ValueError(f"Clip id is missing or duplicated: {clip.id!r}")
            clip_ids.add(clip.id)
            if clip.asset_id not in asset_ids:
                raise ValueError(f"Clip {clip.id!r} references missing media {clip.asset_id!r}")
            asset = asset_index[clip.asset_id]
            if clip.source_in_ms < 0 or clip.source_out_ms <= clip.source_in_ms or clip.timeline_start_ms < 0:
                raise ValueError(f"Clip {clip.id!r} has invalid timing")
            if asset.duration_ms > 0 and clip.source_out_ms > asset.duration_ms:
                raise ValueError(f"Clip {clip.id!r} extends beyond its media")
            crop = clip.crop
            if min(crop.left, crop.top, crop.right, crop.bottom) < 0:
                raise ValueError(f"Clip {clip.id!r} has negative crop")
            # Only non-negative is checked: a trim or speed change can legitimately
            # leave a fade longer than its clip, and the renderer clamps it.
            if min(clip.fade_in_ms, clip.fade_out_ms) < 0:
                raise ValueError(f"Clip {clip.id!r} has negative fade")
            if crop.enabled and asset.has_video and (
                crop.left + crop.right >= asset.width or crop.top + crop.bottom >= asset.height
            ):
                raise ValueError(f"Clip {clip.id!r} crop removes the whole frame")
            values = (
                clip.transform.x,
                clip.transform.y,
                clip.transform.scale_x,
                clip.transform.scale_y,
                clip.transform.rotation_deg,
                clip.opacity,
                clip.volume,
                clip.speed,
            )
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"Clip {clip.id!r} contains non-finite values")
            if clip.transform.scale_x <= 0 or clip.transform.scale_y <= 0:
                raise ValueError(f"Clip {clip.id!r} scale must be positive")
            if not 0 <= clip.opacity <= 1 or clip.volume < 0:
                raise ValueError(f"Clip {clip.id!r} opacity or volume is invalid")
            if not 0.25 <= clip.speed <= 100.0:
                raise ValueError(f"Clip {clip.id!r} speed must be between 0.25 and 100")

    text_ids: set[str] = set()
    for text in timeline.texts:
        if not text.id or text.id in text_ids:
            raise ValueError(f"Text overlay id is missing or duplicated: {text.id!r}")
        text_ids.add(text.id)
        if text.start_ms < 0 or text.end_ms <= text.start_ms:
            raise ValueError(f"Text overlay {text.id!r} has invalid timing")
        if text.size_px <= 0 or text.outline_px < 0:
            raise ValueError(f"Text overlay {text.id!r} has invalid size or outline width")
        if not _HEX_COLOR.match(text.color) or not _HEX_COLOR.match(text.outline_color):
            raise ValueError(f"Text overlay {text.id!r} has an invalid colour")

    defaults = project.export_defaults
    if not math.isfinite(defaults.fps) or defaults.fps <= 0:
        raise ValueError("Export FPS must be positive")
    if defaults.bitrate_kbps <= 0 or defaults.audio_bitrate_kbps <= 0:
        raise ValueError("Export bitrates must be positive")


def save_project(project: Project, path: str | Path) -> None:
    validate_project(project)
    target = Path(path)
    payload = json.dumps(project_to_dict(project), indent=2)
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
        raise


def load_project(path: str | Path) -> Project:
    return project_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
