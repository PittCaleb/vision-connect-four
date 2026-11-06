import json

from connect4.board import RED, YELLOW, evaluate_grid, grid_from_moves
from connect4.game import GameTracker
from connect4.solver import WIN, Analysis

PROVEN = {"win": WIN + 5, "draw": 0, "loss": -(WIN + 5)}


def analysis(outcome):
    return Analysis(3, PROVEN.get(outcome, 7), outcome != "unknown", 10)


def play(tracker, moves, results=None):
    """Feed the position after each prefix of `moves`; `results` maps a prefix to the
    outcome for the player to move there. Returns all announcements."""
    results = results or {}

    def lookup(grid, to_move):
        for prefix, outcome in results.items():
            if grid_from_moves(prefix) == grid:
                return analysis(outcome)
        return None

    events = []
    for i in range(len(moves) + 1):
        state = evaluate_grid(grid_from_moves(moves[:i]))
        events += tracker.update(state, lookup) + tracker.poll(lookup)
    return events


def load(path):
    return json.loads(path.read_text())


def test_logs_each_move(tmp_path):
    tracker = GameTracker(tmp_path)
    play(tracker, "4453")
    log = load(tracker.path)
    assert log["moves"] == "4453"
    assert log["first_player"] == "Red"
    assert [m["player"] for m in log["move_details"]] == ["Red", "Yellow", "Red", "Yellow"]
    assert log["result"] is None


def test_no_log_dir_means_no_files(tmp_path):
    tracker = GameTracker(None)
    play(tracker, "4453")
    assert tracker.path is None
    assert list(tmp_path.iterdir()) == []


def test_alert_when_a_win_is_thrown_away(tmp_path):
    tracker = GameTracker(tmp_path)
    # Red to move after "4453" can force a win; after Red plays 1, Yellow can.
    events = play(tracker, "44531", {"4453": "win", "44531": "win"})
    assert events == ["Red blundered. Yellow can now force a win."]
    detail = load(tracker.path)["move_details"][-1]
    assert detail["best_play_before"] == "win"
    assert detail["best_play_after"] == "loss"
    assert detail["alert"] == events[0]


def test_alerts_from_your_point_of_view(tmp_path):
    mine = GameTracker(tmp_path, me=RED)
    assert play(mine, "44531", {"4453": "win", "44531": "draw"}) == \
        ["Mistake: you let the win slip. Best play now draws."]
    theirs = GameTracker(tmp_path, me=YELLOW)
    assert play(theirs, "44531", {"4453": "draw", "44531": "win"}) == \
        ["Red blundered! You can now force a win."]


def test_no_alert_for_a_good_move_or_unproven_positions(tmp_path):
    tracker = GameTracker(tmp_path)
    assert play(tracker, "44531", {"4453": "win", "44531": "loss"}) == []
    assert play(GameTracker(tmp_path), "44531", {"44531": "win"}) == []


def test_win_is_announced_and_logged(tmp_path):
    tracker = GameTracker(tmp_path, me=YELLOW)
    events = play(tracker, "1212121")
    assert events == ["Red wins."]
    assert load(tracker.path)["result"] == "Red wins"


def test_clearing_the_board_starts_a_new_log(tmp_path):
    tracker = GameTracker(tmp_path)
    play(tracker, "44")
    first = tracker.path
    play(tracker, "45")             # starts from an empty board again
    assert tracker.path != first
    assert load(first)["moves"] == "44"
    assert load(tracker.path)["moves"] == "45"


def test_unexpected_change_closes_the_log(tmp_path):
    tracker = GameTracker(tmp_path)
    play(tracker, "44")
    first = tracker.path
    lookup = lambda grid, to_move: None
    tracker.update(evaluate_grid(grid_from_moves("4455")), lookup)   # two pieces at once
    assert "unexpectedly" in load(first)["note"]
    tracker.update(evaluate_grid(grid_from_moves("44553")), lookup)
    log = load(tracker.path)
    assert log["moves"] == "3"
    assert log["start_position"][-1] == "...RR.."
