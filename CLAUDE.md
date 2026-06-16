# Project: SmugMug ↔ Local Folder Two-Way Sync Tool (with Web UI)

## Development setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) (no Python required first):
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Run the app locally (uv downloads Python 3.12 automatically on first run):
```powershell
$env:SMUGLY_PHOTO_DIR="C:\path\to\photos"; $env:SMUGLY_CONFIG_DIR="C:\path\to\config"; uv run uvicorn smugly.main:app --reload
```

Run via Docker (matches production):
```powershell
copy .env.example .env   # fill in API keys
$env:PHOTO_DIR="C:\path\to\photos"; docker compose up --build
```

Dependencies are declared in `pyproject.toml`. `requirements.txt` mirrors them for Docker.

## Goal
A Python web application, packaged as a Docker container, that runs on my
NAS and keeps a local directory of photos in two-way sync with a SmugMug
account. It should have a web UI for monitoring sync status, managing a
queue of pending actions, reviewing conflicts, browsing both sides as a
tree, and triggering/configuring syncs (including on a subfolder-only
basis).

## Environment
- Runs as a Docker container on a NAS
- Python 3.x backend
- Local photo directory mounted as a volume into the container
- A small persistent volume for config (the state database itself lives
  inside the synced directory — see below)

## Frontend Preference
I have limited web framework experience. I'd like to use FastAPI with
Jinja2 templates and HTMX for interactivity (tree expand/collapse, live
queue/worker status updates), avoiding a separate JS frontend build. If you
think this is a poor fit for any specific requirement below, let me know
and propose an alternative — but please keep the stack as simple and
Python-centric as possible.

## Scope
- Photos only, limited to formats supported by SmugMug's standard (non-
  Source) plans: JPEG (.jpg/.jpeg), PNG (.png), GIF (.gif), and HEIC (.heic).
- Other file types (RAW formats, TIFF, PSD, videos, etc.) should be ignored
  by the scanner entirely — not synced, not flagged as errors, simply
  treated as non-photo files for the purposes of the directory structure
  rule below.
- Note: SmugMug enforces a per-file limit of ~500MB and 4 gigapixels on
  these formats. Files exceeding this should be flagged as upload errors
  rather than silently failing.

## Core Sync Requirements
- Use the SmugMug API (v2, OAuth 1.0a) to read/write albums and images
- State database is a single file (e.g. SQLite) stored in the root of the
  local photo directory. The path/filename should be selectable/configurable
  via the web UI (e.g. defaults to `.sync_state.db` but user can rename or
  relocate it within the directory)
- The state database tracks file hashes, modification times, and SmugMug
  image/album IDs, to detect:
  - new local files → queue upload
  - new remote files → queue download
  - edits/renames on either side
  - deletions on either side

## Conflict & Deletion Handling (fixed behavior — not configurable for v1)
- DELETIONS: if a file is deleted on one side, do NOT delete it on the other
  side automatically. Instead, move the remote-or-local counterpart into a
  local "quarantine" folder (e.g. `_deleted/` inside the synced directory,
  preserving relative path) and record this action in the history log. The
  state database should stop tracking that file as "in sync" going forward.
- EDIT CONFLICTS: if a file has changed on BOTH sides since the last sync
  (different hash on both ends vs. last known state), always flag it as a
  conflict for manual resolution in the UI (keep local / keep remote / keep
  both). Never auto-resolve based on timestamps.
- Quarantine folder, like the state DB, must be excluded from the
  "directory contains only photos OR only subdirectories" structure rule.

## Local-to-SmugMug Structure Mapping
- Local directories map to SmugMug Folders; directories containing photo
  files map to SmugMug Galleries (albums), since SmugMug does not allow
  images directly inside Folders — only inside Galleries.
- CONSTRAINT: every local directory must contain EITHER only subdirectories
  OR only photo files (not both, and not be ambiguous/empty). The sync tool
  should validate this on scan and flag any directory that violates the
  rule as an error/conflict requiring manual cleanup, rather than guessing.
- SmugMug supports up to 5 levels of nested Folders below the root, plus a
  Gallery at the end (~7 layers total). If the local directory structure
  exceeds this depth, flag it as an error during scan.
