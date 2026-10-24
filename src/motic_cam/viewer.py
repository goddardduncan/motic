"""Live viewer and diagnostic test bench for the Motic USB camera.

    motic-live                      # live window
    motic-live --snapshot out.png   # headless: settle exposure, save one frame

Press ; in the window for the key map. Esc quits, space saves.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .camera import (MIRROR_COLS, MIRROR_ROWS, MT9T001_CHIP_VERSION, PIXEL_CLOCK_HZ, REG,
                     SENSOR_H, SENSOR_W, CameraBusy, CameraNotFound, FrameReader, MoticCamera)

DEFAULT_CAPTURES = Path.home() / "Pictures" / "Motic"

PATTERNS = [
    ("GRBG", cv2.COLOR_BayerGB2BGR),
    ("RGGB", cv2.COLOR_BayerBG2BGR),
    ("BGGR", cv2.COLOR_BayerRG2BGR),
    ("GBRG", cv2.COLOR_BayerGR2BGR),
]
VIEWS = {
    ord("1"): "colour", ord("2"): "raw grey", ord("3"): "bayer plane",
    ord("6"): "frame difference", ord("7"): "1:1 centre crop",
}
REQUEST_SIZES = [(FrameReader.NATIVE, "whole frame"), (1 << 20, "1 MiB (old, bands)"),
                 (64 << 10, "64 KiB")]
HBLANKS = [142, 500, 1000, 2000]
VBLANKS = [25, 200, 800]
SKIPS = [0, 1, 3]  # register values -> 1x, 2x, 4x
GAINS = [(0x08, "1x"), (0x10, "2x"), (0x20, "4x"), (0x60, "8x")]
WINDOWS = [  # (name, row_start, col_start, rows, cols) - starts kept even for Bayer phase
    ("full 2048x1536", 20, 32, 1536, 2048),
    ("spec 1600x1200", 188, 256, 1200, 1600),
    ("centre 1024x768", 404, 544, 768, 1024),
]
AE_TARGET = 70.0
GAMMA_LUT = np.array([((i / 255.0) ** (1 / 2.2)) * 255 for i in range(256)], np.uint8)
DISPLAY_W = 1024

HELP = """\
DISPLAY (software only)
 1 colour      2 raw grey (no debayer)   3 single Bayer plane (press again to cycle)
 4 row-profile graph   5 column-profile graph   6 frame difference
 7 1:1 centre crop     8 histogram       9 freeze   0 stats panel
TRANSPORT / SENSOR TIMING (writes camera)
 q clock 48/24/12 MHz   w read size (whole frame / 1 MiB / 64 KiB)
 e horizontal blank     r vertical blank   t row skip 1/2/4x   y column skip 1/2/4x
 u window full/1600x1200/1024x768   i mirror rows   o mirror columns   p Bayer pattern
EXPOSURE / COLOUR
 a auto exposure   s shutter down   d shutter up   f gain 1/2/4/8x
 g gamma   h auto white balance   j reset camera defaults   k save   l print registers
 ; this help      ' reset all tests     space save     Esc quit"""


class State:
    def __init__(self):
        self.view = "colour"
        self.plane = 0
        self.pattern = 0
        self.row_profile = False
        self.col_profile = False
        self.histogram = False
        self.stats = True
        self.help = False
        self.frozen = False
        self.auto_exposure = True
        self.auto_wb = True
        self.gamma = True
        self.wb = np.ones(3, np.float32)
        self.clock = 0
        self.request = 0
        self.hblank = 0
        self.vblank = 0
        self.row_skip = 0
        self.col_skip = 0
        self.window = 0
        self.gain = 0
        self.mirror_rows = False
        self.mirror_cols = False
        self.message = ""
        self.message_until = 0.0

    def note(self, text: str) -> None:
        print(text)
        self.message, self.message_until = text, time.monotonic() + 3

    def settings(self) -> dict:
        return {
            "view": self.view, "pattern": PATTERNS[self.pattern][0], "clock": self.clock,
            "request": REQUEST_SIZES[self.request][1], "hblank": HBLANKS[self.hblank],
            "vblank": VBLANKS[self.vblank], "row_skip": SKIPS[self.row_skip] + 1,
            "col_skip": SKIPS[self.col_skip] + 1, "window": WINDOWS[self.window][0],
            "gain": GAINS[self.gain][1], "mirror_rows": self.mirror_rows,
            "mirror_cols": self.mirror_cols, "auto_wb": self.auto_wb, "gamma": self.gamma,
        }


