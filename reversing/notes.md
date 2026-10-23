# Reversing notes

## Device identity

- USB VID `0x232F`, PID `0x4002`.
- INF `MoticUSBCamera.inf` just binds WinUSB — no kernel protocol logic.
- Siblings on the same INF: PID `4001`, `4003`, `4004`, `4005` (same family,
  likely different sensors); `3521`, `3001`, `3002`, `0100`, `0101`, `1000`,
  `3002` share the vendor driver but are other product lines.
- Installer: Motic Images Plus 3.0 (`motic image pro 3`, 2015/2016 build).

## Known-wrong assumptions from the prior probe

- `WIDTH=1344, HEIGHT=1291` — probably noise.
- `HEADER=1024` — guessed.
- Control transfers `bRequest=0xB0/0xB1` — self-admitted guesses.
- Result: coloured lines, i.e. frame sync is off entirely.

## The real SDK: `MUCam32.dll`

User-space SDK that implements the camera protocol on top of plain WinUSB.
PE32+ x86-64, 281 KB, exports 45 `MUCam_*` C functions:

```
MUCam_findCamera / openCamera / closeCamera / releaseCamera / isConnected
MUCam_getType / getIdentifier / getFrame / getFrameFormat
MUCam_setBitCount / setROI / setFlip / setMirror / setTriggerType
MUCam_setExposure / getExposureRange / setAuto
MUCam_setRGBGainIndex / setRGBGainValue / setRGBOffset / getGainCount / getGainList
MUCam_setBrightness / setContrast / setSaturation / setSharpness / setHue
MUCam_getBrightnessRange / getContrastRange / getSaturationRange / getSharpnessRange / getHueRange
MUCam_setBinningIndex / getBinningCount / getBinningList / getBinningType
MUCam_setFrequencyIndex / getFrequencyIndex / getFrequencyCount
MUCam_getOffsetRange / getTemperature / activateCooler / isCoolerAvailable
MUCam_testCameras
```

USB imports (the entire I/O surface we must replicate):
`WinUsb_Initialize / Free / ControlTransfer / ReadPipe / WritePipe /
GetOverlappedResult / SetPipePolicy / QueryInterfaceSettings / QueryPipe /
QueryDeviceInformation / GetDescriptor`.

Also pulls in `SetupDi*` from SETUPAPI (device enumeration only — not needed
on libusb) and `gdiplus.dll` for in-SDK JPEG decoding of still captures.

The companion DLL `devices/libdevMUCam.dll` is just the plugin shim loaded by
`MoticImageDevices.exe`; it only imports from KERNEL32 and exports a single
`QueryModule` entry. All real logic lives in `MUCam32.dll`.

## Device dispatch

`MUCam_findCamera` (`0x180020780`) walks a 5-entry table of C++ vtables at
`0x180041c30`; vtable[0] of each is a `tryMatch(vid, pid, winusbHandle, ...)`
factory that returns a device object or NULL. The first vtable
(`0x180041c58`) is for every Moticam camera on this driver:

```
0x18000bd4b   cmp dx, 0x232f           ; VID gate
0x18000bd58   cmp ecx, 0x4001          ; PID switch
0x18000bd5e   jg  0x18000bf97          ;   > 0x4001 (ours, 4002..4005)
0x18000bd64   je  0x18000bf25          ;   = 0x4001
0x18000bd70   sub+je/dec+je/sub+je ... ;   0x3001 / 0x3002 / 0x3521 branches
```

For PID 0x4002 the dispatcher is `0x18000c0d9`:

```
0x18000c0d9   mov rcx, r9              ; winusbHandle
0x18000c0dc   call 0x18000bbe0         ; probe A — reads a sensor ID over USB
0x18000c0e1   test al, al
0x18000c0e3   je   0x18000c110         ; probe A failed → try probe B
0x18000c0e5   mov ecx, 0xc8            ; 200-byte object
0x18000c0ea   call alloc
0x18000c103   call 0x180007d30         ; constructor A → vtable [0x180036f40]
0x18000c110   call 0x18000bc80         ; probe B
0x18000c13e   call 0x18000b080         ; constructor B → vtable [0x180034c08]
```

So the Moticam 2 (PID 0x4002) is one of two sensor variants, selected at
runtime by a USB handshake. The probe functions `0x18000bbe0` and `0x18000bc80`
contain the first real reads we need to understand.

## Open questions (reversing to-do)

1. Probe A (`0x18000bbe0`) and Probe B (`0x18000bc80`) — what control
   transfers do they issue, what byte sequence do they look for in the reply?
2. Constructor A (`0x180007d30`) and B (`0x18000b080`) — what state do they
   write into the 200-byte object? Where does the frame dimension come from?
3. Vtables `0x180036f40` and `0x180034c08` — list the method order and find
   which slot is `setExposure`, `setROI`, `getFrame`.
4. `getFrame`'s inner path — the bulk read pipe id, the header layout, the
   pixel format. The exposure LUT in `devices/usb2exps.ref` (1032 entries of
   4-byte LE, monotonically decreasing from 263) likely maps slider index →
   sensor register, which also tells us the register's physical units.
5. `MUCam_setBitCount` — the only clue to pixel bit depth without touching
   `getFrameFormat`.
