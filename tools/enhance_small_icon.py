# coding: utf-8
"""Build a programmatically hardened small-icon master from the 1024px master.

Steps: auto-crop to the opaque content, flatten colors to a small palette,
harden alpha edges, drop tiny noise components, then export a clean master
that survives 16-48px downscaling better.
"""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent.parent
MASTER = BASE / "assets" / "source" / "capswriter_master.png"
OUT = BASE / "assets" / "source" / "capswriter_small_master.png"
PREVIEW = BASE / "logs" / "programmatic_icon_preview.png"

CANVAS = 1024
CONTENT_RATIO = 0.88
PALETTE_K = 4


def normalize(img: np.ndarray) -> np.ndarray:
    alpha = img[..., 3]
    ys, xs = np.where(alpha > 16)
    if len(xs) == 0:
        return img
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    crop = img[y0:y1, x0:x1]
    h, w = crop.shape[:2]
    side = max(h, w)
    scale = (CANVAS * CONTENT_RATIO) / side
    resized = cv2.resize(
        crop,
        (max(1, round(w * scale)), max(1, round(h * scale))),
        interpolation=cv2.INTER_LANCZOS4,
    )
    out = np.zeros((CANVAS, CANVAS, 4), dtype=np.uint8)
    rh, rw = resized.shape[:2]
    x = (CANVAS - rw) // 2
    y = (CANVAS - rh) // 2
    out[y : y + rh, x : x + rw] = resized
    return out


def posterize(img: np.ndarray, k: int) -> np.ndarray:
    alpha = img[..., 3]
    opaque = alpha > 16
    pixels = img[opaque][:, :3].astype(np.float32)
    if len(pixels) < k * 20:
        return img
    criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
        12,
        1.0,
    )
    _, labels, centers = cv2.kmeans(
        pixels, k, None, criteria, 4, cv2.KMEANS_PP_CENTERS
    )
    centers = np.rint(centers).astype(np.uint8)
    brightest = int(np.argmax(centers.sum(axis=1)))
    if centers[brightest].mean() > 210:
        centers[brightest] = (255, 255, 255)
    out = img.copy()
    out[opaque, :3] = centers[labels.ravel()]
    return out


def harden_alpha(img: np.ndarray) -> np.ndarray:
    alpha = img[..., 3].copy()
    alpha = np.where(alpha > 110, 255, 0).astype(np.uint8)
    alpha = cv2.medianBlur(alpha, 3)
    alpha = np.where(alpha > 127, 255, 0).astype(np.uint8)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(alpha, 8)
    keep = np.zeros(n, dtype=bool)
    min_area = max(96, int(alpha.size * 0.0005))
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            keep[i] = True
    if not keep.any():
        keep[1] = True
    clean = np.isin(labels, np.where(keep)[0]).astype(np.uint8) * 255
    out = img.copy()
    out[..., 3] = clean
    return out


def make_preview(old: np.ndarray, new: np.ndarray) -> None:
    sizes = (16, 24, 32, 48)
    scale = 8
    tile = 170
    sheet = Image.new("RGBA", (tile * len(sizes) + 20, tile * 2 + 60), (246, 246, 246, 255))
    draw = ImageDraw.Draw(sheet)
    draw.text((16, 8), "OLD (from capswriter_master.png)", fill=(30, 30, 30))
    draw.text((16, tile + 28), "NEW (programmatic hardened)", fill=(30, 30, 30))

    def to_pil(arr: np.ndarray) -> Image.Image:
        return Image.fromarray(arr, "RGBA")

    def paste_icon(canvas: Image.Image, arr: np.ndarray, xy: tuple, size: int) -> None:
        icon = to_pil(arr).resize((size, size), Image.Resampling.LANCZOS)
        shown = icon.resize((size * scale, size * scale), Image.Resampling.NEAREST)
        white = Image.new("RGBA", (tile - 20, tile - 20), (255, 255, 255, 255))
        white.alpha_composite(
            shown, ((white.width - shown.width) // 2, (white.height - shown.height) // 2)
        )
        canvas.alpha_composite(white, xy)

    for col, size in enumerate(sizes):
        x = 10 + col * tile
        old_icon = cv2.resize(old, (size, size), interpolation=cv2.INTER_LANCZOS4)
        new_icon = cv2.resize(new, (size, size), interpolation=cv2.INTER_LANCZOS4)
        paste_icon(sheet, old_icon, (x, 28), size)
        paste_icon(sheet, new_icon, (x, tile + 48), size)
        draw.text((x + 2, tile * 2 + 30), f"{size}px", fill=(30, 30, 30))

    sheet.convert("RGB").save(PREVIEW)


def main() -> None:
    img = np.asarray(Image.open(MASTER).convert("RGBA"))
    print(f"input: {MASTER.name} {img.shape[1]}x{img.shape[0]}")

    normalized = normalize(img)
    flattened = posterize(normalized, PALETTE_K)
    hardened = harden_alpha(flattened)

    alpha = hardened[..., 3]
    ys, xs = np.where(alpha > 16)
    bbox = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1) if len(xs) else None
    ratio = float((alpha > 16).mean())
    print(f"bbox: {bbox}, opaque ratio: {ratio:.3f}")
    print(f"unique opaque colors: {len(np.unique(hardened[alpha > 16][:, :3].reshape(-1, 3), axis=0))}")

    Image.fromarray(hardened, "RGBA").save(OUT, optimize=True)
    print(f"saved: {OUT}")

    make_preview(normalized, hardened)
    print(f"preview: {PREVIEW}")


if __name__ == "__main__":
    main()
