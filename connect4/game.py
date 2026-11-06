"""Follow a game move by move from steady board readings, log it, and spot mistakes."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .board import COLS, EMPTY, NAMES, ROWS, GameState, Grid, landing_row, other
from .solver import Analysis

Lookup = Callable[[Grid, int], Analysis | None]
RANK = {"loss": 0, "draw": 1, "win": 2}
FLIPPED = {"win": "loss", "loss": "win", "draw": "draw", "unknown": "unknown"}


@dataclass
class Move:
    player: int
    column: int                     # 0-based
    played_at: datetime
    before: str                     # mover's result with best play before the move
    grid_after: Grid
    to_move_after: int | None
    after: str = "unknown"          # mover's result with best play after it, once proven
    alert: str | None = None


def single_drop(before: Grid, after: Grid) -> tuple[int, int] | None:
    """(column, player) if `after` is `before` plus exactly one dropped piece."""
    changed = [(r, c) for r in range(ROWS) for c in range(COLS) if before[r][c] != after[r][c]]
    if len(changed) != 1:
        return None
    row, col = changed[0]
    if before[row][col] != EMPTY or landing_row(before, col) != row:
        return None
    return col, after[row][col]


class GameTracker:
    """Turns the stream of steady positions into moves, a saved log, and alerts.

    A move counts as a mistake when it makes the mover's proven result worse
    (win to draw, win to loss, or draw to loss). Clearing the board starts a
    new game; any other change that isn't one legal move ends the current log.
    """

    def __init__(self, log_dir: Path | None, me: int | None = None):
        self.log_dir = log_dir
        self.me = me
        self._grid: Grid | None = None
        self._to_move: int | None = None
        self._new_game(None)

    def _new_game(self, start: Grid | None) -> None:
        self.moves: list[Move] = []
        self.start_grid = start
        self.result: str | None = None
        self.note: str | None = None
        self.path: Path | None = None
        self.alert: str | None = None       # latest mistake alert, until the next move

    def update(self, state: GameState, lookup: Lookup) -> list[str]:
        """Feed in each steady, valid position. Returns announcements (alerts, game over)."""
        grid, prev, prev_to_move = state.grid, self._grid, self._to_move
        if grid == prev:
            return []
        self._grid, self._to_move = grid, state.to_move
        if all(cell == EMPTY for row in grid for cell in row):
            self._new_game(None)
            return []

        drop = single_drop(prev, grid) if prev is not None else None
        if drop is None or drop[1] != prev_to_move:
            # Joined mid-game, or the board changed in a way that isn't one legal move.
            if self.moves:
                self.note = "The board changed unexpectedly, so this log stops here."
                self._save()
            self._new_game(grid)
            return []

        col, player = drop
        before = lookup(prev, player)
        before_outcome = before.outcome if before else "unknown"
        events = []
        if self.moves:
            # The previous move's result is this position's result for the other side.
            last = self.moves[-1]
            if last.after == "unknown" and before_outcome != "unknown":
                last.after = FLIPPED[before_outcome]
                self._check(last)       # logged only: announcing it now would be confusing
        move = Move(player, col, datetime.now(), before_outcome, grid, state.to_move)
        self.moves.append(move)
        self.alert = None

        if state.winner is not None:
            move.after = "win"
            self.result = f"{NAMES[state.winner]} wins"
            events.append("You win!" if self.me == state.winner else f"{NAMES[state.winner]} wins.")
        elif state.to_move is None:
            move.after = "draw"
            self.result = "Draw"
            events.append("It's a draw.")
        if alert := self._check(move):
            events.insert(0, alert)
        self._save()
        return events

    def poll(self, lookup: Lookup) -> list[str]:
        """Check whether the last move's result has been proven yet. Returns new alerts."""
        if not self.moves:
            return []
        move = self.moves[-1]
        if move.after != "unknown" or move.to_move_after is None:
            return []
        analysis = lookup(move.grid_after, move.to_move_after)
        if analysis is None or analysis.outcome == "unknown":
            return []
        move.after = FLIPPED[analysis.outcome]
        alert = self._check(move)
        self._save()
        return [alert] if alert else []

    def _check(self, move: Move) -> str | None:
        if move.before not in RANK or move.after not in RANK or \
                RANK[move.after] >= RANK[move.before]:
            return None
        move.alert = self._mistake_text(move)
        if move is self.moves[-1]:
            self.alert = move.alert
        return move.alert

    def _mistake_text(self, move: Move) -> str:
        name, opponent = NAMES[move.player], NAMES[other(move.player)]
        if self.me == move.player:
            return {("win", "draw"): "Mistake: you let the win slip. Best play now draws.",
                    ("win", "loss"): "Mistake: you were winning, now you're losing.",
                    ("draw", "loss"): "Mistake: that move loses against perfect play.",
                    }[(move.before, move.after)]
        if self.me is not None:
            if move.after == "draw":
                return f"{name} let their win slip. You can now force a draw."
            return f"{name} blundered! You can now force a win."
        if move.after == "draw":
            return f"{name} let the win slip. Best play now draws."
        return f"{name} blundered. {opponent} can now force a win."

    def _save(self) -> None:
        if self.log_dir is None or not self.moves:
            return
        if self.path is None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{self.moves[0].played_at:%Y-%m-%d_%H-%M-%S}"
            self.path = self.log_dir / f"{stem}.json"
            n = 2
            while self.path.exists():
                self.path = self.log_dir / f"{stem}_{n}.json"
                n += 1
        data = {
            "started": self.moves[0].played_at.isoformat(timespec="seconds"),
            "first_player": NAMES[self.moves[0].player],
            "start_position": None if self.start_grid is None else
            ["".join(".RY"[cell] for cell in row) for row in self.start_grid],
            "moves": "".join(str(m.column + 1) for m in self.moves),
            "result": self.result,
            "note": self.note,
            "move_details": [{
                "number": i + 1,
                "player": NAMES[m.player],
                "column": m.column + 1,
                "time": m.played_at.isoformat(timespec="seconds"),
                "best_play_before": m.before,
                "best_play_after": m.after,
                "alert": m.alert,
            } for i, m in enumerate(self.moves)],
        }
        self.path.write_text(json.dumps(data, indent=2) + "\n")