- Please show me an example mapping using a sample directory tree (including
  an example of a directory that violates the constraint, and how it would
  be flagged) before writing code.

## Dual Tree View (Local vs SmugMug)
- The UI must show a side-by-side tree view: local directory structure on
  one side, corresponding SmugMug folder/gallery structure on the other,
  aligned node-by-node based on the structure mapping above.
- Each node (folder/gallery and individual photo, where expanded) should be
  color-coded based on sync state, e.g.:
  - in sync / matching
  - local-only (pending upload)
  - remote-only (pending download)
  - conflict (changed on both sides)
  - structure error (violates folder/gallery rule)
- This tree is the primary mechanism for scoped sync: I should be able to
  select any folder/subtree in the tree and trigger "Scan now" or queue
  actions limited to that subtree only, rather than always operating on the
  whole directory.
- Propose how to keep this tree reasonably performant for large photo
  collections (e.g. lazy-loading subtrees, caching SmugMug structure
  locally, only diffing on demand).

## Action Queue & Worker
- Scanning/diffing (comparing local vs remote state) populates a persistent
  queue of pending actions (upload X, download Y, quarantine Z, flag
  conflict, etc.)
- Scans can be scoped to a subtree (selected via the tree view) or the
  whole directory.
- A separate background worker thread processes this queue, performing the
  actual uploads/downloads/quarantine moves
- The web UI must show the current queue (each pending action, its type,
  target file/album, status)
- From the UI I should be able to:
  - remove/cancel individual queued actions before they run
  - pause the worker — let the current in-flight upload/download finish,
    then stop picking up new actions (no hard interrupts)
  - resume the worker
  - stop the worker entirely
- Queue state should persist across restarts (stored alongside the state
  database)

## Web UI Requirements
- Dashboard: last scan time, worker status (running/paused/stopped), queue
  summary (counts by action type), conflict count
- Dual tree view (see above) as the main navigation/status view
- Queue view: list of pending actions with the ability to remove individual
  items, and pause/resume/stop controls for the worker
- History/log view of completed actions (including quarantine moves)
- Conflicts view: resolve flagged items (keep local / keep remote / keep
  both / structure errors needing manual cleanup)
- Settings page: SmugMug auth, local folder path, state DB filename/location
- "Scan now" can be triggered globally or on a selected subtree from the
  tree view

## Architecture & Packaging
- Single Docker container, or docker-compose if a second service is
  genuinely warranted — keep it as simple as possible
- Three concerns need to coexist cleanly: web server, scanning/diffing
  process, and the queue-processing worker thread. Propose how (e.g.
  threads vs. separate processes, how they communicate, how pause/stop
  signals are delivered safely — note: pause only needs to happen between
  queue items, not mid-transfer, per the requirement above)
- No periodic/scheduled scanning — all scans are user-initiated (globally or
  on a selected subtree from the tree view)
- Config via environment variables / mounted config file, with sensible
  defaults documented in a .env.example

## What I'd like from you first
Before writing code, please:
1. Propose an overall architecture: web app, scanner, queue, worker, state
   database, and how they communicate; how local folder structure maps to
   SmugMug folders/albums (including the example mapping requested above).
2. Confirm the exact list of "photo" file extensions you'll treat as in
   scope (jpg/jpeg, png, gif, heic).
3. Propose how the dual tree view will be built and kept performant with
   FastAPI + HTMX, including the color-coding scheme and how subtree
   selection feeds into scoped scans/queues.
4. Ask me any remaining clarifying questions — anything not already
   specified above.
5. Confirm key libraries (SmugMug API auth, file hashing, background
   worker/queue mechanism, live UI updates via HTMX).
6. Propose the Dockerfile structure and what gets mounted as volumes.
7. Outline a CLAUDE.md documenting setup, commands, and conventions.

Once we agree on the plan, let's build incrementally: start with scanning,
structure validation, and queue population in "dry run" (visible in UI,
nothing executed, tree view showing color-coded status), then enable the
worker for actual uploads/downloads/quarantine moves.
