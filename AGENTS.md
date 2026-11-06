# AGENTS.md: Vision Connect Four

Instructions for AI coding agents (Claude, Codex, Copilot, Cursor, Gemini and
others) working on this repository. `README.md` is the user guide; this file is
everything an agent needs to understand, run, change and debug the code.

## What this project is

A Python desktop app that watches a physical Connect Four board through a
webcam, reads the position, and overlays move advice on the live video.

1. **Find the board:** locate the 42 holes in the blue frame.
2. **Read the pieces:** classify each hole as red, yellow or empty.
3. **Check the reading:** only accept a position that is steady and legal.
4. **Search:** look for the best move, deepening for as long as the board is
   unchanged.
5. **Show the result:** an arrow, a landing ring, a status panel, and optional
   speech.
6. **Track the game:** log each move and raise mistake alerts.

Stack: Python 3.10+ (developed on 3.14), OpenCV, NumPy, pytest. There is no
compiled code and no network access. The solver is pure Python.

## Setup, run, test

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m connect4 --demo 4455            # no camera needed: rendered board
python -m connect4 --me red --audio       # real game: webcam 0, you are Red, spoken advice
python -m pytest -q                       # about 1 minute, about 150 tests, no webcam needed
python -m pytest --cov=connect4 --cov-report=term
```

- **Always run the app as a module:** `python -m connect4`. `python connect4`
  fails on a relative import.
- **Use the venv's Python** (`.venv/bin/python`) when you aren't in an activated
  shell.
- **The app opens an OpenCV window and blocks.** Don't launch it from a
  non-interactive agent session expecting it to return. To exercise the pipeline
  headlessly, use `App.process(frame)` (see "Headless recipes" below).
- **Run `python -m pytest -q` after every change.** The slowest tests are the
  solver's brute-force comparisons in `tests/test_solver.py`.

## Repository map

```
connect4/
  __main__.py   entry point; guarded by `if __name__ == "__main__"` (required, see Gotchas)
  app.py        App class: per-frame pipeline, panel text, keys, mouse calibration, CLI (main)
  vision.py     VisionConfig (all tunable thresholds), hole detection, grid fit, color classification
  board.py      grid constants and rules: evaluate_grid -> GameState (turn, winner, errors)
  solver.py     bitboard negamax solver: Solver.analyze -> Analysis
  worker.py     SolverWorker: runs the solver in a separate process, streams Progress
  game.py       GameTracker: turns positions into moves, writes JSON logs, mistake alerts
  replay.py     Replay: loads a game log, renders each position, arrow-key navigation
  speech.py     Speaker: macOS `say` on a background thread with a replaceable queue
  overlay.py    all drawing on the frame (board labels, arrows, panel)
  synthetic.py  renders fake camera frames of any position (tests and --demo)
tests/
  test_solver.py   solver vs brute force on random late-game positions, lost-position logic
  test_board.py    rules and validation
  test_vision.py   rendered boards plus real-board photos in tests/fixtures/
  test_game.py     move tracking, logs, alert wording
  test_app.py      worker behavior, suggestion text, announcements (uses fakes)
  test_speech.py   speech queue (patches `say`; never makes sound)
  test_replay.py   loading logs (including mid-game starts and Yellow first), stepping
  fixtures/        real_empty.png, real_pieces.png: webcam frames of the real board, cropped
