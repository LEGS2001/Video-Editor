"""Python implementation of the FFmpeg-first video editor."""

from .ffmpeg import FfmpegCommand
from .hardware import HardwareCapabilities
from .models import (
    Clip,
    Crop,
    ExportDefaults,
    ExportProfile,
    HardwareBackend,
    MediaAsset,
    Project,
    RenderPlan,
    RenderRoute,
    Timeline,
    Track,
    TrackType,
    Transform,
    VideoCodec,
)

__all__ = [
    "__version__",
    "Clip",
    "Crop",
    "ExportDefaults",
    "ExportProfile",
    "FfmpegCommand",
    "HardwareBackend",
    "HardwareCapabilities",
    "MediaAsset",
    "Project",
    "RenderPlan",
    "RenderRoute",
    "Timeline",
    "Track",
    "TrackType",
    "Transform",
    "VideoCodec",
]

__version__ = "0.2.0"
