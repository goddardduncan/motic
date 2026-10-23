"""Find per-frame height.

We have width = 2048 px (16-bit LE). Scan likely total-frame-sizes against
long dump: the right height makes rows[k] and rows[k+H] highly similar
(it's the same pixel row of the next frame).
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DUMP = ROOT / "scratch" / "long_dump.bin"
OUT = ROOT / "scratch" / "frame_hunt"
WIDTH = 2048  # pixels


def main():
    data = np.frombuffer(DUMP.read_bytes(), dtype=np.uint8)
    pixels = data.view(np.uint16)
    print(f"long_dump: {data.size:,} bytes = {pixels.size:,} px")

    total_rows = pixels.size // WIDTH
    print(f"total rows at W={WIDTH}: {total_rows}")

    img = pixels[: total_rows * WIDTH].reshape(total_rows, WIDTH).astype(np.float32)

    # For each candidate frame height, compute mean |row[k] - row[k+H]|.
    # Low value = frame start repeats at that offset = height is correct.
    heights = np.arange(400, 2001)
    scores = []
    for H in heights:
        if total_rows <= H + 20:
            scores.append(np.inf)
            continue
        n = total_rows - H
        a = img[:n]
        b = img[H:H + n]
        scores.append(float(np.mean(np.abs(a - b))))
    scores = np.array(scores)
    order = np.argsort(scores)
    print("\nTop 20 candidate frame heights (lower = frame repeats cleanly):")
    for i in order[:20]:
        H = int(heights[i])
        total_frame_bytes = WIDTH * H * 2
        print(f"  height {H:>5}   mean|diff| = {scores[i]:7.2f}   "
              f"frame size = {total_frame_bytes:,} B = {total_frame_bytes/1024/1024:.2f} MB")

    OUT.mkdir(parents=True, exist_ok=True)
    for i in order[:6]:
        H = int(heights[i])
        if total_rows < H:
            continue
        frame = img[:H].astype(np.uint16)
        lo, hi = np.percentile(frame, (1.0, 99.5))
        if hi > lo:
            vis = np.clip((frame - lo) / (hi - lo), 0, 1) * 255
        else:
            vis = frame >> 8
        cv2.imwrite(str(OUT / f"h{H:04d}.png"), vis.astype(np.uint8))
    print(f"\nTop-6 heights saved to {OUT}")


if __name__ == "__main__":
    main()
