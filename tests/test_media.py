import subprocess
import sys

import pytest

from video_editor import media
from video_editor.media import create_thumbnail, probe_media, thumbnail_path
from video_editor.models import MediaAsset


def test_probe_media_reports_timeout(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("ffprobe", 0.01)

    monkeypatch.setattr(media, "_run_tool", timeout)

    with pytest.raises(RuntimeError, match="timed out"):
        probe_media("slow.mp4", timeout_s=0.01)


def test_thumbnail_timeout_removes_partial_file(monkeypatch, tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"source")
    asset = MediaAsset(path=str(source), width=10, height=10, duration_ms=1000, has_video=True)
    target = thumbnail_path(asset, tmp_path / "cache")

    def timeout(*_args, **_kwargs):
        target.write_bytes(b"partial")
        raise subprocess.TimeoutExpired("ffmpeg", 0.01)

    monkeypatch.setattr(media, "_run_tool", timeout)

    assert create_thumbnail(asset, tmp_path / "cache", timeout_s=0.01) is None
    assert not target.exists()


def test_media_tool_honors_cancellation():
    with pytest.raises(RuntimeError, match="cancelled"):
        media._run_tool(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_s=5,
            cancelled=lambda: True,
        )
