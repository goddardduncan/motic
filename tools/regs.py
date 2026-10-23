"""Read/write Micron sensor registers through the camera's USB bridge.

Protocol recovered from MUCam32.dll (fcn.18001ffb0 / fcn.180020040):

    read : ctrl_transfer(0xC0, 0xF1, 0xBA00 | reg, 0, 2)          -> big-endian u16
    write: ctrl_transfer(0x40, 0xF0, 0xBA00 | reg, 0, [hi, lo])

0xBA is the I2C address of Micron MT9T001-family sensors.

    .venv/bin/python tools/regs.py dump
    .venv/bin/python tools/regs.py read 0x09
    .venv/bin/python tools/regs.py write 0x09 0x0100
"""
from __future__ import annotations

import argparse
import sys

import libusb_package
import usb.backend.libusb1
import usb.core
import usb.util

VID, PID = 0x0634, 0x3222

NAMES = {
    0x00: "chip version",
    0x01: "row start",
    0x02: "column start",
    0x03: "row size (height-1)",
    0x04: "column size (width-1)",
    0x05: "horizontal blank",
    0x06: "vertical blank",
    0x07: "output control",
    0x08: "shutter width upper",
    0x09: "shutter width",
    0x0A: "pixel clock control",
    0x0B: "restart",
    0x0C: "shutter delay",
    0x0D: "reset",
    0x1E: "read mode 1",
    0x20: "read mode 2",
    0x22: "row address mode",
    0x23: "column address mode",
    0x2B: "green1 gain",
    0x2C: "blue gain",
    0x2D: "red gain",
    0x2E: "green2 gain",
    0x35: "global gain",
}


def open_device():
    backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
    dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
    if dev is None:
        sys.exit(f"No device at {VID:04x}:{PID:04x}")
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass
    return dev


def read_reg(dev, reg: int) -> int:
    data = dev.ctrl_transfer(0xC0, 0xF1, 0xBA00 | reg, 0, 2, timeout=500)
    return (data[0] << 8) | data[1]


def write_reg(dev, reg: int, value: int) -> None:
    dev.ctrl_transfer(0x40, 0xF0, 0xBA00 | reg, 0, bytes([value >> 8, value & 0xFF]), timeout=500)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dump")
    r = sub.add_parser("read")
    r.add_argument("reg", type=lambda s: int(s, 0))
    w = sub.add_parser("write")
    w.add_argument("reg", type=lambda s: int(s, 0))
    w.add_argument("value", type=lambda s: int(s, 0))
    args = p.parse_args()

    dev = open_device()
    if args.cmd == "dump":
        for reg, name in NAMES.items():
            try:
                v = read_reg(dev, reg)
                print(f"  0x{reg:02X} {name:24s} = 0x{v:04X} ({v})")
            except usb.core.USBError as e:
                print(f"  0x{reg:02X} {name:24s} : {e}")
    elif args.cmd == "read":
        v = read_reg(dev, args.reg)
        print(f"0x{args.reg:02X} = 0x{v:04X} ({v})")
    else:
        before = read_reg(dev, args.reg)
        write_reg(dev, args.reg, args.value)
        after = read_reg(dev, args.reg)
        print(f"0x{args.reg:02X}: 0x{before:04X} -> wrote 0x{args.value:04X} -> reads 0x{after:04X}")


if __name__ == "__main__":
    main()
