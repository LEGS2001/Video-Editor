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
- See a poster frame on each timeline clip bar, reusing the media bin's cached thumbnail.
- Read the timeline as separate video and audio rows; the audio row mirrors each clip's span and is outlined when the media has no audio track.
- Split the active clip at the playhead.
- Trim clips with playhead-based In/Out buttons or one-second nudges.
- Duplicate a clip with `Ctrl+D`, or copy and paste one with `Ctrl+C` / `Ctrl+V`.
- Fade a clip in or out, video and audio together, and watch the fade dim the preview as the playhead crosses it; a fade forces a reencode and is clamped to the clip's length.
- Change video speed from `0.25x` to `100x`; audio keeps its pitch below `4x` and is silent from `4x`.
- Choose the export frame rate from `1` to `240` FPS; new projects default to `60` FPS and adopt the first video clip's FPS.
- Undo and Redo up to 30 project states.
- Autosave atomic recovery snapshots and restore newer work after an unexpected close.
- Keep projects open when media is offline, then relink one file or a whole folder.
- Add caption text with a chosen font, size, fill colour, and outline; drag its bar in the timeline's text row to retime it and drag the caption itself in the preview to position it. Captions are burned in on export and force a reencode.
- Play and pause the preview with `Space`.
- Navigate by frame with `,` and `.`, jump with `Home`/`End`, and toggle timeline snapping with `N`.
- Reopen recent projects from `File > Open Recent`, and list every keyboard shortcut with `F1`.
- Reset timeline-level properties or the selected clip's editable properties from the top toolbar; both operations support Undo.
- Export untouched compatible clips through FFmpeg stream copy, including compatible container remuxes.
- Smart-render mixed timelines by copying untouched clips and reencoding only changed clips when their streams can be concatenated safely.

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
- Crop, scale, position, opacity, rotation, fades, resolution changes, FPS changes, or incompatible media force reencode.
- Any clip whose speed differs from `1x` is reencoded; untouched smart-render segments may still be copied.
- Auto backend prefers VAAPI, then NVENC, AMF, QSV, and finally CPU.
- Project files remain JSON version `1`; older files without `speed` load at `1x`, files without `texts` load with no captions, and files without fade fields load with no fades.
