"""Driver for the Motic USB camera 0634:3222 / 232F:4002 (USB bridge + Micron MT9T001).

Protocol recovered from MUCam32.dll (class MUCamMC3222); see reversing/notes.md.

- Sensor registers are reached through vendor control transfers that the
  bridge forwards to the sensor's I2C address 0xBA.
- Control transfer 0xF6 sets the bridge/sensor clock: 0 = 48 MHz,
  1 = 24 MHz, 2 = 12 MHz. Power-on is slower still.
- Bulk endpoint 0x82 delivers one frame per transfer, terminated by a short
  packet. A read request larger than a frame returns exactly one aligned
  frame. If no read is pending, the bridge drops the rest of the frame,
  so small reads produce torn, banded images.
"""
from __future__ import annotations

import threading
import time

import libusb_package
import numpy as np
import usb.backend.libusb1
import usb.core
import usb.util

# Both IDs are dispatched to the MUCamMC3222 class by MUCam32.dll.
SUPPORTED_IDS = [(0x0634, 0x3222), (0x232F, 0x4002)]
MT9T001_CHIP_VERSION = 0x1621
EP_IN = 0x82
SENSOR_W, SENSOR_H = 2048, 1536
PIXEL_CLOCK_HZ = {0: 48e6, 1: 24e6, 2: 12e6}
ROW_OVERHEAD = 369  # extra clocks per row, from the DLL's exposure formula

REG = {
    "chip": 0x00, "row_start": 0x01, "col_start": 0x02, "row_size": 0x03,
    "col_size": 0x04, "hblank": 0x05, "vblank": 0x06, "shutter_hi": 0x08,
    "shutter": 0x09, "read_mode1": 0x1E, "read_mode2": 0x20,
    "row_mode": 0x22, "col_mode": 0x23, "gain": 0x35,
}
DEFAULTS = {
    "row_start": 20, "col_start": 32, "row_size": SENSOR_H - 1, "col_size": SENSOR_W - 1,
    "hblank": 142, "vblank": 25, "row_mode": 0, "col_mode": 0, "gain": 0x08,
}
MIRROR_ROWS, MIRROR_COLS = 0x8000, 0x4000
MAX_SHUTTER = 0xFFFF
# Global gain register values, from the DLL's MT9T001 gain table.
GAIN_STEPS = [(0x08, "1x"), (0x10, "2x"), (0x20, "4x"), (0x60, "8x")]


class CameraNotFound(RuntimeError):
    pass


class CameraBusy(RuntimeError):
    pass


def find_device():
    backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
    for vid, pid in SUPPORTED_IDS:
        dev = usb.core.find(idVendor=vid, idProduct=pid, backend=backend)
        if dev is not None:
            return dev
    ids = ", ".join(f"{v:04x}:{p:04x}" for v, p in SUPPORTED_IDS)
    raise CameraNotFound(f"No Motic camera found (looked for {ids})")