def debayer(raw: np.ndarray, st: State) -> np.ndarray:
    bgr = cv2.cvtColor(raw, PATTERNS[st.pattern][1])
    if st.auto_wb:
        means = bgr[::4, ::4].reshape(-1, 3).mean(axis=0) + 1e-3
        st.wb = 0.7 * st.wb + 0.3 * (means[1] / means).astype(np.float32)
    return np.clip(bgr.astype(np.float32) * st.wb, 0, 255).astype(np.uint8)


def finish(img: np.ndarray, st: State) -> np.ndarray:
    return cv2.LUT(img, GAMMA_LUT) if st.gamma else img


def render(raw: np.ndarray, prev: np.ndarray | None, st: State) -> np.ndarray:
    h, w = raw.shape
    if st.view == "colour":
        img = finish(debayer(raw, st), st)
    elif st.view == "raw grey":
        img = cv2.cvtColor(finish(raw, st), cv2.COLOR_GRAY2BGR)
    elif st.view == "bayer plane":
        dy, dx = divmod(st.plane, 2)
        p = raw[dy::2, dx::2]
        img = cv2.cvtColor(finish(p, st), cv2.COLOR_GRAY2BGR)
    elif st.view == "frame difference":
        if prev is None or prev.shape != raw.shape:
            diff = np.zeros_like(raw)
        else:
            diff = cv2.absdiff(raw, prev)
        img = cv2.applyColorMap(np.clip(diff.astype(np.int16) * 8, 0, 255).astype(np.uint8),
                                cv2.COLORMAP_INFERNO)
    else:  # 1:1 centre crop of the colour image
        cy, cx = h // 2, w // 2
        crop = raw[cy - 384:cy + 384, cx - 512:cx + 512]
        crop = crop[: crop.shape[0] // 2 * 2, : crop.shape[1] // 2 * 2]
        img = finish(debayer(np.ascontiguousarray(crop), st), st)
    scale = DISPLAY_W / img.shape[1]
    out = cv2.resize(img, (DISPLAY_W, max(1, int(img.shape[0] * scale))),
                     interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_NEAREST)

    oh, ow = out.shape[:2]
    if st.row_profile:
        prof = raw.mean(axis=1)
        ys = np.linspace(0, oh - 1, prof.size).astype(np.int32)
        xs = (ow - 10 - prof / 255 * 200).astype(np.int32)
        cv2.polylines(out, [np.stack([xs, ys], 1)], False, (0, 255, 0), 1)
        cv2.rectangle(out, (ow - 212, 0), (ow - 8, oh - 1), (0, 255, 0), 1)
    if st.col_profile:
        prof = raw.mean(axis=0)
        xs = np.linspace(0, ow - 1, prof.size).astype(np.int32)
        ys = (oh - 10 - prof / 255 * 150).astype(np.int32)
        cv2.polylines(out, [np.stack([xs, ys], 1)], False, (255, 128, 0), 1)
        cv2.rectangle(out, (0, oh - 162), (ow - 1, oh - 8), (255, 128, 0), 1)
    if st.histogram:
        hist = np.bincount(raw[::4, ::4].ravel(), minlength=256).astype(np.float32)
        hist = hist / max(hist.max(), 1) * 120
        x0, y0 = 10, oh - 20
        cv2.rectangle(out, (x0 - 2, y0 - 122), (x0 + 258, y0 + 2), (40, 40, 40), -1)
        for i, v in enumerate(hist):
            cv2.line(out, (x0 + i, y0), (x0 + i, y0 - int(v)), (220, 220, 220), 1)
    return out


def draw_text(img: np.ndarray, lines: list[str], x: int, y: int, scale: float = 0.5) -> None:
    for i, line in enumerate(lines):
        yy = y + i * int(22 * scale / 0.5)
        cv2.putText(img, line, (x, yy), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3)
        cv2.putText(img, line, (x, yy), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 255, 255), 1)


