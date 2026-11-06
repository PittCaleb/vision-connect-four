"""Live webcam app: read the board, annotate it, and suggest the next move."""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from .board import NAMES, RED, YELLOW, Grid, evaluate_grid, grid_from_moves, landing_row, other
from .game import FLIPPED, GameTracker
from .overlay import (OUTCOME_COLORS, draw_board, draw_clicks, draw_panel, draw_suggestion,
                      draw_win_line)
from .replay import HELP as REPLAY_HELP, Replay
from .speech import Speaker
from .synthetic import render_board
from .vision import VisionConfig, order_corners, read_board
from .worker import FIRST_SUGGESTION, SolverWorker

WINDOW = "Vision Connect Four"
MAX_DISPLAY_WIDTH = 1400
WHITE, GRAY, RED_TEXT = (255, 255, 255), (190, 190, 190), (90, 90, 255)
FOOTER = ("C: click corner holes   X: clear clicks   F: who went first   "
          "M: play as   Q: quit")


class StableGrid:
    """Accept a reading only after it has been the same for several frames in a row."""

    def __init__(self, frames_required: int = 6):
        self.frames_required = frames_required
        self.stable: Grid | None = None
        self._candidate: Grid | None = None
        self._count = 0

    def update(self, grid: Grid | None) -> Grid | None:
        if grid is None:
            self._candidate, self._count = None, 0
        elif grid == self._candidate:
            self._count += 1
        else:
            self._candidate, self._count = grid, 1
        if self._count >= self.frames_required:
            self.stable = self._candidate
        return self.stable


