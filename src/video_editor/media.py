from __future__ import annotations

import json
import subprocess
import time
from hashlib import sha1
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .models import MediaAsset


def _run_tool(
    arguments: list[str], timeout_s: float, cancelled: Callable[[], bool] | None
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout_s
    while True:
        if cancelled and cancelled():
            process.terminate()
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise RuntimeError(f"{arguments[0]} cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(arguments, timeout_s, output=stdout, stderr=stderr)
        try:
            stdout, stderr = process.communicate(timeout=min(0.1, remaining))
            return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            continue


def _ratio_to_float(value: str) -> float:
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        left, right = value.split("/", 1)
        denominator = float(right)
        return 0.0 if denominator == 0 else float(left) / denominator
    return float(value)


def probe_media(
    path: str | Path,
    *,
    timeout_s: float = 15.0,
    cancelled: Callable[[], bool] | None = None,
) -> MediaAsset:
    if cancelled and cancelled():
        raise RuntimeError("Media probe cancelled")
    file_path = str(Path(path).expanduser())
    try:
        result = _run_tool(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                file_path,
            ],
            timeout_s,
            cancelled,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffprobe timed out for {file_path}") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffprobe failed for {file_path}")

    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    duration = float(video.get("duration") or payload.get("format", {}).get("duration") or 0.0)

    return MediaAsset(
        id=uuid4().hex,
        path=file_path,
        container=payload.get("format", {}).get("format_name", "").split(",", 1)[0],
        video_codec=video.get("codec_name", ""),
        audio_codec=audio.get("codec_name", ""),
        pixel_format=video.get("pix_fmt", ""),
        width=int(video.get("width", 0) or 0),
        height=int(video.get("height", 0) or 0),
        fps=_ratio_to_float(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/0"),
        audio_sample_rate=int(audio.get("sample_rate", 0) or 0),
        audio_channels=int(audio.get("channels", 0) or 0),
        duration_ms=int(duration * 1000),
        has_audio=bool(audio),
        has_video=bool(video),
    )


def thumbnail_path(asset: MediaAsset, cache_dir: str | Path) -> Path:
    # Keyed by source path (not asset id) so re-importing the same file or
    # reopening a project reuses the cached thumbnail instead of regenerating.
    source = Path(asset.path)
    try:
        stat = source.stat()
        identity = f"{asset.path}\0{stat.st_size}\0{stat.st_mtime_ns}"
    except OSError:
        identity = asset.path
    key = sha1(identity.encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / f"{key}.jpg"


def create_thumbnail(
    asset: MediaAsset,
    cache_dir: str | Path,
    *,
    timeout_s: float = 30.0,
    cancelled: Callable[[], bool] | None = None,
) -> Path | None:
    if not asset.has_video:
        return None
    if cancelled and cancelled():
        return None
    target = thumbnail_path(asset, cache_dir)
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    timestamp = max(0.0, min((asset.duration_ms / 1000.0) * 0.1, 5.0))
    try:
        result = _run_tool(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                asset.path,
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-1",
                str(target),
            ],
            timeout_s,
            cancelled,
        )
    except subprocess.TimeoutExpired:
        target.unlink(missing_ok=True)
        return None
    if result.returncode == 0 and target.exists():
        return target
    target.unlink(missing_ok=True)
    return None
