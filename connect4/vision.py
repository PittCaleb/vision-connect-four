"""Find the board in a camera frame and read which holes hold which color.

Pipeline: mask the board's saturated blue in HSV, find the round holes inside
it, and fit the 6 x 7 hole grid to them. That fit maps a flat WARP_W x WARP_H
view of the board onto the frame. The frame is warped to that flat view and a
disk is sampled at each of the 42 holes to classify it as red, yellow or empty.

Using the holes rather than the board's outline means a blue stand, rounded
corners or a see-through board don't throw the grid off.
"""

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .board import COLS, EMPTY, RED, ROWS, YELLOW, Grid

WARP_W = 700
WARP_H = 600


@dataclass
class VisionConfig:
    # HSV thresholds in OpenCV units (hue 0-179, saturation and value 0-255).
    blue_lower: tuple[int, int, int] = (95, 150, 50)
    blue_upper: tuple[int, int, int] = (130, 255, 255)
    # Piece colors, measured on the real board: red pieces are hue 0, saturation
    # 250+, value 113+; yellow pieces hue 16-18, saturation 190+, value 139+. Wood
    # seen through empty holes is reddish but dull (saturation <= 120, value <= 80).
    red_hue_max: int = 8            # red wraps around hue 0...
    red_hue_min: int = 170          # ...so it is hue <= max or hue >= min
    yellow_hue: tuple[int, int] = (12, 40)
    min_saturation: int = 160
    min_value: int = 90
    # Hole detection.
    min_hole_area: float = 0.0003   # smallest hole, as a fraction of the frame
    min_hole_circularity: float = 0.6
    min_holes: int = 34             # holes that must match the fitted grid
    # Gap between the outline drawn around the board and the outermost holes'
    # cells, as a fraction of the flat view's width and height.
    margin_x: float = 0.03
    margin_y: float = 0.035
    sample_radius: float = 0.3      # sampled disk radius, as a fraction of cell size
    min_fill: float = 0.45          # fraction of the disk that must match a piece color


@dataclass
class BoardReading:
    corners: np.ndarray             # (4, 2) frame coords: top-left, top-right, bottom-right, bottom-left
    grid: Grid
    to_frame: np.ndarray            # 3x3 homography from warped coords to frame coords

    def project(self, points) -> np.ndarray:
        """Map (N, 2) warped-board coordinates to frame coordinates."""
        pts = np.asarray(points, np.float32).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.to_frame).reshape(-1, 2)


def order_corners(pts) -> np.ndarray:
    """The four most extreme points: top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, np.float32).reshape(-1, 2)
    s = pts.sum(axis=1)
    d = pts[:, 1] - pts[:, 0]
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)


def cell_layout(cfg: VisionConfig) -> tuple[np.ndarray, float, float]:
    """Hole centers in warped coords as a (ROWS, COLS, 2) array, plus cell width and height."""
    x0, y0 = cfg.margin_x * WARP_W, cfg.margin_y * WARP_H
    cw = (WARP_W - 2 * x0) / COLS
    ch = (WARP_H - 2 * y0) / ROWS
    xs = x0 + cw * (np.arange(COLS) + 0.5)
    ys = y0 + ch * (np.arange(ROWS) + 0.5)
    gx, gy = np.meshgrid(xs, ys)
    return np.stack([gx, gy], axis=-1), cw, ch


def corner_hole_centers(cfg: VisionConfig) -> np.ndarray:
    """Warped coords of the four corner holes: top-left, top-right, bottom-right, bottom-left."""
    centers, _, _ = cell_layout(cfg)
    return np.array([centers[0, 0], centers[0, -1], centers[-1, -1], centers[-1, 0]],
                    np.float32)


def find_holes(frame: np.ndarray, cfg: VisionConfig) -> np.ndarray | None:
    """Centers of round non-blue regions enclosed by the board's blue, as an (N, 2) array."""
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(cfg.blue_lower), np.array(cfg.blue_upper))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hierarchy is None:
        return None

    # Group candidate holes by the blue region that encloses them.
    groups: dict[int, list[tuple[float, float, float]]] = {}
    for contour, (_, _, _, parent) in zip(contours, hierarchy[0]):
        if parent < 0:
            continue
        area = cv2.contourArea(contour)
        if area < cfg.min_hole_area * h * w:
            continue
        perimeter = cv2.arcLength(contour, True)
        if 4 * math.pi * area / (perimeter * perimeter) < cfg.min_hole_circularity:
            continue
        m = cv2.moments(contour)
        groups.setdefault(parent, []).append((m["m10"] / m["m00"], m["m01"] / m["m00"], area))
    if not groups:
        return None

    holes = np.array(max(groups.values(), key=len))
    median = np.median(holes[:, 2])
    holes = holes[(holes[:, 2] > 0.35 * median) & (holes[:, 2] < 2.5 * median)]
    return holes[:, :2].astype(np.float32)


