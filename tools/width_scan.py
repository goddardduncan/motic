"""Scan every plausible row width and pick the one with the strongest
row-to-row similarity. A correct row width makes adjacent rows nearly
identical (natural image rows are highly correlated); an incorrect one
produces random-looking stripes.

Also saves top-10 candidates as rasters.
"""
from __future__ import annotations

from pathlib import Path
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DUMP = ROOT / "scratch" / "dump.bin"
OUT = ROOT / "scratch" / "width_scan"


def row_similarity_score(data: np.ndarray, width: int, max_rows: int = 2000) -> float:
    rows = data.size // width
    if rows < 50:
        return -np.inf
    rows = min(rows, max_rows)
    img = data[: rows * width].reshape(rows, width).astype(np.float32)
    # Centre each row's values, then compute per-column correlation across rows.
    # Equivalently: how much do adjacent rows agree.
    d = np.diff(img, axis=0)
    # Lower mean |diff| = more similar rows.
    return -float(np.mean(np.abs(d)))


def main():
    data = np.frombuffer(DUMP.read_bytes(), dtype=np.uint8)
    print(f"dump: {data.size:,} bytes")

    # Scan widths 128..3000.
    widths = np.arange(128, 3001)
    scores = np.array([row_similarity_score(data, int(w)) for w in widths])

    order = np.argsort(-scores)
    print("\nTop 25 widths by row-to-row similarity (higher = adjacent rows agree):")
    for i in order[:25]:
        w = int(widths[i])
        s = scores[i]
        print(f"  width {w:>5}   mean|row[k+1]-row[k]| = {-s:7.3f}   rows≈{data.size // w}")

    OUT.mkdir(parents=True, exist_ok=True)
    for i in order[:12]:
        w = int(widths[i])
        rows = min(data.size // w, 1500)
        img = data[: rows * w].reshape(rows, w)
        cv2.imwrite(str(OUT / f"w{w:04d}.png"), img)
    print(f"\nTop-12 widths saved to {OUT}")


if __name__ == "__main__":
    main()