docs/screenshot.jpg  README image, rendered from --demo 4455 (not a real photo)
games/               game logs (created at runtime; git-ignored)
calibration.json     hand-clicked corner holes (created by the C key; git-ignored)
```

## Core conventions (read before editing)

### Grid, colors and columns

- **A grid** is `tuple[tuple[int, ...], ...]` of shape 6 × 7, indexed
  `grid[row][col]`.
- **Row 0 is the top row**, as seen in the image. Column 0 is the left column as
  seen by the camera.
- **Cell values:** `EMPTY = 0`, `RED = 1`, `YELLOW = 2` (in `board.py`).
  `NAMES[RED] == "Red"`.
- **Column numbering:**
  - 0-based everywhere in code, including `Analysis.best_column` and
    `Move.column`.
  - 1-based in everything a person sees: the panel, speech, log `column`
    fields, and move strings.
- **Move strings** are 1-based column digits in play order, such as `"4453"`.
  They are used by `grid_from_moves(moves, first=RED)`, `--demo`, and the log's
  `"moves"` field.
- **Text rows** in logs and tests: `"".join(".RY"[v] for v in row)`.

### Whose turn

`evaluate_grid(grid, first)` infers the turn from the piece counts. If one color
has one more piece, the other color moves. If the counts are equal, `first`
moves (the `--first` flag, or the F key). It returns a `GameState` with:

- `to_move`: `None` when the game is over or the grid is invalid.
- `winner` and `winning_cells`.
- `error`: a string for impossible positions (floating pieces, bad counts, two
  winners).

### Solver scores and outcomes

- **Bitboards** use Pascal Pons' layout: 7 bits per column (6 cells plus a
  sentinel), bottom to top. A position is `(cur, mask, moves)`, where `cur` holds
  the stones of the player to move. Convert with
  `to_bitboards(grid, to_move)`.
- **Scores** are from the mover's point of view:
  - `>= WIN` (1000) is a proven win, and `<= -WIN` a proven loss. Larger
    magnitude means sooner.
  - Scores strictly between are heuristic.
- **`Analysis`** fields:
  - `best_column` (0-based)
  - `score`
  - `exact`: the search reached the end of the game.
  - `depth`
  - `winning_replies` and `replies_exact`: only set when the position is lost.
  - `outcome` (a property): `"win"`, `"loss"`, `"draw"` (only when exact), or
    `"unknown"`.
- **Win and loss scores are always proven,** even from a depth-limited search.
  Draws are only proven when `exact`.
- **The empty board is hard-coded:** column 4 (index 3), a proven win. Every
  other early position is a heuristic best guess. Pure Python reaches about
  13–15 plies in the first few seconds.
- **Lost positions:** `_swindle` picks the move with the fewest opponent replies
  that still win (ties go to the longest holdout). It deepens and reports as it
  goes, and its counts are lower bounds until `replies_exact`.
- **`Solver.analyze(..., time_limit=None, cancel=obj, on_progress=fn)`:**
  - `time_limit=None` means search until proven or cancelled.
  - `cancel` is any object with `is_set()`. It's checked every 2048 nodes.
  - `on_progress` receives every improved `Analysis`, and every return path
    reports through it.
- **The transposition table** is a dict capped at 1,000,000 entries (about
  150 MB), cleared when full.

### Worker (background search)

- **`SolverWorker` runs the solver in a spawned process,** not a thread. A
  pure-Python search on a thread holds the GIL and dropped the video from 30 fps
  to 3 fps.
- **Every request bumps a shared generation counter,** which cancels the old
  search through `_Superseded.is_set()`.
- **Messages:** the process streams `(key, Analysis)` messages, then
  `(key, None)` when finished. A cancelled search never sends `None`.
- **`result(grid, to_move)`** returns `Progress(analysis, done, elapsed,
  changed_from)`. It drains the update queue, so call it each frame.
- **`ready(progress)`:** a best guess is only shown after `first_suggestion`
  seconds (default 5, `--first-suggestion`). Proven or finished results show
  immediately.
- **`changed_from`** is the previous best column, recorded only for changes
  after that delay. The panel then says "Changed from column N", and speech
  says "Change: column N".

### Game tracker and logs (`game.py`)

- **Input:** `GameTracker.update(state, lookup)` gets each new steady, valid
  `GameState`. `lookup(grid, to_move)` returns the latest `Analysis` for a
  position, or `None`.
- **Moves:** a new position that is the old one plus exactly one legal drop by
  the player to move becomes a `Move`.
- **New games and unexpected changes:**
  - Clearing the board starts a new game.
  - Any other change (two pieces at once, or a piece removed) writes a `note`
    to the current log and starts a new log from that position
    (`start_position`).
- **Results:** `before` is the mover's outcome before the move. `after` is
  filled in when proven, through `poll()` each frame or from the next move's
  `before`, flipped.
- **Mistakes:** a mistake is `RANK[after] < RANK[before]` with both proven
  (`loss < draw < win`). Alert wording depends on `tracker.me`.
- **Return value:** `update()` and `poll()` return strings to announce (alerts
  and the result), and the app speaks them. Alerts found late, through the next
  move, are logged but not announced.
- **Log files:** `games/YYYY-MM-DD_HH-MM-SS.json` (with `_2`, `_3`... on a
  collision), rewritten after every change. Schema:

```json
{
  "started": "ISO time of first move",
  "first_player": "Red|Yellow",
  "start_position": null,
  "moves": "4453",
  "result": "Red wins|Yellow wins|Draw|null",
  "note": "why the log stopped early, or null",
  "move_details": [{"number": 1, "player": "Red", "column": 4, "time": "ISO",
                    "best_play_before": "win|draw|loss|unknown",
                    "best_play_after": "win|draw|loss|unknown",
                    "alert": "text or null"}]
}
```

`start_position` is either `null` or a list of six row strings, top row first,
such as `["......."]`.

### Speech (`speech.py`)

- **`Speaker(enabled)`** runs `/usr/bin/say` on a background thread, one message
  at a time. Without `say` (Linux and Windows), it prints `[audio] ...` instead.
- **`say(text, replaces="suggestion")`** drops any queued, not-yet-spoken message
  with the same tag. Suggestions use the tag; alerts don't, so they're never
  dropped.
- **`App._announce`** speaks a suggestion:
  - when it first appears for a position,
  - when its column changes ("Change: column N"),
  - when its outcome becomes proven.

  With `--me`, only the user's own turns are spoken.

## Vision: how the board and colors are read

All thresholds live in `VisionConfig` (`connect4/vision.py`). Prefer tuning
these over changing the algorithm.

**Pipeline:**

1. **`find_holes`:** HSV mask of the board's blue (`blue_lower`/`blue_upper`,
   with saturation of at least 150 so a bluish window or table is excluded). A
   3×3 opening removes specks. Then `findContours(RETR_CCOMP)`: child contours
   are holes inside blue regions. Keep holes with at least `min_hole_area` of the
   frame and circularity of at least `min_hole_circularity`, group them by
   parent region, take the largest group, and drop outliers by size.
2. **`fit_grid`:**
   - Start from a homography mapping the 4 most extreme hole centers to the
     ideal corner holes (`corner_hole_centers`).
   - Then, three times: project all 42 ideal holes, match each to the nearest
     detected hole within 0.35 × spacing, and refit with `findHomography` on all
     matches.
   - Require at least `min_holes` (34) unique matches. Missing holes, such as one
     behind a cable or a hand, are fine.
3. **`read_board`:** warp the frame to a flat 700×600 view (`WARP_W`,
   `WARP_H`), then run `classify_cells`. That samples a disk at each hole
   (`sample_radius` = 0.3 of a cell) and calls it red or yellow if at least
   `min_fill` (45%) of the pixels match. `BoardReading.project(points)` maps
   flat-view coordinates back to the frame for drawing.
4. **Manual fallback (C key):** the user clicks the 4 corner holes' centers.
   They're saved to `calibration.json` as `{"corner_holes": [[x, y], ...]}` and
   passed to `read_board(frame, cfg, corner_holes=...)`, which skips detection.
5. **`StableGrid`** (`app.py`) accepts a reading only after 6 identical
   consecutive frames.

**Color pick-up values, measured on the real board** (OpenCV HSV: hue 0–179,
saturation and value 0–255):

| Thing | Hue | Saturation | Value |
| --- | --- | --- | --- |
| Red pieces | 0 (wraps to 179) | 250–255 | 113–144 |
| Yellow pieces | 16–18 (orange-tinted) | 190–235 | 139–170 |
| Board blue | 107–111 | 200–255 | 80–130 |
| Blue stand (darker) | 109–114 | 190–255 | 30–57 |
| Wood table seen through an empty hole | 0–6 (reddish) | 65–119 | 57–80 |
| Window / white wall | various | under 105 | high |

The current thresholds are:

- red: hue at most 8 or at least 170
- yellow: hue 12–40
- pieces: saturation at least 160 and value at least 90 (`min_saturation`,
  `min_value`)

The saturation and value floors are what stop dull reddish wood behind empty
holes from reading as red. Don't lower them without checking
`tests/fixtures/real_empty.png` and `real_pieces.png`.

**Retuning colors for a new board or new lighting:**

1. Capture a frame with known pieces (see "Headless recipes").
2. Sample HSV percentiles at specific holes, as in the snippet below.
3. Set thresholds between the pieces' minimums and the background's maximums,
   leaving margin.
4. Add the frame to `tests/fixtures/` with a test asserting the expected rows.
5. Run a live stability check: count distinct readings over about 10 seconds.
   It should be exactly 1.

```python
import cv2, numpy as np
from connect4.vision import VisionConfig, read_board, cell_layout, WARP_W, WARP_H
cfg = VisionConfig(); frame = cv2.imread("frame.png")
r = read_board(frame, cfg)
hsv = cv2.cvtColor(cv2.warpPerspective(frame, np.linalg.inv(r.to_frame), (WARP_W, WARP_H)),
                   cv2.COLOR_BGR2HSV)
