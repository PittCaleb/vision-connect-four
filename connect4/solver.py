"""Connect Four solver: bitboard negamax with alpha-beta pruning.

The bitboard layout follows Pascal Pons' solver (http://blog.gamesolver.org):
each column uses HEIGHT + 1 bits, bottom to top, where the extra bit is a
sentinel that keeps shifts from wrapping between columns. A position is two
ints: `cur` (stones of the player to move) and `mask` (all stones).

Pure Python can't solve an early-game position in real time, so the search
uses iterative deepening under a time budget. If the deepest finished search
never hit its depth limit, the result is exact; otherwise leaves are scored
by a threat-counting heuristic and the result is a best guess. Win and loss
scores are always proven, even inside a depth-limited search.
"""

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .board import COLS, EMPTY, ROWS, Grid

WIDTH = COLS
HEIGHT = ROWS
H1 = HEIGHT + 1
SIZE = WIDTH * HEIGHT

BOTTOM_MASK = sum(1 << (c * H1) for c in range(WIDTH))
BOARD_MASK = BOTTOM_MASK * ((1 << HEIGHT) - 1)
COLUMN_MASKS = [((1 << HEIGHT) - 1) << (c * H1) for c in range(WIDTH)]
TOP_MASKS = [1 << (HEIGHT - 1 + c * H1) for c in range(WIDTH)]
CENTER_ORDER = [3, 2, 4, 1, 5, 0, 6]

WIN = 1000          # proven results score beyond +/-WIN; heuristic scores stay inside
INF = 10_000
EXACT, LOWER, UPPER = 0, 1, 2
MAX_TT_ENTRIES = 1_000_000     # roughly 150 MB


def to_bitboards(grid: Grid, to_move: int) -> tuple[int, int, int]:
    """Convert a grid (row 0 = top) to (cur, mask, moves) for the player to move."""
    cur = mask = moves = 0
    for col in range(WIDTH):
        for height in range(HEIGHT):
            cell = grid[ROWS - 1 - height][col]
            if cell == EMPTY:
                continue
            bit = 1 << (col * H1 + height)
            mask |= bit
            moves += 1
            if cell == to_move:
                cur |= bit
    return cur, mask, moves


def has_alignment(pos: int) -> bool:
    for shift in (1, H1, HEIGHT, HEIGHT + 2):   # vertical, horizontal, both diagonals
        m = pos & (pos >> shift)
        if m & (m >> (2 * shift)):
            return True
    return False


def winning_cells(pos: int, mask: int) -> int:
    """Empty cells that would complete four in a row for the stones in `pos`."""
    r = (pos << 1) & (pos << 2) & (pos << 3)
    for shift in (H1, HEIGHT, HEIGHT + 2):
        p = (pos << shift) & (pos << 2 * shift)
        r |= p & (pos << 3 * shift)
        r |= p & (pos >> shift)
        p = (pos >> shift) & (pos >> 2 * shift)
        r |= p & (pos << shift)
        r |= p & (pos >> 3 * shift)
    return r & (BOARD_MASK ^ mask)


def possible(mask: int) -> int:
    return (mask + BOTTOM_MASK) & BOARD_MASK


def column_of(move: int) -> int:
    return (move.bit_length() - 1) // H1


@dataclass(frozen=True)
class Analysis:
    best_column: int        # 0-based
    score: int
    exact: bool             # True if the search reached the end of the game
    depth: int
    winning_replies: int | None = None  # set when lost: opponent replies that still win...
    replies_exact: bool = True          # ...or, if False, a lower bound from a shallower search

    @property
    def outcome(self) -> str:
        """'win', 'loss' or 'draw' when proven, otherwise 'unknown'."""
        if self.score >= WIN:
            return "win"
        if self.score <= -WIN:
            return "loss"
        if self.exact:
            return "draw"
        return "unknown"


class _Timeout(Exception):
    pass


