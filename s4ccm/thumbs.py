from __future__ import annotations

import io
import os
import threading

from PIL import Image

from .dbpf import Package

THUMB_TYPES = (
    0x3C1AF1F2,
    0x5B282D45,
    0x3C2A8647,
    0xCD9DE247,
    0x2F7D0004,
)
THUMB_SIZE = 256
THUMB_QUALITY = 80
THUMB_BG = (236, 236, 240)
THUMB_EXT = ".jpg"


def _jpeg_with_alfa(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    i = data.find(b"ALFA", 0, 256)
    if i >= 0:
        try:
            alpha = Image.open(io.BytesIO(data[i + 8:]))
            alpha.load()
            alpha = alpha.convert("L")
            if alpha.size != img.size:
                alpha = alpha.resize(img.size)
            img = img.convert("RGB")
            img.putalpha(alpha)
        except Exception:
            pass
    return img


def _decode(data: bytes) -> Image.Image | None:
    try:
        if data[:3] == b"\xff\xd8\xff":
            return _jpeg_with_alfa(data)
        for sig in (b"\x89PNG", b"\xff\xd8\xff"):
            i = data.find(sig, 0, 64)
            if i >= 0:
                if sig == b"\xff\xd8\xff":
                    return _jpeg_with_alfa(data[i:])
                img = Image.open(io.BytesIO(data[i:]))
                img.load()
                return img
    except Exception:
        return None
    return None


def _save(img: Image.Image, out_path: str) -> str:
    img = img.convert("RGBA")
    img.thumbnail((THUMB_SIZE, THUMB_SIZE))
    flat = Image.new("RGB", img.size, THUMB_BG)
    flat.paste(img, mask=img.getchannel("A"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    flat.save(out_path, "JPEG", quality=THUMB_QUALITY, optimize=True)
    return out_path


def convert_to_jpg(path: str) -> str | None:
    out = os.path.splitext(path)[0] + ".jpg"
    try:
        with Image.open(path) as img:
            img.load()
            _save(img, out)
        os.remove(path)
        return out
    except Exception:
        return None


def _pick_best(pk: Package, entries, prefer_instances: list[int]):
    prefer = set(prefer_instances[:1])
    entries = list(entries)
    if not entries:
        return None
    entries.sort(key=lambda e: (e.instance not in prefer, -e.mem_size))
    return entries


def extract_from_package(path: str, out_path: str, prefer_instances: list[int] | None = None) -> str | None:
    try:
        with Package(path) as pk:
            ordered = _pick_best(pk, pk.by_type(*THUMB_TYPES), prefer_instances or [])
            if not ordered:
                return None
            for e in ordered[:6]:
                img = _decode(pk.read(e))
                if img is not None:
                    return _save(img, out_path)
    except Exception:
        return None
    return None


class ThumbCache:

    def __init__(self, path: str | None):
        self.path = path
        self._index: dict[int, object] | None = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        self._index = {}
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with Package(self.path) as pk:
                for e in pk.entries:
                    if e.type in THUMB_TYPES:
                        cur = self._index.get(e.instance)
                        if cur is None or e.mem_size > cur.mem_size:
                            self._index[e.instance] = e
        except Exception:
            self._index = {}

    def extract(self, instances: list[int], out_path: str) -> str | None:
        with self._lock:
            if self._index is None:
                self._load()
            hits = [self._index[i] for i in instances[:50] if i in self._index]
            if not hits:
                return None
            try:
                with Package(self.path) as pk:
                    for e in hits[:3]:
                        img = _decode(pk.read(e))
                        if img is not None:
                            return _save(img, out_path)
            except Exception:
                return None
        return None


def save_user_image(src: str | bytes, out_path: str) -> str | None:
    try:
        img = Image.open(io.BytesIO(src)) if isinstance(src, bytes) else Image.open(src)
        img.load()
        return _save(img, out_path)
    except Exception:
        return None
