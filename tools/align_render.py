"""Align and render MT9T001 frames from a raw bulk dump.

Stream = back-to-back 8-bit Bayer frames, 2048 x 1536 (3 MiB), no markers.
The dump starts at an arbitrary byte, so we find:
  - column phase: the byte position (mod 2048) with the strongest
    discontinuity between pixel i and i+2 (same Bayer colour) = the seam
    where one row's right edge meets the next row's left edge;
  - row phase: the row (mod 1536) with the strongest discontinuity to the
    row two below = the seam between frames.

    .venv/bin/python tools/align_render.py scratch/expo_dump.bin
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

W, H = 2048, 1536
FRAME = W * H
OUT = Path(__file__).resolve().parent.parent / "scratch" / "aligned"

PATTERNS = {
    "GRBG": cv2.COLOR_BayerGB2BGR,
    "RGGB": cv2.COLOR_BayerBG2BGR,
    "BGGR": cv2.COLOR_BayerRG2BGR,
    "GBRG": cv2.COLOR_BayerGR2BGR,
}


def column_phase(data: np.ndarray) -> int:
    n = (data.size - 2) // W * W
    d = np.abs(data[2:n + 2].astype(np.int16) - data[:n].astype(np.int16))
    per_col = d.reshape(-1, W).mean(axis=0)
    # Seam at position p means pixel p-? is a right edge; the jump between i and i+2
    # straddles it for i = p-2 and p-1. Return start-of-row index.
    i = int(np.argmax(per_col[::2] + per_col[1::2]))  # pair even/odd so Bayer doesn't bias
    return (2 * i + 2) % W


def row_phase(rows: np.ndarray) -> int:
    n = (rows.shape[0] - 2) // H * H
    d = np.abs(rows[2:n + 2].astype(np.int16) - rows[:n].astype(np.int16)).mean(axis=1)
    per_row = d.reshape(-1, H).mean(axis=0)
    i = int(np.argmax(per_row[::2] + per_row[1::2]))
    return (2 * i + 2) % H


def stretch(img: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(img, (0.5, 99.5))
    if hi <= lo:
        return img.astype(np.uint8)
    return np.clip((img.astype(np.float32) - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT.parent / "expo_dump.bin"
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    print(f"{path.name}: {data.size:,} bytes = {data.size / FRAME:.2f} frames")
    print(f"stats: min={data.min()} max={data.max()} mean={data.mean():.1f} std={data.std():.1f}")

    c0 = column_phase(data)
    rows = data[c0:c0 + (data.size - c0) // W * W].reshape(-1, W)
    r0 = row_phase(rows)
    print(f"column phase = {c0}, row phase = {r0}")

    start = r0
    if rows.shape[0] < start + H:
        start = max(0, start - H)
    raw = np.ascontiguousarray(rows[start:start + H])
    if raw.shape[0] != H:
        sys.exit("not enough data for one aligned frame")

    OUT.mkdir(parents=True, exist_ok=True)
    gray = raw.reshape(H // 2, 2, W // 2, 2).mean(axis=(1, 3))
    cv2.imwrite(str(OUT / "gray_binned.png"), stretch(gray))
    for name, code in PATTERNS.items():
        bgr = cv2.cvtColor(raw, code)
        bgr = cv2.resize(bgr, (W // 2, H // 2), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(OUT / f"color_{name}.png"), stretch(bgr))
    print(f"wrote {OUT}/gray_binned.png and color_*.png")


if __name__ == "__main__":
    main()