class MoticCamera:
    def __init__(self, clock: int = 0):
        dev = find_device()
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass  # already configured
        try:
            usb.util.claim_interface(dev, 0)
        except usb.core.USBError as e:
            raise CameraBusy(f"Camera is in use by another program ({e})") from e
        try:
            dev.clear_halt(EP_IN)
        except usb.core.USBError:
            pass
        self.dev = dev
        self.usb_id = f"{dev.idVendor:04x}:{dev.idProduct:04x}"
        self.chip_version = self.read_reg(REG["chip"])
        self.shutter = self.read_reg(REG["shutter"])
        self.gain_index = 0
        self.set_clock(clock)

    def read_reg(self, reg: int) -> int:
        data = self.dev.ctrl_transfer(0xC0, 0xF1, 0xBA00 | reg, 0, 2, timeout=500)
        return (data[0] << 8) | data[1]

    def write_reg(self, reg: int, value: int) -> None:
        payload = bytes([(value >> 8) & 0xFF, value & 0xFF])
        self.dev.ctrl_transfer(0x40, 0xF0, 0xBA00 | reg, 0, payload, timeout=500)

    def get(self, name: str) -> int:
        return self.read_reg(REG[name])

    def set(self, name: str, value: int) -> None:
        self.write_reg(REG[name], int(value))

    def apply_defaults(self) -> None:
        for name, value in DEFAULTS.items():
            self.set(name, value)
        self.set("read_mode2", self.get("read_mode2") & ~(MIRROR_ROWS | MIRROR_COLS))
        self.gain_index = 0

    def set_gain_index(self, index: int) -> None:
        index = max(0, min(index, len(GAIN_STEPS) - 1))
        self.set("gain", GAIN_STEPS[index][0])
        self.gain_index = index

    def fast_shutter_limit(self) -> int:
        """Longest shutter (rows) that doesn't stretch the frame time."""
        return self.get("row_size") + 1 + self.get("vblank")

    def set_clock(self, index: int) -> None:
        if index not in PIXEL_CLOCK_HZ:
            raise ValueError("clock index must be 0 (48 MHz), 1 (24 MHz) or 2 (12 MHz)")
        self.dev.ctrl_transfer(0x40, 0xF6, index, 0, None, timeout=500)
        self.clock = index

    def set_shutter(self, rows: float) -> None:
        rows = int(max(1, min(rows, MAX_SHUTTER)))
        self.set("shutter_hi", 0)
        self.set("shutter", rows)
        self.shutter = rows

    def geometry(self) -> tuple[int, int]:
        """Output (width, height) implied by the window and skip registers."""
        col_skip = (self.get("col_mode") & 0x7) + 1
        row_skip = (self.get("row_mode") & 0x7) + 1
        return (self.get("col_size") + 1) // col_skip, (self.get("row_size") + 1) // row_skip

    def expected_fps(self) -> float:
        row_clocks = self.get("col_size") + 1 + self.get("hblank") + ROW_OVERHEAD
        rows = self.get("row_size") + 1 + self.get("vblank")
        return PIXEL_CLOCK_HZ[self.clock] / (row_clocks * rows)

    def read_into(self, buf) -> int:
        try:
            return self.dev.read(EP_IN, buf, timeout=2000)
        except usb.core.USBTimeoutError:
            return 0

    def close(self) -> None:
        try:
            usb.util.release_interface(self.dev, 0)
        except usb.core.USBError:
            pass  # already unplugged
        usb.util.dispose_resources(self.dev)


class FrameReader:
    """Background bulk reader.

    In the normal mode each read is larger than a frame, so the bridge
    returns exactly one aligned frame per transfer. Setting `request_size`
    smaller than a frame switches to concatenating reads; that mode exists
    only to reproduce the torn, three-band images.
    """

    NATIVE = 0

    def __init__(self, cam: MoticCamera):
        self.cam = cam
        self.request_size = self.NATIVE
        self.width, self.height = cam.geometry()
        self.frames = 0
        self.partials = 0
        self.error: str | None = None
        self.last_len = 0
        self.mbps = 0.0
        self.fps = 0.0
        self._latest: np.ndarray | None = None
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set_geometry(self, width: int, height: int) -> None:
        with self._lock:
            self.width, self.height = width, height

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height

    def _publish(self, data: bytes) -> None:
        with self._lock:
            w, h = self.width, self.height
            if len(data) != w * h:
                self.partials += 1
                return
            self._latest = np.frombuffer(data, dtype=np.uint8).reshape(h, w)
            self._seq += 1
            self.frames += 1

    def _run(self) -> None:
        pending = bytearray()
        total, t_mark, frames_mark = 0, time.monotonic(), 0
        buf, buf_size = None, -1
        errors = 0
        while not self._stop.is_set():
            native = self.request_size == self.NATIVE
            size = self.frame_bytes + 4096 if native else self.request_size
            if size != buf_size:
                buf, buf_size = usb.util.create_buffer(size), size
                pending.clear()
            try:
                n = self.cam.read_into(buf)
                errors = 0
            except usb.core.USBError as e:
                errors += 1
                if errors >= 5:
                    self.error = f"Camera disconnected ({e})"
                    return
                time.sleep(0.2)
                continue
            total += n
            if n:
                self.last_len = n
                if native:
                    self._publish(bytes(memoryview(buf)[:n]))
                else:
                    pending += memoryview(buf)[:n]
                    while len(pending) >= self.frame_bytes:
                        self._publish(bytes(pending[:self.frame_bytes]))
                        del pending[:self.frame_bytes]
            now = time.monotonic()
            if now - t_mark >= 1.0:
                self.mbps = total / (now - t_mark) / 1e6
                self.fps = (self.frames - frames_mark) / (now - t_mark)
                total, t_mark, frames_mark = 0, now, self.frames

    def latest(self, after: int = -1) -> tuple[int, np.ndarray | None]:
        """Return (sequence number, frame). Frame is None if nothing newer than `after`."""
        with self._lock:
            if self._seq == after:
                return self._seq, None
            return self._seq, self._latest

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