def fit_grid(holes: np.ndarray, cfg: VisionConfig) -> np.ndarray | None:
    """Homography from warped coords to frame coords that lines the grid up with `holes`."""
    if holes is None or len(holes) < cfg.min_holes:
        return None
    ideal = cell_layout(cfg)[0].reshape(-1, 2).astype(np.float32)
    to_frame = cv2.getPerspectiveTransform(corner_hole_centers(cfg), order_corners(holes))

    for _ in range(3):
        projected = cv2.perspectiveTransform(ideal.reshape(-1, 1, 2), to_frame).reshape(-1, 2)
        spacing = np.median(np.linalg.norm(np.diff(projected.reshape(ROWS, COLS, 2), axis=1),
                                           axis=2))
        dist = np.linalg.norm(projected[:, None, :] - holes[None, :, :], axis=2)
        nearest = dist.argmin(axis=1)
        close = dist[np.arange(len(ideal)), nearest] < 0.35 * spacing
        if close.sum() < cfg.min_holes or len(set(nearest[close])) < close.sum():
            return None
        to_frame, _ = cv2.findHomography(ideal[close], holes[nearest[close]], 0)
        if to_frame is None:
            return None
    return to_frame


def classify_cells(warped: np.ndarray, cfg: VisionConfig) -> Grid:
    hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    vivid = (sat >= cfg.min_saturation) & (val >= cfg.min_value)
    red = vivid & ((hue <= cfg.red_hue_max) | (hue >= cfg.red_hue_min))
    yellow = vivid & (hue >= cfg.yellow_hue[0]) & (hue <= cfg.yellow_hue[1])

    centers, cw, ch = cell_layout(cfg)
    r = max(2, int(cfg.sample_radius * min(cw, ch)))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    disk = xx * xx + yy * yy <= r * r

    grid = []
    for row in range(ROWS):
        cells = []
        for col in range(COLS):
            cx, cy = (int(round(v)) for v in centers[row, col])
            window = (slice(cy - r, cy + r + 1), slice(cx - r, cx + r + 1))
            red_fill = red[window][disk].mean()
            yellow_fill = yellow[window][disk].mean()
            if max(red_fill, yellow_fill) < cfg.min_fill:
                cells.append(EMPTY)
            else:
                cells.append(RED if red_fill >= yellow_fill else YELLOW)
        grid.append(tuple(cells))
    return tuple(grid)


def read_board(frame: np.ndarray, cfg: VisionConfig,
               corner_holes: np.ndarray | None = None) -> BoardReading | None:
    """Read the board from a frame.

    Pass `corner_holes` (frame coords of the four corner holes' centers, any
    order) to skip automatic detection.
    """
    if corner_holes is not None:
        to_frame = cv2.getPerspectiveTransform(corner_hole_centers(cfg),
                                               order_corners(corner_holes))
    else:
        to_frame = fit_grid(find_holes(frame, cfg), cfg)
        if to_frame is None:
            return None
    warped = cv2.warpPerspective(frame, np.linalg.inv(to_frame), (WARP_W, WARP_H))
    outline = np.array([[0, 0], [WARP_W, 0], [WARP_W, WARP_H], [0, WARP_H]], np.float32)
    corners = cv2.perspectiveTransform(outline.reshape(-1, 1, 2), to_frame).reshape(-1, 2)
    return BoardReading(corners, classify_cells(warped, cfg), to_frame)
