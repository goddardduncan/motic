"""Render specific candidate (W, H) layouts, cropping to visible region.

W = 2048 px (16-bit LE confirmed). Try several H values; also crop each
to the likely active area (1600 wide × actual H) to see if padding is
on the right.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DUMP = ROOT / "scratch" / "long_dump.bin"
OUT = ROOT / "scratch" / "candidates16"
W = 2048

HEIGHTS = [1200, 1212, 1216, 1232, 1248, 1256, 1288, 1296, 1536]


def stretch(a):
    lo, hi = np.percentile(a, (1, 99.5))
    if hi <= lo:
        return (a >> 8).astype(np.uint8)
    return np.clip((a.astype(np.float32) - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)


def main():
    data = np.frombuffer(DUMP.read_bytes(), dtype=np.uint8)
    pixels = data.view(np.uint16)
    print(f"long_dump: {pixels.size:,} px at W={W} = {pixels.size // W} rows")

    OUT.mkdir(parents=True, exist_ok=True)
    for H in HEIGHTS:
        frame_px = W * H
        if pixels.size < frame_px:
            continue
        frame = pixels[:frame_px].reshape(H, W)
        # Full width render
        cv2.imwrite(str(OUT / f"full_W{W}_H{H}.png"), stretch(frame))
        # Crop to probable active area 1600 wide
        if frame.shape[1] >= 1600:
            cv2.imwrite(str(OUT / f"crop1600_H{H}.png"), stretch(frame[:, :1600]))
        print(f"  H={H:>4}  rendered")

    # Also render a tall mosaic of the whole dump at W=2048 downsampled,
    # so frame boundaries show as horizontal seams.
    rows = pixels.size // W
    img = pixels[: rows * W].reshape(rows, W)
    # Downsample vertically by 4 and horizontally by 4 for a thumbnail strip
    thumb = img[::4, ::4]
    cv2.imwrite(str(OUT / f"full_stream_thumb.png"), stretch(thumb))
    print(f"\nFull-stream thumbnail at {OUT / 'full_stream_thumb.png'}  "
          f"({thumb.shape[1]}x{thumb.shape[0]})")


if __name__ == "__main__":
    main()
