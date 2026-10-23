"""Probe the Motic USB camera on macOS using libusb.

Based on static reverse-engineering of MUCam32.dll (notes in reversing/).
What we think we know, and are about to verify:

- Camera enumerates as 0634:3222 on this unit; the MUCam32 SDK handles it
  by exactly the same code path as PID 232F:4002 "Motic" variant.
- openCamera issues no vendor control transfers. We open the device, pick
  the bulk-IN pipe, and the camera streams by itself.
- Pipe is auto-discovered: first BULK-IN endpoint on interface 0, alt 0.

What we do NOT know, and are trying to find out:
- Frame width, height, pixel format.
- Whether there is a frame header in the bulk stream (and its layout).
- Nominal frame rate / data rate.

The script does three passes:

  1. identify
     Print device/config/interface/endpoint descriptors, plus the three
     string descriptors (manufacturer, product, serial) — we want to see
     "Motic" in there to confirm the ID path.

  2. drain
     Claim the interface, apply the Windows-side pipe policies as best as
     libusb allows, and bulk-read in a tight loop for a few seconds. Write
     every byte to <scratch>/dump.bin and print bytes-per-second.

  3. analyse
     Scan the dump for repeating byte patterns that would mark frame
     boundaries. Report candidate strides.

Run from the project root:

    .venv/bin/python tools/probe.py            # all three passes
    .venv/bin/python tools/probe.py identify   # just descriptors
    .venv/bin/python tools/probe.py drain
    .venv/bin/python tools/probe.py analyse

The dump path can be overridden with --dump <path>.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import libusb_package
import numpy as np
import usb.backend.libusb1
import usb.core
import usb.util

VID = 0x0634
PID = 0x3222

DEFAULT_DUMP = Path(__file__).resolve().parent.parent / "scratch" / "dump.bin"


def get_backend():
    return usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)


def find_device():
    backend = get_backend()
    dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
    if dev is None:
        sys.exit(f"No device at {VID:04x}:{PID:04x} — is it plugged in?")
    return dev


def try_string(dev, index):
    if not index:
        return None
    try:
        return usb.util.get_string(dev, index)
    except Exception as e:
        return f"<error: {e}>"


def identify():
    dev = find_device()
    print(f"Device {dev.idVendor:04x}:{dev.idProduct:04x}")
    print(f"  bcdUSB         : 0x{dev.bcdUSB:04x}")
    print(f"  bcdDevice      : 0x{dev.bcdDevice:04x}")
    print(f"  Class/Sub/Proto: {dev.bDeviceClass:#x} / {dev.bDeviceSubClass:#x} / {dev.bDeviceProtocol:#x}")
    print(f"  bMaxPacketSize0: {dev.bMaxPacketSize0}")
    print(f"  Manufacturer   : {try_string(dev, dev.iManufacturer)!r}")
    print(f"  Product        : {try_string(dev, dev.iProduct)!r}")
    print(f"  Serial         : {try_string(dev, dev.iSerialNumber)!r}")
    for cfg in dev:
        print(f"  Config {cfg.bConfigurationValue}: {cfg.bNumInterfaces} iface(s), "
              f"self-powered={bool(cfg.bmAttributes & 0x40)}, "
              f"max={cfg.bMaxPower * 2} mA")
        for iface in cfg:
            print(f"    Interface {iface.bInterfaceNumber} alt {iface.bAlternateSetting}: "
                  f"class={iface.bInterfaceClass:#x} sub={iface.bInterfaceSubClass:#x} "
                  f"proto={iface.bInterfaceProtocol:#x} eps={iface.bNumEndpoints}")
            for ep in iface:
                direction = "IN " if ep.bEndpointAddress & 0x80 else "OUT"
                xfer = {0: "CTRL", 1: "ISO ", 2: "BULK", 3: "INT "}[ep.bmAttributes & 0x3]
                print(f"      EP 0x{ep.bEndpointAddress:02x} {direction} {xfer} "
                      f"mps={ep.wMaxPacketSize} interval={ep.bInterval}")


def find_bulk_in(dev):
    cfg = dev.get_active_configuration() if dev.get_active_configuration() is not None else dev[0]
    iface = cfg[(0, 0)]
    for ep in iface:
        if ep.bmAttributes & 0x3 == 0x2 and ep.bEndpointAddress & 0x80:
            return cfg, iface, ep
    sys.exit("No BULK-IN endpoint found on interface 0 alt 0.")


def drain(duration: float, dump_path: Path, chunk: int = 64 * 1024):
    dev = find_device()
    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        # already configured is fine
        if "Resource busy" not in str(e):
            raise
    cfg, iface, ep = find_bulk_in(dev)
    print(f"Reading from EP 0x{ep.bEndpointAddress:02x}, mps={ep.wMaxPacketSize}")
    usb.util.claim_interface(dev, iface.bInterfaceNumber)

    # Clear any lingering stall from a previous run.
    try:
        dev.clear_halt(ep.bEndpointAddress)
    except usb.core.USBError as e:
        print(f"clear_halt: {e}")
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    first_bytes_at = None
    deadline = time.monotonic() + duration
    with dump_path.open("wb") as f:
        while time.monotonic() < deadline:
            try:
                buf = dev.read(ep.bEndpointAddress, chunk, timeout=300)
            except usb.core.USBTimeoutError:
                continue
            except usb.core.USBError as e:
                print(f"read error {e!r}, clear_halt and retry")
                try:
                    dev.clear_halt(ep.bEndpointAddress)
                except usb.core.USBError:
                    pass
                time.sleep(0.05)
                continue
            n = len(buf)
            if n:
                if first_bytes_at is None:
                    first_bytes_at = time.monotonic()
                total += n
                f.write(bytes(buf))
    usb.util.release_interface(dev, iface.bInterfaceNumber)
    elapsed = time.monotonic() - (first_bytes_at or time.monotonic())
    print(f"Captured {total:,} bytes in {elapsed:.2f}s "
          f"({total / max(elapsed, 1e-6) / 1e6:.2f} MB/s) → {dump_path}")


def analyse(dump_path: Path, max_stride: int = 8_000_000, min_stride: int = 1_000):
    if not dump_path.exists():
        sys.exit(f"No dump at {dump_path}; run drain first.")
    data = np.frombuffer(dump_path.read_bytes(), dtype=np.uint8)
    n = data.size
    print(f"Dump {dump_path}: {n:,} bytes")

    # Hunt for repeating structure by autocorrelating on byte values.
    # Downsample first to make this cheap: take max 2 MB signal.
    step = max(1, n // (2_000_000))
    signal = data[::step].astype(np.float32) - 128.0
    print(f"Autocorrelating {signal.size:,} samples (step={step})")

    sig_fft = np.fft.rfft(signal, n=1 << int(np.ceil(np.log2(signal.size * 2))))
    ac = np.fft.irfft(sig_fft * np.conj(sig_fft))[: signal.size]
    ac[0] = 0  # ignore trivial self-match

    # Convert lag indices back to byte strides, filter range.
    lags = np.arange(ac.size) * step
    mask = (lags >= min_stride) & (lags <= max_stride)
    masked = np.where(mask, ac, -np.inf)
    top = np.argpartition(masked, -10)[-10:]
    top = top[np.argsort(-masked[top])]

    print("Top candidate frame strides (byte offsets with strongest periodicity):")
    for idx in top:
        print(f"  stride {lags[idx]:>8,} bytes   score {ac[idx]:>12,.0f}")

    # Also report the first few bytes — a stable preamble is a classic frame marker.
    print("\nFirst 64 bytes of dump:")
    for i in range(0, 64, 16):
        row = data[i:i + 16]
        hexrow = " ".join(f"{b:02x}" for b in row)
        asciirow = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print(f"  {i:04x}  {hexrow:<48}  {asciirow}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", nargs="?", default="all",
                        choices=["all", "identify", "drain", "analyse"])
    parser.add_argument("--dump", type=Path, default=DEFAULT_DUMP)
    parser.add_argument("--seconds", type=float, default=3.0,
                        help="Seconds to bulk-read during drain pass")
    args = parser.parse_args()

    if args.mode in ("all", "identify"):
        identify()
        print()
    if args.mode in ("all", "drain"):
        drain(args.seconds, args.dump)
        print()
    if args.mode in ("all", "analyse"):
        analyse(args.dump)


if __name__ == "__main__":
    main()
