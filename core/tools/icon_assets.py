# coding: utf-8
"""Shared helpers for the single multi-size CapsWriter ICO asset."""

import io
import struct
from pathlib import Path

from PIL import Image

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ICON_PATH = BASE_DIR / "assets" / "source" / "capswriter.ico"


def _ico_frame_offsets(ico_path: Path) -> list[tuple[int, int, int]]:
    data = ico_path.read_bytes()
    if data[:4] != b"\x00\x00\x01\x00":
        return []
    count = struct.unpack_from("<H", data, 4)[0]
    frames = []
    for i in range(count):
        w, h, _, _, _, _, size, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + i * 16
        )
        frames.append((w or 256, h or 256, offset, size))
    return frames


def load_ico_frame(ico_path: Path = ICON_PATH, target_size: int = 64) -> Image.Image:
    """Load the ICO frame closest to target_size as an RGBA image."""
    frames = _ico_frame_offsets(ico_path)
    if not frames:
        raise FileNotFoundError(f"not a valid ICO file: {ico_path}")
    best = min(frames, key=lambda f: (abs(f[0] - target_size), abs(f[1] - target_size)))
    data = ico_path.read_bytes()
    raw = data[best[2] : best[2] + best[3]]
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    w, h = best[0], best[1]
    entry = struct.pack("<BBBBHHII", w & 0xFF, h & 0xFF, 0, 0, 1, 32, len(raw), 22)
    single = b"\x00\x00\x01\x00\x01\x00" + entry + raw
    return Image.open(io.BytesIO(single)).convert("RGBA")
