"""Board representation and validation for positions read off a physical board.

A grid is a tuple of ROWS rows, each a tuple of COLS cells, indexed
grid[row][col] with row 0 at the TOP of the board (the way it looks in the
camera image) and col 0 on the left.
"""

from dataclasses import dataclass

ROWS = 6
COLS = 7

EMPTY = 0
RED = 1
YELLOW = 2

NAMES = {RED: "Red", YELLOW: "Yellow"}

Grid = tuple[tuple[int, ...], ...]


def other(player: int) -> int:
    return YELLOW if player == RED else RED


def empty_grid() -> Grid:
    return tuple((EMPTY,) * COLS for _ in range(ROWS))


def grid_from_moves(moves: str, first: int = RED) -> Grid:
    """Build a grid from a string of 1-based column numbers, e.g. "4453"."""
    return apply_moves(empty_grid(), moves, first)


def apply_moves(grid: Grid, moves: str, first: int) -> Grid:
    """Drop pieces into `grid` for each 1-based column in `moves`, alternating from `first`."""
    cells = [list(row) for row in grid]
    player = first
    for ch in moves:
        col = int(ch) - 1
        row = landing_row(cells, col)
        if row is None:
            raise ValueError(f"Column {col + 1} is full")
        cells[row][col] = player
        player = other(player)
    return tuple(tuple(r) for r in cells)


def grid_from_rows(rows: list[str]) -> Grid:
    """Inverse of the text rows used in logs: top row first, "R", "Y" or "." per cell."""
    return tuple(tuple(".RY".index(ch) for ch in row) for row in rows)


def landing_row(grid, col: int) -> int | None:
    """Row a piece dropped in `col` would land in, or None if the column is full."""
    for row in range(ROWS - 1, -1, -1):
        if grid[row][col] == EMPTY:
            return row
    return None


def find_wins(grid) -> list[tuple[int, list[tuple[int, int]]]]:
    """Return every four-in-a-row as (player, [(row, col), ...])."""
    wins = []
    for r in range(ROWS):
        for c in range(COLS):
            player = grid[r][c]
            if player == EMPTY:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                cells = [(r + i * dr, c + i * dc) for i in range(4)]
                if all(0 <= rr < ROWS and 0 <= cc < COLS and grid[rr][cc] == player
                       for rr, cc in cells):
                    wins.append((player, cells))
    return wins


@dataclass(frozen=True)
class GameState:
    grid: Grid
    to_move: int | None          # None when the game is over or the grid is invalid
    winner: int | None = None
    winning_cells: tuple[tuple[int, int], ...] = ()
    error: str | None = None

    @property
    def red_count(self) -> int:
        return sum(row.count(RED) for row in self.grid)

    @property
    def yellow_count(self) -> int:
        return sum(row.count(YELLOW) for row in self.grid)

    @property
    def is_draw(self) -> bool:
        return self.error is None and self.winner is None and self.to_move is None


def evaluate_grid(grid: Grid, first: int = RED) -> GameState:
    """Check that a grid is a reachable position and work out whose turn it is.

    `first` only matters when both colors have the same number of pieces.
    """
    for col in range(COLS):
        seen_empty = False
        for row in range(ROWS - 1, -1, -1):
            if grid[row][col] == EMPTY:
                seen_empty = True
            elif seen_empty:
                return GameState(grid, None, error=f"Floating piece in column {col + 1}")

    reds = sum(row.count(RED) for row in grid)
    yellows = sum(row.count(YELLOW) for row in grid)
    if reds == yellows:
        to_move = first
    elif reds == yellows + 1:
        to_move = YELLOW
    elif yellows == reds + 1:
        to_move = RED
    else:
        return GameState(grid, None,
                         error=f"Piece counts don't add up (Red {reds}, Yellow {yellows})")

    wins = find_wins(grid)
    winners = {player for player, _ in wins}
    if len(winners) > 1:
        return GameState(grid, None, error="Both colors have four in a row")
    if winners:
        player, cells = wins[0]
        return GameState(grid, None, winner=player, winning_cells=tuple(cells))
    if reds + yellows == ROWS * COLS:
        return GameState(grid, None)
    return GameState(grid, to_move)
