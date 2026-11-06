import argparse
import time

import pytest

from connect4.app import App, StableGrid
from connect4.board import RED, YELLOW, evaluate_grid, grid_from_moves
from connect4.solver import Analysis
from connect4.synthetic import render_board
from connect4.vision import read_board
from connect4.worker import Progress, SolverWorker


def wait_for(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_worker_finishes_proven_positions():
    worker = SolverWorker()
    grid = grid_from_moves("4455")
    worker.request(grid, RED)
    progress = wait_for(lambda: (p := worker.result(grid, RED)) and p.done and p)
    assert progress.analysis.outcome == "win"


def test_worker_keeps_thinking_until_the_board_changes():
    worker = SolverWorker()
    early = grid_from_moves("44")
    worker.request(early, RED)
    first = wait_for(lambda: worker.result(early, RED))
    time.sleep(2.0)
    later = worker.result(early, RED)
    assert not later.done                       # no time limit: still searching
    assert later.analysis.depth > first.analysis.depth

    proven = grid_from_moves("4455")
    worker.request(proven, RED)                 # the board changed
    wait_for(lambda: (p := worker.result(proven, RED)) and p.done)
    assert not worker.result(early, RED).done   # the old search was abandoned


def test_worker_time_limit():
    worker = SolverWorker(time_limit=0.5)
    grid = grid_from_moves("44")
    worker.request(grid, RED)
    progress = wait_for(lambda: (p := worker.result(grid, RED)) and p.done and p, timeout=5)
    assert progress.analysis.outcome == "unknown"


def test_stable_grid_needs_repeated_readings():
    stable = StableGrid(frames_required=3)
    a, b = grid_from_moves("4"), grid_from_moves("44")
    assert stable.update(a) is None
    stable.update(a)
    assert stable.update(a) == a
    assert stable.update(b) == a                # one odd frame doesn't change it
    assert stable.update(None) == a


def make_app(tmp_path, me=None):
    args = argparse.Namespace(first="red", me=me, think=None, audio=False, no_log=False,
                              first_suggestion=5.0,
                              log_dir=str(tmp_path / "games"),
                              calibration=str(tmp_path / "calibration.json"))
    return App(args)


def suggestion_lines(app, moves):
    state = evaluate_grid(grid_from_moves(moves))
    frame = render_board(state.grid)
    for _ in range(app.stable.frames_required):
        app.process(frame)
    wait_for(lambda: (p := app.worker.result(state.grid, state.to_move)) and p.done)
    lines, draw_arrow = app._suggest(frame.copy(), read_board(frame, app.cfg), state)
    draw_arrow()
    return lines


def test_suggestion_without_play_as(tmp_path):
    lines = suggestion_lines(make_app(tmp_path), "4455")
    assert lines[0][0] == "Red to move: play column 3"
    assert lines[1][0] == "Red wins with best play (proven)"


def test_suggestion_on_your_turn(tmp_path):
    lines = suggestion_lines(make_app(tmp_path, me="red"), "4455")
    assert lines[0][0] == "Your move: play column 3"
    assert lines[1][0] == "You win with best play (proven)"


def test_opponents_best_move_is_still_shown(tmp_path):
    lines = suggestion_lines(make_app(tmp_path, me="yellow"), "4455")
    assert lines[0][0] == "Opponent (Red) to move. Their best: column 3"
    assert lines[1][0] == "They win with best play (proven)"


@pytest.mark.parametrize("theirs, expected", [
    (False, "You lose against perfect play (proven); leaves them 1 winning reply"),
    (True, "They lose against perfect play (proven); leaves you 2 winning replies"),
])
def test_lost_verdict_counts_winning_replies(theirs, expected):
    replies = 2 if theirs else 1
    progress = Progress(Analysis(0, -1005, True, 3, replies), True, 1.0, None)
    mover, other_side = ("They", "you") if theirs else ("You", "them")
    assert App._verdict(progress, mover, other_side) == expected


def test_verdict_while_still_thinking():
    progress = Progress(Analysis(3, 12, False, 15), False, 24.6, 2)
    assert App._verdict(progress, "You", "them") == \
        "Best guess so far: 15 moves ahead, still thinking (24s)"


class FakeSpeaker:
    enabled = True

    def __init__(self):
        self.said = []

    def say(self, text, replaces=None):
        self.said.append(text)


def test_announces_suggestion_changes_and_proof(tmp_path):
    app = make_app(tmp_path, me="red")
    app.speaker = FakeSpeaker()
    state = evaluate_grid(grid_from_moves("44"))
    app._announce(state, Analysis(3, 12, False, 13), theirs=False)
    app._announce(state, Analysis(3, 15, False, 14), theirs=False)     # same: stay quiet
    app._announce(state, Analysis(2, 15, False, 15), theirs=False)
    app._announce(state, Analysis(2, 1010, False, 16), theirs=False)
    assert app.speaker.said == ["Column 4.", "Change: column 3.", "You can force a win."]


def test_opponents_moves_are_not_read_out(tmp_path):
    app = make_app(tmp_path, me="yellow")
    app.speaker = FakeSpeaker()
    app._announce(evaluate_grid(grid_from_moves("44")), Analysis(3, 1010, True, 5), theirs=True)
    assert app.speaker.said == []


def test_without_play_as_the_player_is_named(tmp_path):
    app = make_app(tmp_path)
    app.speaker = FakeSpeaker()
    app._announce(evaluate_grid(grid_from_moves("4")), Analysis(3, -1010, True, 5), theirs=False)
    assert app.speaker.said == ["Yellow: column 4. Yellow is losing against perfect play."]


def test_best_guess_waits_for_first_suggestion_time(tmp_path):
    app = make_app(tmp_path)
    app.worker.first_suggestion = 1.5
    state = evaluate_grid(grid_from_moves("44"))
    frame = render_board(state.grid)
    reading = read_board(frame, app.cfg)
    app.worker.request(state.grid, state.to_move)
    time.sleep(0.8)
    lines, arrow = app._suggest(frame.copy(), reading, state)
    assert lines[0][0].startswith("Red to move. Thinking...") and arrow is None
    time.sleep(1.0)
    lines, arrow = app._suggest(frame.copy(), reading, state)
    assert lines[0][0].startswith("Red to move: play column") and arrow is not None


def test_proven_result_shows_without_waiting(tmp_path):
    app = make_app(tmp_path)
    state = evaluate_grid(grid_from_moves("4455"))
    frame = render_board(state.grid)
    app.worker.request(state.grid, state.to_move)
    wait_for(lambda: app.worker.result(state.grid, state.to_move))
    lines, _ = app._suggest(frame.copy(), read_board(frame, app.cfg), state)
    assert lines[0][0] == "Red to move: play column 3"
