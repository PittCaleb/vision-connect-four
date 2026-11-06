"""Step through a saved game log on a rendered board."""

import json
from pathlib import Path

import numpy as np

from .board import RED, YELLOW, apply_moves, empty_grid, grid_from_rows
from .synthetic import render_board

# Key codes from cv2.waitKeyEx: arrow keys differ by platform (macOS, Linux, Windows).
LEFT = {63234, 65361, 2424832, ord(",")}
RIGHT = {63235, 65363, 2555904, ord(".")}
HOME = {63273, 65360, 2359296}
END = {63275, 65367, 2293760}
WHITE, GRAY = (255, 255, 255), (190, 190, 190)
HELP = "Replay: <- / -> step   Home / End jump   Q quit"


class Replay:
    """Positions from a game log, one per move, with the log's notes for each move."""

    def __init__(self, log: dict):
        start = grid_from_rows(log["start_position"]) if log["start_position"] else empty_grid()
        self.first = YELLOW if log["first_player"] == "Yellow" else RED
        self.details = log["move_details"]
        self.result = log.get("result")
        moves = log["moves"]
        self.positions = [apply_moves(start, moves[:i], self.first) for i in range(len(moves) + 1)]
        self.step = 0
        self._frames: dict[int, np.ndarray] = {}

    @classmethod
    def load(cls, path: str | Path) -> "Replay":
        return cls(json.loads(Path(path).read_text()))

    @property
    def last(self) -> int:
        return len(self.positions) - 1

    def frame(self) -> np.ndarray:
        if self.step not in self._frames:
            self._frames[self.step] = render_board(self.positions[self.step])
        return self._frames[self.step]

    def handle_key(self, key: int) -> bool:
        """Move through the game for navigation keys. Returns True if the key was used."""
        if key in LEFT:
            self.step = max(0, self.step - 1)
        elif key in RIGHT:
            self.step = min(self.last, self.step + 1)
        elif key in HOME:
            self.step = 0
        elif key in END:
            self.step = self.last
        else:
            return False
        return True

    def lines(self) -> list[tuple[str, tuple[int, int, int]]]:
        lines = [(f"Replay: move {self.step} of {self.last}", WHITE)]
        if self.step == 0:
            lines.append(("Start of the game. Press -> to step forward", GRAY))
        else:
            move = self.details[self.step - 1]
            lines.append((f"{move['player']} played column {move['column']}", WHITE))
            if move["alert"]:
                lines.append((f"Logged alert: {move['alert']}", (0, 140, 255)))
        if self.step == self.last and self.result:
            lines.append((f"Result: {self.result}", GRAY))
        return lines
