"""Run the solver in a separate process so the video stays smooth.

The solver is pure Python, so on a thread it would hold the GIL and starve
the video loop; a process gets its own CPU core instead.

A position is searched until the board changes or the result is proven, so
the suggestion keeps improving the longer a player takes to move.
"""

import multiprocessing as mp
import queue
import time
from dataclasses import dataclass

from .board import Grid
from .solver import Analysis, Solver, to_bitboards

Key = tuple[Grid, int]
FIRST_SUGGESTION = 5.0      # default seconds to think before showing a suggestion


@dataclass(frozen=True)
class Progress:
    analysis: Analysis
    done: bool                      # search finished (proven, or hit the time limit)
    elapsed: float                  # seconds spent on this position so far
    changed_from: int | None        # previous best column, if the suggestion ever changed


class _Superseded:
    """Cancel flag for the solver: set once a newer request has been made."""

    def __init__(self, generation, mine: int):
        self._generation = generation
        self._mine = mine

    def is_set(self) -> bool:
        return self._generation.value != self._mine


def _serve(requests, updates, generation, time_limit) -> None:
    """Solver process: search each requested position, streaming results back."""
    solver = Solver()
    while True:
        job = requests.get()
        while True:                 # skip straight to the newest request
            try:
                job = requests.get_nowait()
            except queue.Empty:
                break
        gen, key, bitboards = job
        cancel = _Superseded(generation, gen)
        if cancel.is_set():
            continue
        solver.analyze(*bitboards, time_limit=time_limit, cancel=cancel,
                       on_progress=lambda analysis: updates.put((key, analysis)))
        if not cancel.is_set():
            updates.put((key, None))    # finished


class SolverWorker:
    def __init__(self, time_limit: float | None = None,
                 first_suggestion: float = FIRST_SUGGESTION):
        """`first_suggestion`: seconds before a best guess is worth showing. Changes
        to the best move after that are reported through Progress.changed_from."""
        self.first_suggestion = first_suggestion
        ctx = mp.get_context("spawn")
        self._requests = ctx.Queue()
        self._updates = ctx.Queue()
        self._generation = ctx.Value("i", 0)
        ctx.Process(target=_serve, daemon=True,
                    args=(self._requests, self._updates, self._generation, time_limit)).start()
        self._current: Key | None = None
        self._started = 0.0
        self._results: dict[Key, Analysis] = {}
        self._changed_from: dict[Key, int] = {}
        self._done: set[Key] = set()
        self._elapsed: dict[Key, float] = {}

    def request(self, grid: Grid, to_move: int) -> None:
        """Ask for an analysis. Abandons any search of a different position."""
        key = (grid, to_move)
        if key in self._done or key == self._current:
            return
        self._stop_clock()
        if len(self._results) > 500:
            for d in (self._results, self._changed_from, self._elapsed):
                d.clear()
            self._done.clear()
        with self._generation.get_lock():
            self._generation.value += 1
            gen = self._generation.value
        self._current, self._started = key, time.monotonic()
        self._requests.put((gen, key, to_bitboards(grid, to_move)))

    def result(self, grid: Grid, to_move: int) -> Progress | None:
        self._receive()
        key = (grid, to_move)
        analysis = self._results.get(key)
        if analysis is None:
            return None
        return Progress(analysis, key in self._done, self._elapsed_on(key),
                        self._changed_from.get(key))

    def ready(self, progress: Progress) -> bool:
        """Whether a result is worth showing: proven, finished, or thought about long enough."""
        return (progress.done or progress.analysis.outcome != "unknown"
                or progress.elapsed >= self.first_suggestion)

    def _elapsed_on(self, key: Key) -> float:
        elapsed = self._elapsed.get(key, 0.0)
        if key == self._current:
            elapsed += time.monotonic() - self._started
        return elapsed

    def _stop_clock(self) -> None:
        if self._current is not None:
            self._elapsed[self._current] = self._elapsed_on(self._current)
            self._current = None

    def _receive(self) -> None:
        while True:
            try:
                key, analysis = self._updates.get_nowait()
            except queue.Empty:
                return
            if analysis is None:
                self._done.add(key)
                if key == self._current:
                    self._stop_clock()
                continue
            previous = self._results.get(key)
            if previous and previous.depth > analysis.depth and not analysis.exact:
                continue        # a restarted search hasn't caught up yet
            settled = self._elapsed_on(key) >= self.first_suggestion
            if previous and settled and previous.best_column != analysis.best_column:
                self._changed_from[key] = previous.best_column
            self._results[key] = analysis
