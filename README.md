# Fast Video Editor

Python/PySide6 video editor focused on fast FFmpeg exports. The app keeps the render-heavy work in `ffmpeg` and keeps Python responsible for project state, timeline editing, media probing, command planning, and UI.

## Stack

- Python 3.11+
- PySide6 Qt Widgets UI
- FFmpeg CLI for export, thumbnails, and fastest stream-copy routes
- ffprobe CLI for media metadata
- Standard-library core modules with tests that do not require the UI

## Current Editor Features

- Import multiple media files into a media bin.
- Add media as ordered timeline clips.
- Preview and play video inside the GUI with PySide6 multimedia.
- Click the timeline to seek between clips.
- Drag across the timeline ruler to scrub the playhead in real time; double-click a clip row to jump to its start.
- Split the active clip at the playhead.
- Trim clips with playhead-based In/Out buttons or one-second nudges.
- Change video speed from `0.25x` to `100x`; audio keeps its pitch below `4x` and is silent from `4x`.
- Undo and Redo up to 30 project states.
- Autosave atomic recovery snapshots and restore newer work after an unexpected close.
- Keep projects open when media is offline, then relink one file or a whole folder.
- Navigate by frame with `,` and `.`, jump with `Home`/`End`, and toggle timeline snapping with `N`.
- Reset timeline-level properties or the selected clip's editable properties from the top toolbar; both operations support Undo.
- Export through FFmpeg with stream-copy when possible.

## Project Layout

```text
src/video_editor/
  models.py           Domain dataclasses and enums
  project_io.py       Versioned JSON project load/save
  project_service.py  Timeline editing and undo helpers
  render_planner.py   Stream-copy vs reencode decisions
  hardware.py         FFmpeg encoder and hardware backend detection
  ffmpeg.py           FFmpeg command builder and executor
  media.py            ffprobe metadata and thumbnail generation
  ui.py               PySide6 interface
  __main__.py         App entry point
tests/
  test_*.py           Core behavior tests
```

## Install

```bash
uv sync
```

Runtime tools:

- `ffmpeg`
- `ffprobe`
- `nvidia-smi` is optional for NVENC detection

## Run

```bash
uv run video-editor
```

or:

```bash
uv run python -m video_editor
```

## Test

```bash
uv run --extra test pytest -q
```

Without extra dependencies, the core can still be syntax-checked:

```bash
uv run python -m compileall -q src tests
```

Run the same static checks used by CI:

```bash
uv lock --check
uv run --extra dev ruff check src tests
```

Tests marked `ffmpeg` are integration tests. CI runs them only when both
`ffmpeg` and `ffprobe` are available.

## Preserved Behavior

- Compatible single clips export with `-c copy`.
- Compatible multiple clips use FFmpeg concat demuxer with stream copy.
- Crop, scale, position, opacity, rotation, resolution changes, FPS changes, or incompatible media force reencode.
- Any clip whose speed differs from `1x` is reencoded; untouched smart-render segments may still be copied.
- Auto backend prefers VAAPI, then NVENC, AMF, QSV, and finally CPU.
- Project files remain JSON version `1`; older files without `speed` load at `1x`.
