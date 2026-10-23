import usb.core
import usb.util
import usb.backend.libusb1 as libusb1
import libusb_package
import numpy as np
import cv2

WIDTH, HEIGHT = 1344, 1291
HEADER = 1024

# Candidate Bayer layouts. Wrong pattern (or an odd-pixel shift in where the frame
# starts) gives a strong colour cast, so we let you cycle through them live.
PATTERNS = [
    ("BG", cv2.COLOR_BayerBG2BGR),
    ("GB", cv2.COLOR_BayerGB2BGR),
    ("RG", cv2.COLOR_BayerRG2BGR),
    ("GR", cv2.COLOR_BayerGR2BGR),
]

backend = libusb1.get_backend(find_library=libusb_package.find_library)
dev = usb.core.find(idVendor=0x232F, idProduct=0x4002, backend=backend)
if not dev:
    print("Camera not found.")
    raise SystemExit

dev.set_configuration()
usb.util.claim_interface(dev, 0)

# NOTE: these two requests are guesses, not taken from the driver. Left in because
# your stream starts with them, but they do NOT set gain/white balance.
try:
    dev.ctrl_transfer(0x40, 0xB0, 0x01, 0x00, 100)
    dev.ctrl_transfer(0x40, 0xB1, 0x00C8, 0x00, 100)
except Exception as e:
    print(f"ctrl_transfer note: {e}")

# --- live-adjustable state -------------------------------------------------
pat_i = 0
bits16 = False           # False: 8-bit pixels, True: 16-bit little-endian pixels
auto_wb = True           # gray-world white balance
gains = np.array([1.0, 1.0, 1.0], np.float32)   # B, G, R (used when auto_wb is off)
black = 0.0              # black level subtracted from raw
gamma = 1.0 / 2.2
shift = 0                # skip N pixels at frame start (fixes odd-offset Bayer phase)

HELP = ("keys: p=next Bayer pattern  x=shift 1px  w=auto WB on/off  "
        "r/R b/B = red/blue gain -/+  k/K = black level -/+  "
        "g/G = gamma  d=8/16-bit  s=save raw  q=quit")
print(HELP)


def grab_frame():
    bpp = 2 if bits16 else 1
    need = HEADER + WIDTH * HEIGHT * bpp + 2          # +2 so shift=1 still fits
    buf = bytearray()
    while len(buf) < need:
        try:
            buf += bytes(dev.read(0x82, 512 * 64, timeout=300))
        except usb.core.USBTimeoutError:
            break
    return buf if len(buf) >= need else None


def process(buf):
    bpp = 2 if bits16 else 1
    start = HEADER + shift * bpp
    n = WIDTH * HEIGHT
    if bits16:
        raw = np.frombuffer(buf, dtype="<u2", count=n, offset=start).astype(np.float32)
        raw /= 65535.0 if raw.max() > 4095 else 4095.0   # 12-bit or full 16-bit
    else:
        raw = np.frombuffer(buf, dtype=np.uint8, count=n, offset=start).astype(np.float32) / 255.0
    raw = raw.reshape(HEIGHT, WIDTH)

    raw = np.clip(raw - black, 0, None)
    hi = np.percentile(raw, 99.5)
    raw = np.clip(raw / max(hi, 1e-6), 0, 1)

    # debayer in 16-bit for precision, then colour-correct in float
    bgr = cv2.cvtColor((raw * 65535).astype(np.uint16), PATTERNS[pat_i][1]).astype(np.float32) / 65535.0

    if auto_wb:
        means = bgr.reshape(-1, 3).mean(axis=0) + 1e-6      # B, G, R
        g = means[1] / means                                 # scale B and R to match G
    else:
        g = gains
    bgr = np.clip(bgr * g, 0, 1) ** gamma
    return (bgr * 255).astype(np.uint8), g


try:
    while True:
        buf = grab_frame()
        if buf is None:
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            continue

        img, g = process(buf)
        view = cv2.resize(img, (WIDTH // 2, HEIGHT // 2), interpolation=cv2.INTER_AREA)
        label = (f"{PATTERNS[pat_i][0]} shift={shift} {'16' if bits16 else '8'}bit "
                 f"WB={'auto' if auto_wb else 'man'} gB/G/R={g[0]:.2f}/{g[1]:.2f}/{g[2]:.2f} "
                 f"black={black:.3f} gamma={gamma:.2f}")
        cv2.putText(view, label, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        cv2.imshow("Moticam Live Feed", view)

        k = cv2.waitKey(1) & 0xFF
        if k == ord('q'):
            break
        elif k == ord('p'):
            pat_i = (pat_i + 1) % len(PATTERNS)
        elif k == ord('x'):
            shift = (shift + 1) % 2
        elif k == ord('w'):
            auto_wb = not auto_wb
            gains = g.copy()
        elif k == ord('d'):
            bits16 = not bits16
        elif k in (ord('r'), ord('R')):
            auto_wb = False; gains[2] *= 0.95 if k == ord('r') else 1.05
        elif k in (ord('b'), ord('B')):
            auto_wb = False; gains[0] *= 0.95 if k == ord('b') else 1.05
        elif k == ord('k'):
            black = max(0.0, black - 0.005)
        elif k == ord('K'):
            black += 0.005
        elif k == ord('g'):
            gamma = max(0.2, gamma - 0.05)
        elif k == ord('G'):
            gamma = min(1.0, gamma + 0.05)
        elif k == ord('s'):
            open("moticam_raw_frame.bin", "wb").write(buf)
            cv2.imwrite("moticam_frame.png", img)
            print("saved moticam_raw_frame.bin and moticam_frame.png")
finally:
    usb.util.release_interface(dev, 0)
    cv2.destroyAllWindows()