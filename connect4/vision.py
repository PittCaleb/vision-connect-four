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
    # Piece colors, measured on the real board. Red pieces: hue 0-3, saturation
    # 234+, value 113+. Yellow pieces: hue 16-21, saturation 190+ in daylight but
    # only 142+ in evening light, value 139+. Wood seen through empty holes is
    # reddish but dull (saturation <= 120), so red needs a higher floor than yellow.
    red_hue_max: int = 8            # red wraps around hue 0...
    red_hue_min: int = 170          # ...so it is hue <= max or hue >= min
    yellow_hue: tuple[int, int] = (12, 40)
    red_min_saturation: int = 160
    yellow_min_saturation: int = 110
    min_value: int = 90
    # Hole detection. Far-side holes on an angled board look smaller and squashed.
    min_hole_area: float = 0.0003   # smallest hole, as a fraction of the frame
    min_hole_circularity: float = 0.45
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
    found = _detect_holes(frame, cfg)
    return None if found is None else found[0]


def _detect_holes(frame, cfg):
    """Hole centers, plus the blue mask and the filled board region they came from."""
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, np.array(cfg.blue_lower), np.array(cfg.blue_upper))
    blue = cv2.morphologyEx(blue, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    contours, hierarchy = cv2.findContours(blue, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
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

    parent = max(groups, key=lambda k: len(groups[k]))
    holes = np.array(groups[parent])
    median = np.median(holes[:, 2])
    holes = holes[(holes[:, 2] > 0.2 * median) & (holes[:, 2] < 2.5 * median)]
    board = np.zeros_like(blue)
    cv2.drawContours(board, contours, parent, 255, -1)
    return holes[:, :2].astype(np.float32), blue, board


def _pairwise(points: np.ndarray) -> np.ndarray:
    """Distances between every pair of points, with inf on the diagonal."""
    dist = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    np.fill_diagonal(dist, np.inf)
    return dist


def _lattice_coords(holes: np.ndarray) -> dict[int, tuple[int, int]]:
    """(column, row) steps of each hole from a starting hole, found by walking to
    neighbors one hole away horizontally or vertically. Holes that can't be reached
    that way are left out."""
    dist = _pairwise(holes)
    nearest = dist.min(axis=1)
    start = int(np.argmin(np.linalg.norm(holes - holes.mean(axis=0), axis=1)))
    coords = {start: (0, 0)}
    queue = [start]
    while queue:
        a = queue.pop(0)
        for b in np.nonzero(dist[a] < 1.7 * nearest[a])[0]:
            b = int(b)
            if b in coords:
                continue
            dx, dy = holes[b] - holes[a]
            if abs(dy) < 0.5 * abs(dx):
                step = (1 if dx > 0 else -1, 0)
            elif abs(dx) < 0.5 * abs(dy):
                step = (0, 1 if dy > 0 else -1)
            else:
                continue                    # a diagonal neighbor
            coords[b] = (coords[a][0] + step[0], coords[a][1] + step[1])
            queue.append(b)
    return coords


def fit_grid(holes: np.ndarray, cfg: VisionConfig, blue: np.ndarray,
             board: np.ndarray) -> np.ndarray | None:
    """Homography from warped coords to frame coords that lines the grid up with `holes`.

    Holes are first placed on a lattice by their neighbors, so missing holes, even
    a whole missing edge row or column, don't throw the fit off. If the holes found
    span fewer than 7 columns or 6 rows, every placement on the board is tried and
    the one whose 42 holes best land on non-blue spots inside the board wins.
    """
    if holes is None or len(holes) < cfg.min_holes:
        return None
    coords = _lattice_coords(holes)
    if len(coords) < cfg.min_holes:
        return None
    index = np.array(list(coords))
    steps = np.array([coords[k] for k in index])
    steps -= steps.min(axis=0)
    span_cols, span_rows = steps.max(axis=0) + 1
    if span_cols > COLS or span_rows > ROWS:
        return None

    centers = cell_layout(cfg)[0]
    ideal = centers.reshape(-1, 2).astype(np.float32)
    points = holes[index]
    spacing = float(np.median(_pairwise(holes).min(axis=1)))
    h, w = blue.shape
    best, best_score = None, -1
    for col0 in range(COLS - span_cols + 1):
        for row0 in range(ROWS - span_rows + 1):
            src = centers[steps[:, 1] + row0, steps[:, 0] + col0].astype(np.float32)
            to_frame, _ = cv2.findHomography(src, points, cv2.RANSAC, 0.3 * spacing)
            if to_frame is None:
                continue
            projected = cv2.perspectiveTransform(ideal.reshape(-1, 1, 2), to_frame).reshape(-1, 2)
            px = np.round(projected).astype(int)
            inside = (px[:, 0] >= 0) & (px[:, 0] < w) & (px[:, 1] >= 0) & (px[:, 1] < h)
            px = px[inside]
            score = int(np.sum((board[px[:, 1], px[:, 0]] > 0) & (blue[px[:, 1], px[:, 0]] == 0)))
            if score > best_score:
                best, best_score = to_frame, score
    if best is None:
        return None
    return _refine(best, holes, ideal, cfg)


def _refine(to_frame, holes, ideal, cfg):
    """Re-fit the homography to every detected hole near a projected grid hole."""
    for _ in range(3):
        projected = cv2.perspectiveTransform(ideal.reshape(-1, 1, 2), to_frame).reshape(-1, 2)
        grid = projected.reshape(ROWS, COLS, 2)
        spacing = np.minimum(np.linalg.norm(np.diff(grid, axis=1), axis=2).min(),
                             np.linalg.norm(np.diff(grid, axis=0), axis=2).min())
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
    bright = val >= cfg.min_value
    red = (bright & (sat >= cfg.red_min_saturation)
           & ((hue <= cfg.red_hue_max) | (hue >= cfg.red_hue_min)))
    yellow = (bright & (sat >= cfg.yellow_min_saturation)
              & (hue >= cfg.yellow_hue[0]) & (hue <= cfg.yellow_hue[1]))

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
        found = _detect_holes(frame, cfg)
        to_frame = None
        if found is not None:
            holes, blue, board = found
            to_frame = fit_grid(holes, cfg, blue, board)
        if to_frame is None:
            return None
    warped = cv2.warpPerspective(frame, np.linalg.inv(to_frame), (WARP_W, WARP_H))
    outline = np.array([[0, 0], [WARP_W, 0], [WARP_W, WARP_H], [0, WARP_H]], np.float32)
    corners = cv2.perspectiveTransform(outline.reshape(-1, 1, 2), to_frame).reshape(-1, 2)
    return BoardReading(corners, classify_cells(warped, cfg), to_frame)
