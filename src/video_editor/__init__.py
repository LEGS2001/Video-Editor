"""Python implementation of the FFmpeg-first video editor."""

from .ffmpeg import FfmpegCommand, FfmpegCommandBuilder, FfmpegExecutor
from .hardware import HardwareCapabilities, HardwareDetector
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
from .project_io import (
    ProjectJson,
    hardware_backend_from_string,
    hardware_backend_to_string,
    video_codec_from_string,
    video_codec_to_string,
)
from .render_planner import RenderPlanner

__all__ = [
    "__version__",
    "Clip",
    "Crop",
    "ExportDefaults",
    "ExportProfile",
    "FfmpegCommand",
    "FfmpegCommandBuilder",
    "FfmpegExecutor",
    "HardwareBackend",
    "HardwareCapabilities",
    "HardwareDetector",
    "MediaAsset",
    "Project",
    "ProjectJson",
    "RenderPlan",
    "RenderPlanner",
    "RenderRoute",
    "Timeline",
    "Track",
    "TrackType",
    "Transform",
    "VideoCodec",
    "hardware_backend_from_string",
    "hardware_backend_to_string",
    "video_codec_from_string",
    "video_codec_to_string",
]

__version__ = "0.2.0"