def auto_expose(cam: MoticCamera, raw: np.ndarray) -> None:
    sample = raw[::8, ::8]
    mean = float(sample.mean())
    if float(np.mean(sample >= 250)) > 0.02:
        ratio = 0.6
    elif abs(mean - AE_TARGET) / AE_TARGET < 0.12:
        return
    else:
        ratio = float(np.clip(AE_TARGET / max(mean - 10.0, 1.0), 0.5, 2.0))
    new = int(round(cam.shutter * ratio))
    if new == cam.shutter:
        new += 1 if ratio > 1 else -1
    cam.set_shutter(new)


def save(raw: np.ndarray, view: np.ndarray, st: State, cam: MoticCamera, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = folder / f"motic-{stamp}"
    full = finish(debayer(raw, st), st)
    cv2.imwrite(str(base.with_suffix(".png")), full)
    cv2.imwrite(str(base) + "-view.png", view)
    base.with_suffix(".raw").write_bytes(raw.tobytes())
    meta = st.settings() | {"width": raw.shape[1], "height": raw.shape[0],
                            "shutter": cam.shutter,
                            "registers": {k: cam.get(k) for k in REG}}
    base.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    return base.with_suffix(".png")


def apply_window(cam: MoticCamera, reader: FrameReader, st: State) -> None:
    _, r0, c0, rows, cols = WINDOWS[st.window]
    cam.set("row_start", r0)
    cam.set("col_start", c0)
    cam.set("row_size", rows - 1)
    cam.set("col_size", cols - 1)
    cam.set("row_mode", SKIPS[st.row_skip])
    cam.set("col_mode", SKIPS[st.col_skip])
    reader.set_geometry(*cam.geometry())


def reset_all(cam: MoticCamera, reader: FrameReader, st: State) -> None:
    keep_view = (st.view, st.stats)
    st.__init__()
    st.view, st.stats = keep_view
    cam.apply_defaults()
    cam.set_clock(0)
    reader.request_size = FrameReader.NATIVE
    reader.set_geometry(*cam.geometry())


def handle_key(k: int, cam: MoticCamera, reader: FrameReader, st: State) -> bool:
    """Apply one key press. Returns False to quit."""
    if k == 27:
        return False
    if k in VIEWS:
        if k == ord("3") and st.view == "bayer plane":
            st.plane = (st.plane + 1) % 4
        st.view = VIEWS[k]
        extra = f" {['top-left', 'top-right', 'bottom-left', 'bottom-right'][st.plane]}" \
            if st.view == "bayer plane" else ""
        st.note(f"view: {st.view}{extra}")
    elif k == ord("4"):
        st.row_profile = not st.row_profile
    elif k == ord("5"):
        st.col_profile = not st.col_profile
    elif k == ord("8"):
        st.histogram = not st.histogram
    elif k == ord("9"):
        st.frozen = not st.frozen
        st.note("frozen" if st.frozen else "live")
    elif k == ord("0"):
        st.stats = not st.stats
    elif k == ord("q"):
        st.clock = (st.clock + 1) % 3
        cam.set_clock(st.clock)
        st.note(f"clock {PIXEL_CLOCK_HZ[st.clock] / 1e6:.0f} MHz")
    elif k == ord("w"):
        st.request = (st.request + 1) % len(REQUEST_SIZES)
        reader.request_size = REQUEST_SIZES[st.request][0]
        st.note(f"read size: {REQUEST_SIZES[st.request][1]}")
    elif k == ord("e"):
        st.hblank = (st.hblank + 1) % len(HBLANKS)
        cam.set("hblank", HBLANKS[st.hblank])
        st.note(f"horizontal blank {HBLANKS[st.hblank]}")
    elif k == ord("r"):
        st.vblank = (st.vblank + 1) % len(VBLANKS)
        cam.set("vblank", VBLANKS[st.vblank])
        st.note(f"vertical blank {VBLANKS[st.vblank]}")
    elif k == ord("t"):
        st.row_skip = (st.row_skip + 1) % len(SKIPS)
        apply_window(cam, reader, st)
        st.note(f"row skip {SKIPS[st.row_skip] + 1}x -> {reader.width}x{reader.height}")
    elif k == ord("y"):
        st.col_skip = (st.col_skip + 1) % len(SKIPS)
        apply_window(cam, reader, st)
        st.note(f"column skip {SKIPS[st.col_skip] + 1}x -> {reader.width}x{reader.height}")
    elif k == ord("u"):
        st.window = (st.window + 1) % len(WINDOWS)
        apply_window(cam, reader, st)
        st.note(f"window {WINDOWS[st.window][0]}")
    elif k in (ord("i"), ord("o")):
        bit = MIRROR_ROWS if k == ord("i") else MIRROR_COLS
        v = cam.get("read_mode2") ^ bit
        cam.set("read_mode2", v)
        st.mirror_rows, st.mirror_cols = bool(v & MIRROR_ROWS), bool(v & MIRROR_COLS)
        st.note(f"read mode 2 = 0x{cam.get('read_mode2'):04X} (rows {st.mirror_rows}, "
                f"cols {st.mirror_cols})")
    elif k == ord("p"):
        st.pattern = (st.pattern + 1) % len(PATTERNS)
        st.note(f"Bayer {PATTERNS[st.pattern][0]}")
    elif k == ord("a"):
        st.auto_exposure = not st.auto_exposure
        st.note(f"auto exposure {'on' if st.auto_exposure else 'off'}")
    elif k in (ord("s"), ord("d")):
        st.auto_exposure = False
        cam.set_shutter(cam.shutter * (0.8 if k == ord("s") else 1.25))
        st.note(f"shutter {cam.shutter}")
    elif k == ord("f"):
        st.gain = (st.gain + 1) % len(GAINS)
        cam.set("gain", GAINS[st.gain][0])
        st.note(f"gain {GAINS[st.gain][1]}")
    elif k == ord("g"):
        st.gamma = not st.gamma
    elif k == ord("h"):
        st.auto_wb = not st.auto_wb
        st.note(f"white balance {'auto' if st.auto_wb else 'fixed'}")
    elif k in (ord("j"), ord("'")):
        reset_all(cam, reader, st)
        st.note("camera reset to defaults")
    elif k == ord("l"):
        print("registers:", {k: f"0x{cam.get(k):04X}" for k in REG})
        st.note("registers printed to terminal")
    elif k == ord(";"):
        st.help = not st.help
    return True


def stats_lines(cam: MoticCamera, reader: FrameReader, st: State, raw: np.ndarray) -> list[str]:
    try:
        expected = cam.expected_fps()
    except Exception:
        expected = float("nan")
    s = st.settings()
    return [
        f"{reader.fps:4.1f} fps (sensor ~{expected:4.1f})  {reader.mbps:5.1f} MB/s  "
        f"frames {reader.frames}  torn/partial {reader.partials}  last read {reader.last_len:,}",
        f"frame {raw.shape[1]}x{raw.shape[0]}  clock {PIXEL_CLOCK_HZ[st.clock] / 1e6:.0f} MHz  "
        f"read {s['request']}  hblank {s['hblank']}  vblank {s['vblank']}",
        f"shutter {cam.shutter}{' (auto)' if st.auto_exposure else ''}  gain {s['gain']}  "
        f"bayer {s['pattern']}  view {s['view']}  window {s['window']}",
        f"skip rows {s['row_skip']}x cols {s['col_skip']}x  mirror rows {st.mirror_rows} "
        f"cols {st.mirror_cols}  mean {raw[::8, ::8].mean():5.1f}   ; = help",
    ]


def wait_frame(reader: FrameReader, after: int = -1, timeout: float = 5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        seq, frame = reader.latest(after)
        if frame is not None:
            return seq, frame
        time.sleep(0.01)
    raise RuntimeError("no frames from camera")


def message_screen(lines: list[str]) -> np.ndarray:
    img = np.full((300, DISPLAY_W, 3), 30, np.uint8)
    draw_text(img, lines, 30, 80, 0.8)
    return img


def wait_for_camera(clock: int) -> MoticCamera | None:
    """Open the camera, showing a waiting screen until it appears. None if the user quits."""
    shown = False
    while True:
        try:
            return MoticCamera(clock=clock)
        except (CameraNotFound, CameraBusy) as e:
            if isinstance(e, CameraBusy):
                lines = ["The Motic camera is in use by another program.",
                         "Close that program and this window will connect.", "", "Esc to quit"]
            else:
                lines = ["Looking for the Motic camera...",
                         "Plug it into any USB port.", "", "Esc to quit"]
            if not shown:
                cv2.namedWindow("Motic", cv2.WINDOW_NORMAL)
                shown = True
            cv2.imshow("Motic", message_screen(lines))
            if (cv2.waitKey(700) & 0xFF) == 27:
                return None


def run_live(cam: MoticCamera, reader: FrameReader, captures: Path) -> None:
    st = State()
    print(HELP)
    if cam.chip_version != MT9T001_CHIP_VERSION:
        st.note(f"Warning: unexpected sensor chip 0x{cam.chip_version:04X} - image may be wrong")
    try:
        seq, raw = wait_frame(reader)
    except RuntimeError:
        cv2.imshow("Motic", message_screen(["The camera was found but sent no images.",
                                            "Unplug it, plug it back in, and reopen."]))
        cv2.waitKey(0)
        return
    prev = None
    view = render(raw, prev, st)
    cv2.namedWindow("Motic", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Motic", DISPLAY_W, DISPLAY_W * 3 // 4)
    n = 0
    dirty = True
    stats: list[str] = []
    stats_at = 0.0
    while True:
        if not st.frozen:
            new_seq, frame = reader.latest(seq)
            if frame is not None:
                prev, raw, seq = raw, frame, new_seq
                n += 1
                dirty = True
                if st.auto_exposure and n % 2 == 0:
                    auto_expose(cam, raw)
        now = time.monotonic()
        if reader.error:
            cv2.imshow("Motic", message_screen([reader.error, "Plug it back in and reopen the app.",
                                                "", "Esc to quit"]))
            while (cv2.waitKey(500) & 0xFF) != 27:
                pass
            break
        if st.message and now >= st.message_until:
            st.message, dirty = "", True
        if dirty:
            view = render(raw, prev, st)
            if st.stats:
                if now - stats_at > 0.5:
                    stats, stats_at = stats_lines(cam, reader, st, raw), now
                draw_text(view, stats, 10, 22)
            if st.help:
                overlay = view.copy()
                cv2.rectangle(overlay, (0, 0), (view.shape[1], 330), (0, 0, 0), -1)
                view = cv2.addWeighted(overlay, 0.75, view, 0.25, 0)
                draw_text(view, HELP.splitlines(), 10, 110, 0.5)
            if st.message:
                draw_text(view, [st.message], 10, view.shape[0] - 20, 0.7)
            cv2.imshow("Motic", view)
            dirty = False

        k = cv2.waitKey(10) & 0xFF
        if k == 255:
            continue
        dirty, stats_at = True, 0.0
        if k in (ord(" "), ord("k")):
            st.note(f"saved {save(raw, view, st, cam, captures)}")
        elif not handle_key(k, cam, reader, st):
            break
    cv2.destroyAllWindows()


def main() -> None:
    ap = argparse.ArgumentParser(description="Live viewer for the Motic USB camera")
    ap.add_argument("--clock", type=int, default=0, choices=(0, 1, 2),
                    help="0 = 48 MHz (default), 1 = 24 MHz, 2 = 12 MHz")
    ap.add_argument("--shutter", type=int, default=600, help="initial shutter width in rows")
    ap.add_argument("--snapshot", type=Path, help="save one auto-exposed frame here and exit")
    ap.add_argument("--captures", type=Path, default=DEFAULT_CAPTURES,
                    help=f"folder for saved images (default {DEFAULT_CAPTURES})")
    args, _ = ap.parse_known_args()  # tolerate extra arguments macOS may pass to apps

    cam = MoticCamera(clock=args.clock) if args.snapshot else wait_for_camera(args.clock)
    if cam is None:
        return
    cam.apply_defaults()
    cam.set_shutter(args.shutter)
    print(f"Camera {cam.usb_id}, sensor chip 0x{cam.chip_version:04X}, "
          f"{SENSOR_W}x{SENSOR_H}, expected {cam.expected_fps():.1f} fps")
    reader = FrameReader(cam)
    try:
        if args.snapshot:
            st = State()
            seq = -1
            for _ in range(10):
                seq, raw = wait_frame(reader, seq)
                auto_expose(cam, raw)
            for _ in range(6):
                seq, raw = wait_frame(reader, seq)
                img = finish(debayer(raw, st), st)
            args.snapshot.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(args.snapshot), img)
            print(f"saved {args.snapshot}: {raw.shape[1]}x{raw.shape[0]}, shutter {cam.shutter}, "
                  f"{reader.fps:.1f} fps, {reader.mbps:.1f} MB/s, torn {reader.partials}")
        else:
            run_live(cam, reader, args.captures)
    finally:
        reader.close()
        cam.close()


if __name__ == "__main__":
    main()