class App:
    def __init__(self, args):
        self.cfg = VisionConfig()
        self.first = YELLOW if args.first == "yellow" else RED
        self.me = {"red": RED, "yellow": YELLOW}.get(args.me)
        self.calibration_path = Path(args.calibration)
        self.corners = self._load_corners()
        self.stable = StableGrid()
        self.worker = SolverWorker(time_limit=args.think, first_suggestion=args.first_suggestion)
        self.tracker = GameTracker(None if args.no_log else Path(args.log_dir), self.me)
        self.speaker = Speaker(args.audio)
        self._spoken = None     # (position, column, outcome) last read aloud
        self.replay: Replay | None = None
        self.footer = FOOTER
        self.clicks: list[tuple[float, float]] | None = None    # set while calibrating
        self.scale = 1.0

    # Calibration -------------------------------------------------------------

    def _load_corners(self) -> np.ndarray | None:
        if self.calibration_path.exists():
            return np.array(json.loads(self.calibration_path.read_text())["corner_holes"], np.float32)
        return None

    def _save_corners(self, corners: np.ndarray) -> None:
        self.corners = corners
        self.calibration_path.write_text(json.dumps({"corner_holes": corners.tolist()}))

    def clear_corners(self) -> None:
        self.corners = None
        self.calibration_path.unlink(missing_ok=True)

    def on_mouse(self, event, x, y, flags, param) -> None:
        if self.clicks is None or event != cv2.EVENT_LBUTTONDOWN:
            return
        self.clicks.append((x / self.scale, y / self.scale))
        if len(self.clicks) == 4:
            self._save_corners(order_corners(self.clicks))
            self.clicks = None

    # Per-frame work ----------------------------------------------------------

    def process(self, frame: np.ndarray) -> np.ndarray:
        out = frame.copy()
        if self.clicks is not None:
            draw_clicks(out, self.clicks)
            draw_panel(out, [(f"Click the centers of the 4 corner holes ({len(self.clicks)}/4)", WHITE),
                             ("Press C again to cancel", GRAY)], self.footer)
            return out

        reading = read_board(frame, self.cfg, self.corners)
        stable = self.stable.update(reading.grid if reading else None)
        source = "corner holes set by hand" if self.corners is not None else "found automatically"

        if reading is None:
            draw_panel(out, [("Board not found", RED_TEXT),
                             ("Press C and click its 4 corner holes to locate it by hand", GRAY)],
                       self.footer)
            return out

        draw_board(out, reading, self.cfg)
        draw_arrow = None
        lines = self.replay.lines() if self.replay else [(f"Board {source}", GRAY)]
        if stable != reading.grid:
            lines.append(("Waiting for the board to hold still...", WHITE))
            draw_panel(out, lines, self.footer)
            return out

        state = evaluate_grid(stable, self.first)
        you = f", you are {NAMES[self.me]}" if self.me else ""
        lines.append((f"Red {state.red_count}   Yellow {state.yellow_count}   "
                      f"({NAMES[self.first]} went first{you})", GRAY))
        if state.error:
            lines.append((f"That doesn't look like a real position: {state.error}", RED_TEXT))
        else:
            for event in (self.tracker.update(state, self._lookup)
                          + self.tracker.poll(self._lookup)):
                self.speaker.say(event)
            if self.tracker.alert:
                lines.append((self.tracker.alert, self._alert_color()))
            if state.winner:
                draw_win_line(out, reading, self.cfg, state.winning_cells)
                lines.append((f"{NAMES[state.winner]} wins!", OUTCOME_COLORS["win"]))
            elif state.to_move is None:
                lines.append(("Board full: it's a draw", WHITE))
            else:
                suggestion, draw_arrow = self._suggest(out, reading, state)
                lines.extend(suggestion)
        if self.tracker.path:
            lines.append((f"Logging to {self.tracker.path}", GRAY))
        draw_panel(out, lines, self.footer)
        if draw_arrow:
            draw_arrow()        # after the panel, so its shading never hides the arrow
        return out

    def _suggest(self, out, reading, state):
        """Status lines for the move suggestion, and a function that draws its arrow."""
        self.worker.request(state.grid, state.to_move)
        progress = self.worker.result(state.grid, state.to_move)
        player = NAMES[state.to_move]
        theirs = self.me is not None and state.to_move != self.me
        if progress is None or not self.worker.ready(progress):
            seconds = f" ({int(progress.elapsed)}s)" if progress else ""
            return [(f"{player} to move. Thinking...{seconds}", WHITE)], None

        analysis = progress.analysis
        col = analysis.best_column
        label = "THEM" if theirs else "YOU" if self.me else ""
        def draw_arrow():
            draw_suggestion(out, reading, self.cfg, col, landing_row(state.grid, col),
                            analysis.outcome, label, opponent=theirs)

        # Color everything from your point of view when playing as a color.
        color = OUTCOME_COLORS[FLIPPED[analysis.outcome] if theirs else analysis.outcome]
        if theirs:
            headline = f"Opponent ({player}) to move. Their best: column {col + 1}"
        elif self.me:
            headline = f"Your move: play column {col + 1}"
        else:
            headline = f"{player} to move: play column {col + 1}"
        if self.me:
            mover, other_side = ("They", "you") if theirs else ("You", "them")
        else:
            mover, other_side = player, NAMES[other(state.to_move)]
        lines = [(headline, color), (self._verdict(progress, mover, other_side), color)]
        if progress.changed_from is not None:
            lines.append((f"Changed from column {progress.changed_from + 1} "
                          f"after more thought", WHITE))
        self._announce(state, analysis, theirs)
        return lines, draw_arrow

    def _lookup(self, grid, to_move):
        progress = self.worker.result(grid, to_move)
        return progress.analysis if progress else None

    def _alert_color(self):
        """Mistake alerts are bad news if you made the mistake, good news if they did."""
        mover = self.tracker.moves[-1].player
        if self.me is None:
            return WHITE
        return OUTCOME_COLORS["loss" if mover == self.me else "win"]

    def _announce(self, state, analysis, theirs: bool) -> None:
        """Read the suggestion aloud when it first appears, changes, or becomes proven.

        When playing as a color, only your own moves are read out.
        """
        if not self.speaker.enabled or theirs:
            return
        col, outcome = analysis.best_column, analysis.outcome
        position = (state.grid, state.to_move)
        result = self._spoken_outcome(state.to_move, outcome)
        if self._spoken and self._spoken[0] == position:
            if self._spoken[1] != col:
                text = f"Change: column {col + 1}. {result}"
            elif self._spoken[2] != outcome:
                text = result
            else:
                return
        elif self.me:
            text = f"Column {col + 1}. {result}"
        else:
            text = f"{NAMES[state.to_move]}: column {col + 1}. {result}"
        self._spoken = (position, col, outcome)
        self.speaker.say(text.strip(), replaces="suggestion")

    def _spoken_outcome(self, player: int, outcome: str) -> str:
        who = "You" if self.me else NAMES[player]
        return {"win": f"{who} can force a win.",
                "draw": "Best play is a draw.",
                "loss": f"{who} {'are' if self.me else 'is'} losing against perfect play.",
                }.get(outcome, "")

    @staticmethod
    def _verdict(progress, mover: str, other_side: str) -> str:
        """Describe the result for `mover` ("You", "They" or a color name)."""
        analysis = progress.analysis
        plural = mover in ("You", "They")
        if analysis.outcome == "win":
            return f"{mover} {'win' if plural else 'wins'} with best play (proven)"
        if analysis.outcome == "draw":
            return "Draw with best play (proven)"
        if analysis.outcome == "loss":
            text = f"{mover} {'lose' if plural else 'loses'} against perfect play (proven)"
            n = analysis.winning_replies
            if n is not None:
                at_least = "" if analysis.replies_exact else "at least "
                text += (f"; leaves {other_side} {at_least}{n} winning "
                         f"{'reply' if n == 1 else 'replies'}")
            return text
        seconds = int(progress.elapsed)
        if progress.done:
            return f"Best guess: looked {analysis.depth} moves ahead in {seconds}s"
        return f"Best guess so far: {analysis.depth} moves ahead, still thinking ({seconds}s)"

    # Main loop ---------------------------------------------------------------

    def run(self, next_frame) -> None:
        cv2.namedWindow(WINDOW)
        cv2.setMouseCallback(WINDOW, self.on_mouse)
        while True:
            frame = next_frame()
            if frame is None:
                print("Lost the camera feed.", file=sys.stderr)
                break
            out = self.process(frame)
            self.scale = min(1.0, MAX_DISPLAY_WIDTH / out.shape[1])
            if self.scale < 1.0:
                out = cv2.resize(out, None, fx=self.scale, fy=self.scale,
                                 interpolation=cv2.INTER_AREA)
            cv2.imshow(WINDOW, out)

            key = cv2.waitKeyEx(1)
            if self.replay and self.replay.handle_key(key):
                continue
            key &= 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("c"):
                self.clicks = [] if self.clicks is None else None
            elif key == ord("x"):
                self.clear_corners()
            elif key == ord("f"):
                self.first = other(self.first)
            elif key == ord("m"):
                self.me = self.tracker.me = {None: RED, RED: YELLOW, YELLOW: None}[self.me]
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
        cv2.destroyAllWindows()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--camera", type=int, default=0, help="webcam index (default 0)")
    source.add_argument("--image", help="read a still photo instead of the webcam")
    source.add_argument("--demo", nargs="?", const="4453", metavar="MOVES",
                        help="use a rendered board; optionally give moves as column digits")
    source.add_argument("--replay", metavar="LOG",
                        help="step through a saved game log (a .json file in games/) "
                             "with the arrow keys")
    parser.add_argument("--first", choices=["red", "yellow"], default="red",
                        help="who moved first; only matters when the piece counts are equal")
    parser.add_argument("--me", choices=["red", "yellow"],
                        help="the color you play; the other side's best move is still shown")
    parser.add_argument("--first-suggestion", type=float, default=FIRST_SUGGESTION,
                        metavar="SECONDS",
                        help=f"think this long before showing a best guess; it keeps improving "
                             f"afterwards (default {FIRST_SUGGESTION:g}). Proven results show "
                             f"at once")
    parser.add_argument("--think", type=float, default=None, metavar="SECONDS",
                        help="stop searching a position after this long "
                             "(default: keep improving until the board changes)")
    parser.add_argument("--audio", action="store_true",
                        help="speak suggestions, proven results and mistakes aloud (macOS)")
    parser.add_argument("--log-dir", default="games",
                        help="folder for game logs (default: games)")
    parser.add_argument("--no-log", action="store_true", help="don't save game logs")
    parser.add_argument("--calibration", default="calibration.json",
                        help="where hand-clicked corner holes are saved")
    args = parser.parse_args(argv)
    if args.replay:
        args.no_log = True      # don't log the replay as a new game

    app = App(args)
    if args.replay:
        app.replay = Replay.load(args.replay)
        app.first = app.replay.first
        app.footer = REPLAY_HELP
        app.run(app.replay.frame)
    elif args.demo:
        frame = render_board(grid_from_moves(args.demo, first=app.first))
        app.run(lambda: frame)
    elif args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            parser.error(f"Couldn't read image {args.image}")
        app.run(lambda: frame)
    else:
        cap = cv2.VideoCapture(args.camera)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        if not cap.isOpened():
            parser.error(f"Couldn't open camera {args.camera}. On macOS, check that your "
                         "terminal has camera access in System Settings > Privacy & Security.")

        def next_frame():
            ok, frame = cap.read()
            return frame if ok else None

        try:
            app.run(next_frame)
        finally:
            cap.release()