centers, cw, ch = cell_layout(cfg); rad = int(cfg.sample_radius * min(cw, ch))
row, col = 5, 1                                   # bottom row, second column (0-based)
cx, cy = (int(v) for v in centers[row, col])
px = hsv[cy - rad:cy + rad, cx - rad:cx + rad].reshape(-1, 3)
print(np.percentile(px, [10, 50, 90], axis=0))    # rows: p10/p50/p90; cols: H, S, V
```

**Physical limits:**

- **Angle:** the board must face the camera to within about 20–25°. At about 50°
  only 11–19 of the 42 holes are visible as openings, and no setting fixes that.
  Tell the user to turn the board instead.
- **Camera index:** on the developer's Mac, camera 0 is the board webcam.
  Camera 1 returns black frames, and camera 2 is a virtual camera placeholder.

## Headless recipes (for agents)

Grab a webcam frame. On macOS, the terminal running the agent needs camera
permission.

```python
import time, cv2
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
for _ in range(30): ok, frame = cap.read(); time.sleep(0.03)   # let exposure settle
cap.release(); cv2.imwrite("frame.png", frame)
```

Run the full app pipeline without a window, and save what the user would see.
Put the script in a file, not `python -c`. The worker spawns a process, so the
entry point needs a `__main__` guard. Run it with `PYTHONPATH=.` from the repo
root.

```python
import argparse, time, cv2
from connect4.app import App
from connect4.board import grid_from_moves
from connect4.synthetic import render_board

