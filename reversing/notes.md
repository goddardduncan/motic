# Reversing notes

All addresses are from `MUCam32.dll` as extracted from Motic Images Plus 3
(2016 build). File is PE32+ x86-64, 281 KB.

## Device identity

- USB VID `0x232F`, PID `0x4002`.
- INF `MoticUSBCamera.inf` binds plain WinUSB — no kernel protocol logic.
- Sibling PIDs on the same INF: `4001`, `4003`, `4004`, `4005` (same family,
  likely different sensors); `3521`, `3001`, `3002`, `0100`, `0101`, `1000`
  share the WinUSB driver but are other product lines.

## Known-wrong assumptions from `tools/grab_initial.py`

- `WIDTH=1344, HEIGHT=1291` — pulled out of thin air.
- `HEADER=1024` — pulled out of thin air.
- Control transfers `bRequest=0xB0/0xB1` — self-admitted guesses.

## MUCam32.dll architecture

Public C API, 45 `MUCam_*` exports:

```
MUCam_findCamera / openCamera / closeCamera / releaseCamera / isConnected / testCameras
MUCam_getType / getIdentifier / getFrame / getFrameFormat
MUCam_setBitCount / setROI / setFlip / setMirror / setTriggerType
MUCam_setExposure / getExposureRange / setAuto
MUCam_setRGBGainIndex / setRGBGainValue / setRGBOffset / getGainCount / getGainList
MUCam_setBrightness / setContrast / setSaturation / setSharpness / setHue
MUCam_getBrightnessRange / getContrastRange / getSaturationRange / getSharpnessRange / getHueRange
MUCam_setBinningIndex / getBinningCount / getBinningList / getBinningType
MUCam_setFrequencyIndex / getFrequencyIndex / getFrequencyCount
MUCam_getOffsetRange / getTemperature / activateCooler / isCoolerAvailable
```

WinUSB imports (the complete I/O surface we must mirror):
`WinUsb_Initialize / Free / ControlTransfer / ReadPipe / WritePipe /
GetOverlappedResult / SetPipePolicy / QueryInterfaceSettings / QueryPipe /
QueryDeviceInformation / GetDescriptor`. SETUPAPI is used only for Windows
device enumeration (irrelevant to libusb). GDI+ is used only for in-SDK JPEG
decoding of still captures.

`devices/libdevMUCam.dll` is a 148 KB plugin shim loaded by
`MoticImageDevices.exe` — it only imports from KERNEL32 and exports a single
`QueryModule` entry. All real logic lives in `MUCam32.dll`.

## Device-type dispatch

`MUCam_findCamera` (`0x180020780`) iterates a 5-entry array of C++ class
pointers at `0x180041c30`. For each, it calls vfunc 0 with the device's VID,
PID, identifier wstring (read from USB string descriptors by
`fcn.180020410`), and the WinUSB handle. The first class matches every
Moticam camera on this INF; the dispatcher at `0x18000bd20`:

```
cmp dx, 0x232f           ; VID gate
cmp ecx, 0x4001          ; PID switch
jg  0x18000bf97          ;   > 0x4001   (4002..4005 branch, see below)
je  0x18000bf25          ;   == 0x4001
sub ecx, 0x3001 / dec / sub 0x51f / dec / ... ; 3001 / 3002 / 3521 / 3522
```

For PID 0x4002, the dispatcher at `0x18000c0d9` tries two sensor variants in
turn, selected by searching the device's identifier wstring:

| Probe (`std::wstring::find`) | Needle | Constructor | Variant vtable |
| --- | --- | --- | --- |
| `fcn.18000bbe0` | `"Motic"` | `fcn.180007d30` | `0x180033df8` |
| `fcn.18000bc80` | `"Swift"` | `fcn.18000b080` | — |

Both constructors stamp a 200-byte C++ object. The identifier wstring is
built from USB string descriptors (manufacturer / product / serial) during
findCamera. A Motic-branded Moticam 2 goes down the first branch.

## Variant A (PID 0x4002, "Motic") vtable

Vtable base `0x180033df8`. Slot addresses map public API → implementation
via the thunks in MUCam_* exports. Each MUCam_* function is a short
`jmp qword [this + N]`:

| Public API | this vtable offset | Implementation (variant A) |
| --- | --- | --- |
| openCamera          | +0x10 | `0x18000a300` |
| getFrame            | +0x20 | `0x18001d840` |
| setBitCount         | +0x28 | `0x18000a3c0` (stub, returns 0) |
| setROI              | +0x50 | `0x180015530` |
| setRGBGainValue     | +0x78 | `0x18000a410` |
| setExposure         | +0x98 | `0x180015590` |
| getFrameFormat      | +0xc8 | `0x18000a5e0` (returns `[this+0x34]`) |
| setAuto             | +0xe0 | `0x18000a3c0` (stub) |

A great many slots point to `0x18000a3c0`, which is a no-op stub returning
zero — this camera doesn't expose brightness/contrast/binning/cooler/etc.

## Object layout (variant A, inferred from accessors)

