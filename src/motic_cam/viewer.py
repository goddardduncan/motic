"""Live viewer for the Motic USB camera.

    motic-live                      # live window
    motic-live --snapshot out.png   # headless: settle exposure, save one frame

Keys:
  q / Esc  quit               s  save PNG + raw frame to captures/
  a        auto-exposure      e / E  shutter down / up (turns auto off)
  w        auto white balance g  gamma on/off
  p        next Bayer pattern c  cycle bridge clock (fast/medium/slow)
  [ ]      shift frame up/down 2 rows     { }  by 64 rows
  , .      shift frame left/right 2 px    r  re-detect alignment
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .camera import FRAME_BYTES, HEIGHT, WIDTH, FrameStream, MoticCamera

PATTERNS = [
    ("GRBG", cv2.COLOR_BayerGB2BGR),
    ("RGGB", cv2.COLOR_BayerBG2BGR),
    ("BGGR", cv2.COLOR_BayerRG2BGR),
    ("GBRG", cv2.COLOR_BayerGR2BGR),
]
AE_TARGET = 70.0
GAMMA_LUT = np.array([((i / 255.0) ** (1 / 2.2)) * 255 for i in range(256)], np.uint8)


class Pipeline:
    def __init__(self):
        self.pattern = 0
        self.auto_wb = True
        self.gamma = True
        self.wb = np.ones(3, np.float32)

    def render(self, raw: np.ndarray, full: bool = False) -> np.ndarray:
        bgr = cv2.cvtColor(raw, PATTERNS[self.pattern][1])
        if not full:
            bgr = cv2.resize(bgr, (WIDTH // 2, HEIGHT // 2), interpolation=cv2.INTER_AREA)
        if self.auto_wb:
            means = bgr[::4, ::4].reshape(-1, 3).mean(axis=0) + 1e-3
            target = means[1] / means
            self.wb = 0.7 * self.wb + 0.3 * target.astype(np.float32)
        out = np.clip(bgr.astype(np.float32) * self.wb, 0, 255).astype(np.uint8)
        return cv2.LUT(out, GAMMA_LUT) if self.gamma else out


def auto_expose(cam: MoticCamera, raw: np.ndarray) -> None:
    sample = raw[::8, ::8]
    mean = float(sample.mean())
    saturated = float(np.mean(sample >= 250))
    if saturated > 0.02:
        ratio = 0.6
    elif abs(mean - AE_TARGET) / AE_TARGET < 0.12:
        return
    else:
        ratio = float(np.clip(AE_TARGET / max(mean - 10.0, 1.0), 0.5, 2.0))
    new = int(round(cam.shutter * ratio))
    if new == cam.shutter:
        new += 1 if ratio > 1 else -1
    cam.set_shutter(new)


def wait_for_frames(stream: FrameStream, cam: MoticCamera, settle: int, auto: bool):
    while not stream.calibrate():
        time.sleep(0.05)
    raw = None
    for _ in range(settle):
        raw = None
        while raw is None:
            time.sleep(0.02)
            raw = stream.latest_frame()
        if auto:
            auto_expose(cam, raw)
    return raw


def save(raw: np.ndarray, pipe: Pipeline, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    png = folder / f"motic-{stamp}.png"
    cv2.imwrite(str(png), pipe.render(raw, full=True))
    (folder / f"motic-{stamp}.raw").write_bytes(raw.tobytes())
    return png


def run_live(cam: MoticCamera, stream: FrameStream, pipe: Pipeline, captures: Path) -> None:
    auto_exp = True
    clock_names = {0: "fast", 1: "medium", 2: "slow"}
    print("Waiting for frames to align…")
    raw = wait_for_frames(stream, cam, settle=1, auto=False)
    cv2.namedWindow("Motic", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Motic", WIDTH // 2, HEIGHT // 2)
    last, fps, frame_no = time.monotonic(), 0.0, 0
    while True:
        new = stream.latest_frame()
        if new is not None:
            raw = new
            frame_no += 1
            now = time.monotonic()
            fps = 0.8 * fps + 0.2 / max(now - last, 1e-3)
            last = now
            if auto_exp and frame_no % 2 == 0:
                auto_expose(cam, raw)
            view = pipe.render(raw)
            label = (f"{fps:4.1f} fps  clock {clock_names[cam.clock]}  shutter {cam.shutter}"
                     f"{' (auto)' if auto_exp else ''}  bayer {PATTERNS[pipe.pattern][0]}"
                     f"  WB {'auto' if pipe.auto_wb else 'fixed'}  offset {stream.offset}")
            cv2.putText(view, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
            cv2.putText(view, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
            cv2.imshow("Motic", view)

        k = cv2.waitKey(10) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord("s") and raw is not None:
            print(f"saved {save(raw, pipe, captures)}")
        elif k == ord("a"):
            auto_exp = not auto_exp
        elif k in (ord("e"), ord("E")):
            auto_exp = False
            cam.set_shutter(cam.shutter * (0.8 if k == ord("e") else 1.25))
        elif k == ord("w"):
            pipe.auto_wb = not pipe.auto_wb
        elif k == ord("g"):
            pipe.gamma = not pipe.gamma
        elif k == ord("p"):
            pipe.pattern = (pipe.pattern + 1) % len(PATTERNS)
        elif k == ord("c"):
            cam.set_clock((cam.clock + 1) % 3)
        elif k == ord("["):
            stream.nudge(-2 * WIDTH)
        elif k == ord("]"):
            stream.nudge(2 * WIDTH)
        elif k == ord("{"):
            stream.nudge(-64 * WIDTH)
        elif k == ord("}"):
            stream.nudge(64 * WIDTH)
        elif k == ord(","):
            stream.nudge(-2)
        elif k == ord("."):
            stream.nudge(2)
        elif k == ord("r"):
            stream.offset = None
            raw = wait_for_frames(stream, cam, settle=1, auto=False)
    cv2.destroyAllWindows()


def main() -> None:
    ap = argparse.ArgumentParser(description="Live viewer for the Motic USB camera")
    ap.add_argument("--clock", type=int, default=0, choices=(0, 1, 2),
                    help="bridge clock: 0 fast (default), 1 medium, 2 slow")
    ap.add_argument("--shutter", type=int, default=800, help="initial shutter width in rows")
    ap.add_argument("--snapshot", type=Path, help="save one auto-exposed frame here and exit")
    ap.add_argument("--captures", type=Path, default=Path("captures"))
    args = ap.parse_args()

    cam = MoticCamera(clock=args.clock)
    print(f"Sensor chip version 0x{cam.chip_version:04X}, {WIDTH}x{HEIGHT}, "
          f"frame {FRAME_BYTES:,} bytes")
    cam.set_shutter(args.shutter)
    stream = FrameStream(cam)
    pipe = Pipeline()
    try:
        if args.snapshot:
            raw = wait_for_frames(stream, cam, settle=8, auto=True)
            for _ in range(6):  # let white balance converge
                pipe.render(raw)
            args.snapshot.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(args.snapshot), pipe.render(raw))
            print(f"saved {args.snapshot} (shutter {cam.shutter}, offset {stream.offset})")
        else:
            run_live(cam, stream, pipe, args.captures)
    finally:
        stream.close()
        cam.close()


if __name__ == "__main__":
    main()
