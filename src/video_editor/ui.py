from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import subprocess
import traceback
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QElapsedTimer,
    QEvent,
    QPointF,
    QRectF,
    QSettings,
    QSize,
    QSizeF,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .ffmpeg import FONT_FILES, build_ffmpeg_command, build_smart_render_commands
from .hardware import choose_backend, detect_hardware_cached, supported_backends_for_platform
from .media import create_thumbnail, probe_media, thumbnail_path
from .models import ExportProfile, HardwareBackend, Project, RenderRoute, Timeline, Track, TrackType, VideoCodec
from .project_io import load_project, project_to_dict, save_project
from .project_service import ProjectService
from .recovery import RecoveryService
from .render_planner import build_render_plan, plan_smart_segments

# ---------------------------------------------------------------------------
# Palette — a calm, neutral dark workspace with a single restrained accent.
# Kept intentionally muted so the editor reads like a tool, not a showcase.
# ---------------------------------------------------------------------------
APP_BG = "#1a1c20"
PANEL_BG = "#222428"
CARD_BG = "#262a30"
INPUT_BG = "#2c3036"
INPUT_HOVER = "#323741"
BORDER = "#343941"
BORDER_STRONG = "#434954"
TEXT = "#e7e9ec"
TEXT_MUTED = "#969ca6"
TEXT_FAINT = "#a0a6af"
ACCENT = "#416db3"
ACCENT_HOVER = "#4776bd"
ACCENT_PRESSED = "#355f9f"
PLAYHEAD = "#efa544"
TRACK_BG = "#17181c"
CLIP_BG = "#2f3540"
CLIP_BORDER = "#3c424e"
ICON_COLOR = "#c7ccd4"


# ---------------------------------------------------------------------------
# Hand-drawn line icons (24px grid, 2px stroke). Crisp at any size, themeable,
# and consistent — no platform emoji or stock pixmaps.
# ---------------------------------------------------------------------------
_SVG_ICONS = {
    "new": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
           '<path d="M14 3v5h5"/>',
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>'
            '<polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
    "add": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "undo": '<path d="M9 14 4 9l5-5"/>'
            '<path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    "trash": '<polyline points="3 6 5 6 21 6"/>'
             '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
             '<line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
    "play": '<polygon points="6 4 20 12 6 20 6 4"/>',
    "pause": '<line x1="9" y1="5" x2="9" y2="19"/><line x1="15" y1="5" x2="15" y2="19"/>',
    "arrow_down": '<line x1="12" y1="4" x2="12" y2="16"/><polyline points="6 10 12 16 18 10"/>',
    "export": '<path d="M12 15V3"/><polyline points="7 8 12 3 17 8"/>'
              '<path d="M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"/>',
    "volume": '<polygon points="3 9 7 9 12 4 12 20 7 15 3 15 3 9"/>'
              '<path d="M16 8a5 5 0 0 1 0 8"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
}


_ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# Output presets for the two publishing formats.
EXPORT_MODES = {
    "youtube": {"label": "YouTube", "width": 1920, "height": 1080, "icon": "youtube.png"},
    "tiktok": {"label": "TikTok", "width": 1080, "height": 1920, "icon": "tiktok.png"},
}


def _tinted_pixmap(path: Path, color: str) -> QPixmap:
    """Recolor a monochrome (alpha-masked) PNG so it reads on the dark UI."""
    source = QPixmap(str(path))
    if source.isNull():
        return source
    out = QPixmap(source.size())
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(out.rect(), QColor(color))
    painter.end()
    return out


def _brand_icon(filename: str, off_color: str, on_color: str) -> QIcon:
    """Two-state icon: muted when the mode is inactive, bright when selected."""
    path = _ASSETS_DIR / filename
    icon = QIcon()
    icon.addPixmap(_tinted_pixmap(path, off_color), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_tinted_pixmap(path, on_color), QIcon.Mode.Normal, QIcon.State.On)
    return icon


def _svg_icon(name: str, color: str = ICON_COLOR, size: int = 40) -> QIcon:
    """Render a named line-icon to a crisp QIcon."""
    body = _SVG_ICONS[name]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )
    if name == "play":
        # The play triangle reads better solid than stroked.
        svg = svg.replace('fill="none"', f'fill="{color}"')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class _CanvasView(QGraphicsView):
    """Scrollbar-less graphics view that always shows the whole canvas."""

    def __init__(self, scene: QGraphicsScene) -> None:
        super().__init__(scene)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QColor("#050608"))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)

    def refit(self) -> None:
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refit()


def caption_font(family: str, size_px: int) -> QFont:
    """The Qt font for the file drawtext will load. "Arial Bold" is arialbd.ttf,
    which Qt resolves through the bold weight rather than the family name."""
    font = QFont(family.removesuffix(" Bold"))
    font.setBold(family.endswith(" Bold"))
    font.setPixelSize(max(1, size_px))
    return font


class _PreviewTextItem(QGraphicsPathItem):
    """Draggable caption proxy. Scene coordinates are canvas pixels, so the
    position it reports is exactly what drawtext gets on export.

    Glyphs are carried as a path rather than as text so the preview can match
    drawtext on the two things a text item gets wrong: drawtext anchors y on the
    top of the rendered ink (not on the font ascent), and it grows the border
    outward from the glyph instead of stroking over it."""

    #: Centre-snap tolerance, in canvas pixels.
    SNAP_PX = 20

    def __init__(self) -> None:
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(10)
        self.setVisible(False)
        self.moved = None
        self.snapped = None
        self.canvas_width = 1920
        self.canvas_height = 1080
        self._dragging = False

    def set_caption(self, text: str, font: QFont) -> None:
        """Lay the glyphs out with their ink top-left at the item origin, which
        is where drawtext puts x/y."""
        path = QPainterPath()
        path.addText(0, 0, font, text)
        path.translate(0, -path.boundingRect().top())
        self.setPath(path)

    def centre_offset(self) -> QPointF:
        """Item-local centre of the ink — what the eye centres on, and what the
        centre snap lines up with the canvas middle."""
        return self.path().boundingRect().center()

    def paint(self, painter, option, widget=None) -> None:
        # Stroke first, fill over it: drawtext's border only ever grows outward,
        # so a centred stroke of twice the width leaves exactly borderw showing.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.pen().style() != Qt.PenStyle.NoPen:
            painter.setPen(self.pen())
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.path())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.brush())
        painter.drawPath(self.path())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self._dragging:
            centre = self.centre_offset()
            snap_x = abs(value.x() + centre.x() - self.canvas_width / 2) <= self.SNAP_PX
            snap_y = abs(value.y() + centre.y() - self.canvas_height / 2) <= self.SNAP_PX
            value = QPointF(
                self.canvas_width / 2 - centre.x() if snap_x else value.x(),
                self.canvas_height / 2 - centre.y() if snap_y else value.y(),
            )
            if self.snapped is not None:
                self.snapped(snap_x, snap_y)
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:
        self._dragging = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._dragging = False
        if self.snapped is not None:
            self.snapped(False, False)
        if self.moved is not None:
            self.moved(int(round(self.pos().x())), int(round(self.pos().y())))


