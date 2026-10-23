"""Render the dump as 16-bit little-endian pixel data.

Each pixel is 2 bytes LE. We know the row stride is ~2048 bytes = 1024
pixels. Scan a tight range of pixel widths and look for the best
row-to-row similarity, then save several heights for each top width.

Also auto-stretches contrast per candidate so the image is actually
visible even when the raw values are near-saturation.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DUMP = ROOT / "scratch" / "dump.bin"
OUT = ROOT / "scratch" / "render16"


def auto_stretch(img: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(img, (1.0, 99.5))
    if hi <= lo:
        return (img >> 8).astype(np.uint8)
    out = np.clip((img.astype(np.float32) - lo) / (hi - lo), 0, 1)
    return (out * 255).astype(np.uint8)


def row_similarity(pixels: np.ndarray, width: int, max_rows: int = 2000) -> float:
    rows = min(pixels.size // width, max_rows)
    if rows < 50:
        return -np.inf
    img = pixels[: rows * width].reshape(rows, width).astype(np.float32)
    return -float(np.mean(np.abs(np.diff(img, axis=0))))


def main():
    data = np.frombuffer(DUMP.read_bytes(), dtype=np.uint8)
    pixels = data.view(np.uint16)
    print(f"dump: {data.size:,} bytes = {pixels.size:,} 16-bit pixels")
    print(f"pixel stats: min={pixels.min()} max={pixels.max()} "
          f"mean={pixels.mean():.1f} std={pixels.std():.1f}")

    # Sweep pixel widths around the obvious 1024, with wider range for safety.
    widths = np.arange(1000, 3001, 1)
    scores = np.array([row_similarity(pixels, int(w)) for w in widths])
    order = np.argsort(-scores)
    print("\nTop 15 pixel widths (16-bit interpretation):")
    for i in order[:15]:
        w = int(widths[i])
        print(f"  width {w:>5} px   mean|row-diff| = {-scores[i]:7.2f}   rows≈{pixels.size // w}")

    OUT.mkdir(parents=True, exist_ok=True)
    for i in order[:8]:
        w = int(widths[i])
        rows = min(pixels.size // w, 1500)
        img = pixels[: rows * w].reshape(rows, w)
        cv2.imwrite(str(OUT / f"w{w:04d}.png"), auto_stretch(img))
    print(f"\nTop-8 widths saved to {OUT}")


if __name__ == "__main__":
    main()