if __name__ == "__main__":
    args = argparse.Namespace(first="red", me="red", think=None, audio=False, no_log=True,
                              first_suggestion=5.0, log_dir="games",
                              calibration="calibration.json")
    app = App(args)
    frame = render_board(grid_from_moves("4455"))      # or a cv2.imread / webcam frame
    for _ in range(8): app.process(frame)              # let StableGrid accept the reading
    time.sleep(1.0)                                    # let the solver work
    cv2.imwrite("annotated.png", app.process(frame))
```

`App(args)` needs every attribute shown above, because `main()` normally fills
them from argparse. If you add a command-line option, update `make_app` in
`tests/test_app.py` and this example.

Analyze a position directly:

```python
from connect4.board import evaluate_grid, grid_from_moves
from connect4.solver import Solver, to_bitboards
state = evaluate_grid(grid_from_moves("4455"))
a = Solver().analyze(*to_bitboards(state.grid, state.to_move), time_limit=5.0)
print(a.best_column + 1, a.outcome, a.depth, a.exact)
```

## Command-line options (keep in sync with `main()` in `app.py` and README)

| Option | Default | Notes |
| --- | --- | --- |
| `--camera N` / `--image PATH` / `--demo [MOVES]` / `--replay LOG` | camera 0 | Mutually exclusive frame sources. `--demo` alone uses `4453`. `--replay` forces `--no-log`. |
| `--first {red,yellow}` | red | Used only when the counts are equal. F key at runtime. |
| `--me {red,yellow}` | none | Perspective for arrows, colors, alerts and speech. M key cycles none, Red, Yellow. |
| `--first-suggestion SECONDS` | 5 | Delay before showing an unproven best guess. |
| `--think SECONDS` | none (unlimited) | Hard cap per position. |
| `--audio` | off | `Speaker` with macOS `say`. |
| `--log-dir DIR` / `--no-log` | `games` | `GameTracker(None)` disables logging. |
| `--calibration PATH` | `calibration.json` | Hand-clicked corner holes. |

Keys: C (click the 4 corner holes), X (clear them), F (who went first), M (play
as), Q or Esc (quit). In replay: ← / → (or `,` / `.`) step, Home / End jump.

- **How replay works:** `App.replay` holds a `Replay`. Its `lines()` replace the
  "Board found" line, `app.footer` shows the replay help, and `App.run` passes
  each key to `replay.handle_key` first.
- **Use `cv2.waitKeyEx`, not `waitKey`.** Arrow-key codes differ by platform
  (macOS 63234/63235, Linux 65361/65363, Windows 2424832/2555904; see
  `replay.py`). They're compared before masking with `& 0xFF` for the letter
  keys.
- **Log helpers:** `board.apply_moves(grid, moves, first)` replays a move string
  onto any starting grid, and `board.grid_from_rows(rows)` parses a log's
  `start_position`.

## Gotchas

- **Keep the `__main__` guard.** `connect4/__main__.py` must keep
  `if __name__ == "__main__": main()`. Worker processes use the `spawn` start
  method, which re-imports the main module. Without the guard, every solver
  process launches a second app. The same applies to any script that creates an
  `App` or a `SolverWorker`.
- **Don't move the solver back onto a thread.** It holds the GIL and makes the
  video choppy. Verify any worker change by measuring fps with `App.process` on
  live frames (it should be about 30).
- **`cv2.imshow` and `waitKey` must run on the main thread** (macOS).
- **Tests must never make sound.** Patch `speech.subprocess.run` and
  `speech.shutil.which` (see `tests/test_speech.py`), or replace `app.speaker`
  with a fake (see `tests/test_app.py`).
- **Pure-Python speed:** about 125,000 nodes per second. Each extra ply costs
  roughly 4–5× the time, so a longer time limit buys little depth early on.
  Don't promise perfect early-game play.
- **Grid row order is top-down,** but bitboards are bottom-up per column.
  `to_bitboards` handles the flip; don't duplicate it.
- **Overlay drawing order:** the suggestion arrow is drawn after the panel,
  through the `draw_arrow` callback returned by `App._suggest`, so the panel's
  shading never covers it.
- **Real-board fixtures are cropped to the board,** so the repository doesn't
  publish photos of the user's home. Crop any new real fixture the same way:
  use the reading's `corners` plus a small margin, and confirm the reading is
  unchanged.

## Known limitations and ideas

Open work is tracked in `TODO.md`.

- **Early-game suggestions aren't guaranteed perfect.** A user who followed the
  suggestions for both sides saw the second player win. The planned fix is the
  optional C solver below. It's not started.
- **Possible features:** Linux and Windows text-to-speech (for example `espeak`
  or `pyttsx3`), and automatic color calibration from the first frames of a game.

## Optional C solver: design notes (TODO, not started)

**Goal:** proven-perfect suggestions from move 1, without breaking users who
can't compile C. There would be two paths, chosen at runtime:

- **Compiled path:** if a built native library is present, `Solver` delegates to
  it.
- **Pure-Python path:** otherwise, today's `connect4/solver.py` runs unchanged.
  This must stay the default that always works.

**Why C:** the Python solver manages about 125,000 nodes per second. The same
algorithm in C (bitboards, alpha-beta, transposition table) typically runs about
50–100 times faster. That brings mid-early positions into real-time exact range,
but not the first few moves.

**Approach:**

1. **Write the solver in plain C99** with no dependencies, in `native/solver.c`.
   Use the same algorithm as `solver.py`:
   - Pons-style bitboards, negamax with alpha-beta, and null-window (weak or
     strong) search.
   - A fixed-size transposition table, and move ordering by threats created.
   - Exact scores only, with no heuristic leaves.
   Write it ourselves rather than vendoring Pascal Pons' solver, which is
   AGPL-licensed.
2. **Expose a tiny C API,** for example
   `int c4_solve(uint64_t cur, uint64_t mask, int moves, int scores_out[7], volatile int *cancel)`.
   It returns exact scores for each column, and `cancel` is polled so the worker
   can abandon a search. Call it from Python with `ctypes`, so there's no
   CPython extension build and no ABI coupling to the Python version.
3. **Build it** with a small script: `python -m connect4.build_native`. That
   runs `cc -O3 -shared -fPIC native/solver.c -o connect4/_c4solver.{so,dylib,dll}`.
   Alternatively, try the build lazily on first run and fall back quietly. Don't
   make `pip install` require a compiler.
4. **Opening book:** positions in roughly the first 8–12 plies are still too slow
   to solve live, even in C. Precompute them once offline with the C solver, for
   example every position reachable within N plies, deduplicated by mirror
   symmetry. Ship the book as a compact data file in the repo (best move and
   score per position key), so book lookups need no compiler at all. That means
   even pure-Python users get perfect openings. Only the middle game benefits
   from the C path.
5. **Integrate:**
   - In `Solver.analyze`, check the book first, then the C solver if loaded,
     then the Python search.
   - Keep the `Analysis` and `on_progress` contracts unchanged, so `worker.py`,
     `app.py` and `game.py` need no changes.
   - Show which engine is active in the panel, for example "engine: C + book".
6. **Test:**
   - Run the C solver against the Python solver's exact results and the
     brute-force tests in `tests/test_solver.py`.
   - Check book entries against a sample of fresh C solves.
   - Skip C tests with `pytest.importorskip`-style guards when the library isn't
     built, so CI without a compiler still passes.

**Downsides and trade-offs:**

- **Users need a C compiler for the fast middle game.** That means Xcode Command
  Line Tools on macOS, build-essential on Linux, or MSVC or MinGW on Windows. The
  README must explain the optional build, and the app must work without it.
- **There's an extra build step,** and platform-specific library names to handle
  (`.dylib`, `.so`, `.dll`).
- **The opening book adds a data file** (likely a few MB) and a one-off
  generation run that may take hours.
- **There are two solver implementations to keep in agreement.** The tests above
  are what keeps them honest.

## Working style for this repo

- **Match the surrounding code:** small functions, dataclasses, type hints,
  short comments that explain *why*, and no new dependencies without a strong
  reason.
- **Every behavior change gets a test.** Use `synthetic.render_board` for vision
  tests that don't need a camera. Add a real fixture when a real-world failure
  is fixed.
- **Keep `README.md` (user guide) and this file in sync** when you change options,
  keys, log format or thresholds.
