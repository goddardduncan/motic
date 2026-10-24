# Moticam Viewer (unofficial)

A driver and live viewer for an old Motic USB microscope camera, written by
reverse-engineering the vendor's Windows SDK for interoperability. It runs on
macOS, should run on Windows and Linux (anything libusb supports), and needs
no Motic software.

> **Unofficial.** This project is not affiliated with, endorsed by, or
> supported by Motic. "Motic" and "Moticam" are used only to say which
> hardware this works with. No Motic code, drivers or binaries are included.

Supported camera: USB ID `0634:3222` ("Motic USB2.0"), Micron MT9T001
sensor, 2048 × 1536. The SDK treats `232F:4002` as the same camera type,
so that ID is accepted too.

## Download

Prebuilt apps come from the **build apps** workflow (Actions tab →
latest run → Artifacts): Windows, Apple Silicon Mac and Intel Mac. Each zip
has a "READ ME FIRST" covering first-launch warnings and, on Windows, the
one-time WinUSB driver step.

## Run from source

```
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/moticam-viewer
```

Press `;` in the window for the key map: display modes, exposure, gain,
white balance, sensor window and skip modes, plus `space` to save a PNG,
the raw Bayer frame and a JSON dump of every setting.

## How the camera works

Recovered from `MUCam32.dll` (details in [reversing/notes.md](reversing/notes.md)):

- Sensor registers: `ctrl_transfer(0x40, 0xF0, 0xBA00|reg, 0, [hi, lo])`
  writes, `ctrl_transfer(0xC0, 0xF1, 0xBA00|reg, 0, 2)` reads. `0xBA` is
  the sensor's I²C address.
- Clock: `ctrl_transfer(0x40, 0xF6, index, 0)` with 0/1/2 = 48/24/12 MHz.
  The power-on clock is very slow.
- Frames: bulk endpoint `0x82` sends one 8-bit Bayer frame per transfer,
  ended by a short packet. Read requests must be larger than a frame:
  while no read is pending, the camera discards the rest of the frame.
- Exposure: register `0x09` (shutter width in rows); gain: register `0x35`.

At 48 MHz the camera delivers full-resolution frames at about 12 fps.

## Layout

- `src/motic_cam/`: driver (`camera.py`) and viewer (`viewer.py`)
- `packaging/`: app build scripts for macOS and Windows
- `tools/`: the probes used during reverse engineering
- `reversing/`: notes on the Windows SDK internals

## Licence

MIT, see [LICENSE](LICENSE).
