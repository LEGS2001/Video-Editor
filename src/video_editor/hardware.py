from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from shutil import which

from .models import HardwareBackend, VideoCodec


@dataclass
class HardwareCapabilities:
    encoders: list[str] = field(default_factory=list)
    has_vaapi: bool = False
    has_nvenc: bool = False
    has_amf: bool = False
    has_qsv: bool = False
    vaapi_device: str = "/dev/dri/renderD128"

    def supports_encoder(self, encoder: str) -> bool:
        return encoder in self.encoders

    def supports(self, backend: HardwareBackend, codec: VideoCodec = VideoCodec.H264) -> bool:
        """Return whether the requested backend/codec pair is usable.

        Backend flags include runtime checks (for example the NVIDIA driver),
        while the encoder list proves that this particular codec is present.
        """
        if backend == HardwareBackend.AUTO:
            return any(self.supports(item, codec) for item in _AUTO_BACKEND_ORDER)
        runtime_available = {
            HardwareBackend.VAAPI: self.has_vaapi,
            HardwareBackend.NVENC: self.has_nvenc,
            HardwareBackend.AMF: self.has_amf,
            HardwareBackend.QSV: self.has_qsv,
            HardwareBackend.CPU: True,
        }[backend]
        return runtime_available and self.supports_encoder(encoder_for(codec, backend))


_AUTO_BACKEND_ORDER = (
    HardwareBackend.VAAPI,
    HardwareBackend.NVENC,
    HardwareBackend.AMF,
    HardwareBackend.QSV,
)


def _first_vaapi_render_device(dev_dri: Path = Path("/dev/dri")) -> str:
    if not sys.platform.startswith("linux"):
        return ""
    dri = dev_dri
    if not dri.exists():
        return ""
    devices = sorted(dri.glob("renderD*"))
    return str(devices[0]) if devices else ""


def _has_nvidia_runtime(proc_nvidia: Path = Path("/proc/driver/nvidia/version")) -> bool:
    if sys.platform.startswith("linux") and (
        proc_nvidia.exists() or Path("/dev/nvidia0").exists() or Path("/dev/nvidiactl").exists()
    ):
        return True
    if not which("nvidia-smi"):
        return False
    try:
        return subprocess.run(["nvidia-smi", "-L"], timeout=1, check=False, capture_output=True).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect_hardware(
    *,
    ffmpeg_program: str = "ffmpeg",
    dev_dri: Path = Path("/dev/dri"),
    proc_nvidia: Path = Path("/proc/driver/nvidia/version"),
) -> HardwareCapabilities:
    capabilities = HardwareCapabilities()
    vaapi_device = _first_vaapi_render_device(dev_dri)
    if vaapi_device:
        capabilities.vaapi_device = vaapi_device
    if not which(ffmpeg_program):
        return capabilities

    try:
        result = subprocess.run(
            [ffmpeg_program, "-hide_banner", "-encoders"],
            timeout=5,
            check=False,
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return capabilities

    output = result.stdout + result.stderr
    wanted = [
        "libx264",
        "libx265",
        "libsvtav1",
        "h264_vaapi",
        "hevc_vaapi",
        "av1_vaapi",
        "h264_nvenc",
        "hevc_nvenc",
        "av1_nvenc",
        "h264_amf",
        "hevc_amf",
        "av1_amf",
        "h264_qsv",
        "hevc_qsv",
        "av1_qsv",
    ]
    capabilities.encoders = [encoder for encoder in wanted if encoder in output]
    capabilities.has_vaapi = (
        sys.platform.startswith("linux")
        and any(item.endswith("_vaapi") for item in capabilities.encoders)
        and Path(capabilities.vaapi_device).exists()
    )
    capabilities.has_nvenc = (
        any(item.endswith("_nvenc") for item in capabilities.encoders)
        and _has_nvidia_runtime(proc_nvidia)
    )
    capabilities.has_amf = any(item.endswith("_amf") for item in capabilities.encoders)
    capabilities.has_qsv = any(item.endswith("_qsv") for item in capabilities.encoders)
    return capabilities


@lru_cache(maxsize=1)
def detect_hardware_cached() -> HardwareCapabilities:
    """Hardware doesn't change mid-session; probing ffmpeg's encoder list on
    every export click would stall the UI for no reason."""
    return detect_hardware()


def supported_backends_for_platform() -> list[HardwareBackend]:
    backends = [HardwareBackend.AUTO]
    if sys.platform.startswith("linux"):
        backends.append(HardwareBackend.VAAPI)
    backends.extend(
        [
            HardwareBackend.NVENC,
            HardwareBackend.AMF,
            HardwareBackend.QSV,
            HardwareBackend.CPU,
        ]
    )
    return backends


def _backend_available(
    capabilities: HardwareCapabilities,
    backend: HardwareBackend,
    codec: VideoCodec = VideoCodec.H264,
) -> bool:
    return capabilities.supports(backend, codec)


def choose_backend(
    capabilities: HardwareCapabilities,
    requested: HardwareBackend,
    codec: VideoCodec = VideoCodec.H264,
) -> HardwareBackend:
    if requested != HardwareBackend.AUTO:
        return requested if _backend_available(capabilities, requested, codec) else HardwareBackend.CPU
    return next(
        (backend for backend in _AUTO_BACKEND_ORDER if _backend_available(capabilities, backend, codec)),
        HardwareBackend.CPU,
    )


def encoder_for(codec: VideoCodec, backend: HardwareBackend) -> str:
    table = {
        HardwareBackend.VAAPI: {VideoCodec.H264: "h264_vaapi", VideoCodec.H265: "hevc_vaapi", VideoCodec.AV1: "av1_vaapi"},
        HardwareBackend.NVENC: {VideoCodec.H264: "h264_nvenc", VideoCodec.H265: "hevc_nvenc", VideoCodec.AV1: "av1_nvenc"},
        HardwareBackend.AMF: {VideoCodec.H264: "h264_amf", VideoCodec.H265: "hevc_amf", VideoCodec.AV1: "av1_amf"},
        HardwareBackend.QSV: {VideoCodec.H264: "h264_qsv", VideoCodec.H265: "hevc_qsv", VideoCodec.AV1: "av1_qsv"},
        HardwareBackend.CPU: {VideoCodec.H264: "libx264", VideoCodec.H265: "libx265", VideoCodec.AV1: "libsvtav1"},
        HardwareBackend.AUTO: {VideoCodec.H264: "libx264", VideoCodec.H265: "libx265", VideoCodec.AV1: "libsvtav1"},
    }
    return table[backend][codec]


class HardwareDetector:
    detect = staticmethod(detect_hardware)
    choose_backend = staticmethod(choose_backend)
    encoder_for = staticmethod(encoder_for)
    supported_backends_for_platform = staticmethod(supported_backends_for_platform)
    first_vaapi_render_device = staticmethod(_first_vaapi_render_device)
    has_nvidia_runtime = staticmethod(_has_nvidia_runtime)
