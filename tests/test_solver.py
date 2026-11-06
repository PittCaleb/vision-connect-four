import random
from functools import lru_cache

import pytest

from connect4.board import COLS, EMPTY, RED, YELLOW, evaluate_grid, find_wins, grid_from_moves, \
    landing_row, other
from connect4.solver import Solver, to_bitboards


def analyze(moves: str, time_limit=5.0):
    state = evaluate_grid(grid_from_moves(moves))
    return Solver().analyze(*to_bitboards(state.grid, state.to_move), time_limit=time_limit)


def drop(grid, col, player):
    row = landing_row(grid, col)
    cells = [list(r) for r in grid]
    cells[row][col] = player
    return tuple(tuple(r) for r in cells)


@lru_cache(maxsize=None)
def brute_force(grid, player) -> int:
    """+1 win, 0 draw, -1 loss for `player` to move, by plain minimax."""
    moves = [c for c in range(COLS) if grid[0][c] == EMPTY]
    if not moves:
        return 0
    best = -1
    for col in moves:
        child = drop(grid, col, player)
        if find_wins(child):
            return 1
        best = max(best, -brute_force(child, other(player)))
        if best == 1:
            break
    return best


def test_empty_board_plays_center():
    result = analyze("")
    assert result.best_column == 3
    assert result.outcome == "win"


def test_takes_immediate_win():
    result = analyze("121212")      # Red has three stacked in column 1
    assert result.best_column == 0
    assert result.outcome == "win"


def test_blocks_vertical_threat():
    # Yellow has three stacked in column 7; Red must block on top.
    result = analyze("173757")
    assert result.best_column == 6


def test_open_three_is_a_proven_win():
    # Red d1, e1 with c1 and f1 open: playing c1 or f1 makes an unstoppable double threat.
    result = analyze("4455")
    assert result.best_column in (2, 5)
    assert result.outcome == "win"


def test_double_threat_against_us_is_a_proven_loss():
    # Yellow has c1, d1, e1 with b1 and f1 open, and Red can't win first.
    result = analyze("133445")
    assert result.outcome == "loss"


def random_position(rng, n_moves):
    while True:
        grid, player = grid_from_moves(""), RED
        for _ in range(n_moves):
            col = rng.choice([c for c in range(COLS) if grid[0][c] == EMPTY])
            grid = drop(grid, col, player)
            player = other(player)
            if find_wins(grid):
                break
        else:
            return grid, player


@pytest.mark.parametrize("seed", range(40))
def test_matches_brute_force_late_game(seed):
    rng = random.Random(seed)
    grid, player = random_position(rng, rng.randint(28, 34))
    expected = brute_force(grid, player)
    result = Solver().analyze(*to_bitboards(grid, player), time_limit=10.0)
    assert result.exact or result.outcome != "unknown"
    assert {"win": 1, "draw": 0, "loss": -1}[result.outcome] == expected
    # The suggested move must actually achieve that result.
    child = drop(grid, result.best_column, player)
    achieved = 1 if find_wins(child) else -brute_force(child, other(player))
    assert achieved == expected


def test_yellow_to_move_uses_yellow_perspective():
    state = evaluate_grid(grid_from_moves("1"))
    assert state.to_move == YELLOW
    result = Solver().analyze(*to_bitboards(state.grid, YELLOW), time_limit=0.5)
    assert 0 <= result.best_column < COLS


def winning_replies(grid, player, col):
    """Brute-force count of opponent replies to `col` that win for the opponent."""
    child = drop(grid, col, player)
    count = 0
    for reply in range(COLS):
        if child[0][reply] != EMPTY:
            continue
        after = drop(child, reply, other(player))
        if find_wins(after) or brute_force(after, player) == -1:
            count += 1
    return count


@pytest.mark.parametrize("seed", range(60))
def test_lost_positions_leave_fewest_winning_replies(seed):
    rng = random.Random(1000 + seed)
    while True:
        grid, player = random_position(rng, rng.randint(26, 34))
        if brute_force(grid, player) == -1:
            break
    result = Solver().analyze(*to_bitboards(grid, player), time_limit=10.0)
    assert result.outcome == "loss"
    playable = [c for c in range(COLS) if grid[0][c] == EMPTY]
    fewest = min(winning_replies(grid, player, c) for c in playable)
    assert result.winning_replies == fewest
    assert winning_replies(grid, player, result.best_column) == fewest



def test_early_lost_position_reports_while_counting_replies():
    # Yellow has an unstoppable double threat; counting replies to the end would take forever.
    state = evaluate_grid(grid_from_moves("133445"))
    reports = []
    result = Solver().analyze(*to_bitboards(state.grid, state.to_move), time_limit=1.0,
                              on_progress=reports.append)
    assert reports[0].outcome == "loss"            # reported before counting replies
    assert result.outcome == "loss"
    assert result.winning_replies is not None
