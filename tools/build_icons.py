# coding: utf-8
"""Rebuild all CapsWriter icon assets from the master PNG.

Small icon frames are written as 32bpp DIB (BMP) data, which Windows shell
components (Explorer, taskbar, title bar) render most predictably. Large
128/256 frames use PNG compression to keep file size reasonable.

Optional source overrides:
- assets/source/capswriter_small_master.png: simplified design used for the
  16/20/24/32/40/48 frames instead of downscaling the 1024px master.
- assets/source/capswriter_<size>.png: exact-size dedicated frame; takes
  priority over the small master for that size.
"""

import io
import struct
import sys
from pathlib import Path

from PIL import Image

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.tools.icon_assets import load_ico_frame  # noqa: E402

ASSETS = BASE / "assets"
MASTER = ASSETS / "source" / "capswriter_master.png"
SMALL_MASTER = ASSETS / "source" / "capswriter_small_master.png"
USER_ICO = ASSETS / "source" / "capswriter.ico"
SMALL_SIZES = {16, 20, 24, 32, 40, 48}

APP_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
INSTALLER_SIZES = [16, 24, 32, 48, 64, 128, 256]
PNG_THRESHOLD = 64  # sizes above this use PNG-compressed frames


def _png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _autocrop(image: Image.Image, margin_ratio: float = 0.04) -> Image.Image:
    bbox = image.split()[3].getbbox()
    if not bbox:
        return image
    x0, y0, x1, y1 = bbox
    w, h = image.size
    side = max(x1 - x0, y1 - y0)
    pad = max(2, int(side * margin_ratio))
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(w, x1 + pad)
    y1 = min(h, y1 + pad)
    return image.crop((x0, y0, x1, y1))


def _dib_bytes(image: Image.Image) -> bytes:
    w, h = image.size
    rgba = image.convert("RGBA")
    xor_rows = []
    for y in range(h - 1, -1, -1):
        row = bytearray()
        for r, g, b, a in rgba.crop((0, y, w, y + 1)).getdata():
            row += bytes((b, g, r, a))
        xor_rows.append(bytes(row))
    xor_size = h * w * 4
    and_row_bytes = ((w + 31) // 32) * 4
    and_size = and_row_bytes * h
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        w,
        h * 2,
        1,
        32,
        0,
        xor_size + and_size,
        0,
        0,
        0,
        0,
    )
    and_mask = b"\x00" * and_size
    return header + b"".join(xor_rows) + and_mask


def _ico_bytes(frames: list[tuple[int, Image.Image]]) -> bytes:
    payloads = []
    for size, image in frames:
        if size > PNG_THRESHOLD:
            payloads.append((size, 32, "PNG", _png_bytes(image)))
        else:
            payloads.append((size, 32, "BMP", _dib_bytes(image)))

    header = struct.pack("<HHH", 0, 1, len(payloads))
    entries = b""
    offset = 6 + 16 * len(payloads)
    for size, bpp, _, data in payloads:
        w_byte = 0 if size == 256 else size
        h_byte = 0 if size == 256 else size
        entries += struct.pack(
            "<BBBBHHII", w_byte, h_byte, 0, 0, 1, bpp, len(data), offset
        )
        offset += len(data)
    return header + entries + b"".join(data for _, _, _, data in payloads)


def main() -> None:
    app_ico = ASSETS / "app" / "capswriter.ico"
    installer_ico = ASSETS / "installer" / "capswriter_installer.ico"
    tray_png = ASSETS / "tray" / "capswriter_tray.png"
    ui_png = ASSETS / "ui" / "capswriter_logo.png"

    if USER_ICO.exists():
        # 用户提供多尺寸 ICO 时直接复用，避免自动生成覆盖手工帧
        app_ico.write_bytes(USER_ICO.read_bytes())
        installer_ico.write_bytes(USER_ICO.read_bytes())
        load_ico_frame(USER_ICO, 64).save(tray_png, format="PNG", optimize=True)
        load_ico_frame(USER_ICO, 256).save(ui_png, format="PNG", optimize=True)
        print(f"using user-provided ico: {USER_ICO.name}")
        print(f"app icon:     {app_ico} ({app_ico.stat().st_size} bytes)")
        print(f"installer:    {installer_ico} ({installer_ico.stat().st_size} bytes)")
        print(f"tray png:     {tray_png} ({tray_png.stat().st_size} bytes)")
        print(f"ui logo png:  {ui_png} ({ui_png.stat().st_size} bytes)")
        return

    master = Image.open(MASTER).convert("RGBA")
    if master.size != (1024, 1024):
        print(f"master size is {master.size}, expected 1024x1024")

    def load_scaled(path: Path, size: int) -> Image.Image:
        img = Image.open(path).convert("RGBA")
        img = _autocrop(img)
        if img.size != (size, size):
            img = img.resize((size, size), Image.Resampling.LANCZOS)
        return img

    def scaled(size: int) -> Image.Image:
        override = ASSETS / "source" / f"capswriter_{size}.png"
        if override.exists():
            print(f"using dedicated source: {override.name}")
            return load_scaled(override, size)
        if size in SMALL_SIZES and SMALL_MASTER.exists():
            print(f"using small master for {size}px")
            return load_scaled(SMALL_MASTER, size)
        return master.resize((size, size), Image.Resampling.LANCZOS)

    app_frames = [(size, scaled(size)) for size in APP_SIZES]
    installer_frames = [(size, scaled(size)) for size in INSTALLER_SIZES]

    app_ico.write_bytes(_ico_bytes(app_frames))
    installer_ico.write_bytes(_ico_bytes(installer_frames))
    scaled(64).save(tray_png, format="PNG", optimize=True)
    scaled(256).save(ui_png, format="PNG", optimize=True)

    print(f"app icon:     {app_ico} ({app_ico.stat().st_size} bytes)")
    print(f"installer:    {installer_ico} ({installer_ico.stat().st_size} bytes)")
    print(f"tray png:     {tray_png} ({tray_png.stat().st_size} bytes)")
    print(f"ui logo png:  {ui_png} ({ui_png.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
