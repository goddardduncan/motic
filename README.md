# motic

Reviving an old Moticam-series USB microscope camera (VID `232F` PID `4002`) on
modern systems. The vendor stopped shipping Mac software years ago and the
Windows build is temperamental, so this project reverse-engineers the USB
protocol from the shipped Windows app and reimplements it in Python with
libusb.

## Layout

- `src/motic_cam/` — the Python library and live viewer (Mac first, portable).
- `tools/` — one-off scripts (bulk dumpers, frame hunters, etc).
- `reversing/` — notes, Ghidra/r2 projects, and the installer payload
  (gitignored).
- `docs/` — protocol documentation as we recover it.
- `scratch/` — ephemeral experiments, gitignored.

## Status

Starting point: a prior probe script that pulled bulk data from endpoint `0x82`
and produced only coloured lines — confirming the device enumerates and
streams, but with unknown framing, init sequence, and pixel format.