class PreviewArea(QWidget):
    """Holds the video preview inside a canvas-shaped frame whose aspect ratio
    follows the selected export mode (16:9 for YouTube, 9:16 for TikTok).

    The video is drawn through a QGraphicsScene sized in canvas pixels, with
    the video item inside a clipping container. Crop, scale, and position are
    applied to that container with the same math the ffmpeg filters use, so
    the preview mirrors clip transforms live, exactly as they will export."""

    def __init__(self) -> None:
        super().__init__()
        self.canvas_width = 1920
        self.canvas_height = 1080
        self._ratio = 16 / 9
        self.frame = QFrame(self)
        self.frame.setObjectName("previewFrame")
        inner = QVBoxLayout(self.frame)
        inner.setContentsMargins(1, 1, 1, 1)

        self.scene = QGraphicsScene(self)
        self.clip_frame = QGraphicsRectItem()
        self.clip_frame.setPen(QPen(Qt.PenStyle.NoPen))
        self.clip_frame.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)
        self.video_item = QGraphicsVideoItem(self.clip_frame)
        self.video_item.setAspectRatioMode(Qt.AspectRatioMode.IgnoreAspectRatio)
        self.scene.addItem(self.clip_frame)
        # A scene-level sibling, not a child of clip_frame: captions must not
        # inherit the clip's crop, scale or rotation.
        self.text_item = _PreviewTextItem()
        self.scene.addItem(self.text_item)
        self.text_item.snapped = self._show_centre_guides
        self.centre_guides = []
        for _ in range(2):
            guide = QGraphicsLineItem()
            guide.setPen(QPen(QColor("#4cc2ff"), 2, Qt.PenStyle.DashLine))
            guide.setZValue(11)
            guide.setVisible(False)
            self.scene.addItem(guide)
            self.centre_guides.append(guide)
        self.view = _CanvasView(self.scene)
        self.view.setObjectName("previewView")
        inner.addWidget(self.view)
        self.setMinimumHeight(280)
        self.set_canvas(1920, 1080)

    def set_canvas(self, width: int, height: int) -> None:
        self.canvas_width = max(1, width)
        self.canvas_height = max(1, height)
        self._ratio = self.canvas_width / self.canvas_height
        self.scene.setSceneRect(0, 0, self.canvas_width, self.canvas_height)
        self.text_item.canvas_width = self.canvas_width
        self.text_item.canvas_height = self.canvas_height
        self.centre_guides[0].setLine(self.canvas_width / 2, 0, self.canvas_width / 2, self.canvas_height)
        self.centre_guides[1].setLine(0, self.canvas_height / 2, self.canvas_width, self.canvas_height / 2)
        self.view.refit()
        self._reflow()

    def _show_centre_guides(self, x_snapped: bool, y_snapped: bool) -> None:
        self.centre_guides[0].setVisible(x_snapped)
        self.centre_guides[1].setVisible(y_snapped)

    def apply_clip(self, asset, clip) -> None:
        """Mirror the clip's crop/scale/position on the canvas; a bare asset
        (or a clip without transforms) is aspect-fit and centered, matching
        the letterboxed export path."""
        width = asset.width if asset is not None and asset.width > 0 else self.canvas_width
        height = asset.height if asset is not None and asset.height > 0 else self.canvas_height
        crop = clip.crop if clip is not None and clip.crop.enabled else None
        crop_left = crop.left if crop else 0
        crop_top = crop.top if crop else 0
        visible_w = max(1, width - crop_left - (crop.right if crop else 0))
        visible_h = max(1, height - crop_top - (crop.bottom if crop else 0))

        self.video_item.setSize(QSizeF(width, height))
        self.video_item.setPos(-crop_left, -crop_top)
        self.clip_frame.setRect(0, 0, visible_w, visible_h)
        self.clip_frame.setTransformOriginPoint(visible_w / 2, visible_h / 2)

        if clip is not None and clip.has_canvas_transform:
            scale_x = max(0.01, clip.transform.scale_x)
            scale_y = max(0.01, clip.transform.scale_y)
            offset = QPointF(clip.transform.x, clip.transform.y)
        else:
            fit = min(self.canvas_width / visible_w, self.canvas_height / visible_h)
            scale_x = scale_y = fit
            offset = QPointF(
                (self.canvas_width - visible_w * fit) / 2,
                (self.canvas_height - visible_h * fit) / 2,
            )
        self.clip_frame.setTransform(QTransform.fromScale(scale_x, scale_y))
        self.clip_frame.setPos(offset)
        self.clip_frame.setRotation(clip.transform.rotation_deg if clip is not None else 0.0)
        self.clip_frame.setOpacity(max(0.0, min(1.0, clip.opacity if clip is not None else 1.0)))

    def set_text(self, overlay) -> None:
        """Show one caption on the canvas, styled to match the drawtext export."""
        if overlay is None:
            self.text_item.setVisible(False)
            self._show_centre_guides(False, False)
            return
        self.text_item.set_caption(overlay.text, caption_font(overlay.font, overlay.size_px))
        self.text_item.setBrush(QColor(overlay.color))
        self.text_item.setPen(
            # Twice the border: paint() hides the inner half under the fill, so
            # what is left matches drawtext's outward-only borderw.
            QPen(QColor(overlay.outline_color), overlay.outline_px * 2,
                 Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            if overlay.outline_px > 0
            else QPen(Qt.PenStyle.NoPen)
        )
        self.text_item.setPos(overlay.x_px, overlay.y_px)
        self.text_item.setVisible(True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self) -> None:
        available_w, available_h = self.width(), self.height()
        if available_w <= 0 or available_h <= 0:
            return
        if available_w / available_h > self._ratio:
            frame_h = available_h
            frame_w = int(round(frame_h * self._ratio))
        else:
            frame_w = available_w
            frame_h = int(round(frame_w / self._ratio))
        self.frame.setGeometry((available_w - frame_w) // 2, (available_h - frame_h) // 2, frame_w, frame_h)


class MediaListWidget(QListWidget):
    """Media bin that also accepts video files dragged in from the OS."""

    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

_DEBUG = os.environ.get("VIDEO_EDITOR_DEBUG") == "1"


def _dbg(*args) -> None:
    if _DEBUG:
        print("[video-editor]", *args, flush=True)


def _format_ms(value: int, show_ms: bool = False) -> str:
    value = max(0, value)
    seconds = value // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    suffix = f".{value % 1000:03d}" if show_ms else ""
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{suffix}"


_TEXT_MODES = ("drag_text", "trim_text_left", "trim_text_right")


class TimelineCanvas(QWidget):
    clip_selected = Signal(str)
    seek_requested = Signal(int)
    move_committed = Signal(str, int)
    trim_committed = Signal(str, object, object)
    text_selected = Signal(str)
    text_range_committed = Signal(str, int, int)

    MARGIN = 14
    LANE_TOP = 56
    LANE_HEIGHT = 48
    TEXT_LANE_TOP = 108
    TEXT_LANE_HEIGHT = 24
    RULER_TOP = 24
    RULER_BOTTOM = 50
    EDGE_HIT_PX = 6
    MIN_CLIP_MS = 100
    THUMB_HEIGHT = 36
    ZOOM_MIN = 1.0
    ZOOM_MAX = 200.0
    TICK_CANDIDATES_MS = (100, 250, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000, 600000, 1800000, 3600000)

    def __init__(self, service: ProjectService, cache_dir: Path) -> None:
        super().__init__()
        self.service = service
        self.cache_dir = cache_dir
        # Scaled poster frames keyed by asset id. paintEvent runs on every mouse
        # move during a drag, so misses are cached too (as a null pixmap) and a
        # missing file is never re-stat'd per frame. Cleared on a media refresh.
        self.thumbnails: dict[str, QPixmap] = {}
        self.selected_clip_id = ""
        self.selected_text_id = ""
        self.active_text_id = ""
        self.preview_text_start_ms = 0
        self.preview_text_end_ms = 0
        self.playhead_ms = 0
        self.zoom_factor = 1.0
        self.pan_offset_ms = 0
        self.mode = "idle"
        self.active_clip_id = ""
        self.drag_anchor_ms = 0
        self.press_x = 0.0
        self.preview_start_ms = 0
        self.preview_in_ms = 0
        self.preview_out_ms = 0
        self.pan_anchor_x = 0.0
        self.pan_anchor_offset_ms = 0
        self.snap_enabled = True
        self.setMinimumHeight(152)
        self.setMouseTracking(True)

    def _thumbnail(self, asset) -> QPixmap:
        cached = self.thumbnails.get(asset.id)
        if cached is not None:
            return cached
        source = thumbnail_path(asset, self.cache_dir)
        pixmap = QPixmap(str(source)) if source.exists() else QPixmap()
        if not pixmap.isNull():
            pixmap = pixmap.scaledToHeight(self.THUMB_HEIGHT, Qt.TransformationMode.SmoothTransformation)
        self.thumbnails[asset.id] = pixmap
        return pixmap

    def set_selection(self, clip_id: str, text_id: str = "") -> None:
        self.selected_clip_id = clip_id
        self.selected_text_id = text_id
        self.update()

    def set_playhead(self, timeline_ms: int) -> None:
        self.playhead_ms = max(0, timeline_ms)
        self.update()

    def set_snapping(self, enabled: bool) -> None:
        self.snap_enabled = enabled

    def _snap_time(self, timeline_ms: int, exclude_clip_id: str = "") -> int:
        if not self.snap_enabled:
            return timeline_ms
        threshold_ms = max(1, int(round(8 * self._visible_duration_ms() / self._content_width())))
        candidates = [self.playhead_ms]
        for clip in self.service.video_track.clips:
            if clip.id != exclude_clip_id:
                candidates.extend((clip.timeline_start_ms, clip.timeline_start_ms + clip.duration_ms))
        nearest = min(candidates, key=lambda value: abs(value - timeline_ms), default=timeline_ms)
        return nearest if abs(nearest - timeline_ms) <= threshold_ms else timeline_ms

    def _content_width(self) -> int:
        return max(1, self.width() - self.MARGIN * 2)

    def _visible_duration_ms(self) -> int:
        duration = max(1, self.service.timeline_duration_ms())
        return max(1, int(duration / self.zoom_factor))

    def _clamp_pan(self) -> None:
        duration = self.service.timeline_duration_ms()
        max_offset = max(0, duration - self._visible_duration_ms())
        self.pan_offset_ms = min(max(0, self.pan_offset_ms), max_offset)

    def _ms_to_x(self, ms: int) -> float:
        vis = self._visible_duration_ms()
        return self.MARGIN + ((ms - self.pan_offset_ms) / vis) * self._content_width()

    def _x_to_ms(self, x: float) -> int:
        vis = self._visible_duration_ms()
        x_clamped = min(max(x, self.MARGIN), self.width() - self.MARGIN)
        return self.pan_offset_ms + int(((x_clamped - self.MARGIN) / self._content_width()) * vis)

    def _tick_interval_ms(self) -> int:
        vis = self._visible_duration_ms()
        target = max(1, vis // 8)
        return next(
            (candidate for candidate in self.TICK_CANDIDATES_MS if candidate >= target),
            self.TICK_CANDIDATES_MS[-1],
        )

    def _clip_under(self, timeline_ms: int):
        for clip in self.service.visible_video_clips(timeline_ms, timeline_ms + 1):
            if clip.timeline_start_ms <= timeline_ms < clip.timeline_start_ms + clip.duration_ms:
                return clip
        return None

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(TRACK_BG))

        duration = max(1, self.service.timeline_duration_ms())
        self._clamp_pan()
        vis = self._visible_duration_ms()
        view_end = self.pan_offset_ms + vis
        clips = self.service.visible_video_clips(self.pan_offset_ms, view_end)

        # Lane background bands so each track reads as a distinct surface.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1e2024"))
        painter.drawRoundedRect(
            QRectF(self.MARGIN, self.LANE_TOP, self._content_width(), self.LANE_HEIGHT), 6, 6
        )
        painter.drawRoundedRect(
            QRectF(self.MARGIN, self.TEXT_LANE_TOP, self._content_width(), self.TEXT_LANE_HEIGHT), 6, 6
        )

        painter.setPen(QPen(QColor(TEXT_FAINT), 1))
        fine_time = self._tick_interval_ms() < 1000
        header = f"{_format_ms(self.pan_offset_ms, fine_time)}–{_format_ms(view_end, fine_time)}   ·   total {_format_ms(duration, fine_time)}   ·   {self.zoom_factor:.1f}×"
        painter.drawText(self.MARGIN, 18, header)

        tick_ms = self._tick_interval_ms()
        major_ms = tick_ms * 5
        painter.setPen(QPen(QColor(BORDER), 1))
        painter.drawLine(self.MARGIN, self.RULER_BOTTOM, self.width() - self.MARGIN, self.RULER_BOTTOM)
        first_tick = (self.pan_offset_ms // tick_ms) * tick_ms
        ms = first_tick
        while ms <= view_end:
            if ms >= 0:
                x = self._ms_to_x(ms)
                if self.MARGIN <= x <= self.width() - self.MARGIN:
                    is_major = (ms % major_ms) == 0
                    tick_top = self.RULER_TOP + (0 if is_major else 8)
                    painter.setPen(QPen(QColor(TEXT_MUTED if is_major else BORDER_STRONG), 1))
                    painter.drawLine(int(x), tick_top, int(x), self.RULER_BOTTOM)
                    if is_major:
                        painter.setPen(QColor(TEXT_MUTED))
                        painter.drawText(int(x) + 4, self.RULER_TOP + 10, _format_ms(ms, fine_time))
            ms += tick_ms

        for clip in clips:
            start_ms = clip.timeline_start_ms
            dur_ms = clip.duration_ms
            if clip.id == self.active_clip_id:
                if self.mode == "drag_clip":
                    start_ms = self.preview_start_ms
                elif self.mode == "trim_left":
                    start_ms = clip.timeline_start_ms + int(
                        round((self.preview_in_ms - clip.source_in_ms) / clip.speed)
                    )
                    dur_ms = int(round((clip.source_out_ms - self.preview_in_ms) / clip.speed))
                elif self.mode == "trim_right":
                    dur_ms = int(round((self.preview_out_ms - clip.source_in_ms) / clip.speed))
            end_ms = start_ms + dur_ms
            if end_ms < self.pan_offset_ms or start_ms > view_end:
                continue
            start_x = self._ms_to_x(start_ms)
            end_x = self._ms_to_x(end_ms)
            clip_width = max(24.0, end_x - start_x)
            rect = QRectF(start_x, self.LANE_TOP, clip_width, self.LANE_HEIGHT)
            selected = clip.id == self.selected_clip_id
            painter.setBrush(QColor(ACCENT if selected else CLIP_BG))
            painter.setPen(QPen(QColor(ACCENT_HOVER if selected else CLIP_BORDER), 2 if selected else 1))
            painter.drawRoundedRect(rect, 6, 6)

            # Accent stripe along the top edge gives each clip a tactile, label-like cap.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#9cbcf0" if selected else BORDER_STRONG))
            painter.drawRoundedRect(QRectF(rect.left() + 1, rect.top() + 1, max(2.0, rect.width() - 2), 4), 2, 2)

            asset = self.service.asset_by_id(clip.asset_id)
            label_rect = rect.adjusted(10, 8, -10, -4)
            thumb = self._thumbnail(asset) if asset else QPixmap()
            # Only worth it when the bar is wide enough to leave room for the name.
            if not thumb.isNull() and rect.width() >= thumb.width() + 48:
                painter.save()
                clip_path = QPainterPath()
                clip_path.addRoundedRect(rect, 6, 6)
                painter.setClipPath(clip_path)
                painter.drawPixmap(
                    QPointF(rect.left() + 1, rect.top() + (self.LANE_HEIGHT - thumb.height()) / 2 + 2),
                    thumb,
                )
                painter.restore()
                label_rect = rect.adjusted(thumb.width() + 8, 8, -10, -4)

            label = Path(asset.path).name if asset else "Missing media"
            painter.setPen(QColor("#f4f6f9" if selected else TEXT))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter, label)

        for overlay in self.service.texts:
            start_ms, end_ms = overlay.start_ms, overlay.end_ms
            if overlay.id == self.active_text_id and self.mode in _TEXT_MODES:
                start_ms, end_ms = self.preview_text_start_ms, self.preview_text_end_ms
            if end_ms < self.pan_offset_ms or start_ms > view_end:
                continue
            start_x = self._ms_to_x(start_ms)
            width = max(18.0, self._ms_to_x(end_ms) - start_x)
            rect = QRectF(start_x, self.TEXT_LANE_TOP, width, self.TEXT_LANE_HEIGHT)
            selected = overlay.id == self.selected_text_id
            painter.setBrush(QColor(ACCENT if selected else "#3a4150"))
            painter.setPen(QPen(QColor(ACCENT_HOVER if selected else BORDER_STRONG), 2 if selected else 1))
            painter.drawRoundedRect(rect, 4, 4)
            label = painter.fontMetrics().elidedText(
                overlay.text or "Text", Qt.TextElideMode.ElideRight, int(rect.width()) - 12
            )
            painter.setPen(QColor("#f4f6f9" if selected else TEXT))
            painter.drawText(rect.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, label)

        playhead_x = self._ms_to_x(min(self.playhead_ms, duration))
        if self.MARGIN <= playhead_x <= self.width() - self.MARGIN:
            painter.setPen(QPen(QColor(PLAYHEAD), 2))
            painter.drawLine(int(playhead_x), self.RULER_TOP, int(playhead_x), self.height() - 16)
            # Small triangle handle at the top of the playhead for grab affordance.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(PLAYHEAD))
            tip = int(playhead_x)
            painter.drawPolygon(QPolygonF([
                QPointF(tip - 5, self.RULER_TOP - 6),
                QPointF(tip + 5, self.RULER_TOP - 6),
                QPointF(tip, self.RULER_TOP + 2),
            ]))

    def _edge_zone(self, x: float, start_ms: int, end_ms: int):
        if abs(x - self._ms_to_x(start_ms)) <= self.EDGE_HIT_PX:
            return "left"
        if abs(x - self._ms_to_x(end_ms)) <= self.EDGE_HIT_PX:
            return "right"
        return None

    def _clip_edge_zone(self, x: float, clip):
        return self._edge_zone(x, clip.timeline_start_ms, clip.timeline_start_ms + clip.duration_ms)

    def _text_under(self, x: float):
        """Hit-test in pixels, widened by the edge grab zone: the trim handles
        sit on the bar's boundaries, so a strictly-inside test would make the
        end edge (and any very short caption) impossible to grab."""
        # Reversed so the topmost (last painted) overlay wins when they overlap.
        for overlay in reversed(self.service.texts):
            start_x = self._ms_to_x(overlay.start_ms) - self.EDGE_HIT_PX
            end_x = self._ms_to_x(overlay.end_ms) + self.EDGE_HIT_PX
            if start_x <= x <= end_x:
                return overlay
        return None

    def _press_text_lane(self, x: float, timeline_ms: int) -> None:
        overlay = self._text_under(x)
        if overlay is None:
            return
        self.active_text_id = overlay.id
        self.preview_text_start_ms = overlay.start_ms
        self.preview_text_end_ms = overlay.end_ms
        self.text_selected.emit(overlay.id)
        edge = self._edge_zone(x, overlay.start_ms, overlay.end_ms)
        if edge:
            self.mode = "trim_text_left" if edge == "left" else "trim_text_right"
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.mode = "drag_text"
            self.drag_anchor_ms = timeline_ms - overlay.start_ms
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _move_text_lane(self, timeline_ms: int) -> None:
        overlay = self.service.text_by_id(self.active_text_id)
        if overlay is None:
            return
        if self.mode == "drag_text":
            start = max(0, self._snap_time(max(0, timeline_ms - self.drag_anchor_ms)))
            self.preview_text_start_ms = start
            self.preview_text_end_ms = start + overlay.duration_ms
        elif self.mode == "trim_text_left":
            edge = self._snap_time(timeline_ms)
            self.preview_text_start_ms = max(0, min(edge, overlay.end_ms - self.MIN_CLIP_MS))
        else:
            edge = self._snap_time(timeline_ms)
            self.preview_text_end_ms = max(edge, overlay.start_ms + self.MIN_CLIP_MS)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.mode = "pan"
            self.pan_anchor_x = float(event.position().x())
            self.pan_anchor_offset_ms = self.pan_offset_ms
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.service.timeline_duration_ms() <= 0:
            return
        x = float(event.position().x())
        y = float(event.position().y())
        timeline_ms = self._x_to_ms(x)
        if y < self.LANE_TOP:
            self.mode = "scrub_playhead"
            self.seek_requested.emit(timeline_ms)
            self.setCursor(Qt.CursorShape.SplitHCursor)
            return
        self.press_x = x
        if self.TEXT_LANE_TOP <= y <= self.TEXT_LANE_TOP + self.TEXT_LANE_HEIGHT:
            self._press_text_lane(x, timeline_ms)
            return
        in_lane = self.LANE_TOP <= y <= self.LANE_TOP + self.LANE_HEIGHT
        clip = self._clip_under(timeline_ms) if in_lane else None
        if clip is None:
            return
        self.active_clip_id = clip.id
        self.preview_start_ms = clip.timeline_start_ms
        self.preview_in_ms = clip.source_in_ms
        self.preview_out_ms = clip.source_out_ms
        self.clip_selected.emit(clip.id)
        edge = self._clip_edge_zone(x, clip)
        if edge == "left":
            self.mode = "trim_left"
            self.drag_anchor_ms = timeline_ms
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edge == "right":
            self.mode = "trim_right"
            self.drag_anchor_ms = timeline_ms
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.mode = "drag_clip"
            self.drag_anchor_ms = timeline_ms - clip.timeline_start_ms
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        x = float(event.position().x())
        if self.mode == "scrub_playhead":
            self.seek_requested.emit(self._x_to_ms(x))
            return
        if self.mode == "pan":
            delta_px = x - self.pan_anchor_x
            vis = self._visible_duration_ms()
            delta_ms = int(-(delta_px / self._content_width()) * vis)
            self.pan_offset_ms = self.pan_anchor_offset_ms + delta_ms
            self._clamp_pan()
            self.update()
            return
        timeline_ms = self._x_to_ms(x)
        if self.mode in _TEXT_MODES:
            self._move_text_lane(timeline_ms)
            return
        clip = self.service.clip_by_id(self.active_clip_id)
        if self.mode == "drag_clip" and clip is not None:
            target = max(0, timeline_ms - self.drag_anchor_ms)
            self.preview_start_ms = max(0, self._snap_time(target, clip.id))
            self.update()
            return
        if self.mode == "trim_left" and clip is not None:
            edge = self._snap_time(clip.timeline_start_ms + timeline_ms - self.drag_anchor_ms, clip.id)
            delta = edge - clip.timeline_start_ms
            new_in = max(0, clip.source_in_ms + int(round(delta * clip.speed)))
            self.preview_in_ms = min(new_in, clip.source_out_ms - self.MIN_CLIP_MS)
            self.update()
            return
        if self.mode == "trim_right" and clip is not None:
            current_end = clip.timeline_start_ms + clip.duration_ms
            edge = self._snap_time(current_end + timeline_ms - self.drag_anchor_ms, clip.id)
            delta = edge - current_end
            new_out = max(
                clip.source_in_ms + self.MIN_CLIP_MS,
                clip.source_out_ms + int(round(delta * clip.speed)),
            )
            asset = self.service.asset_by_id(clip.asset_id)
            if asset is not None:
                new_out = min(new_out, asset.duration_ms)
            self.preview_out_ms = new_out
            self.update()
            return
        self._update_hover_cursor(x, float(event.position().y()))

    def mouseReleaseEvent(self, event) -> None:
        mode = self.mode
        clip_id = self.active_clip_id
        release_x = float(event.position().x())
        moved = abs(release_x - self.press_x) > 3
        if mode in _TEXT_MODES:
            if moved and self.active_text_id:
                self.text_range_committed.emit(
                    self.active_text_id, self.preview_text_start_ms, self.preview_text_end_ms
                )
            self.mode = "idle"
            self.active_text_id = ""
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            return
        if mode == "drag_clip" and clip_id:
            if moved:
                self.move_committed.emit(clip_id, self.preview_start_ms)
            else:
                self.seek_requested.emit(max(0, self._x_to_ms(release_x)))
        elif mode == "trim_left" and clip_id and moved:
            self.trim_committed.emit(clip_id, self.preview_in_ms, None)
        elif mode == "trim_right" and clip_id and moved:
            self.trim_committed.emit(clip_id, None, self.preview_out_ms)
        elif mode == "scrub_playhead":
            self.seek_requested.emit(max(0, self._x_to_ms(release_x)))
        elif mode == "idle" and event.button() == Qt.MouseButton.LeftButton:
            self.seek_requested.emit(max(0, self._x_to_ms(release_x)))
        self.mode = "idle"
        self.active_clip_id = ""
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        x = float(event.position().x())
        pre_ms = self._x_to_ms(x)
        factor = 1.25 if delta > 0 else 1 / 1.25
        self.zoom_factor = min(self.ZOOM_MAX, max(self.ZOOM_MIN, self.zoom_factor * factor))
        vis = self._visible_duration_ms()
        self.pan_offset_ms = pre_ms - int(((x - self.MARGIN) / self._content_width()) * vis)
        self._clamp_pan()
        self.update()

    def _update_hover_cursor(self, x: float, y: float) -> None:
        if y < self.LANE_TOP:
            self.setCursor(Qt.CursorShape.SplitHCursor)
            return
        timeline_ms = self._x_to_ms(x)
        if self.TEXT_LANE_TOP <= y <= self.TEXT_LANE_TOP + self.TEXT_LANE_HEIGHT:
            overlay = self._text_under(x)
            if overlay is None:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            elif self._edge_zone(x, overlay.start_ms, overlay.end_ms):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        clip = self._clip_under(timeline_ms) if y <= self.LANE_TOP + self.LANE_HEIGHT else None
        if clip is None:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        edge = self._clip_edge_zone(x, clip)
        if edge:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)


class ExportWorker(QThread):
    progress_changed = Signal(float, str)
    log_line = Signal(str)
    finished_with_code = Signal(int)

    def __init__(self, jobs) -> None:
        super().__init__()
        # jobs: list of (FfmpegCommand, weight_ms). A plain export is one job;
        # a smart-render export is several (per-segment encodes/copies + concat).
        self.jobs = list(jobs)
        self.process: subprocess.Popen[str] | None = None
        self.cancelled = False

    def _all_temp_files(self) -> list[str]:
        return [path for command, _ in self.jobs for path in command.temporary_files]

    def run(self) -> None:
        total = sum(max(1, weight) for _, weight in self.jobs) or 1
        done = 0
        try:
            for command, weight in self.jobs:
                if self.cancelled:
                    self.finished_with_code.emit(-1)
                    return
                try:
                    self.process = subprocess.Popen(
                        command.argv,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        errors="replace",
                    )
                except OSError as exc:
                    self.log_line.emit(f"Failed to launch ffmpeg: {exc}")
                    self.finished_with_code.emit(-1)
                    return
                assert self.process.stdout is not None
                for line in self.process.stdout:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if stripped.startswith("out_time_ms="):
                        try:
                            out_time_ms = int(stripped.split("=", 1)[1]) // 1000
                            fraction = min(out_time_ms, weight)
                            self.progress_changed.emit(min(1.0, (done + fraction) / total), stripped)
                        except ValueError:
                            pass
                    elif not stripped.startswith(("frame=", "fps=", "bitrate=", "total_size=", "out_time=", "speed=", "progress=", "stream_")):
                        self.log_line.emit(stripped)
                code = self.process.wait()
                if code != 0:
                    self.finished_with_code.emit(code)
                    return
                done += max(1, weight)
            self.progress_changed.emit(1.0, "")
            self.finished_with_code.emit(0)
        except Exception as exc:
            self.log_line.emit(f"Export worker error: {exc!r}")
            try:
                if self.process:
                    self.process.kill()
            except Exception:
                pass
            self.finished_with_code.emit(-1)
        finally:
            for path in self._all_temp_files():
                Path(path).unlink(missing_ok=True)

    def cancel(self) -> None:
        self.cancelled = True
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def force_stop(self, timeout_ms: int = 2000) -> None:
        self.cancel()
        if self.wait(timeout_ms):
            return
        if self.process and self.process.poll() is None:
            self.process.kill()
        self.wait(timeout_ms)


class ImportWorker(QThread):
    progress_changed = Signal(int, int, str)
    completed = Signal(object, object)

    def __init__(self, cache_dir: Path, paths: list[str] | None = None, assets=None) -> None:
        super().__init__()
        self.cache_dir = cache_dir
        self.paths = list(paths or [])
        self.assets = list(assets or [])
        self.cancelled = False

    def run(self) -> None:
        imported, errors = [], []
        entries = self.paths or self.assets
        for index, entry in enumerate(entries, 1):
            if self.cancelled:
                break
            name = Path(entry if isinstance(entry, str) else entry.path).name
            self.progress_changed.emit(index - 1, len(entries), name)
            try:
                asset = probe_media(entry, timeout_s=15, cancelled=lambda: self.cancelled) if isinstance(entry, str) else entry
                create_thumbnail(asset, self.cache_dir, timeout_s=20, cancelled=lambda: self.cancelled)
                imported.append(asset)
            except Exception as exc:
                errors.append((str(entry if isinstance(entry, str) else entry.path), str(exc)))
            self.progress_changed.emit(index, len(entries), name)
        self.completed.emit(imported, errors)

    def cancel(self) -> None:
        self.cancelled = True


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.service = ProjectService(Project())
        self.settings = QSettings("FastVideoEditor", "FastVideoEditor")
        self.selected_asset_id = ""
        self.selected_clip_id = ""
        self.selected_text_id = ""
        self._preview_text_id = ""
        self.playing_clip_id = ""
        self.playhead_ms = 0
        self.ignore_player_position = False
        self._current_clip_volume = 1.0
        self._current_clip_speed = 1.0
        self.mode = "youtube"
        self.current_project_path: Path | None = None
        self.dirty = False
        self._saved_fingerprint = self._project_fingerprint()
        self._edit_key = ""
        self._continuous_edit_key = ""
        self._edit_timer = QTimer(self)
        self._edit_timer.setSingleShot(True)
        self._edit_timer.timeout.connect(lambda: setattr(self, "_edit_key", ""))
        recovery_root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.recovery_service = RecoveryService(recovery_root / "recovery")
        self._recovery_debounce = QTimer(self)
        self._recovery_debounce.setSingleShot(True)
        self._recovery_debounce.timeout.connect(self._write_recovery)
        self._recovery_deadline = QTimer(self)
        self._recovery_deadline.setSingleShot(True)
        self._recovery_deadline.timeout.connect(self._write_recovery)
        self._sampled_timer = QTimer(self)
        self._sampled_timer.setInterval(67)
        self._sampled_timer.timeout.connect(self._advance_sampled_preview)
        self._sampled_clock = QElapsedTimer()
        self.cache_dir = Path(tempfile.gettempdir()) / "fast-video-editor"
        self.export_worker: ExportWorker | None = None
        self.import_worker: ImportWorker | None = None
        self.export_output_path = ""
        self.export_temp_path = ""
        self._export_expected_fps = 0.0
        self._export_expected_codec = VideoCodec.H264
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(1.0)
        self.player.positionChanged.connect(self.on_player_position_changed)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)

        self.setWindowTitle("Fast Video Editor")
        self.resize(1320, 840)
        self.setMinimumSize(1040, 680)
        self._build_actions()
        self._build_ui()
        self._apply_theme()
        self.refresh()
        self._sync_mode_from_timeline()
        self._apply_export_defaults()
        self._update_window_title()
        QApplication.instance().installEventFilter(self)
        QTimer.singleShot(0, self._offer_startup_recovery)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
            focused = QApplication.focusWidget()
            interactive = isinstance(
                focused,
                (QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QAbstractButton, QSlider, QAbstractItemView),
            )
            if event.key() == Qt.Key.Key_Space and not interactive:
                self.toggle_playback()
                return True
            if not interactive and event.key() in (Qt.Key.Key_Comma, Qt.Key.Key_Period):
                self.step_frame(-1 if event.key() == Qt.Key.Key_Comma else 1)
                return True
            if not interactive and event.key() == Qt.Key.Key_Home:
                self.seek_timeline(0)
                return True
            if not interactive and event.key() == Qt.Key.Key_End:
                self.seek_timeline(self.service.timeline_duration_ms())
                return True
            if not interactive and event.key() == Qt.Key.Key_N:
                self.set_snapping(not self.snap_action.isChecked())
                return True
        return super().eventFilter(watched, event)

    def _build_actions(self) -> None:
        self.new_action = QAction(_svg_icon("new"), "New", self)
        self.new_action.setToolTip("Start a new, empty project")
        self.open_action = QAction(_svg_icon("open"), "Open", self)
        self.open_action.setToolTip("Open a saved project file (.json)")
        self.save_action = QAction(_svg_icon("save"), "Save", self)
        self.save_action.setToolTip("Save the current project to a file")
        self.import_action = QAction(_svg_icon("add"), "Add", self)
        self.import_action.setToolTip("Add video or audio files to the media bin")
        self.undo_action = QAction(_svg_icon("undo"), "Undo", self)
        self.undo_action.setToolTip("Undo the last timeline edit")
        self.redo_action = QAction("Redo", self)
        self.redo_action.setToolTip("Redo the last undone timeline edit")
        self.relink_action = QAction("Relink", self)
        self.relink_action.setToolTip("Relink the selected offline media file")
        self.relink_folder_action = QAction("Relink Missing from Folder", self)
        self.snap_action = QAction("Snapping", self)
        self.snap_action.setCheckable(True)
        self.snap_action.setChecked(self.settings.value("timeline/snapping", True, type=bool))
        self.reset_timeline_action = QAction("Reset Timeline", self)
        self.reset_timeline_action.setToolTip("Reset canvas, frame rate, and master volume")
        self.reset_clip_action = QAction("Reset Clip", self)
        self.reset_clip_action.setToolTip("Reset crop, transform, opacity, volume, and speed for the selected clip")
        self.delete_action = QAction(_svg_icon("trash"), "Remove", self)
        self.delete_action.setToolTip("Remove the selected clip from the timeline")
        self.previous_clip_action = QAction("Select Previous Clip", self)
        self.next_clip_action = QAction("Select Next Clip", self)
        self.move_clip_left_action = QAction("Move Clip Left", self)
        self.move_clip_right_action = QAction("Move Clip Right", self)

        self.new_action.setShortcut("Ctrl+N")
        self.open_action.setShortcut("Ctrl+O")
        self.save_action.setShortcut("Ctrl+S")
        self.import_action.setShortcut("Ctrl+I")
        self.undo_action.setShortcut("Ctrl+Z")
        self.redo_action.setShortcut("Ctrl+Shift+Z")
        self.relink_action.setShortcut("Ctrl+Shift+R")
        self.delete_action.setShortcut("Delete")
        self.previous_clip_action.setShortcut("Alt+Up")
        self.next_clip_action.setShortcut("Alt+Down")
        self.move_clip_left_action.setShortcut("Alt+Left")
        self.move_clip_right_action.setShortcut("Alt+Right")

        self.new_action.triggered.connect(self.new_project)
        self.open_action.triggered.connect(self.open_project)
        self.save_action.triggered.connect(self.save_project)
        self.import_action.triggered.connect(self.import_media)
        self.undo_action.triggered.connect(self.undo)
        self.redo_action.triggered.connect(self.redo)
        self.relink_action.triggered.connect(self.relink_selected_media)
        self.relink_folder_action.triggered.connect(self.relink_missing_from_folder)
        self.snap_action.toggled.connect(self.set_snapping)
        self.reset_timeline_action.triggered.connect(self.reset_timeline_properties)
        self.reset_clip_action.triggered.connect(self.reset_selected_clip_properties)
        self.delete_action.triggered.connect(self.remove_selected_clip)
        self.previous_clip_action.triggered.connect(lambda: self.select_relative_clip(-1))
        self.next_clip_action.triggered.connect(lambda: self.select_relative_clip(1))
        self.move_clip_left_action.triggered.connect(lambda: self.move_selected_clip(-1))
        self.move_clip_right_action.triggered.connect(lambda: self.move_selected_clip(1))
        self.addActions([
            self.previous_clip_action, self.next_clip_action,
            self.move_clip_left_action, self.move_clip_right_action,
        ])

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        # Project file actions
        for action in [self.new_action, self.open_action, self.save_action]:
            toolbar.addAction(action)
        toolbar.addSeparator()
        # Media
        toolbar.addAction(self.import_action)
        toolbar.addSeparator()
        # Editing
        for action in [self.undo_action, self.redo_action, self.delete_action]:
            toolbar.addAction(action)
        toolbar.addAction(self.snap_action)
        toolbar.addSeparator()
        toolbar.addAction(self.reset_timeline_action)
        toolbar.addAction(self.reset_clip_action)

        media_menu = self.menuBar().addMenu("Media")
        media_menu.addAction(self.import_action)
        media_menu.addAction(self.relink_action)
        media_menu.addAction(self.relink_folder_action)

        # Export-format toggle, pushed to the top-right corner.
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        toolbar.addSeparator()
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QToolButton] = {}
        for key, info in EXPORT_MODES.items():
            button = QToolButton()
            button.setObjectName("modeButton")
            button.setCheckable(True)
            button.setIcon(_brand_icon(info["icon"], TEXT_MUTED, "#ffffff"))
            button.setIconSize(QSize(22, 22))
            button.setToolTip(f'{info["label"]}  ·  {info["width"]}×{info["height"]}')
            button.clicked.connect(lambda _checked=False, mode=key: self.set_mode(mode))
            self.mode_group.addButton(button)
            toolbar.addWidget(button)
            self.mode_buttons[key] = button
        self.addToolBar(toolbar)

    def _build_ui(self) -> None:
        root = QSplitter(Qt.Orientation.Horizontal)
        root.setObjectName("rootSplitter")
        root.setHandleWidth(1)
        root.setChildrenCollapsible(False)
        root.addWidget(self._build_media_panel())
        root.addWidget(self._build_center_panel())
        root.addWidget(self._build_export_panel())
        root.setStretchFactor(0, 0)
        root.setStretchFactor(1, 1)
        root.setStretchFactor(2, 0)
        root.setSizes([280, 680, 340])
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())

    # -- small composition helpers -----------------------------------------

    def _card(self, title: str) -> tuple[QGroupBox, QVBoxLayout]:
        box = QGroupBox(title)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(14, 16, 14, 14)
        inner.setSpacing(10)
        return box, inner

    def _field_label(self, text: str, buddy: QWidget | None = None) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        if buddy is not None:
            label.setBuddy(buddy)
        return label

    def _build_media_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("sidePanel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(12, 12, 8, 12)
        outer.setSpacing(0)

        card, layout = self._card("Media")
        hint = QLabel("Drag files here or use Add. Double-click an item or choose Add to Timeline.")
        hint.setObjectName("cardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.media_list = MediaListWidget()
        self.media_list.setAccessibleName("Project media")
        self.media_list.setIconSize(QSize(64, 36))
        self.media_list.setSpacing(2)
        self.media_list.itemSelectionChanged.connect(self.select_media)
        self.media_list.itemDoubleClicked.connect(lambda _item: self.add_selected_asset())
        self.media_list.files_dropped.connect(self.import_paths)
        layout.addWidget(self.media_list, 1)

        self.add_button = QPushButton(_svg_icon("arrow_down", "#ffffff"), "Add to Timeline")
        self.add_button.setAccessibleName("Add selected media to timeline")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self.add_selected_asset)
        layout.addWidget(self.add_button)

        outer.addWidget(card)
        return panel

    def _build_center_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("centerPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(12)

        self.preview_area = PreviewArea()
        self.preview_area.text_item.moved = self._on_preview_text_moved
        self.player.setVideoOutput(self.preview_area.video_item)
        layout.addWidget(self.preview_area, 1)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.play_icon = _svg_icon("play")
        self.pause_icon = _svg_icon("pause")
        self.play_button = QPushButton(self.play_icon, "Play")
        self.play_button.setAccessibleName("Play or pause preview")
        self.play_button.setObjectName("transport")
        self.play_button.clicked.connect(self.toggle_playback)
        controls.addWidget(self.play_button)
        previous_frame = QPushButton("‹ Frame")
        previous_frame.setAccessibleName("Previous frame")
        previous_frame.setToolTip("Previous frame (,)")
        previous_frame.clicked.connect(lambda: self.step_frame(-1))
        next_frame = QPushButton("Frame ›")
        next_frame.setAccessibleName("Next frame")
        next_frame.setToolTip("Next frame (.)")
        next_frame.clicked.connect(lambda: self.step_frame(1))
        controls.addWidget(previous_frame)
        controls.addWidget(next_frame)
        self.time_label = QLabel("00:00:00 / 00:00:00")
        self.time_label.setObjectName("timeLabel")
        controls.addWidget(self.time_label)
        self.playhead_slider = QSlider(Qt.Orientation.Horizontal)
        self.playhead_slider.setAccessibleName("Timeline playhead")
        self.playhead_slider.setEnabled(False)
        self.playhead_slider.sliderMoved.connect(self.seek_timeline)
        controls.addWidget(self.playhead_slider, 1)

        speaker = QLabel()
        speaker.setPixmap(_svg_icon("volume", TEXT_MUTED).pixmap(18, 18))
        speaker.setToolTip("Master volume — affects every clip")
        controls.addSpacing(6)
        controls.addWidget(speaker)
        self.master_slider = self._make_volume_slider()
        self.master_slider.setAccessibleName("Master volume")
        self.master_slider.setFixedWidth(120)
        self.master_slider.setToolTip("Master volume for all clips (0–400%)")
        self.master_slider.valueChanged.connect(self.on_master_volume_changed)
        self.master_slider.sliderPressed.connect(lambda: self._start_continuous_edit("master-volume"))
        self.master_slider.sliderReleased.connect(self._finish_continuous_edit)
        controls.addWidget(self.master_slider)
        self.master_value = QLabel("100%")
        self.master_value.setObjectName("volumeValue")
        controls.addWidget(self.master_value)
        layout.addLayout(controls)

        card, timeline_layout = self._card("Timeline")
        self.timeline_canvas = TimelineCanvas(self.service, self.cache_dir)
        self.timeline_canvas.set_snapping(self.snap_action.isChecked())
        self.timeline_canvas.setAccessibleName("Visual timeline")
        self.timeline_canvas.clip_selected.connect(self.select_clip_by_id)
        self.timeline_canvas.seek_requested.connect(self.seek_timeline)
        self.timeline_canvas.move_committed.connect(self.on_clip_moved)
        self.timeline_canvas.trim_committed.connect(self.on_clip_trimmed)
        self.timeline_canvas.text_selected.connect(self.select_text_by_id)
        self.timeline_canvas.text_range_committed.connect(self.on_text_range_changed)
        timeline_layout.addWidget(self.timeline_canvas)

        edit_row = QHBoxLayout()
        edit_row.setSpacing(8)
        split_button = QPushButton("Split at Playhead")
        split_button.clicked.connect(self.split_selected_clip)
        add_text_button = QPushButton("Add Text")
        add_text_button.setToolTip("Add a caption at the playhead; drag its bar in the text row to retime it")
        add_text_button.clicked.connect(self.add_text_overlay)
        trim_left_button = QPushButton("Trim Left")
        trim_left_button.setToolTip("Move the clip's left edge (start) to the playhead")
        trim_left_button.clicked.connect(self.set_trim_in_to_playhead)
        trim_right_button = QPushButton("Trim Right")
        trim_right_button.setToolTip("Move the clip's right edge (end) to the playhead")
        trim_right_button.clicked.connect(self.set_trim_out_to_playhead)
        edit_row.addWidget(split_button)
        edit_row.addWidget(add_text_button)
        edit_row.addStretch()
        edit_row.addWidget(trim_left_button)
        edit_row.addWidget(trim_right_button)
        timeline_layout.addLayout(edit_row)

        self.timeline_table = QTableWidget(0, 6)
        self.timeline_table.setAccessibleName("Timeline clips")
        self.timeline_table.setToolTip("Keyboard: Alt+Up/Down selects clips; Alt+Left/Right reorders the selected clip")
        self.timeline_table.setHorizontalHeaderLabels(["#", "Clip", "Start", "In", "Out", "Duration"])
        self.timeline_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.timeline_table.verticalHeader().setVisible(False)
        self.timeline_table.setShowGrid(False)
        self.timeline_table.setAlternatingRowColors(True)
        self.timeline_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.timeline_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.timeline_table.setMaximumHeight(200)
        self.timeline_table.itemSelectionChanged.connect(self.select_clip)
        self.timeline_table.cellDoubleClicked.connect(self.seek_table_clip_start)
        timeline_layout.addWidget(self.timeline_table)

        layout.addWidget(card)
        return panel

    def _build_export_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("sidePanel")
        # The scroll area's viewport owns the sidebar width. Ignoring the
        # panel's aggregate size hint prevents long labels and multi-column
        # forms from keeping a stale, wider geometry after maximising.
        panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(8, 12, 12, 12)
        outer.setSpacing(12)

        # --- Inspector card ---
        inspector, ins_layout = self._card("Clip Properties")
        self.inspector_card = inspector
        self.clip_name = QLineEdit()
        self.clip_name.setAccessibleName("Selected clip name")
        self.clip_name.setReadOnly(True)
        self.clip_name.setPlaceholderText("No clip selected")
        ins_layout.addWidget(self.clip_name)

        self.crop_left = self._spin(0, 10000)
        self.crop_top = self._spin(0, 10000)
        self.crop_right = self._spin(0, 10000)
        self.crop_bottom = self._spin(0, 10000)
        self.scale = self._spin(10, 400, suffix=" %")
        self.scale.setValue(100)
        self.pos_x = self._spin(-10000, 10000, suffix=" px")
        self.pos_y = self._spin(-10000, 10000, suffix=" px")
        self.rotation = self._spin(-360, 360, suffix="°")
        self.opacity = self._spin(0, 100, suffix=" %")
        self.opacity.setValue(100)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.25, 100.0)
        self.speed.setDecimals(2)
        self.speed.setSingleStep(0.25)
        self.speed.setSuffix("×")
        self.speed.setValue(1.0)
        self.speed.setAccessibleName("Clip speed")
        self.speed.editingFinished.connect(self.apply_clip_speed)
        self.speed_presets = QComboBox()
        self.speed_presets.setAccessibleName("Clip speed presets")
        self.speed_presets.addItem("Preset…", None)
        for value in (0.25, 0.5, 1.0, 2.0, 4.0, 10.0, 25.0, 50.0, 100.0):
            self.speed_presets.addItem(f"{value:g}×", value)
        self.speed_presets.activated.connect(self.apply_speed_preset)
        self.speed_duration = QLabel("Duration: 00:00:00.000")
        self.speed_duration.setObjectName("cardHint")
        speed_widget = QWidget()
        speed_layout = QVBoxLayout(speed_widget)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        speed_layout.setSpacing(6)
        speed_row = QHBoxLayout()
        speed_row.setContentsMargins(0, 0, 0, 0)
        speed_row.addWidget(self.speed, 1)
        speed_row.addWidget(self.speed_presets)
        speed_layout.addLayout(speed_row)
        speed_layout.addWidget(self.speed_duration)
        editors = {
            "Crop left": self.crop_left,
            "Crop top": self.crop_top,
            "Crop right": self.crop_right,
            "Crop bottom": self.crop_bottom,
            "Clip scale": self.scale,
            "Clip position X": self.pos_x,
            "Clip position Y": self.pos_y,
            "Clip rotation": self.rotation,
            "Clip opacity": self.opacity,
        }
        for name, widget in editors.items():
            widget.setAccessibleName(name)
            widget.valueChanged.connect(self.update_clip_transform)

        transform_form = QFormLayout()
        transform_form.setSpacing(8)
        transform_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        transform_form.addRow(self._field_label("Scale", self.scale), self.scale)
        transform_form.addRow(self._field_label("Position X", self.pos_x), self.pos_x)
        transform_form.addRow(self._field_label("Position Y", self.pos_y), self.pos_y)
        transform_form.addRow(self._field_label("Rotation", self.rotation), self.rotation)
        transform_form.addRow(self._field_label("Opacity", self.opacity), self.opacity)
        transform_form.addRow(self._field_label("Speed", self.speed), speed_widget)

        self.clip_volume_slider = self._make_volume_slider()
        self.clip_volume_slider.setAccessibleName("Selected clip volume")
        self.clip_volume_slider.setToolTip("Volume for this clip (0–400%)")
        self.clip_volume_slider.valueChanged.connect(self.on_clip_volume_changed)
        self.clip_volume_slider.sliderPressed.connect(lambda: self._start_continuous_edit("clip-volume"))
        self.clip_volume_slider.sliderReleased.connect(self._finish_continuous_edit)
        self.clip_volume_value = QLabel("100%")
        self.clip_volume_value.setObjectName("volumeValue")
        self.clip_volume_value.setMinimumWidth(42)
        self.clip_volume_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        volume_widget = QWidget()
        volume_row = QHBoxLayout(volume_widget)
        volume_row.setContentsMargins(0, 0, 0, 0)
        volume_row.setSpacing(8)
        volume_row.addWidget(self.clip_volume_slider, 1)
        volume_row.addWidget(self.clip_volume_value)
        transform_form.addRow(self._field_label("Volume", self.clip_volume_slider), volume_widget)
        ins_layout.addLayout(transform_form)

        crop_caption = self._field_label("Crop (pixels)")
        crop_caption.setObjectName("subCaption")
        ins_layout.addWidget(crop_caption)
        crop_grid = QGridLayout()
        crop_grid.setHorizontalSpacing(8)
        crop_grid.setVerticalSpacing(8)
        crop_grid.addWidget(self._field_label("Left", self.crop_left), 0, 0)
        crop_grid.addWidget(self.crop_left, 0, 1)
        crop_grid.addWidget(self._field_label("Top", self.crop_top), 0, 2)
        crop_grid.addWidget(self.crop_top, 0, 3)
        crop_grid.addWidget(self._field_label("Right", self.crop_right), 1, 0)
        crop_grid.addWidget(self.crop_right, 1, 1)
        crop_grid.addWidget(self._field_label("Bottom", self.crop_bottom), 1, 2)
        crop_grid.addWidget(self.crop_bottom, 1, 3)
        ins_layout.addLayout(crop_grid)
        outer.addWidget(inspector)

        # --- Text card ---
        text_card, text_layout = self._card("Text")
        self.text_card = text_card
        self.text_content = QLineEdit()
        self.text_content.setAccessibleName("Caption text")
        self.text_content.setPlaceholderText("Caption text")
        self.text_content.textEdited.connect(self.apply_text_properties)
        text_layout.addWidget(self.text_content)

        self.text_font = QComboBox()
        self.text_font.setAccessibleName("Caption font")
        for name in FONT_FILES:
            self.text_font.addItem(name)
        self.text_font.currentIndexChanged.connect(self.apply_text_properties)
        self.text_size = self._spin(8, 400, suffix=" px")
        self.text_size.setAccessibleName("Caption size")
        self.text_outline = self._spin(0, 20, suffix=" px")
        self.text_outline.setAccessibleName("Caption outline width")
        self.text_x = self._spin(-10000, 10000, suffix=" px")
        self.text_x.setAccessibleName("Caption position X")
        self.text_y = self._spin(-10000, 10000, suffix=" px")
        self.text_y.setAccessibleName("Caption position Y")
        for spin in (self.text_size, self.text_outline, self.text_x, self.text_y):
            spin.valueChanged.connect(self.apply_text_properties)

        self.text_color_button = QPushButton()
        self.text_color_button.setAccessibleName("Caption fill colour")
        self.text_color_button.clicked.connect(lambda: self._pick_text_color("color"))
        self.text_outline_color_button = QPushButton()
        self.text_outline_color_button.setAccessibleName("Caption outline colour")
        self.text_outline_color_button.clicked.connect(lambda: self._pick_text_color("outline_color"))

        text_form = QFormLayout()
        text_form.setSpacing(8)
        text_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        text_form.addRow(self._field_label("Font", self.text_font), self.text_font)
        text_form.addRow(self._field_label("Size", self.text_size), self.text_size)
        text_form.addRow(self._field_label("Colour", self.text_color_button), self.text_color_button)
        text_form.addRow(self._field_label("Outline", self.text_outline_color_button), self.text_outline_color_button)
        text_form.addRow(self._field_label("Outline width", self.text_outline), self.text_outline)
        text_form.addRow(self._field_label("Position X", self.text_x), self.text_x)
        text_form.addRow(self._field_label("Position Y", self.text_y), self.text_y)
        text_layout.addLayout(text_form)

        text_hint = QLabel("Drag the caption in the preview to move it, or its bar in the text row to retime it.")
        text_hint.setObjectName("cardHint")
        text_hint.setWordWrap(True)
        text_layout.addWidget(text_hint)
        remove_text_button = QPushButton("Remove Text")
        remove_text_button.clicked.connect(self.remove_text_overlay)
        text_layout.addWidget(remove_text_button)
        text_card.setVisible(False)
        outer.addWidget(text_card)

        # --- Export card ---
        export_card, exp_layout = self._card("Export")
        self.codec = QComboBox()
        self.codec.setAccessibleName("Export codec")
        for label, codec in (("H.264", VideoCodec.H264), ("HEVC", VideoCodec.H265), ("AV1", VideoCodec.AV1)):
            self.codec.addItem(label, codec)
        self.backend = QComboBox()
        self.backend.setAccessibleName("Export encoder")
        for backend in supported_backends_for_platform():
            self.backend.addItem(backend.value.upper() if backend != HardwareBackend.AUTO else "Auto", backend)
        self.export_fps = QDoubleSpinBox()
        self.export_fps.setRange(1.0, 240.0)
        self.export_fps.setDecimals(3)
        self.export_fps.setSingleStep(1.0)
        self.export_fps.setSuffix(" fps")
        self.export_fps.setValue(60.0)
        self.export_fps.setAccessibleName("Export frame rate")
        self.export_fps.setToolTip("Match the source FPS to keep untouched clips eligible for stream copy")
        self.bitrate = self._spin(500, 200000, suffix=" kbps")
        self.bitrate.setAccessibleName("Export bitrate")
        self.bitrate.setValue(12000)
        self.allow_stream_copy = QCheckBox("Use fast stream copy when possible")
        self.allow_stream_copy.setAccessibleName("Allow fast stream copy")
        self.allow_stream_copy.setToolTip(
            "Skip re-encoding when the source already matches the export settings"
        )
        self.allow_stream_copy.setChecked(True)

        export_form = QFormLayout()
        export_form.setSpacing(8)
        export_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        export_form.addRow(self._field_label("Codec", self.codec), self.codec)
        export_form.addRow(self._field_label("Encoder", self.backend), self.backend)
        export_form.addRow(self._field_label("Frame rate", self.export_fps), self.export_fps)
        export_form.addRow(self._field_label("Bitrate", self.bitrate), self.bitrate)
        exp_layout.addLayout(export_form)
        exp_layout.addWidget(self.allow_stream_copy)
        self.codec.currentIndexChanged.connect(self._save_export_defaults)
        self.backend.currentIndexChanged.connect(self._save_export_defaults)
        self.export_fps.valueChanged.connect(self._save_export_defaults)
        self.bitrate.valueChanged.connect(self._save_export_defaults)
        self.allow_stream_copy.toggled.connect(self._save_export_defaults)

        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        self.output_path = QLineEdit()
        self.output_path.setAccessibleName("Export output file")
        self.output_path.setPlaceholderText("Choose where to save…")
        exp_layout.addWidget(self._field_label("Output file", self.output_path))
        output_row.addWidget(self.output_path, 1)
        browse = QPushButton("Browse")
        browse.clicked.connect(self.pick_output)
        output_row.addWidget(browse)
        exp_layout.addLayout(output_row)

        self.export_button = QPushButton(_svg_icon("export", "#ffffff"), "Export Timeline")
        self.export_button.setObjectName("primary")
        self.export_button.setToolTip("Render the whole timeline to one video file")
        self.export_button.clicked.connect(self.export_project)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.clicked.connect(self.cancel_export)
        self.cancel_button.setEnabled(False)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(self.export_button, 1)
        action_row.addWidget(self.cancel_button)
        exp_layout.addLayout(action_row)

        self.export_clip_button = QPushButton(_svg_icon("export"), "Export Selected Clip")
        self.export_clip_button.setToolTip("Render only the clip selected in the timeline to its own file")
        self.export_clip_button.setEnabled(False)
        self.export_clip_button.clicked.connect(self.export_selected_clip)
        exp_layout.addWidget(self.export_clip_button)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        exp_layout.addWidget(self.progress)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced details")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setAccessibleName("Show FFmpeg command and log")
        exp_layout.addWidget(self.advanced_toggle)
        self.command_box = QTextEdit()
        self.command_box.setObjectName("commandBox")
        self.command_box.setReadOnly(True)
        self.command_box.setMinimumHeight(120)
        self.command_box.setVisible(False)
        self.advanced_toggle.toggled.connect(self.command_box.setVisible)
        exp_layout.addWidget(self.command_box, 1)
        outer.addWidget(export_card, 1)
        scroll = QScrollArea()
        scroll.setObjectName("sidePanel")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(300)
        return scroll

    def _spin(self, minimum: int, maximum: int, suffix: str = "") -> QSpinBox:
        box = QSpinBox()
        box.setRange(minimum, maximum)
        box.setAlignment(Qt.AlignmentFlag.AlignRight)
        box.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        # QSpinBox bases its minimum width on every possible digit in its
        # range. Let sidebar layouts shrink the editor while retaining the
        # complete numeric range and its step buttons.
        box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if suffix:
            box.setSuffix(suffix)
        return box

    def _make_volume_slider(self) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 400)
        slider.setValue(100)
        slider.setSingleStep(5)
        slider.setPageStep(25)
        return slider

    def _apply_theme(self) -> None:
        font = QFont("Segoe UI", 10)
        QApplication.instance().setFont(font)
        self.setStyleSheet(
            """
            QMainWindow { background: #1a1c20; }
            QWidget { color: #e7e9ec; font-size: 13px; }
            QWidget#sidePanel, QWidget#centerPanel { background: #1a1c20; }
            QSplitter#rootSplitter::handle { background: #343941; }

            /* --- Toolbar --- */
            QToolBar {
                background: #16181b; border: 0; border-bottom: 1px solid #2a2e34;
                padding: 6px 8px; spacing: 2px;
            }
            QToolBar::separator {
                background: #343941; width: 1px; margin: 4px 8px;
            }
            QToolButton {
                background: transparent; color: #c7ccd4; border: 1px solid transparent;
                border-radius: 6px; padding: 6px 10px; margin: 0 1px;
            }
            QToolButton:hover { background: #262a30; color: #e7e9ec; }
            QToolButton:pressed { background: #2c3036; }
            QToolButton#modeButton { padding: 5px 9px; margin: 0 2px; }
            QToolButton#modeButton:checked {
                background: #2b3550; border: 1px solid #79a8ff;
            }

            /* --- Cards / group boxes --- */
            QGroupBox {
                background: #222428; border: 1px solid #2f343b; border-radius: 12px;
                margin-top: 14px; padding-top: 6px; font-size: 12px; font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin; subcontrol-position: top left;
                left: 14px; top: 2px; padding: 2px 4px; color: #9ba2ac;
            }

            /* --- Labels --- */
            QLabel { color: #e7e9ec; background: transparent; }
            QLabel#fieldLabel { color: #9ba2ac; font-size: 12px; }
            QLabel#subCaption {
                color: #a0a6af; font-size: 11px; font-weight: 600;
                text-transform: uppercase; padding-top: 4px;
            }
            QLabel#cardHint { color: #a7adb6; font-size: 12px; padding-bottom: 2px; }
            QLabel#timeLabel {
                color: #c7ccd4; font-family: "Cascadia Mono", "Consolas", monospace;
                font-size: 13px; padding: 0 4px;
            }
            QLabel#volumeValue {
                color: #9ba2ac; font-family: "Cascadia Mono", "Consolas", monospace;
                font-size: 12px;
            }

            /* --- Inputs --- */
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QListWidget, QTableWidget {
                background: #2c3036; border: 1px solid #383d45; border-radius: 8px;
                padding: 7px 9px; selection-background-color: #4a78c8; color: #e7e9ec;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus,
            QListWidget:focus, QTableWidget:focus, QPushButton:focus, QToolButton:focus {
                border: 2px solid #79a8ff;
            }
            QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border: 1px solid #4a5260; }
            QLineEdit:read-only { background: #25282d; color: #b6bcc4; }
            QTextEdit#commandBox {
                font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px;
                color: #b6bcc4; background: #17181c;
            }
            QComboBox { padding-right: 34px; }
            QComboBox::drop-down {
                subcontrol-origin: padding; subcontrol-position: top right;
                background: #353a42; border: 0; border-left: 1px solid #383d45;
                border-top-right-radius: 7px; border-bottom-right-radius: 7px;
                width: 24px;
            }
            QComboBox::drop-down:hover { background: #434954; }
            QComboBox::down-arrow {
                image: url("__COMBO_DOWN_ICON__"); width: 10px; height: 6px;
            }
            QComboBox QAbstractItemView {
                background: #2c3036; border: 1px solid #434954; border-radius: 8px;
                selection-background-color: #4a78c8; outline: 0; padding: 4px;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                background: #353a42; border: 0; width: 20px; border-radius: 4px; margin: 1px;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover,
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: #434954; }
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
                image: url("__SPIN_UP_ICON__"); width: 10px; height: 6px;
            }
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
                image: url("__SPIN_DOWN_ICON__"); width: 10px; height: 6px;
            }

            /* --- Lists & tables --- */
            QListWidget { padding: 4px; }
            QListWidget::item {
                border-radius: 6px; padding: 8px; margin: 1px 0; color: #cdd2d9;
            }
            QListWidget::item:hover { background: #2c3036; }
            QListWidget::item:selected { background: #314a73; color: #f4f6f9; }
            QTableWidget {
                padding: 0; gridline-color: transparent; alternate-background-color: #262a30;
            }
            QTableWidget::item { padding: 6px 8px; border: 0; }
            QTableWidget::item:selected, QTableView::item:selected {
                background: #314a73; color: #f4f6f9;
            }
            QHeaderView::section {
                background: #222428; color: #a0a6af; padding: 7px 8px; border: 0;
                border-bottom: 1px solid #2f343b; font-size: 11px; font-weight: 600;
            }

            /* --- Buttons --- */
            QPushButton {
                background: #2f343b; color: #e2e5ea; border: 1px solid #3c424b;
                border-radius: 8px; padding: 8px 14px; font-weight: 500;
            }
            QPushButton:hover { background: #383e47; border: 1px solid #4a5260; }
            QPushButton:pressed { background: #2a2f36; }
            QPushButton:disabled { background: #25282d; color: #5b616b; border: 1px solid #2c3036; }
            QPushButton#primary {
                background: #416db3; color: #ffffff; border: 0; font-weight: 600;
            }
            QPushButton#primary:hover { background: #4776bd; }
            QPushButton#primary:pressed { background: #355f9f; }
            QPushButton#primary:disabled { background: #33425c; color: #8a96a8; }
            QPushButton#transport {
                background: #2f343b; color: #e2e5ea; min-width: 76px; padding: 9px 14px;
            }
            QPushButton#transport:hover { background: #383e47; }
            QPushButton#danger {
                background: transparent; color: #d98a8a; border: 1px solid #5a3f42;
            }
            QPushButton#danger:hover { background: #3a2a2c; color: #e8a0a0; }
            QPushButton#danger:disabled { background: transparent; color: #4a4044; border: 1px solid #322a2c; }
            QCheckBox { spacing: 8px; color: #c7ccd4; padding: 3px 0; }
            QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #68717f; border-radius: 4px; background: #2c3036; }
            QCheckBox::indicator:checked { background: #4a78c8; border-color: #79a8ff; }
            QCheckBox:focus { color: #ffffff; }

            /* --- Preview --- */
            QFrame#previewFrame {
                background: #050608; border: 1px solid #2f343b; border-radius: 12px;
            }
            QGraphicsView#previewView { background: #050608; border: 0; border-radius: 11px; }

            /* --- Slider --- */
            QSlider::groove:horizontal { height: 4px; background: #383d45; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #4a78c8; border-radius: 2px; }
            QSlider::handle:horizontal {
                background: #e7e9ec; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
            }
            QSlider::handle:horizontal:hover { background: #ffffff; }
            QSlider:focus::handle:horizontal { border: 2px solid #79a8ff; }
            QSlider:disabled::sub-page:horizontal { background: #383d45; }
            QSlider:disabled::handle:horizontal { background: #4a5260; }

            /* --- Progress --- */
            QProgressBar {
                background: #2c3036; border: 0; border-radius: 5px; height: 8px; text-align: center;
            }
            QProgressBar::chunk { background: #4a78c8; border-radius: 5px; }

            /* --- Scrollbars --- */
            QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
            QScrollBar::handle:vertical {
                background: #3c424b; border-radius: 5px; min-height: 28px;
            }
            QScrollBar::handle:vertical:hover { background: #4a5260; }
            QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
            QScrollBar::handle:horizontal {
                background: #3c424b; border-radius: 5px; min-width: 28px;
            }
            QScrollBar::handle:horizontal:hover { background: #4a5260; }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
            QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

            /* --- Status bar --- */
            QStatusBar {
                background: #16181b; color: #828892; border-top: 1px solid #2a2e34;
            }
            QStatusBar::item { border: 0; }
            QToolTip {
                background: #2c3036; color: #e7e9ec; border: 1px solid #434954;
                border-radius: 6px; padding: 5px 8px;
            }
            """
            .replace("__COMBO_DOWN_ICON__", (_ASSETS_DIR / "spin-down.svg").as_posix())
            .replace("__SPIN_UP_ICON__", (_ASSETS_DIR / "spin-up.svg").as_posix())
            .replace("__SPIN_DOWN_ICON__", (_ASSETS_DIR / "spin-down.svg").as_posix())
        )

    def _update_window_title(self) -> None:
        name = self.current_project_path.name if self.current_project_path else self.service.project.name
        self.setWindowTitle(f"{'*' if self.dirty else ''}{name} — Fast Video Editor")

    def _project_fingerprint(self) -> str:
        return json.dumps(project_to_dict(self.service.project), sort_keys=True, separators=(",", ":"))

    def _sync_dirty(self) -> None:
        dirty = self._project_fingerprint() != self._saved_fingerprint
        self._set_dirty(dirty)
        if not dirty:
            self.recovery_service.clear(self.service.project.id)

    def _set_dirty(self, value: bool = True) -> None:
        self.dirty = value
        if value:
            self._recovery_debounce.start(30000)
            if not self._recovery_deadline.isActive():
                self._recovery_deadline.start(60000)
        else:
            self._recovery_debounce.stop()
            self._recovery_deadline.stop()
        self._update_window_title()

    def _write_recovery(self) -> None:
        self._recovery_debounce.stop()
        self._recovery_deadline.stop()
        if not self.dirty:
            return
        try:
            self.recovery_service.snapshot(self.service.project, str(self.current_project_path or ""))
        except Exception as exc:
            self.statusBar().showMessage(f"Recovery snapshot failed: {exc}")

    def _offer_startup_recovery(self) -> None:
        record = self.recovery_service.latest_newer_than_saved()
        if record is None:
            return
        if QMessageBox.question(
            self,
            "Recover unsaved project?",
            "A newer recovery snapshot was found. Restore it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Discard,
            QMessageBox.StandardButton.Yes,
        ) != QMessageBox.StandardButton.Yes:
            self.recovery_service.clear(record.project_id)
            return
        try:
            project = self.recovery_service.load(record)
        except Exception as exc:
            QMessageBox.critical(self, "Recovery failed", str(exc))
            return
        self.service.set_project(project)
        self.current_project_path = Path(record.project_path) if record.project_path else None
        self._saved_fingerprint = ""
        self._set_dirty(True)
        self._reset_editor_state()
        self.statusBar().showMessage("Recovered unsaved project")

    def _begin_coalesced_edit(self, key: str) -> None:
        if self._edit_key != key:
            self.service.snapshot()
            self._edit_key = key
            self.undo_action.setEnabled(True)
            self.redo_action.setEnabled(False)
        if self._continuous_edit_key == key:
            return
        self._edit_timer.start(350)

    def _start_continuous_edit(self, key: str) -> None:
        self._begin_coalesced_edit(key)
        self._continuous_edit_key = key
        self._edit_timer.stop()

    def _finish_continuous_edit(self) -> None:
        self._continuous_edit_key = ""
        self._edit_key = ""

    def _save_export_defaults(self, *_args) -> None:
        defaults = self.service.project.export_defaults
        values = (
            self.codec.currentData() or VideoCodec.H264,
            self.backend.currentData() or HardwareBackend.AUTO,
            self.export_fps.value(),
            self.bitrate.value(),
            self.allow_stream_copy.isChecked(),
        )
        previous = (
            defaults.codec,
            defaults.hardware_backend,
            defaults.fps,
            defaults.bitrate_kbps,
            defaults.allow_stream_copy,
        )
        if values == previous:
            return
        (
            defaults.codec,
            defaults.hardware_backend,
            defaults.fps,
            defaults.bitrate_kbps,
            defaults.allow_stream_copy,
        ) = values
        self._set_dirty()

    def _apply_export_defaults(self) -> None:
        defaults = self.service.project.export_defaults
        widgets = [self.codec, self.backend, self.export_fps, self.bitrate, self.allow_stream_copy]
        for widget in widgets:
            widget.blockSignals(True)
        self.codec.setCurrentIndex(max(0, self.codec.findData(defaults.codec)))
        self.backend.setCurrentIndex(max(0, self.backend.findData(defaults.hardware_backend)))
        self.export_fps.setValue(defaults.fps)
        self.bitrate.setValue(defaults.bitrate_kbps)
        self.allow_stream_copy.setChecked(defaults.allow_stream_copy)
        for widget in widgets:
            widget.blockSignals(False)

    def set_mode(self, mode: str) -> None:
        if mode not in EXPORT_MODES:
            return
        info = EXPORT_MODES[mode]
        if (self.service.project.timeline.width, self.service.project.timeline.height) == (info["width"], info["height"]):
            self.mode = mode
            self._apply_mode_ui()
            return
        self.service.snapshot()
        self.mode = mode
        self.service.project.timeline.width = info["width"]
        self.service.project.timeline.height = info["height"]
        self._set_dirty()
        self._apply_mode_ui()
        self.statusBar().showMessage(f'{info["label"]} mode  ·  exports at {info["width"]}×{info["height"]}')

    def _sync_mode_from_timeline(self) -> None:
        timeline = self.service.project.timeline
        self.mode = "tiktok" if timeline.height > timeline.width else "youtube"
        self._apply_mode_ui()

    def _apply_mode_ui(self) -> None:
        timeline = self.service.project.timeline
        self.preview_area.set_canvas(timeline.width, timeline.height)
        self._refresh_preview_transform()
        for key, button in self.mode_buttons.items():
            button.setChecked(key == self.mode)

    def _refresh_preview_transform(self) -> None:
        """Re-apply the previewed clip's crop/scale/position to the canvas."""
        clip = self.service.clip_by_id(self.playing_clip_id) if self.playing_clip_id else None
        if clip is not None:
            asset = self.service.asset_by_id(clip.asset_id)
        else:
            asset = self.service.asset_by_id(self.selected_asset_id)
        self.preview_area.apply_clip(asset, clip)

    def refresh(self, media: bool = True) -> None:
        # Rebuilding the media bin re-reads thumbnails from disk. Skip it for
        # timeline-only edits (split/trim/move) so playback video never stalls
        # mid-frame — a stall there desyncs the audio that keeps draining.
        if media:
            # Media changed, so a relinked or newly imported asset may have a
            # different poster frame on disk than the one the canvas cached.
            self.timeline_canvas.thumbnails.clear()
            # Rebuild silently: clear() would otherwise emit selection changes
            # that reset the user's current media selection mid-refresh.
            self.media_list.blockSignals(True)
            self.media_list.clear()
            for asset in self.service.project.media:
                name = Path(asset.path).name
                offline = not Path(asset.path).is_file()
                prefix = "Offline — " if offline else ""
                item = QListWidgetItem(
                    f"{prefix}{name}\n{asset.width}x{asset.height} {asset.video_codec}  {_format_ms(asset.duration_ms)}"
                )
                item.setData(Qt.ItemDataRole.UserRole, asset.id)
                if offline:
                    item.setForeground(QColor("#e8a0a0"))
                thumb = thumbnail_path(asset, self.cache_dir)
                if thumb.exists():
                    item.setIcon(QIcon(str(thumb)))
                self.media_list.addItem(item)
                if asset.id == self.selected_asset_id:
                    item.setSelected(True)
            self.media_list.blockSignals(False)

        clips = self.service.video_track.clips
        self.timeline_table.blockSignals(True)
        self.timeline_table.setRowCount(len(clips))
        for row, clip in enumerate(clips):
            asset = self.service.asset_by_id(clip.asset_id)
            values = [
                str(row + 1),
                Path(asset.path).name if asset else "Missing media",
                _format_ms(clip.timeline_start_ms),
                _format_ms(clip.source_in_ms),
                _format_ms(clip.source_out_ms),
                _format_ms(clip.duration_ms),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, clip.id)
                self.timeline_table.setItem(row, column, item)
            if clip.id == self.selected_clip_id:
                self.timeline_table.selectRow(row)
        self.timeline_table.blockSignals(False)

        duration = self.service.timeline_duration_ms()
        self.playhead_slider.setMaximum(duration)
        self.playhead_slider.setEnabled(duration > 0)
        self.playhead_slider.setValue(min(self.playhead_ms, duration))
        self.timeline_canvas.set_selection(self.selected_clip_id, self.selected_text_id)
        self.timeline_canvas.set_playhead(self.playhead_ms)
        self.update_time_label()
        self._update_text_card()
        self._refresh_preview_text(force=True)

        master_pct = int(round(self.service.project.timeline.master_volume * 100))
        self.master_slider.blockSignals(True)
        self.master_slider.setValue(master_pct)
        self.master_slider.blockSignals(False)
        self.master_value.setText(f"{master_pct}%")

        if not self.selected_clip_id:
            self.clip_name.clear()
            self.clip_volume_slider.blockSignals(True)
            self.clip_volume_slider.setValue(100)
            self.clip_volume_slider.blockSignals(False)
            self.clip_volume_value.setText("100%")
        self.inspector_card.setEnabled(bool(self.selected_clip_id))
        self.add_button.setEnabled(bool(self.selected_asset_id))
        self.export_clip_button.setEnabled(bool(self.selected_clip_id) and not self._is_exporting())
        self.delete_action.setEnabled(bool(self.selected_clip_id))
        self.reset_clip_action.setEnabled(bool(self.selected_clip_id))
        self.undo_action.setEnabled(bool(self.service.undo_stack))
        self.redo_action.setEnabled(bool(self.service.redo_stack))
        selected_asset = self.service.asset_by_id(self.selected_asset_id)
        self.relink_action.setEnabled(bool(selected_asset and not Path(selected_asset.path).is_file()))
        self.relink_folder_action.setEnabled(any(not Path(asset.path).is_file() for asset in self.service.project.media))
        media_count = len(self.service.project.media)
        clip_count = len(clips)
        self.statusBar().showMessage(
            f"{media_count} media item{'s' if media_count != 1 else ''}  ·  "
            f"{clip_count} clip{'s' if clip_count != 1 else ''} on timeline"
        )

    def _reset_editor_state(self) -> None:
        """Clear selections and playback after the whole project is replaced."""
        self.selected_asset_id = ""
        self.selected_clip_id = ""
        self.selected_text_id = ""
        self.playing_clip_id = ""
        self.playhead_ms = 0
        self._stop_sampled_preview()
        self.player.stop()
        self.player.setSource(QUrl())
        self.command_box.clear()
        self.refresh()
        self._sync_mode_from_timeline()
        self._apply_export_defaults()

    def new_project(self) -> None:
        if not self._confirm_unsaved_changes():
            return
        self._stop_import_worker()
        self.service.set_project(Project())
        self.current_project_path = None
        self._saved_fingerprint = self._project_fingerprint()
        self._set_dirty(False)
        self._reset_editor_state()

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "Video Editor Project (*.json)")
        if not path:
            return
        try:
            project = load_project(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", f"The project was not changed.\n\n{exc}")
            return
        if not self._confirm_unsaved_changes():
            return
        self._stop_import_worker()
        self.service.set_project(project)
        self.current_project_path = Path(path)
        self._saved_fingerprint = self._project_fingerprint()
        self._set_dirty(False)
        self._reset_editor_state()
        missing = [asset.path for asset in project.media if not Path(asset.path).is_file()]
        if missing:
            shown = "\n".join(missing[:5])
            suffix = f"\n…and {len(missing) - 5} more" if len(missing) > 5 else ""
            QMessageBox.warning(self, "Missing media", f"Some media files could not be found:\n\n{shown}{suffix}")
        self._start_import_worker(assets=[asset for asset in project.media if Path(asset.path).is_file()])

    def save_project(self) -> bool:
        path = str(self.current_project_path or "")
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save Project", "", "Video Editor Project (*.json)")
        if not path:
            return False
        target = Path(path if Path(path).suffix else path + ".json")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            save_project(self.service.project, target)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", f"Your project remains unsaved.\n\n{exc}")
            self._set_dirty(True)
            return False
        self.current_project_path = target
        self._saved_fingerprint = self._project_fingerprint()
        self.recovery_service.clear(self.service.project.id)
        self._set_dirty(False)
        self.statusBar().showMessage(f"Saved {target}")
        return True

    def _confirm_unsaved_changes(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes to the current project?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        if answer == QMessageBox.StandardButton.Discard:
            self.recovery_service.clear(self.service.project.id)
            self._set_dirty(False)
            return True
        return False

    def import_media(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Media",
            "",
            "Media Files (*.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav);;All Files (*)",
        )
        self.import_paths(paths)

    def import_paths(self, paths: list[str]) -> None:
        if paths:
            self._start_import_worker(paths=paths, add_to_project=True)

    def relink_selected_media(self) -> None:
        asset = self.service.asset_by_id(self.selected_asset_id)
        if asset is None:
            QMessageBox.information(self, "Relink media", "Select an offline media item first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Relink Media", str(Path(asset.path).parent), "Media Files (*)")
        if not path:
            return
        try:
            replacement = probe_media(path, timeout_s=15)
            self.service.relink_asset(asset.id, replacement)
        except Exception as exc:
            QMessageBox.critical(self, "Relink failed", str(exc))
            return
        self._set_dirty()
        self.refresh()
        self._set_selection(asset_id=asset.id)
        self.statusBar().showMessage(f"Relinked {Path(path).name}")

    def relink_missing_from_folder(self) -> None:
        missing = [asset for asset in self.service.project.media if not Path(asset.path).is_file()]
        if not missing:
            QMessageBox.information(self, "Relink media", "The project has no offline media.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Relink Missing Media from Folder")
        if not folder:
            return
        candidates: dict[str, Path] = {}
        try:
            for path in Path(folder).rglob("*"):
                if path.is_file():
                    candidates.setdefault(path.name.casefold(), path)
        except OSError as exc:
            QMessageBox.critical(self, "Relink failed", str(exc))
            return
        replacements, unmatched = {}, []
        try:
            for asset in missing:
                candidate = candidates.get(Path(asset.path).name.casefold())
                if candidate is None:
                    unmatched.append(Path(asset.path).name)
                    continue
                replacements[asset.id] = probe_media(candidate, timeout_s=15)
            self.service.relink_assets(replacements)
        except Exception as exc:
            QMessageBox.critical(self, "Relink failed", f"No files were changed.\n\n{exc}")
            return
        if replacements:
            self._set_dirty()
            self.refresh()
        message = f"Relinked {len(replacements)} file(s)."
        if unmatched:
            message += f" {len(unmatched)} file(s) were not found by name."
        self.statusBar().showMessage(message)

    def _start_import_worker(self, paths: list[str] | None = None, assets=None, add_to_project: bool = False) -> None:
        if self.import_worker is not None and self.import_worker.isRunning():
            QMessageBox.information(self, "Import in progress", "Wait for the current media scan to finish.")
            return
        paths, assets = list(paths or []), list(assets or [])
        if not paths and not assets:
            return
        if paths:
            for path in paths:
                item = QListWidgetItem(f"{Path(path).name}\nAnalyzing…")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.media_list.addItem(item)
        self._import_add_assets = add_to_project
        self.import_worker = ImportWorker(self.cache_dir, paths=paths, assets=assets)
        self.import_worker.progress_changed.connect(self._on_import_progress)
        self.import_worker.completed.connect(self._on_import_finished)
        self.import_worker.start()

    def _on_import_progress(self, done: int, total: int, name: str) -> None:
        self.statusBar().showMessage(f"Analyzing media {done}/{total}: {name}")

    def _on_import_finished(self, assets, errors) -> None:
        if getattr(self, "_import_add_assets", False) and assets:
            self.service.add_media_batch(assets)
            self._set_dirty()
        self.refresh()
        if errors:
            details = "\n".join(f"{path}\n  {error}" for path, error in errors[:5])
            suffix = f"\n…and {len(errors) - 5} more" if len(errors) > 5 else ""
            QMessageBox.warning(self, "Some media could not be added", details + suffix)
        elif getattr(self, "_import_add_assets", False):
            self.statusBar().showMessage(f"Added {len(assets)} file{'s' if len(assets) != 1 else ''} to the media bin")

    def _stop_import_worker(self) -> None:
        if self.import_worker is not None and self.import_worker.isRunning():
            try:
                self.import_worker.completed.disconnect(self._on_import_finished)
            except RuntimeError:
                pass
            self.import_worker.cancel()
            self.import_worker.wait(5000)

    def select_media(self) -> None:
        items = self.media_list.selectedItems()
        asset_id = items[0].data(Qt.ItemDataRole.UserRole) if items else ""
        self._set_selection(asset_id=asset_id, preview=bool(asset_id))

    def add_selected_asset(self) -> None:
        if not self.selected_asset_id:
            return
        clip = self.service.add_asset_to_timeline(self.selected_asset_id)
        self._apply_export_defaults()
        self.refresh(media=False)
        self._set_dirty()
        self.select_clip_by_id(clip.id, seek=True)

    def select_clip(self) -> None:
        items = self.timeline_table.selectedItems()
        self._set_selection(clip_id=items[0].data(Qt.ItemDataRole.UserRole) if items else "")

    def seek_table_clip_start(self, row: int, _column: int) -> None:
        item = self.timeline_table.item(row, 0)
        if item is not None:
            self.select_clip_by_id(item.data(Qt.ItemDataRole.UserRole), seek=True)

    def _set_selection(self, asset_id: str = "", clip_id: str = "", *, seek: bool = False, preview: bool = False) -> None:
        clip = self.service.clip_by_id(clip_id) if clip_id else None
        if clip is not None:
            asset_id = clip.asset_id
        asset = self.service.asset_by_id(asset_id) if asset_id else None
        self.selected_asset_id = asset.id if asset is not None else ""
        self.selected_clip_id = clip.id if clip is not None else ""

        self.media_list.blockSignals(True)
        self.media_list.clearSelection()
        for row in range(self.media_list.count()):
            item = self.media_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == self.selected_asset_id:
                item.setSelected(True)
                self.media_list.scrollToItem(item)
                break
        self.media_list.blockSignals(False)

        self.timeline_table.blockSignals(True)
        self.timeline_table.clearSelection()
        for row in range(self.timeline_table.rowCount()):
            item = self.timeline_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == self.selected_clip_id:
                self.timeline_table.selectRow(row)
                self.timeline_table.scrollToItem(item)
                break
        self.timeline_table.blockSignals(False)
        # Clip and caption selection are mutually exclusive — one inspector card
        # at a time.
        self.selected_text_id = ""
        self.timeline_canvas.set_selection(self.selected_clip_id)
        self._update_text_card()
        self._update_inspector(clip)
        self.add_button.setEnabled(bool(self.selected_asset_id))
        self.delete_action.setEnabled(bool(self.selected_clip_id))
        self.reset_clip_action.setEnabled(bool(self.selected_clip_id))
        self.export_clip_button.setEnabled(bool(self.selected_clip_id) and not self._is_exporting())
        self.relink_action.setEnabled(bool(asset and not Path(asset.path).is_file()))

        if preview and asset is not None and not clip:
            self.playing_clip_id = ""
            self.load_asset(asset.path, 0, autoplay=False)
            self.statusBar().showMessage(f"Selected media: {Path(asset.path).name}")
        if seek and clip is not None:
            self.seek_timeline(clip.timeline_start_ms)

    def _update_inspector(self, clip) -> None:
        if clip is None:
            self.clip_name.clear()
            self.speed.blockSignals(True)
            self.speed.setValue(1.0)
            self.speed.blockSignals(False)
            self.speed_duration.setText("Duration: 00:00:00.000")
            self.inspector_card.setEnabled(False)
            return
        self.inspector_card.setEnabled(True)
        asset = self.service.asset_by_id(clip.asset_id)
        self.clip_name.setText(Path(asset.path).name if asset else "")
        volume_pct = int(round(clip.volume * 100))
        editors = [
            self.crop_left, self.crop_top, self.crop_right, self.crop_bottom,
            self.scale, self.pos_x, self.pos_y, self.rotation, self.opacity,
            self.clip_volume_slider, self.speed,
        ]
        for widget in editors:
            widget.blockSignals(True)
        self._configure_crop_limits(asset, clip)
        self.crop_left.setValue(clip.crop.left)
        self.crop_top.setValue(clip.crop.top)
        self.crop_right.setValue(clip.crop.right)
        self.crop_bottom.setValue(clip.crop.bottom)
        self.scale.setValue(int(clip.transform.scale_x * 100))
        self.pos_x.setValue(int(clip.transform.x))
        self.pos_y.setValue(int(clip.transform.y))
        self.rotation.setValue(int(round(clip.transform.rotation_deg)))
        self.opacity.setValue(int(round(clip.opacity * 100)))
        self.speed.setValue(clip.speed)
        self.clip_volume_slider.setValue(volume_pct)
        self.clip_volume_value.setText(f"{volume_pct}%")
        self.speed_duration.setText(f"Duration: {_format_ms(clip.duration_ms, True)}")
        self.speed.setEnabled(bool(asset and asset.has_video))
        self.speed_presets.setEnabled(bool(asset and asset.has_video))
        for widget in editors:
            widget.blockSignals(False)

    def _configure_crop_limits(self, asset, clip) -> None:
        width = asset.width if asset is not None and asset.has_video else 0
        height = asset.height if asset is not None and asset.has_video else 0
        self.crop_left.setMaximum(max(0, width - 1 - clip.crop.right))
        self.crop_right.setMaximum(max(0, width - 1 - clip.crop.left))
        self.crop_top.setMaximum(max(0, height - 1 - clip.crop.bottom))
        self.crop_bottom.setMaximum(max(0, height - 1 - clip.crop.top))
        for widget in (self.crop_left, self.crop_top, self.crop_right, self.crop_bottom):
            widget.setEnabled(width > 0 and height > 0)

    def update_clip_transform(self) -> None:
        clip = self.service.clip_by_id(self.selected_clip_id)
        if clip is None:
            return
        asset = self.service.asset_by_id(clip.asset_id)
        width = asset.width if asset is not None and asset.has_video else 0
        height = asset.height if asset is not None and asset.has_video else 0
        left, right = self.crop_left.value(), self.crop_right.value()
        top, bottom = self.crop_top.value(), self.crop_bottom.value()
        left, right = min(left, max(0, width - 1 - right)), min(right, max(0, width - 1 - left))
        top, bottom = min(top, max(0, height - 1 - bottom)), min(bottom, max(0, height - 1 - top))
        values = (left, top, right, bottom, self.scale.value(), self.pos_x.value(), self.pos_y.value(), self.rotation.value(), self.opacity.value())
        current = (
            clip.crop.left, clip.crop.top, clip.crop.right, clip.crop.bottom,
            int(round(clip.transform.scale_x * 100)), int(round(clip.transform.x)), int(round(clip.transform.y)),
            int(round(clip.transform.rotation_deg)), int(round(clip.opacity * 100)),
        )
        if values == current:
            return
        self._begin_coalesced_edit("clip-transform")
        clip.crop.left, clip.crop.top, clip.crop.right, clip.crop.bottom = left, top, right, bottom
        clip.crop.enabled = any(value > 0 for value in [clip.crop.left, clip.crop.top, clip.crop.right, clip.crop.bottom])
        clip.transform.scale_x = self.scale.value() / 100.0
        clip.transform.scale_y = self.scale.value() / 100.0
        clip.transform.x = self.pos_x.value()
        clip.transform.y = self.pos_y.value()
        clip.transform.rotation_deg = self.rotation.value()
        clip.opacity = self.opacity.value() / 100.0
        self._configure_crop_limits(asset, clip)
        self._set_dirty()
        self._refresh_preview_transform()

    def apply_speed_preset(self, index: int) -> None:
        value = self.speed_presets.itemData(index)
        if value is None:
            return
        self.speed.setValue(float(value))
        self.apply_clip_speed()
        self.speed_presets.setCurrentIndex(0)

    def apply_clip_speed(self) -> None:
        clip_id = self.selected_clip_id
        if self.service.set_clip_speed(clip_id, self.speed.value()):
            self.playhead_ms = min(self.playhead_ms, self.service.timeline_duration_ms())
            self._set_dirty()
            self.refresh(media=False)
            self._set_selection(clip_id=clip_id)

    def reset_timeline_properties(self) -> None:
        if not self.service.reset_timeline_properties():
            return
        self.playhead_ms = min(self.playhead_ms, self.service.timeline_duration_ms())
        self._set_dirty()
        self.refresh(media=False)
        self._sync_mode_from_timeline()
        self._apply_audio_volume()
        self.statusBar().showMessage("Timeline properties reset")

    def reset_selected_clip_properties(self) -> None:
        clip_id = self.selected_clip_id
        if not self.service.reset_clip_properties(clip_id):
            return
        self.playhead_ms = min(self.playhead_ms, self.service.timeline_duration_ms())
        self._set_dirty()
        self.refresh(media=False)
        self._set_selection(clip_id=clip_id)
        if self.playing_clip_id == clip_id:
            self.seek_timeline(self.playhead_ms)
        self.statusBar().showMessage("Selected clip properties reset")

    def on_master_volume_changed(self, value: int) -> None:
        self.master_value.setText(f"{value}%")
        if abs(self.service.project.timeline.master_volume - value / 100.0) <= 1e-6:
            return
        self._begin_coalesced_edit("master-volume")
        self.service.project.timeline.master_volume = value / 100.0
        self._set_dirty()
        self._apply_audio_volume()

    def on_clip_volume_changed(self, value: int) -> None:
        self.clip_volume_value.setText(f"{value}%")
        clip = self.service.clip_by_id(self.selected_clip_id)
        if clip is None or abs(clip.volume - value / 100.0) <= 1e-6:
            return
        self._begin_coalesced_edit("clip-volume")
        clip.volume = value / 100.0
        self._set_dirty()
        # The selected clip is the one loaded in the preview, so reflect it live.
        self._current_clip_volume = clip.volume
        self._apply_audio_volume()

    def remove_selected_clip(self) -> None:
        if self.selected_clip_id and self.service.remove_clip(self.selected_clip_id):
            if self.playing_clip_id == self.selected_clip_id:
                self.player.stop()
                self.playing_clip_id = ""
            self._set_selection(asset_id=self.selected_asset_id)
            self.playhead_ms = min(self.playhead_ms, self.service.timeline_duration_ms())
            self._set_dirty()
            self.refresh(media=False)

    def split_selected_clip(self) -> None:
        clip = self.service.clip_by_id(self.selected_clip_id) or self.service.clip_at_timeline(self.playhead_ms)
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        _dbg("SPLIT at", self.playhead_ms, "ms  (playing=", playing, ")")
        if clip and self.service.split_clip(clip.id, self.playhead_ms):
            self._set_dirty()
            self.refresh(media=False)

    def on_clip_moved(self, clip_id: str, new_start_ms: int) -> None:
        if self.service.move_clip(clip_id, new_start_ms):
            self._set_dirty()
            self.refresh(media=False)

    def on_clip_trimmed(self, clip_id: str, new_in, new_out) -> None:
        if self.service.trim_clip(clip_id, source_in_ms=new_in, source_out_ms=new_out):
            self._set_dirty()
            self.refresh(media=False)

    def undo(self) -> None:
        if self.service.undo():
            self._sync_dirty()
            self.playhead_ms = min(self.playhead_ms, self.service.timeline_duration_ms())
            self.refresh()
            self._apply_export_defaults()
            self._sync_mode_from_timeline()

    def redo(self) -> None:
        if self.service.redo():
            self._sync_dirty()
            self.playhead_ms = min(self.playhead_ms, self.service.timeline_duration_ms())
            self.refresh()
            self._apply_export_defaults()
            self._sync_mode_from_timeline()

    def select_clip_by_id(self, clip_id: str, seek: bool = True) -> None:
        self._set_selection(clip_id=clip_id, seek=seek)

    # --- Captions -----------------------------------------------------------

    def add_text_overlay(self) -> None:
        overlay = self.service.add_text(self.playhead_ms, self.playhead_ms + 3000)
        self._set_dirty()
        self.refresh(media=False)
        self.select_text_by_id(overlay.id)
        self.text_content.setFocus()
        self.text_content.selectAll()
        self.statusBar().showMessage("Caption added — drag its bar in the text row to retime it")

    def remove_text_overlay(self) -> None:
        if not self.service.remove_text(self.selected_text_id):
            return
        self._set_dirty()
        self.select_text_by_id("")
        self.refresh(media=False)
        self.statusBar().showMessage("Caption removed")

    def select_text_by_id(self, text_id: str) -> None:
        self.selected_text_id = text_id
        if text_id:
            # A caption and a clip are never selected at once.
            self.selected_clip_id = ""
            self.timeline_table.blockSignals(True)
            self.timeline_table.clearSelection()
            self.timeline_table.blockSignals(False)
            self._update_inspector(None)
        self.timeline_canvas.set_selection(self.selected_clip_id, text_id)
        self._update_text_card()
        self._refresh_preview_text(force=True)

    def on_text_range_changed(self, text_id: str, start_ms: int, end_ms: int) -> None:
        if not self.service.set_text_range(text_id, start_ms, end_ms):
            return
        self._set_dirty()
        self.refresh(media=False)
        self.select_text_by_id(text_id)

    def _update_text_card(self) -> None:
        overlay = self.service.text_by_id(self.selected_text_id)
        self.text_card.setVisible(overlay is not None)
        if overlay is None:
            return
        widgets = (
            self.text_content, self.text_font, self.text_size,
            self.text_outline, self.text_x, self.text_y,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.text_content.setText(overlay.text)
        self.text_font.setCurrentText(overlay.font)
        self.text_size.setValue(overlay.size_px)
        self.text_outline.setValue(overlay.outline_px)
        self.text_x.setValue(overlay.x_px)
        self.text_y.setValue(overlay.y_px)
        for widget in widgets:
            widget.blockSignals(False)
        self._paint_color_button(self.text_color_button, overlay.color)
        self._paint_color_button(self.text_outline_color_button, overlay.outline_color)

    @staticmethod
    def _paint_color_button(button: QPushButton, color: str) -> None:
        button.setText(color)
        button.setStyleSheet(
            f"background: {color}; color: {'#101215' if QColor(color).lightness() > 128 else '#f4f6f9'};"
        )

    def _pick_text_color(self, field: str) -> None:
        overlay = self.service.text_by_id(self.selected_text_id)
        if overlay is None:
            return
        chosen = QColorDialog.getColor(QColor(getattr(overlay, field)), self, "Caption colour")
        if not chosen.isValid():
            return
        self._commit_text_change(**{field: chosen.name()})

    def apply_text_properties(self, *_args) -> None:
        overlay = self.service.text_by_id(self.selected_text_id)
        if overlay is None:
            return
        self._commit_text_change(
            text=self.text_content.text(),
            font=self.text_font.currentText(),
            size_px=self.text_size.value(),
            outline_px=self.text_outline.value(),
            x_px=self.text_x.value(),
            y_px=self.text_y.value(),
        )

    def _commit_text_change(self, **fields) -> None:
        if not self.service.update_text(self.selected_text_id, **fields):
            return
        self._set_dirty()
        self._update_text_card()
        self.timeline_canvas.update()
        self._refresh_preview_text(force=True)

    def _on_preview_text_moved(self, x_px: int, y_px: int) -> None:
        """Committed after a drag of the caption in the preview canvas."""
        if not self._preview_text_id:
            return
        if self.service.update_text(self._preview_text_id, x_px=x_px, y_px=y_px):
            self._set_dirty()
            self._update_text_card()

    def _active_text_overlay(self):
        """The caption to show on the canvas: the selected one while editing,
        otherwise whatever covers the playhead."""
        overlay = self.service.text_by_id(self.selected_text_id)
        if overlay is not None:
            return overlay
        at_playhead = self.service.texts_at(self.playhead_ms)
        return at_playhead[-1] if at_playhead else None

    def _refresh_preview_text(self, force: bool = False) -> None:
        overlay = self._active_text_overlay()
        overlay_id = overlay.id if overlay is not None else ""
        # Playback ticks constantly; only touch the scene when it would change.
        if not force and overlay_id == self._preview_text_id:
            return
        self._preview_text_id = overlay_id
        self.preview_area.set_text(overlay)

    def select_relative_clip(self, offset: int) -> None:
        clips = self.service.video_track.clips
        if not clips:
            return
        index = next((i for i, clip in enumerate(clips) if clip.id == self.selected_clip_id), -1)
        self.select_clip_by_id(clips[min(max(0, index + offset), len(clips) - 1)].id)

    def move_selected_clip(self, offset: int) -> None:
        clips = self.service.video_track.clips
        index = next((i for i, clip in enumerate(clips) if clip.id == self.selected_clip_id), -1)
        target = index + offset
        if index < 0 or not 0 <= target < len(clips):
            return
        self.service.snapshot()
        clips[index], clips[target] = clips[target], clips[index]
        self.service.normalize_timeline()
        self._set_dirty()
        self.refresh(media=False)
        self.select_clip_by_id(self.selected_clip_id)

    def _set_play_button(self, playing: bool) -> None:
        self.play_button.setText("Pause" if playing else "Play")
        self.play_button.setIcon(self.pause_icon if playing else self.play_icon)

    def _apply_audio_volume(self) -> None:
        # Preview is capped at the device max (1.0); export still applies the
        # full master x clip gain up to 400%.
        master = self.service.project.timeline.master_volume
        gain = 0.0 if self._current_clip_speed >= 4.0 else max(0.0, master * self._current_clip_volume)
        self.audio_output.setVolume(min(1.0, gain))

    def load_asset(
        self,
        path: str,
        source_position_ms: int,
        autoplay: bool,
        clip_volume: float = 1.0,
        speed: float = 1.0,
    ) -> None:
        self._current_clip_volume = clip_volume
        self._current_clip_speed = speed
        self._apply_audio_volume()
        self.ignore_player_position = True
        # Only (re)load the file when it actually changes. Reloading the same
        # source on every seek hammers the platform media backend and can freeze
        # or crash playback (notably Media Foundation on Windows).
        url = QUrl.fromLocalFile(path)
        if self.player.source() != url:
            _dbg("load_asset: RELOAD source", Path(path).name)
            self.player.setSource(url)
        self.player.setPlaybackRate(speed if speed <= 4.0 else 1.0)
        _dbg("load_asset: SEEK to", source_position_ms, "ms  autoplay", autoplay)
        self.player.setPosition(max(0, source_position_ms))
        self.ignore_player_position = False
        self._refresh_preview_transform()
        if autoplay:
            self.player.play()
            self._set_play_button(True)
        else:
            self.player.pause()
            self._set_play_button(False)

    def seek_timeline(self, timeline_ms: int) -> None:
        duration = self.service.timeline_duration_ms()
        self.playhead_ms = max(0, min(timeline_ms, duration))
        self.playhead_slider.setValue(self.playhead_ms)
        self.timeline_canvas.set_playhead(self.playhead_ms)
        self.update_time_label()
        self._refresh_preview_text()

        clip = self.service.clip_at_timeline(self.playhead_ms)
        if clip is None:
            self.player.pause()
            return
        self._set_selection(clip_id=clip.id)
        asset = self.service.asset_by_id(clip.asset_id)
        if asset is None:
            return

        source_position = clip.source_in_ms + int(
            round(max(0, self.playhead_ms - clip.timeline_start_ms) * clip.speed)
        )
        is_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.playing_clip_id = clip.id
        self.load_asset(asset.path, source_position, autoplay=is_playing, clip_volume=clip.volume, speed=clip.speed)

    def toggle_playback(self) -> None:
        if self._sampled_timer.isActive():
            self._stop_sampled_preview()
            self._set_play_button(False)
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self._set_play_button(False)
            return
        if self.service.timeline_duration_ms() > 0:
            self.play_timeline_from(self.playhead_ms)
            return
        asset = self.service.asset_by_id(self.selected_asset_id)
        if asset is not None:
            self.load_asset(asset.path, self.player.position(), autoplay=True)

    def play_timeline_from(self, timeline_ms: int) -> None:
        clip = self.service.clip_at_timeline(timeline_ms)
        if clip is None:
            return
        self._set_selection(clip_id=clip.id)
        asset = self.service.asset_by_id(clip.asset_id)
        if asset is None:
            return
        self.playhead_ms = max(clip.timeline_start_ms, min(timeline_ms, clip.timeline_start_ms + clip.duration_ms))
        source_position = clip.source_in_ms + int(
            round(max(0, self.playhead_ms - clip.timeline_start_ms) * clip.speed)
        )
        self.playing_clip_id = clip.id
        self.timeline_canvas.set_playhead(self.playhead_ms)
        if clip.speed > 4.0:
            self.load_asset(asset.path, source_position, autoplay=False, clip_volume=clip.volume, speed=clip.speed)
            self._sampled_clock.start()
            self._sampled_timer.start()
            self._set_play_button(True)
        else:
            self.load_asset(asset.path, source_position, autoplay=True, clip_volume=clip.volume, speed=clip.speed)

    def _stop_sampled_preview(self) -> None:
        self._sampled_timer.stop()

    def _advance_sampled_preview(self) -> None:
        if not self._sampled_clock.isValid():
            self._sampled_clock.start()
            return
        elapsed = max(1, self._sampled_clock.restart())
        target = self.playhead_ms + elapsed
        if target >= self.service.timeline_duration_ms():
            self._stop_sampled_preview()
            self.seek_timeline(self.service.timeline_duration_ms())
            self._set_play_button(False)
            return
        clip = self.service.clip_at_timeline(target)
        if clip is not None and clip.speed <= 4.0:
            self._stop_sampled_preview()
            self.play_timeline_from(target)
            return
        self.seek_timeline(target)
        self._set_play_button(True)

    def _is_seamless_continuation(self, current, following) -> bool:
        """True when playback can flow from one clip into the next with no seek
        (same source file, picking up exactly where this clip ends — e.g. a split)."""
        return (
            following.asset_id == current.asset_id
            and abs(following.source_in_ms - current.source_out_ms) <= 1
            and abs(following.speed - current.speed) <= 1e-6
        )

    def on_player_position_changed(self, position_ms: int) -> None:
        if self.ignore_player_position or not self.playing_clip_id:
            return
        clip = self.service.clip_by_id(self.playing_clip_id)
        if clip is None:
            return
        # Walk forward through any clips we've already played past. Contiguous
        # same-source clips (a split chain) are adopted with NO seek so the
        # player keeps decoding one continuous stream. We only seek when the
        # next clip is a genuine cut (different file or a gap in the source).
        while position_ms >= clip.source_out_ms:
            next_clip = self.service.next_clip_after(clip.id)
            if next_clip is None:
                _dbg("end of timeline at", position_ms, "-> pause")
                self.player.pause()
                self._set_play_button(False)
                self.seek_timeline(clip.timeline_start_ms + clip.duration_ms)
                return
            if not self._is_seamless_continuation(clip, next_clip):
                _dbg(
                    "non-seamless cut -> SEEK. same_asset=",
                    next_clip.asset_id == clip.asset_id,
                    "gap_ms=",
                    next_clip.source_in_ms - clip.source_out_ms,
                )
                self.play_timeline_from(next_clip.timeline_start_ms)
                return
            _dbg("seamless advance", clip.id[:6], "->", next_clip.id[:6], "(no seek)")
            if abs(next_clip.volume - self._current_clip_volume) > 1e-6:
                self._current_clip_volume = next_clip.volume
                self._apply_audio_volume()
            self._current_clip_speed = next_clip.speed
            self.player.setPlaybackRate(next_clip.speed)
            self.playing_clip_id = next_clip.id
            self._set_selection(clip_id=next_clip.id)
            clip = next_clip
            # The adopted clip may carry different crop/scale/position.
            self._refresh_preview_transform()

        self.timeline_canvas.set_selection(self.playing_clip_id)
        self.playhead_ms = clip.timeline_start_ms + int(
            round(max(0, position_ms - clip.source_in_ms) / clip.speed)
        )
        self.playhead_slider.blockSignals(True)
        self.playhead_slider.setValue(self.playhead_ms)
        self.playhead_slider.blockSignals(False)
        self.timeline_canvas.set_playhead(self.playhead_ms)
        self.update_time_label()
        self._refresh_preview_text()

    def on_media_status_changed(self, _status) -> None:
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._set_play_button(False)

    def update_time_label(self) -> None:
        self.time_label.setText(f"{_format_ms(self.playhead_ms, True)} / {_format_ms(self.service.timeline_duration_ms(), True)}")

    def step_frame(self, direction: int) -> None:
        frame_ms = max(1, int(round(1000 / self.service.project.timeline.fps)))
        self.seek_timeline(self.playhead_ms + direction * frame_ms)

    def set_snapping(self, enabled: bool) -> None:
        self.snap_action.blockSignals(True)
        self.snap_action.setChecked(enabled)
        self.snap_action.blockSignals(False)
        self.settings.setValue("timeline/snapping", enabled)
        if hasattr(self, "timeline_canvas"):
            self.timeline_canvas.set_snapping(enabled)
        self.statusBar().showMessage(f"Snapping {'enabled' if enabled else 'disabled'}")

    def set_trim_in_to_playhead(self) -> None:
        clip = self.service.clip_by_id(self.selected_clip_id) or self.service.clip_at_timeline(self.playhead_ms)
        if clip is None:
            return
        source_position = clip.source_in_ms + int(
            round(max(0, self.playhead_ms - clip.timeline_start_ms) * clip.speed)
        )
        if self.service.trim_clip(clip.id, source_in_ms=source_position):
            self._set_dirty()
            self.seek_timeline(clip.timeline_start_ms)
            self.refresh(media=False)

    def set_trim_out_to_playhead(self) -> None:
        clip = self.service.clip_by_id(self.selected_clip_id) or self.service.clip_at_timeline(self.playhead_ms)
        if clip is None:
            return
        source_position = clip.source_in_ms + int(
            round(max(0, self.playhead_ms - clip.timeline_start_ms) * clip.speed)
        )
        if self.service.trim_clip(clip.id, source_out_ms=source_position):
            self._set_dirty()
            self.seek_timeline(clip.timeline_start_ms)
            self.refresh(media=False)

    def pick_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Video", "", "MP4 Video (*.mp4);;Matroska Video (*.mkv);;All Files (*)")
        if path:
            self.output_path.setText(path)

    def _is_exporting(self) -> bool:
        return self.export_worker is not None and self.export_worker.isRunning()

    def export_project(self) -> None:
        output = self.output_path.text().strip()
        if not output:
            self.pick_output()
            output = self.output_path.text().strip()
        if not output:
            return
        if not Path(output).suffix:
            output = output + ".mp4"
            self.output_path.setText(output)
        self._begin_export(self.service.project, output, "timeline")

    def _single_clip_project(self, clip) -> Project:
        """A throwaway project holding just one clip, rendered on the same canvas."""
        source = self.service.project
        only_clip = deepcopy(clip)
        only_clip.timeline_start_ms = 0
        timeline = Timeline(
            width=source.timeline.width,
            height=source.timeline.height,
            fps=source.timeline.fps,
            master_volume=source.timeline.master_volume,
            tracks=[Track(type=TrackType.VIDEO, clips=[only_clip])],
        )
        return Project(media=list(source.media), timeline=timeline, export_defaults=source.export_defaults)

    def export_selected_clip(self) -> None:
        clip = self.service.clip_by_id(self.selected_clip_id)
        if clip is None:
            QMessageBox.information(
                self, "No clip selected", "Select a clip in the timeline to export it on its own."
            )
            return
        asset = self.service.asset_by_id(clip.asset_id)
        default_name = (Path(asset.path).stem if asset else "clip") + "_clip.mp4"
        existing = self.output_path.text().strip()
        suggested = str(Path(existing).parent / default_name) if existing else default_name
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Selected Clip", suggested, "MP4 Video (*.mp4);;Matroska Video (*.mkv);;All Files (*)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path = path + ".mp4"
        self._begin_export(self._single_clip_project(clip), path, "selected clip")

    def _begin_export(self, project: Project, output: str, label: str) -> None:
        if self._is_exporting():
            QMessageBox.information(
                self, "Export in progress", "An export is already running. Wait for it to finish or cancel it."
            )
            return

        final_path = Path(output).expanduser().resolve()
        error = self._preflight_export(project, final_path)
        if error:
            QMessageBox.critical(self, "Cannot export", error)
            return
        if final_path.exists() and QMessageBox.question(
            self,
            "Replace existing file?",
            f"{final_path.name} already exists. Replace it only after export succeeds?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{final_path.stem}.", suffix=final_path.suffix, dir=final_path.parent
            )
            os.close(descriptor)
            os.unlink(temporary)
        except OSError as exc:
            QMessageBox.critical(self, "Cannot export", f"Could not create a temporary output file.\n\n{exc}")
            return

        codec = self.codec.currentData() or VideoCodec.H264
        backend = self.backend.currentData() or HardwareBackend.AUTO
        profile = ExportProfile(
            output_path=temporary,
            codec=codec,
            hardware_backend=backend,
            width=project.timeline.width,
            height=project.timeline.height,
            fps=self.export_fps.value(),
            bitrate_kbps=self.bitrate.value(),
            allow_stream_copy=self.allow_stream_copy.isChecked(),
            master_volume=project.timeline.master_volume,
        )
        capabilities = detect_hardware_cached()
        resolved = choose_backend(capabilities, profile.hardware_backend, profile.codec)
        if backend not in (HardwareBackend.AUTO, resolved):
            QMessageBox.warning(
                self, "Encoder fallback",
                f"{backend.value.upper()} cannot encode {codec.value.upper()} on this system. Using CPU instead.",
            )
        plan = build_render_plan(project, profile, resolved)
        if plan.route == RenderRoute.REENCODE and not capabilities.supports(resolved, profile.codec):
            Path(temporary).unlink(missing_ok=True)
            QMessageBox.critical(
                self, "Encoder unavailable",
                f"No installed FFmpeg encoder can produce {codec.value.upper()} with the selected settings.",
            )
            return
        if not plan.clips:
            Path(temporary).unlink(missing_ok=True)
            QMessageBox.warning(self, "Nothing to export", plan.reason or "The timeline has no clips to export.")
            return
        self.command_box.clear()

        # Smart render: when the alternative is a full reencode, copy the
        # untouched clips and only reencode the ones that actually changed.
        segments = None
        if plan.route == RenderRoute.REENCODE:
            segments = plan_smart_segments(project, profile, plan.clips, plan.assets)

        if segments is not None:
            jobs = build_smart_render_commands(segments, profile)
            encoded = sum(1 for seg in segments if seg.encode)
            copied = len(segments) - encoded
            self.command_box.append(
                f"Exporting {label}  ·  Smart render: {copied} copied, {encoded} reencoded "
                f"({len(segments)} segments)"
            )
            for command, _ in jobs:
                self.command_box.append(command.to_shell_string())
        else:
            command = build_ffmpeg_command(plan, profile)
            total = sum(clip.duration_ms for clip in plan.clips) or plan.clip.duration_ms
            jobs = [(command, max(1, total))]
            self.command_box.append(f"Exporting {label}  ·  Route: {plan.route.value}  ·  Backend: {plan.backend.value}")
            if plan.reason:
                self.command_box.append(f"Reason: {plan.reason}")
            self.command_box.append(command.to_shell_string())
        self.command_box.append("")

        self.export_output_path = str(final_path)
        self.export_temp_path = temporary
        self._export_expected_fps = profile.fps
        self._export_expected_codec = profile.codec
        self.progress.setValue(0)
        self.export_button.setEnabled(False)
        self.export_clip_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.statusBar().showMessage(f"Exporting {label}…")
        self.export_worker = ExportWorker(jobs)
        self.export_worker.progress_changed.connect(self.on_export_progress)
        self.export_worker.log_line.connect(self.on_export_log)
        self.export_worker.finished_with_code.connect(self.on_export_finished)
        self.export_worker.start()

    def _preflight_export(self, project: Project, output: Path) -> str:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            return "FFmpeg and ffprobe must both be available on PATH."
        if not output.suffix:
            return "Choose an output file with a container extension."
        if not output.parent.is_dir():
            return "The output folder does not exist."
        if output.exists() and not output.is_file():
            return "The output path is not a regular file."
        if not os.access(output.parent, os.W_OK):
            return "The output folder is not writable."
        if shutil.disk_usage(output.parent).free < 1024 * 1024:
            return "The output drive has less than 1 MB free."
        source_paths = {str(Path(asset.path).expanduser().resolve()) for asset in project.media}
        if str(output) in source_paths:
            return "The output file cannot replace source media used by the project."
        assets = {asset.id: asset for asset in project.media}
        for track in project.timeline.tracks:
            for clip in track.clips:
                asset = assets.get(clip.asset_id)
                if asset is None or not Path(asset.path).is_file():
                    return f"Media for clip {clip.id or '<unnamed>'} is missing."
                if asset.has_video and (
                    clip.crop.left + clip.crop.right >= asset.width
                    or clip.crop.top + clip.crop.bottom >= asset.height
                ):
                    return f"Crop settings for {Path(asset.path).name} leave no visible pixels."
        return ""

    def on_export_progress(self, value: float, line: str) -> None:
        if value > 0:
            self.progress.setValue(int(value * 100))
        if line:
            self.statusBar().showMessage(line)

    def on_export_log(self, line: str) -> None:
        self.command_box.append(line)

    def on_export_finished(self, code: int) -> None:
        self.export_button.setEnabled(True)
        self.export_clip_button.setEnabled(bool(self.selected_clip_id))
        self.cancel_button.setEnabled(False)
        self.progress.setValue(100 if code == 0 else self.progress.value())
        temporary = Path(self.export_temp_path) if self.export_temp_path else None
        if code == 0 and temporary is not None:
            try:
                rendered = probe_media(temporary, timeout_s=15)
                if not rendered.has_video:
                    raise RuntimeError("The exported file contains no video stream")
                if abs(rendered.fps - self._export_expected_fps) > 0.05:
                    raise RuntimeError(f"Expected {self._export_expected_fps:g} fps, got {rendered.fps:g} fps")
                codec_aliases = {
                    VideoCodec.H264: {"h264", "avc", "avc1"},
                    VideoCodec.H265: {"hevc", "h265", "hev1", "hvc1"},
                    VideoCodec.AV1: {"av1", "av01"},
                }
                if rendered.video_codec.lower() not in codec_aliases[self._export_expected_codec]:
                    raise RuntimeError(
                        f"Expected {self._export_expected_codec.value.upper()}, got "
                        f"{rendered.video_codec or 'an unknown codec'}"
                    )
                os.replace(temporary, self.export_output_path)
                msg = f"Export complete: {self.export_output_path}"
            except Exception as exc:
                code = -1
                msg = f"Export validation failed; the previous output was preserved: {exc}"
        elif self.export_worker is not None and self.export_worker.cancelled:
            msg = "Export cancelled"
        else:
            msg = f"Export failed with code {code} (see FFmpeg log)"
        if code != 0 and temporary is not None:
            temporary.unlink(missing_ok=True)
        self.statusBar().showMessage(msg)
        self.command_box.append(msg)
        self.export_temp_path = ""

    def cancel_export(self) -> None:
        if self.export_worker is not None:
            self.export_worker.cancel()
            self.statusBar().showMessage("Cancelling export")
            QTimer.singleShot(2000, self._kill_export_if_running)

    def _kill_export_if_running(self) -> None:
        worker = self.export_worker
        if worker is not None and worker.isRunning() and worker.process and worker.process.poll() is None:
            worker.process.kill()

    def closeEvent(self, event) -> None:
        self._stop_sampled_preview()
        if self._is_exporting():
            answer = QMessageBox.question(
                self,
                "Export in progress",
                "Cancel the export and close the editor? The existing output file will be preserved.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            assert self.export_worker is not None
            self.export_worker.force_stop(2000)
            if self.export_temp_path:
                Path(self.export_temp_path).unlink(missing_ok=True)
        if not self._confirm_unsaved_changes():
            event.ignore()
            return
        self._stop_import_worker()
        QApplication.instance().removeEventFilter(self)
        event.accept()


def run_app() -> int:
    app = QApplication(sys.argv)

    # Since PySide6 6.5, log unhandled slot exceptions instead of aborting.
    sys.excepthook = traceback.print_exception

    window = MainWindow()
    window.show()
    return app.exec()
