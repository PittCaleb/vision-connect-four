import json

from connect4.board import RED, YELLOW, evaluate_grid, grid_from_moves
from connect4.game import GameTracker
from connect4.replay import LEFT, RIGHT, Replay
from connect4.vision import VisionConfig, read_board


def logged_game(tmp_path, moves="4453", first=RED):
    tracker = GameTracker(tmp_path)
    lookup = lambda grid, to_move: None
    for i in range(len(moves) + 1):
        tracker.update(evaluate_grid(grid_from_moves(moves[:i], first), first), lookup)
    return tracker.path


def test_steps_through_a_logged_game(tmp_path):
    replay = Replay.load(logged_game(tmp_path))
    assert replay.last == 4
    assert replay.positions[0] == grid_from_moves("")
    assert replay.positions[4] == grid_from_moves("4453")
    assert replay.lines()[0][0] == "Replay: move 0 of 4"

    for _ in range(3):
        replay.handle_key(next(iter(RIGHT)))
    assert replay.step == 3
    assert replay.lines()[1][0] == "Red played column 5"
    replay.handle_key(next(iter(LEFT)))
    assert replay.step == 2


def test_stays_within_the_game(tmp_path):
    replay = Replay.load(logged_game(tmp_path))
    replay.handle_key(next(iter(LEFT)))
    assert replay.step == 0
    for _ in range(10):
        replay.handle_key(next(iter(RIGHT)))
    assert replay.step == replay.last
    assert not replay.handle_key(ord("q"))


def test_yellow_first_and_mid_game_start(tmp_path):
    log = {"start_position": ["......."] * 5 + ["...R..."], "first_player": "Yellow",
           "moves": "45", "result": None,
           "move_details": [{"player": "Yellow", "column": 4, "alert": None},
                            {"player": "Red", "column": 5, "alert": None}]}
    replay = Replay(log)
    assert replay.first == YELLOW
    assert replay.positions[-1][4][3] == YELLOW and replay.positions[-1][5][4] == RED


def test_rendered_frames_read_back_correctly(tmp_path):
    replay = Replay.load(logged_game(tmp_path, "44536"))
    replay.step = 5
    assert read_board(replay.frame(), VisionConfig()).grid == replay.positions[5]


def test_logged_alert_is_shown(tmp_path):
    path = logged_game(tmp_path)
    log = json.loads(path.read_text())
    log["move_details"][2]["alert"] = "Red blundered. Yellow can now force a win."
    replay = Replay(log)
    replay.step = 3
    assert ("Logged alert: Red blundered. Yellow can now force a win."
            in [text for text, _ in replay.lines()])
