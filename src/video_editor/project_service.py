from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from uuid import uuid4

from .models import (
    Clip,
    Crop,
    MediaAsset,
    Project,
    TextOverlay,
    Timeline,
    Track,
    TrackType,
    Transform,
    VideoCodec,
)


class ProjectService:
    MAX_UNDO = 30

    def __init__(self, project: Project | None = None) -> None:
        self.project = project or Project()
        self.undo_stack: list[Project] = []
        self.redo_stack: list[Project] = []
        self._asset_index: dict[str, MediaAsset] = {}
        self._clip_index: dict[str, Clip] = {}
        self._clip_positions: dict[str, int] = {}
        self._video_starts: list[int] = []
        self._video_starts_sorted = True
        self._timeline_duration_ms = 0
        self._rebuild_indexes()

    def snapshot(self) -> None:
        self.undo_stack.append(deepcopy(self.project))
        del self.undo_stack[: -self.MAX_UNDO]
        self.redo_stack.clear()

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(deepcopy(self.project))
        self.project = self.undo_stack.pop()
        self._rebuild_indexes()
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(deepcopy(self.project))
        self.project = self.redo_stack.pop()
        self._rebuild_indexes()
        return True

    def set_project(self, project: Project) -> None:
        self.project = project
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._ensure_video_track()
        self._rebuild_indexes()

    def add_media(self, asset: MediaAsset) -> None:
        self.snapshot()
        self.project.media.append(asset)
        self._asset_index[asset.id] = asset

    def add_media_batch(self, assets: list[MediaAsset]) -> None:
        if not assets:
            return
        self.snapshot()
        self.project.media.extend(assets)
        self._rebuild_indexes()

    def add_asset_to_timeline(self, asset_id: str) -> Clip:
        asset = self.asset_by_id(asset_id)
        if asset is None:
            raise ValueError("Media asset not found")
        self.snapshot()
        track = self._ensure_video_track()
        has_video = any(
            clip.asset_id in self._asset_index and self._asset_index[clip.asset_id].has_video
            for clip in track.clips
        )
        if asset.has_video and not has_video:
            if asset.fps > 0:
                self.project.export_defaults.fps = asset.fps
            codec = asset.video_codec.lower()
            if codec in {"h265", "hevc", "hev1", "hvc1"}:
                self.project.export_defaults.codec = VideoCodec.H265
            elif codec in {"av1", "av01"}:
                self.project.export_defaults.codec = VideoCodec.AV1
            elif codec in {"h264", "avc", "avc1"}:
                self.project.export_defaults.codec = VideoCodec.H264
        start = self.timeline_duration_ms()
        clip = Clip(asset_id=asset.id, source_in_ms=0, source_out_ms=asset.duration_ms, timeline_start_ms=start)
        track.clips.append(clip)
        self._rebuild_indexes()
        return clip

    def remove_clip(self, clip_id: str) -> bool:
        track = self._ensure_video_track()
        for index, clip in enumerate(track.clips):
            if clip.id == clip_id:
                self.snapshot()
                del track.clips[index]
                self.normalize_timeline()
                return True
        return False

    def split_clip(self, clip_id: str, timeline_ms: int) -> bool:
        track = self._ensure_video_track()
        for index, clip in enumerate(track.clips):
            end = clip.timeline_start_ms + clip.duration_ms
            if clip.id != clip_id or not (clip.timeline_start_ms < timeline_ms < end):
                continue
            self.snapshot()
            offset = int(round((timeline_ms - clip.timeline_start_ms) * clip.speed))
            right = deepcopy(clip)
            right.id = uuid4().hex
            right.source_in_ms = clip.source_in_ms + offset
            right.timeline_start_ms = timeline_ms
            clip.source_out_ms = clip.source_in_ms + offset
            track.clips.insert(index + 1, right)
            self.normalize_timeline()
            return True
        return False

    def move_clip(self, clip_id: str, new_timeline_start_ms: int) -> bool:
        track = self._ensure_video_track()
        target = next((clip for clip in track.clips if clip.id == clip_id), None)
        if target is None:
            return False
        self.snapshot()
        target.timeline_start_ms = max(0, new_timeline_start_ms)
        self.normalize_timeline()
        return True

    def trim_clip(self, clip_id: str, source_in_ms: int | None = None, source_out_ms: int | None = None) -> bool:
        clip = self.clip_by_id(clip_id)
        if clip is None:
            return False
        new_in = clip.source_in_ms if source_in_ms is None else max(0, source_in_ms)
        new_out = clip.source_out_ms if source_out_ms is None else max(0, source_out_ms)
        if new_out <= new_in:
            return False
        self.snapshot()
        clip.source_in_ms = new_in
        clip.source_out_ms = new_out
        self.normalize_timeline()
        return True

    def set_clip_speed(self, clip_id: str, speed: float) -> bool:
        clip = self.clip_by_id(clip_id)
        speed = min(100.0, max(0.25, float(speed)))
        if clip is None or abs(clip.speed - speed) <= 1e-6:
            return False
        self.snapshot()
        clip.speed = speed
        self.normalize_timeline()
        return True

    def add_text(self, start_ms: int, end_ms: int) -> TextOverlay:
        timeline = self.project.timeline
        self.snapshot()
        overlay = TextOverlay(
            start_ms=max(0, start_ms),
            end_ms=max(start_ms + 1, end_ms),
            x_px=timeline.width // 10,
            y_px=timeline.height - timeline.height // 5,
        )
        timeline.texts.append(overlay)
        return overlay

    def remove_text(self, text_id: str) -> bool:
        texts = self.project.timeline.texts
        for index, overlay in enumerate(texts):
            if overlay.id == text_id:
                self.snapshot()
                del texts[index]
                return True
        return False

    def update_text(self, text_id: str, **fields) -> bool:
        overlay = self.text_by_id(text_id)
        if overlay is None:
            return False
        changed = {
            name: value for name, value in fields.items() if getattr(overlay, name) != value
        }
        if not changed:
            return False
        self.snapshot()
        for name, value in changed.items():
            setattr(overlay, name, value)
        return True

    def set_text_range(self, text_id: str, start_ms: int, end_ms: int) -> bool:
        start_ms = max(0, start_ms)
        if end_ms <= start_ms:
            return False
        return self.update_text(text_id, start_ms=start_ms, end_ms=end_ms)

    def text_by_id(self, text_id: str) -> TextOverlay | None:
        return next((text for text in self.project.timeline.texts if text.id == text_id), None)

    @property
    def texts(self) -> list[TextOverlay]:
        return self.project.timeline.texts

    def texts_at(self, timeline_ms: int) -> list[TextOverlay]:
        return [
            text for text in self.project.timeline.texts
            if text.start_ms <= timeline_ms < text.end_ms
        ]

    def reset_timeline_properties(self) -> bool:
        timeline, defaults = self.project.timeline, Timeline()
        current = (timeline.width, timeline.height, timeline.fps, timeline.master_volume)
        reset = (defaults.width, defaults.height, defaults.fps, defaults.master_volume)
        if current == reset:
            return False
        self.snapshot()
        timeline.width, timeline.height, timeline.fps, timeline.master_volume = reset
        self.normalize_timeline()
        return True

    def reset_clip_properties(self, clip_id: str) -> bool:
        clip = self.clip_by_id(clip_id)
        if clip is None:
            return False
        current = (clip.transform, clip.crop, clip.opacity, clip.volume, clip.speed)
        reset = (Transform(), Crop(), 1.0, 1.0, 1.0)
        if current == reset:
            return False
        self.snapshot()
        clip.transform, clip.crop = Transform(), Crop()
        clip.opacity = clip.volume = clip.speed = 1.0
        self.normalize_timeline()
        return True

    @staticmethod
    def validate_relink(original: MediaAsset, replacement: MediaAsset) -> str:
        if original.has_video != replacement.has_video or original.has_audio != replacement.has_audio:
            return "Replacement media type does not match"
        if original.has_video and (original.width, original.height) != (replacement.width, replacement.height):
            return "Replacement video dimensions do not match"
        tolerance = max(1000, int(round(original.duration_ms * 0.02)))
        if original.duration_ms > 0 and abs(original.duration_ms - replacement.duration_ms) > tolerance:
            return "Replacement media duration differs by more than the allowed tolerance"
        return ""

    def relink_assets(self, replacements: dict[str, MediaAsset]) -> None:
        resolved: dict[str, MediaAsset] = {}
        for asset_id, replacement in replacements.items():
            original = self.asset_by_id(asset_id)
            if original is None:
                raise ValueError(f"Media asset not found: {asset_id}")
            error = self.validate_relink(original, replacement)
            if error:
                raise ValueError(f"{error}: {original.path}")
            updated = deepcopy(replacement)
            updated.id = original.id
            resolved[asset_id] = updated
        if not resolved:
            return
        self.snapshot()
        self.project.media = [resolved.get(asset.id, asset) for asset in self.project.media]
        self._rebuild_indexes()

    def relink_asset(self, asset_id: str, replacement: MediaAsset) -> None:
        self.relink_assets({asset_id: replacement})

    def normalize_timeline(self) -> None:
        cursor = 0
        track = self._ensure_video_track()
        track.clips.sort(key=lambda item: item.timeline_start_ms)
        for clip in track.clips:
            clip.timeline_start_ms = cursor
            cursor += clip.duration_ms
        self._rebuild_indexes()

    def timeline_duration_ms(self) -> int:
        self._ensure_video_track()
        return self._timeline_duration_ms

    def clip_at_timeline(self, timeline_ms: int) -> Clip | None:
        clips = self._ensure_video_track().clips
        if self._video_starts_sorted and clips:
            index = bisect_right(self._video_starts, timeline_ms) - 1
            if index >= 0 and timeline_ms < clips[index].timeline_start_ms + clips[index].duration_ms:
                return clips[index]
        else:
            for clip in clips:
                if clip.timeline_start_ms <= timeline_ms < clip.timeline_start_ms + clip.duration_ms:
                    return clip
        return clips[-1] if clips and timeline_ms >= self.timeline_duration_ms() else None

    def next_clip_after(self, clip_id: str) -> Clip | None:
        clips = self._ensure_video_track().clips
        index = self._clip_positions.get(clip_id)
        return clips[index + 1] if index is not None and index + 1 < len(clips) else None

    def clip_by_id(self, clip_id: str) -> Clip | None:
        return self._clip_index.get(clip_id)

    def asset_by_id(self, asset_id: str) -> MediaAsset | None:
        return self._asset_index.get(asset_id)

    def visible_video_clips(self, start_ms: int, end_ms: int) -> list[Clip]:
        clips = self.video_track.clips
        if not clips or end_ms < start_ms:
            return []
        if not self._video_starts_sorted:
            return [
                clip for clip in clips
                if clip.timeline_start_ms <= end_ms and clip.timeline_start_ms + clip.duration_ms >= start_ms
            ]
        index = max(0, bisect_right(self._video_starts, start_ms) - 1)
        visible: list[Clip] = []
        for clip in clips[index:]:
            if clip.timeline_start_ms > end_ms:
                break
            if clip.timeline_start_ms + clip.duration_ms >= start_ms:
                visible.append(clip)
        return visible

    @property
    def video_track(self) -> Track:
        return self._ensure_video_track()

    def _ensure_video_track(self) -> Track:
        for track in self.project.timeline.tracks:
            if track.type == TrackType.VIDEO:
                return track
        track = Track(type=TrackType.VIDEO)
        self.project.timeline.tracks.insert(0, track)
        self._rebuild_indexes()
        return track

    def _rebuild_indexes(self) -> None:
        for track in self.project.timeline.tracks:
            for clip in track.clips:
                clip.set_timeline_fps(self.project.timeline.fps)
        self._asset_index = {asset.id: asset for asset in self.project.media}
        self._clip_index = {
            clip.id: clip for track in self.project.timeline.tracks for clip in track.clips
        }
        video = next((track for track in self.project.timeline.tracks if track.type == TrackType.VIDEO), None)
        clips = video.clips if video is not None else []
        self._clip_positions = {clip.id: index for index, clip in enumerate(clips)}
        self._video_starts = [clip.timeline_start_ms for clip in clips]
        self._video_starts_sorted = all(
            left <= right for left, right in zip(self._video_starts, self._video_starts[1:])
        )
        self._timeline_duration_ms = max(
            (clip.timeline_start_ms + clip.duration_ms for clip in clips), default=0
        )
