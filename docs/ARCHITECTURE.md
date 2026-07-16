# Architecture

The editor is split into a pure-Python core and a thin PySide6 interface.

## Core

- `models.py` defines dataclasses and enums. These types have no UI dependencies.
- `project_io.py` reads and writes versioned JSON project files.
- `project_service.py` owns timeline mutations, normalization, and undo snapshots.
- `render_planner.py` decides whether an export can use stream copy or must reencode.
- `hardware.py` detects FFmpeg hardware encoders and resolves the requested backend.
- `ffmpeg.py` builds and executes FFmpeg commands.
- `media.py` probes files with ffprobe and generates thumbnails with FFmpeg.

## UI

`ui.py` uses Qt Widgets through PySide6. It keeps UI state local and delegates project changes to `ProjectService`. Export command creation stays in the core modules so it can be tested without launching Qt.

## Export Flow

1. Probe media with `ffprobe`.
2. Add assets to the single video timeline.
3. Build an `ExportProfile` from UI settings.
4. Detect hardware and resolve `auto` backend.
5. Build a `RenderPlan`.
6. Build and run the FFmpeg command.

The fastest path is always preferred: compatible clips use stream copy; visual transforms, incompatible media, resolution changes, or FPS changes force reencode.
