from pathlib import Path

import cv2
import numpy as np
import pytest

from connect4.board import empty_grid, grid_from_moves
from connect4.synthetic import DEFAULT_CORNERS, corner_holes, render_board
from connect4.vision import (VisionConfig, corner_hole_centers, find_holes, order_corners,
                             read_board)

FIXTURES = Path(__file__).parent / "fixtures"
POSITIONS = ["", "4", "4453", "4444443333221", "1234567123456712345671234567"]


@pytest.mark.parametrize("moves", POSITIONS)
def test_reads_grid_from_angled_board(moves):
    grid = grid_from_moves(moves)
    reading = read_board(render_board(grid), VisionConfig())
    assert reading is not None
    assert reading.grid == grid


def test_finds_all_holes():
    holes = find_holes(render_board(grid_from_moves("4453")), VisionConfig())
    assert len(holes) == 42


def test_grid_lines_up_with_holes():
    reading = read_board(render_board(empty_grid()), VisionConfig())
    expected = corner_holes()
    found = reading.project(corner_hole_centers(VisionConfig()))
    assert np.abs(found - expected).max() < 3


def test_head_on_board():
    corners = np.array([[200, 60], [1080, 60], [1080, 680], [200, 680]], np.float32)
    grid = grid_from_moves("33445566")
    assert read_board(render_board(grid, corners=corners), VisionConfig()).grid == grid


def test_some_holes_hidden():
    # A cable or hand in front of a few holes shouldn't stop the grid fitting.
    grid = grid_from_moves("4453")
    frame = render_board(grid)
    holes = corner_holes()
    middle = holes.mean(axis=0).astype(int)
    cv2.line(frame, tuple(middle - [120, 40]), tuple(middle + [120, 40]), (20, 20, 20), 25)
    assert read_board(frame, VisionConfig()).grid == grid


def test_hand_clicked_corner_holes():
    grid = grid_from_moves("4453")
    shuffled = corner_holes()[[2, 0, 3, 1]]
    assert read_board(render_board(grid), VisionConfig(), corner_holes=shuffled).grid == grid


def test_no_board_in_frame():
    frame = np.full((720, 1280, 3), 80, np.uint8)
    assert read_board(frame, VisionConfig()) is None


def test_order_corners_any_input_order():
    shuffled = DEFAULT_CORNERS[[2, 0, 3, 1]]
    assert np.array_equal(order_corners(shuffled), DEFAULT_CORNERS)


def test_real_empty_board():
    # Webcam photo of the real board: see-through holes, blue stand, window behind.
    frame = cv2.imread(str(FIXTURES / "real_empty.png"))
    reading = read_board(frame, VisionConfig())
    assert reading is not None
    assert reading.grid == empty_grid()


def test_real_board_with_pieces():
    # Real pieces: orange-tinted yellows low in the board, and the bottom-left
    # empty holes show reddish wood that must not read as red.
    frame = cv2.imread(str(FIXTURES / "real_pieces.png"))
    reading = read_board(frame, VisionConfig())
    rows = ["".join(".RY"[v] for v in row) for row in reading.grid]
    assert rows == [".......", ".......", ".......", ".......", "...R...", ".R.Y..Y"]
