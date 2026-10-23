"""Hunt for the frame geometry in a bulk-stream dump.

Takes the raw dump written by probe.py and tries two things:

  1. Compute a wide-range autocorrelation to find the dominant stride, which
     should equal (header + width * height * bpp) if a frame has one.

  2. Rasterise the dump at a list of candidate (width, height) and save each
     as a PNG, so you can eyeball which one shows the real scene.

The autocorrelation operates on byte differences, not raw values, which
cancels DC bias — so a mostly-white or mostly-black image still shows
structural periodicity when edges exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

DUMP = Path(__file__).resolve().parent.parent / "scratch" / "dump.bin"
OUT = Path(__file__).resolve().parent.parent / "scratch" / "candidates"

CANDIDATE_SIZES = [
    (352, 240),
    (352, 288),
    (320, 240),
    (640, 480),
    (720, 480),
    (720, 576),
    (800, 600),
    (640, 512),
    (1024, 768),
    (1280, 960),
    (1280, 1024),
    (1600, 1200),
    # Weird candidates — raw script tried these so include them
    (1344, 1024),
    (1344, 1291),
]


def autocorr(data: np.ndarray, min_lag: int = 5_000, max_lag: int = 4_000_000) -> list[tuple[int, float]]:
    # Differentiate to kill DC and emphasise edges.
    d = np.diff(data.astype(np.int16)).astype(np.float32)
    # Zero-mean, then FFT autocorrelation.
    d -= d.mean()
    n = 1 << int(np.ceil(np.log2(d.size * 2)))
    f = np.fft.rfft(d, n=n)
    ac = np.fft.irfft(f * np.conj(f))[:d.size]
    # Normalise against lag-0 ONCE so scores are comparable.
    ac /= ac[0] if ac[0] != 0 else 1.0

    lags = np.arange(ac.size)
    mask = (lags >= min_lag) & (lags <= max_lag)
    scored = np.where(mask, ac, -np.inf)
    top_idx = np.argpartition(scored, -40)[-40:]
    top_idx = top_idx[np.argsort(-scored[top_idx])]
    return [(int(l), float(ac[l])) for l in top_idx]


def rasterise(data: np.ndarray, w: int, h: int, out: Path, header: int = 0) -> None:
    n = w * h
    if data.size < header + n:
        return
    frame = data[header:header + n].reshape(h, w)

    # Save grayscale as-is, plus a naive debayer in each pattern just so the
    # scene is recognisable even with wrong pattern.
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame)


def main():
    data = np.frombuffer(DUMP.read_bytes(), dtype=np.uint8)
    print(f"dump: {data.size:,} bytes")
    print(f"stats: min={data.min()} max={data.max()} mean={data.mean():.2f} std={data.std():.2f}")

    print("\nTop autocorrelation lags (differenced signal, 5kB..4MB window):")
    peaks = autocorr(data)
    for lag, score in peaks[:20]:
        # Try to factor the lag into "likely sensor dimensions"
        hint = ""
        for w in (320, 352, 640, 720, 800, 1024, 1280, 1344, 1600):
            if lag % w == 0:
                hint = f"= {w}×{lag // w}"
                break
            # Also try bayer-doubled or packed-16bit
            if lag % (w * 2) == 0:
                hint = f"= {w}×{lag // (w * 2)} ×2bpp"
                break
        print(f"  lag {lag:>10,}  score {score:>7.4f}  {hint}")

    OUT.mkdir(parents=True, exist_ok=True)
    print(f"\nWriting candidate rasters to {OUT}:")
    for w, h in CANDIDATE_SIZES:
        rasterise(data, w, h, OUT / f"{w}x{h}.png", header=0)
        print(f"  {w:>4}x{h:<4}  {w*h:>10,} bytes/frame")


if __name__ == "__main__":
    main()
