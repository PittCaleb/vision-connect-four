from connect4.board import EMPTY, RED, YELLOW, empty_grid, evaluate_grid, grid_from_moves


def with_cells(grid, cells):
    rows = [list(r) for r in grid]
    for (row, col), value in cells.items():
        rows[row][col] = value
    return tuple(tuple(r) for r in rows)


def test_empty_board_first_player_moves():
    assert evaluate_grid(empty_grid(), RED).to_move == RED
    assert evaluate_grid(empty_grid(), YELLOW).to_move == YELLOW


def test_turn_comes_from_piece_counts():
    assert evaluate_grid(grid_from_moves("4")).to_move == YELLOW
    assert evaluate_grid(grid_from_moves("4", first=YELLOW)).to_move == RED
    # Equal counts: whoever went first is to move again.
    assert evaluate_grid(grid_from_moves("44", first=YELLOW), first=YELLOW).to_move == YELLOW


def test_floating_piece_is_rejected():
    grid = with_cells(empty_grid(), {(3, 2): RED})
    assert "column 3" in evaluate_grid(grid).error


def test_impossible_counts_are_rejected():
    grid = with_cells(empty_grid(), {(5, 0): RED, (5, 1): RED})
    assert "counts" in evaluate_grid(grid).error


def test_winner_is_found():
    state = evaluate_grid(grid_from_moves("1212121"))
    assert state.winner == RED
    assert state.to_move is None
    assert len(state.winning_cells) == 4


def test_landing_rows_from_moves():
    grid = grid_from_moves("44")
    assert grid[5][3] == RED and grid[4][3] == YELLOW and grid[3][3] == EMPTY
