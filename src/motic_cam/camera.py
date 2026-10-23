"""Driver for the Motic USB camera 0634:3222 (USB bridge + Micron MT9T001).

Protocol recovered from MUCam32.dll (class MUCamMC3222); see reversing/notes.md.

- Sensor registers are reached through vendor control transfers that the
  bridge forwards to the sensor's I2C address 0xBA.
- Control transfer 0xF6 sets the bridge clock: 0 = fast, 1 = medium,
  2 = slow. The power-on default is very slow (~0.4 MB/s).
- Bulk endpoint 0x82 carries back-to-back 8-bit Bayer frames of
  WIDTH x HEIGHT bytes, with no headers or end-of-frame markers.
"""
from __future__ import annotations

import threading

import libusb_package
import numpy as np
import usb.backend.libusb1
import usb.core
import usb.util

VID, PID = 0x0634, 0x3222
EP_IN = 0x82
WIDTH, HEIGHT = 2048, 1536
FRAME_BYTES = WIDTH * HEIGHT

REG_CHIP_VERSION = 0x00
REG_SHUTTER_UPPER = 0x08
REG_SHUTTER = 0x09
MAX_SHUTTER = 0xFFFF


class MoticCamera:
    def __init__(self, clock: int = 0):
        backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
        dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
        if dev is None:
            raise RuntimeError(f"No Motic camera at {VID:04x}:{PID:04x}")
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass  # already configured
        usb.util.claim_interface(dev, 0)
        try:
            dev.clear_halt(EP_IN)
        except usb.core.USBError:
            pass
        self.dev = dev
        self.chip_version = self.read_reg(REG_CHIP_VERSION)
        self.shutter = self.read_reg(REG_SHUTTER)
        self.set_clock(clock)

    def read_reg(self, reg: int) -> int:
        data = self.dev.ctrl_transfer(0xC0, 0xF1, 0xBA00 | reg, 0, 2, timeout=500)
        return (data[0] << 8) | data[1]

    def write_reg(self, reg: int, value: int) -> None:
        payload = bytes([(value >> 8) & 0xFF, value & 0xFF])
        self.dev.ctrl_transfer(0x40, 0xF0, 0xBA00 | reg, 0, payload, timeout=500)

    def set_clock(self, index: int) -> None:
        if index not in (0, 1, 2):
            raise ValueError("clock index must be 0 (fast), 1 or 2 (slow)")
        self.dev.ctrl_transfer(0x40, 0xF6, index, 0, None, timeout=500)
        self.clock = index

    def set_shutter(self, rows: int) -> None:
        rows = int(max(1, min(rows, MAX_SHUTTER)))
        self.write_reg(REG_SHUTTER_UPPER, 0)
        self.write_reg(REG_SHUTTER, rows)
        self.shutter = rows

    def read_into(self, buf) -> int:
        try:
            return self.dev.read(EP_IN, buf, timeout=2000)
        except usb.core.USBTimeoutError:
            return 0

    def close(self) -> None:
        usb.util.release_interface(self.dev, 0)
        usb.util.dispose_resources(self.dev)


def column_phase(data: np.ndarray) -> int:
    """Byte index (mod WIDTH) where rows start: the strongest jump between
    same-colour pixels two apart is the right-edge/left-edge seam."""
    n = (data.size - 2) // WIDTH * WIDTH
    d = np.abs(data[2:n + 2].astype(np.int16) - data[:n].astype(np.int16))
    per_col = d.reshape(-1, WIDTH).mean(axis=0)
    i = int(np.argmax(per_col[::2] + per_col[1::2]))
    return (2 * i + 2) % WIDTH


def row_phase(rows: np.ndarray) -> int:
    """Row index (mod HEIGHT) where frames start: the strongest jump between
    same-colour rows two apart is the bottom/top seam."""
    n = (rows.shape[0] - 2) // HEIGHT * HEIGHT
    d = np.abs(rows[2:n + 2].astype(np.int16) - rows[:n].astype(np.int16)).mean(axis=1)
    per_row = d.reshape(-1, HEIGHT).mean(axis=0)
    i = int(np.argmax(per_row[::2] + per_row[1::2]))
    return (2 * i + 2) % HEIGHT


class FrameStream:
    """Reads the bulk stream on a background thread and slices it into frames.

    Frame boundaries are tracked as absolute stream positions congruent to
    `self.offset` modulo FRAME_BYTES, so dropping old data never loses sync.
    """

    CHUNK = 1 << 20
    KEEP_FRAMES = 3

    def __init__(self, cam: MoticCamera):
        self.cam = cam
        self.offset: int | None = None
        self._buf = bytearray()
        self._base = 0  # absolute stream position of _buf[0]
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        chunk = usb.util.create_buffer(self.CHUNK)
        view = memoryview(chunk)
        limit = (self.KEEP_FRAMES + 1) * FRAME_BYTES
        while not self._stop.is_set():
            n = self.cam.read_into(chunk)
            if not n:
                continue
            with self._lock:
                self._buf += view[:n]
                excess = len(self._buf) - limit
                if excess > 0:
                    del self._buf[:excess]
                    self._base += excess

    def calibrate(self) -> bool:
        """Find frame alignment from at least two frames of buffered data."""
        with self._lock:
            if len(self._buf) < 2 * FRAME_BYTES + WIDTH * 4:
                return False
            data = np.frombuffer(bytes(self._buf), dtype=np.uint8)
            base = self._base
        c0 = column_phase(data)
        rows = data[c0:c0 + (data.size - c0) // WIDTH * WIDTH].reshape(-1, WIDTH)
        r0 = row_phase(rows)
        self.offset = (base + c0 + r0 * WIDTH) % FRAME_BYTES
        return True

    def nudge(self, nbytes: int) -> None:
        if self.offset is not None:
            self.offset = (self.offset + nbytes) % FRAME_BYTES

    def latest_frame(self) -> np.ndarray | None:
        """Most recent complete frame as a HEIGHT x WIDTH uint8 array."""
        if self.offset is None:
            return None
        with self._lock:
            end = self._base + len(self._buf)
            k = (end - FRAME_BYTES - self.offset) // FRAME_BYTES
            start = self.offset + k * FRAME_BYTES
            if start < self._base:
                return None
            i = start - self._base
            raw = bytes(self._buf[i:i + FRAME_BYTES])
            del self._buf[:i + FRAME_BYTES]
            self._base = start + FRAME_BYTES
        return np.frombuffer(raw, dtype=np.uint8).reshape(HEIGHT, WIDTH)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
