from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4


class HardwareBackend(StrEnum):
    AUTO = "auto"
    VAAPI = "vaapi"
    NVENC = "nvenc"
    AMF = "amf"
    QSV = "qsv"
    CPU = "cpu"


class VideoCodec(StrEnum):
    H264 = "h264"
    H265 = "h265"
    AV1 = "av1"


class RenderRoute(StrEnum):
    STREAM_COPY = "stream_copy"
    REENCODE = "reencode"


class TrackType(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


@dataclass
class Crop:
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0
    enabled: bool = False


@dataclass
class Transform:
    x: float = 0.0
    y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation_deg: float = 0.0


@dataclass
class Clip:
    id: str = field(default_factory=lambda: uuid4().hex)
    asset_id: str = ""
    source_in_ms: int = 0
    source_out_ms: int = 0
    timeline_start_ms: int = 0
    transform: Transform = field(default_factory=Transform)
    crop: Crop = field(default_factory=Crop)
    opacity: float = 1.0
    volume: float = 1.0
    muted: bool = False
    group_id: str = ""
    speed: float = 1.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    _timeline_fps: float = field(default=30.0, init=False, repr=False, compare=False)

    @property
    def source_duration_ms(self) -> int:
        return max(0, self.source_out_ms - self.source_in_ms)

    @property
    def duration_ms(self) -> int:
        if not self.source_duration_ms:
            return 0
        fps = max(1.0, self._timeline_fps)
        frames = max(1, int(round(self.source_duration_ms * fps / (1000 * max(0.25, self.speed)))))
        return max(1, int(round(frames * 1000 / fps)))

    def set_timeline_fps(self, fps: float) -> None:
        self._timeline_fps = max(1.0, float(fps))

    @property
    def has_audio_adjustment(self) -> bool:
        return abs(self.volume - 1.0) > 1e-6

    @property
    def has_fade(self) -> bool:
        return self.fade_in_ms > 0 or self.fade_out_ms > 0

    def fade_bounds(self) -> tuple[int, int]:
        """Fade durations clamped to what the clip can actually hold. A trim or a
        speed change can leave a fade longer than its clip, so this is the single
        definition shared by the renderer and the preview — fade out is capped
        against whatever fade in leaves, so the two never overlap."""
        duration_ms = self.duration_ms
        fade_in = min(max(0, self.fade_in_ms), duration_ms)
        return fade_in, min(max(0, self.fade_out_ms), duration_ms - fade_in)

    def fade_factor_at(self, offset_ms: int) -> float:
        """Brightness multiplier this many ms into the clip, 0.0-1.0."""
        fade_in, fade_out = self.fade_bounds()
        offset_ms = max(0, min(offset_ms, self.duration_ms))
        factor = 1.0
        if fade_in > 0 and offset_ms < fade_in:
            factor = offset_ms / fade_in
        if fade_out > 0:
            remaining = self.duration_ms - offset_ms
            if remaining < fade_out:
                factor = min(factor, max(0.0, remaining) / fade_out)
        return max(0.0, min(1.0, factor))

    @property
    def has_visual_transform(self) -> bool:
        # A fade counts here so that every stream-copy gate rejects it. It is
        # deliberately not in has_canvas_transform, which forces the CPU backend.
        return self.crop.enabled or self.has_canvas_transform or self.has_fade

    @property
    def has_canvas_transform(self) -> bool:
        return (
            self.transform.x != 0.0
            or self.transform.y != 0.0
            or self.transform.scale_x != 1.0
            or self.transform.scale_y != 1.0
            or self.transform.rotation_deg != 0.0
            or self.opacity != 1.0
        )


@dataclass
class TextOverlay:
    """A caption burned onto the whole timeline between start_ms and end_ms.
    Position is the text's top-left corner in canvas pixels, which is what both
    the preview item and the ffmpeg drawtext filter anchor on."""

    id: str = field(default_factory=lambda: uuid4().hex)
    text: str = "Caption"
    start_ms: int = 0
    end_ms: int = 3000
    font: str = "Arial"
    size_px: int = 48
    color: str = "#ffffff"
    outline_color: str = "#000000"
    outline_px: int = 3
    x_px: int = 0
    y_px: int = 0

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass
class Track:
    id: str = field(default_factory=lambda: uuid4().hex)
    type: TrackType = TrackType.VIDEO
    clips: list[Clip] = field(default_factory=list)
    muted: bool = False
    locked: bool = False


@dataclass
class Timeline:
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    master_volume: float = 1.0
    tracks: list[Track] = field(default_factory=lambda: [Track()])
    texts: list[TextOverlay] = field(default_factory=list)


@dataclass
class MediaAsset:
    id: str = field(default_factory=lambda: uuid4().hex)
    path: str = ""
    container: str = ""
    video_codec: str = ""
    audio_codec: str = ""
    pixel_format: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    audio_sample_rate: int = 0
    audio_channels: int = 0
    duration_ms: int = 0
    has_audio: bool = False
    has_video: bool = False


@dataclass
class ExportDefaults:
    codec: VideoCodec = VideoCodec.H264
    hardware_backend: HardwareBackend = HardwareBackend.AUTO
    fps: float = 60.0
    bitrate_kbps: int = 12000
    audio_bitrate_kbps: int = 192
    prefer_speed_over_quality: bool = True
    allow_stream_copy: bool = True


@dataclass
class ExportProfile:
    output_path: str = ""
    codec: VideoCodec = VideoCodec.H264
    hardware_backend: HardwareBackend = HardwareBackend.AUTO
    width: int = 1920
    height: int = 1080
    fps: float = 60.0
    bitrate_kbps: int = 12000
    audio_bitrate_kbps: int = 192
    prefer_speed_over_quality: bool = True
    allow_stream_copy: bool = True
    master_volume: float = 1.0


@dataclass
class Project:
    id: str = field(default_factory=lambda: uuid4().hex)
    name: str = "Untitled"
    timeline: Timeline = field(default_factory=Timeline)
    export_defaults: ExportDefaults = field(default_factory=ExportDefaults)
    media: list[MediaAsset] = field(default_factory=list)


@dataclass
class RenderPlan:
    route: RenderRoute = RenderRoute.REENCODE
    backend: HardwareBackend = HardwareBackend.CPU
    reason: str = ""
    clip: Clip = field(default_factory=Clip)
    asset: MediaAsset = field(default_factory=MediaAsset)
    clips: list[Clip] = field(default_factory=list)
    assets: list[MediaAsset] = field(default_factory=list)
    texts: list[TextOverlay] = field(default_factory=list)
