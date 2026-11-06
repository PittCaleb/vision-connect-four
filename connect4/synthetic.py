"""Render fake camera frames of a board, for tests and the --demo mode."""

import cv2
import numpy as np

from .board import COLS, RED, ROWS, YELLOW, Grid
from .vision import WARP_H, WARP_W, VisionConfig, cell_layout, corner_hole_centers

BLUE = (180, 60, 20)            # BGR
RED_PIECE = (40, 40, 210)
YELLOW_PIECE = (30, 210, 240)
HOLE_BACKGROUND = (205, 205, 210)
TABLE = (70, 75, 80)

DEFAULT_CORNERS = np.array([[330, 120], [960, 150], [990, 650], [300, 630]], np.float32)


def render_board(grid: Grid, frame_size=(1280, 720), corners=DEFAULT_CORNERS,
                 noise: float = 4.0, seed: int = 0) -> np.ndarray:
    """Draw `grid` on a flat board, then warp it into a frame at `corners`."""
    board = np.full((WARP_H, WARP_W, 3), BLUE, np.uint8)
    centers, cw, ch = cell_layout(VisionConfig())
    radius = int(0.38 * min(cw, ch))
    colors = {RED: RED_PIECE, YELLOW: YELLOW_PIECE}
    for row in range(ROWS):
        for col in range(COLS):
            center = tuple(int(round(v)) for v in centers[row, col])
            cv2.circle(board, center, radius, colors.get(grid[row][col], HOLE_BACKGROUND),
                       -1, cv2.LINE_AA)

    w, h = frame_size
    src = np.array([[0, 0], [WARP_W - 1, 0], [WARP_W - 1, WARP_H - 1], [0, WARP_H - 1]],
                   np.float32)
    m = cv2.getPerspectiveTransform(src, np.asarray(corners, np.float32))
    warped = cv2.warpPerspective(board, m, (w, h))
    coverage = cv2.warpPerspective(np.full((WARP_H, WARP_W), 255, np.uint8), m, (w, h))

    frame = np.full((h, w, 3), TABLE, np.uint8)
    frame[coverage > 127] = warped[coverage > 127]
    if noise:
        rng = np.random.default_rng(seed)
        frame = np.clip(frame + rng.normal(0, noise, frame.shape), 0, 255).astype(np.uint8)
    return frame


def corner_holes(corners=DEFAULT_CORNERS) -> np.ndarray:
    """Frame coords of the corner holes' centers on a board rendered at `corners`."""
    src = np.array([[0, 0], [WARP_W - 1, 0], [WARP_W - 1, WARP_H - 1], [0, WARP_H - 1]],
                   np.float32)
    m = cv2.getPerspectiveTransform(src, np.asarray(corners, np.float32))
    return cv2.perspectiveTransform(corner_hole_centers(VisionConfig()).reshape(-1, 1, 2),
                                    m).reshape(-1, 2)
