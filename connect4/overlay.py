"""Draw the annotations on top of the live camera frame."""

import cv2
import numpy as np

from .board import COLS, EMPTY, RED, YELLOW
from .vision import BoardReading, VisionConfig, cell_layout

# BGR
OUTLINE = (80, 255, 80)
PIECE_RING = {RED: (0, 0, 255), YELLOW: (0, 230, 255)}
EMPTY_RING = (170, 170, 170)
OPPONENT = (255, 90, 200)       # purple
OUTCOME_COLORS = {"win": (80, 255, 80), "draw": (255, 255, 255),
                  "loss": (0, 140, 255), "unknown": (255, 220, 120)}
FONT = cv2.FONT_HERSHEY_SIMPLEX


def draw_board(frame: np.ndarray, reading: BoardReading, cfg: VisionConfig) -> None:
    cv2.polylines(frame, [reading.corners.astype(np.int32)], True, OUTLINE, 3, cv2.LINE_AA)

    centers, cw, ch = cell_layout(cfg)
    radius = 0.36 * min(cw, ch)
    flat = centers.reshape(-1, 2)
    frame_centers = reading.project(flat)
    frame_edges = reading.project(flat + [radius, 0])
    radii = np.linalg.norm(frame_edges - frame_centers, axis=1)

    for i, (center, r) in enumerate(zip(frame_centers, radii)):
        cell = reading.grid[i // COLS][i % COLS]
        c = tuple(int(v) for v in center)
        if cell == EMPTY:
            cv2.circle(frame, c, int(r), EMPTY_RING, 1, cv2.LINE_AA)
        else:
            cv2.circle(frame, c, int(r) + 3, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.circle(frame, c, int(r), PIECE_RING[cell], 4, cv2.LINE_AA)
            label = "R" if cell == RED else "Y"
            (tw, th), _ = cv2.getTextSize(label, FONT, 0.7, 2)
            cv2.putText(frame, label, (c[0] - tw // 2, c[1] + th // 2), FONT, 0.7,
                        (255, 255, 255), 2, cv2.LINE_AA)


def draw_suggestion(frame: np.ndarray, reading: BoardReading, cfg: VisionConfig,
                    col: int, row: int, outcome: str, label: str = "",
                    opponent: bool = False) -> None:
    """Arrow above column `col`, plus a ring on the hole the piece would land in.

    The opponent's best move gets a thinner purple arrow so it can't be
    mistaken for yours.
    """
    color = OPPONENT if opponent else OUTCOME_COLORS.get(outcome, OUTCOME_COLORS["unknown"])
    outer, inner = (11, 6) if opponent else (14, 8)
    centers, cw, ch = cell_layout(cfg)
    x = centers[0, col, 0]
    top_y = centers[0, col, 1] - ch / 2
    tail, tip, land, land_edge = reading.project([
        (x, top_y - 1.6 * ch), (x, top_y - 0.25 * ch),
        centers[row, col], centers[row, col] + [0.45 * min(cw, ch), 0]])

    h, w = frame.shape[:2]
    tail = np.clip(tail, 0, [w - 1, h - 1])
    tip = np.clip(tip, 0, [w - 1, h - 1])
    tail_pt, tip_pt = tuple(int(v) for v in tail), tuple(int(v) for v in tip)
    cv2.arrowedLine(frame, tail_pt, tip_pt, (0, 0, 0), outer, cv2.LINE_AA, tipLength=0.4)
    cv2.arrowedLine(frame, tail_pt, tip_pt, color, inner, cv2.LINE_AA, tipLength=0.4)
    if label:
        (tw, th), _ = cv2.getTextSize(label, FONT, 0.8, 2)
        org = (tail_pt[0] + 14, max(th + 4, tail_pt[1] + th))
        cv2.putText(frame, label, org, FONT, 0.8, (0, 0, 0), 6, cv2.LINE_AA)
        cv2.putText(frame, label, org, FONT, 0.8, color, 2, cv2.LINE_AA)

    r = int(np.linalg.norm(land_edge - land))
    land_pt = tuple(int(v) for v in land)
    cv2.circle(frame, land_pt, r, (0, 0, 0), 7, cv2.LINE_AA)
    cv2.circle(frame, land_pt, r, color, 3 if opponent else 4, cv2.LINE_AA)


def draw_win_line(frame: np.ndarray, reading: BoardReading, cfg: VisionConfig,
                  cells) -> None:
    centers, _, _ = cell_layout(cfg)
    (r0, c0), (r1, c1) = cells[0], cells[-1]
    a, b = reading.project([centers[r0, c0], centers[r1, c1]])
    pa, pb = tuple(int(v) for v in a), tuple(int(v) for v in b)
    cv2.line(frame, pa, pb, (0, 0, 0), 14, cv2.LINE_AA)
    cv2.line(frame, pa, pb, (80, 255, 80), 8, cv2.LINE_AA)


def draw_panel(frame: np.ndarray, lines: list[tuple[str, tuple[int, int, int]]],
               footer: str) -> None:
    """Status text in a translucent box at the top left, key help along the bottom."""
    scale, thickness, line_h, pad = 0.7, 2, 30, 12
    if lines:
        width = max(cv2.getTextSize(text, FONT, scale, thickness)[0][0] for text, _ in lines)
        box_h = pad * 2 + line_h * len(lines) - 8
        _shade(frame, 10, 10, width + pad * 2, box_h)
        for i, (text, color) in enumerate(lines):
            y = 10 + pad + 20 + i * line_h
            cv2.putText(frame, text, (10 + pad, y), FONT, scale, color, thickness, cv2.LINE_AA)

    h = frame.shape[0]
    (fw, _), _ = cv2.getTextSize(footer, FONT, 0.55, 1)
    _shade(frame, 10, h - 40, fw + 20, 30)
    cv2.putText(frame, footer, (20, h - 19), FONT, 0.55, (230, 230, 230), 1, cv2.LINE_AA)


def draw_clicks(frame: np.ndarray, points) -> None:
    for i, (x, y) in enumerate(points):
        cv2.circle(frame, (int(x), int(y)), 8, (255, 0, 255), -1, cv2.LINE_AA)
        cv2.putText(frame, str(i + 1), (int(x) + 10, int(y) - 10), FONT, 0.8, (255, 0, 255), 2,
                    cv2.LINE_AA)
    if len(points) > 1:
        cv2.polylines(frame, [np.array(points, np.int32)], False, (255, 0, 255), 2, cv2.LINE_AA)


def _shade(frame, x, y, w, h, alpha=0.6):
    fh, fw = frame.shape[:2]
    x1, y1 = min(fw, x + w), min(fh, y + h)
    if x1 <= x or y1 <= y:
        return
    roi = frame[y:y1, x:x1]
    roi[:] = (roi * (1 - alpha)).astype(np.uint8)