class Solver:
    def __init__(self):
        self.tt: dict[int, tuple[int, int, int, bool]] = {}
        self.nodes = 0
        self._deadline = 0.0
        self._cancel = threading.Event()
        self._cutoff = False

    def analyze(self, cur: int, mask: int, moves: int, time_limit: float | None = 2.0,
                on_progress: Callable[[Analysis], None] | None = None,
                cancel=None) -> Analysis:
        """Find the best move for the player to move.

        Searches until `time_limit` seconds pass (never, if None), `cancel` (any
        object with an is_set() method, such as a threading.Event) is set, or
        the result is proven; then returns the best result so far.
        `on_progress` gets each improved result as the search deepens.

        If every move loses against perfect play, pick the one that leaves the
        opponent the fewest winning replies, so a human opponent is most likely
        to go wrong.
        """
        playable = [c for c in CENTER_ORDER if not mask & TOP_MASKS[c]]
        if not playable:
            raise ValueError("Board is full")

        if moves == 0:
            # Known result: the first player wins by starting in the center.
            return self._report(Analysis(3, WIN + 1, True, SIZE), on_progress)

        for col in playable:
            move = (mask + (1 << (col * H1))) & COLUMN_MASKS[col]
            if has_alignment(cur | move):
                return self._report(Analysis(col, WIN + (SIZE + 1 - moves) // 2, True, 1),
                                    on_progress)

        self._deadline = math.inf if time_limit is None else time.monotonic() + time_limit
        self._cancel = cancel or threading.Event()

        opp_win = winning_cells(cur ^ mask, mask)
        poss = possible(mask)
        forced = poss & opp_win
        candidates = forced if forced else poss
        nonlosing = candidates & ~(opp_win >> 1)
        if forced & (forced - 1) or not nonlosing:
            # Every move lets the opponent win next turn.
            lost = self._report(Analysis(playable[0], -(WIN + (SIZE - moves) // 2), True, 1),
                                on_progress)
            return self._swindle(cur, mask, moves, lost, on_progress)

        order = [c for c in self._ordered_columns(cur, mask, nonlosing)]
        result = None
        for depth in range(1, SIZE - moves + 1):
            try:
                col, score, cutoff = self._root(cur, mask, moves, order, depth)
            except _Timeout:
                break
            result = self._report(Analysis(col, score, not cutoff, depth), on_progress)
            if not cutoff or abs(score) >= WIN:
                break
            order.remove(col)
            order.insert(0, col)
        if result is None:
            result = self._report(Analysis(order[0], 0, False, 0), on_progress)
        if result.outcome == "loss":
            result = self._swindle(cur, mask, moves, result, on_progress)
        return result

    def _swindle(self, cur, mask, moves, lost: Analysis, on_progress) -> Analysis:
        """In a lost position, prefer the move with the fewest opponent replies that still win.

        Counts deepen like the main search, reporting after each pass; shallow
        passes can miss deep wins, so their counts are lower bounds. Ties go to
        `lost.best_column`, which holds out longest.
        """
        result = lost
        for depth in range(2, SIZE - moves + 1):
            self._cutoff = False
            counts = {}
            try:
                for col in CENTER_ORDER:
                    if not mask & TOP_MASKS[col]:
                        counts[col] = self._winning_replies(cur, mask, moves, col, depth)
            except _Timeout:
                break
            best = min(counts, key=lambda c: (counts[c], c != lost.best_column))
            exact = not self._cutoff
            result = self._report(Analysis(best, lost.score, lost.exact, lost.depth,
                                           counts[best], exact), on_progress)
            if exact:
                break
        return result

    def _winning_replies(self, cur, mask, moves, col, depth) -> int:
        """How many opponent replies to playing `col` are proven wins for them."""
        move = (mask + (1 << (col * H1))) & COLUMN_MASKS[col]
        opp, mask1 = cur ^ mask, mask | move        # opponent to move
        count = 0
        for rcol in range(WIDTH):
            if mask1 & TOP_MASKS[rcol]:
                continue
            reply = (mask1 + (1 << (rcol * H1))) & COLUMN_MASKS[rcol]
            if has_alignment(opp | reply):
                count += 1
                continue
            mine, mask2 = cur | move, mask1 | reply     # back to us
            if moves + 2 >= SIZE or winning_cells(mine, mask2) & possible(mask2):
                continue                                # board full, or we win at once
            if self._search(mine, mask2, moves + 2, -INF, -WIN + 1, depth) <= -WIN:
                count += 1
        return count

    @staticmethod
    def _report(analysis, on_progress):
        if on_progress:
            on_progress(analysis)
        return analysis

    def _root(self, cur, mask, moves, order, depth):
        alpha, beta = -INF, INF
        best_col, best = order[0], -INF
        cutoff = False
        for col in order:
            move = (mask + (1 << (col * H1))) & COLUMN_MASKS[col]
            self._cutoff = False
            score = -self._search(cur ^ mask, mask | move, moves + 1, -beta, -alpha, depth - 1)
            cutoff |= self._cutoff
            if score > best:
                best, best_col = score, col
            alpha = max(alpha, score)
        return best_col, best, cutoff

    def _ordered_columns(self, cur, mask, moves_mask):
        """Columns in `moves_mask`, most new threats first, then center-out."""
        scored = []
        for i, col in enumerate(CENTER_ORDER):
            move = moves_mask & COLUMN_MASKS[col]
            if move:
                threats = winning_cells(cur | move, mask | move).bit_count()
                scored.append((-threats, i, col))
        scored.sort()
        return [col for _, _, col in scored]

    def _search(self, cur, mask, moves, alpha, beta, depth):
        """Negamax score for the player to move. Assumes they can't win immediately."""
        self.nodes += 1
        if not self.nodes & 2047 and (self._cancel.is_set() or time.monotonic() > self._deadline):
            raise _Timeout

        opp = cur ^ mask
        opp_win = winning_cells(opp, mask)
        poss = possible(mask)
        forced = poss & opp_win
        if forced:
            if forced & (forced - 1):
                return -(WIN + (SIZE - moves) // 2)     # two threats: can't block both
            poss = forced
        nonlosing = poss & ~(opp_win >> 1)
        if not nonlosing:
            return -(WIN + (SIZE - moves) // 2)
        if moves >= SIZE - 2:
            return 0
        if depth == 0:
            self._cutoff = True
            return self._evaluate(cur, opp, mask, opp_win)

        key = cur + mask
        entry = self.tt.get(key)
        if entry is not None:
            e_depth, flag, value, e_cut = entry
            if e_depth >= depth:
                if flag == EXACT or (flag == LOWER and value >= beta) or \
                        (flag == UPPER and value <= alpha):
                    self._cutoff |= e_cut
                    return value

        alpha0 = alpha
        outer_cut = self._cutoff
        self._cutoff = False
        best = -INF
        for col in self._ordered_columns(cur, mask, nonlosing):
            move = nonlosing & COLUMN_MASKS[col]
            score = -self._search(opp, mask | move, moves + 1, -beta, -alpha, depth - 1)
            if score > best:
                best = score
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        break
        node_cut = self._cutoff
        self._cutoff = outer_cut or node_cut

        flag = UPPER if best <= alpha0 else LOWER if best >= beta else EXACT
        if len(self.tt) >= MAX_TT_ENTRIES:
            self.tt.clear()
        self.tt[key] = (depth, flag, best, node_cut)
        return best

    @staticmethod
    def _evaluate(cur, opp, mask, opp_win):
        """Heuristic score for the player to move: open threats and center control."""
        my_win = winning_cells(cur, mask)
        center = COLUMN_MASKS[3]
        return (4 * (my_win.bit_count() - opp_win.bit_count())
                + (cur & center).bit_count() - (opp & center).bit_count())