| offset | field |
| --- | --- |
| +0x00 | vfptr → `0x180033df8` |
| +0x08 | base-class vfptr → `0x180033fa8` or similar |
| +0x10 | per-frame alloc pointer (populated by `fcn.180021180` on open) |
| +0x20 | WinUSB interface handle |
| +0x28 | discovered bulk-IN pipe id (byte) |
| +0x29 | isOpen flag (byte) |
| +0x34 | frame format enum (0..9, remapped by `MUCam_getFrameFormat`) |
| +0x38 | frame width  (dword) |
| +0x3c | frame height (dword) |
| +0x48 | frame-receive queue + critical section |
| +0x58 | mode / binning index (set to 1 in ctor) |
| +0x5c | pending-frame flag |
| +0x60 | critical section (16 bytes) |
| +0x65 | ROI/crop enabled flag |
| +0x68 | crop width / +0x6c crop height / +0x70 crop offset |
| +0x88 | output image bitmap |
| +0x98 | internal raw frame buffer |
| +0xb0 | stride |
| +0xb8 | RGB gain buffer |
| +0xc0 | gain count |
| +0xc4 | exposure (float, defaulted to `0x46bb8000` = 6000.0, probably µs or 100 ns ticks) |

## Open path (what runs when the camera is initialised)

```
MUCam_openCamera(this)                                   ; thunk → this.vfunc[0x10]
  → fcn.18000a300(this)                                  ; variant A opener
    if !isOpen:
      fcn.180020b60(this.winusbHandle*, this.pipeIdOut*) ; real device open
        fcn.180020aa0(handle, type=BULK(2), dir=IN(0x80), outPipeId)
          WinUsb_QueryInterfaceSettings(handle, 0, &iface)
          for ep in 0..iface.bNumEndpoints:
            WinUsb_QueryPipe(handle, 0, ep, &pipe)
            if pipe.PipeType==BULK and pipe.PipeID&0x80==IN:
              *outPipeId = pipe.PipeID; return true
        WinUsb_SetPipePolicy(handle, pipeId, AUTO_CLEAR_STALL=2, 1, 1)
        WinUsb_SetPipePolicy(handle, pipeId, RAW_IO=7,          1, 1)
      fcn.180021180(this)                                ; allocate frame buffers
```

Notable: `openCamera` issues **no vendor control transfers**. The camera
starts streaming the moment WinUSB has the pipe set up and the host begins
reading. That matches the prior probe getting bulk data without any special
command. The `0xB0 / 0xB1` control transfers in the prior script were
unnecessary.

## Frame geometry — still unknown

`getFrame` reads width/height from `[this+0x38]/[this+0x3c]`, but openCamera
doesn't set them. They must be populated either by the base ctor
`fcn.180015240` (from the parameter table at `0x180036f40`), or by a
background receive thread parsing the bulk stream header. Still to trace.

## Exposure LUT (`devices/usb2exps.ref`)

1032 entries × 4-byte little-endian ints, monotonically decreasing from
`0x107 = 263`. Shape of a time → register map. Each slider index selects
one value to write to a sensor register via `WinUsb_ControlTransfer`.
Not yet linked into the setExposure code path.

## Reversing to-do

1. Trace `fcn.180015240` (base ctor) to see if the parameter table at
   `0x180036f40` contains default width/height.
2. Find the bulk-receive thread (spawned by `fcn.180021180`?). This is
   where frame headers are parsed — resolves the width/height/pixel-format
   question.
3. Walk every `WinUsb_ControlTransfer` call site to list the full
   command set (bRequest / wValue / wIndex / wLength per call site).
4. Decode `setExposure` (`0x180015590`) — ties the usb2exps.ref LUT to
   actual register writes.
5. Decode `setROI` and `setRGBGainValue` — these are the only non-stub
   setters exposed to the user.

## Confirmed on hardware (0634:3222, class MUCamMC3222)

Supersedes the "2048x1200 16-bit" guess in an earlier commit — that was wrong.

- RTTI: vtable `0x180033df8` is `MUCamMC3222`; its initializer is
  `MUCamMC3001Initializer` (`0x180036f40`), which allocates a `0x300000`
  (2048 x 1536 x 1 byte) frame buffer and builds MT9T001 gain tables.
- Sensor register access (fcn.18001ffb0 / fcn.180020040):
  - read : `ctrl_transfer(0xC0, 0xF1, 0xBA00|reg, 0, 2)` -> big-endian u16
  - write: `ctrl_transfer(0x40, 0xF0, 0xBA00|reg, 0, [hi, lo])`
- Chip version reg 0x00 = `0x1621` -> **Micron MT9T001**, window 2048 x 1536
  (regs 0x03/0x04), row start 20, col start 32.
- Stream: back-to-back **8-bit Bayer** frames of 3 MiB, no headers, no
  short-packet frame markers. ~0.4 MB/s at power-on clocking (~8 s/frame).
- Exposure = reg 0x09 shutter width in rows. Default 1561 saturates under
  room light; 10 gives a mid-grey image. Row time ~5 ms at default clocks.
- The 0xB0/0xB1 control transfers from grab.py STALL on this unit and are
  not needed.

## Bridge clock command (the missing init step)

`setFrequencyIndex` (vtable +0xA0, fcn.18001d5c0) sends
`ctrl_transfer(0x40, 0xF6, wValue=index, 0, none)`, index 0/1/2, and stores
a row-time constant 48000/24000/12000 (≈ 48/24/12 MHz). The constructor sets
index 1 in the object but never sends it; power-on default is far slower.

Measured on this unit:

| clock | throughput | full-frame rate |
| --- | --- | --- |
| power-on default | 0.4 MB/s | ~0.13 fps |
| 0xF6 idx 1 | 6.4 MB/s | ~2 fps |
| 0xF6 idx 0 | 12.6 MB/s | ~4 fps |

At idx 0, shutter 1000 rows ≈ mid-grey under room light (no optics).
Still unresolved: every row has an identical horizontal profile
(row-mean std < 1) in all scenes tried so far — need an optical image to
tell whether that's the scene or a readout issue.
