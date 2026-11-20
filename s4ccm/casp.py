from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass
class CaspInfo:
    version: int
    name: str
    body_type: int
    body_sub_type: int
    age_gender: int


class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def u8(self) -> int:
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.d, self.p)[0]
        self.p += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def skip(self, n: int) -> None:
        self.p += n

    def be_unicode(self) -> str:
        length = 0
        shift = 0
        while True:
            b = self.u8()
            length |= (b & 0x7F) << shift
            if not b & 0x80:
                break
            shift += 7
        raw = self.d[self.p:self.p + length]
        self.p += length
        return raw.decode("utf-16-be", errors="replace")


def parse_casp(data: bytes) -> CaspInfo | None:
    version = struct.unpack_from("<I", data)[0] if len(data) >= 4 else 0
    extras = [14, 2, 0] if version >= 51 else [2, 0, 14] if version == 50 else [0]
    for extra in extras:
        info = _parse(data, extra)
        if info is not None:
            return info
    return None


def _parse(data: bytes, extra: int) -> CaspInfo | None:
    try:
        r = _Reader(data)
        version = r.u32()
        r.u32()
        r.u32()
        name = r.be_unicode()
        r.skip(4)
        r.u16()
        r.u32()
        r.u32()
        r.u8()
        if version >= 39:
            r.u8()
        r.skip(8)
        if version >= 41:
            r.skip(8)
        r.skip(8 if version > 36 else 4)
        r.skip(extra)
        tag_count = r.u32()
        if tag_count > 2000:
            return None
        r.skip(tag_count * (6 if version >= 37 else 4))
        r.u32()
        r.u32()
        r.u32()
        if version >= 43:
            r.u32()
        r.u8()
        body_type = r.i32()
        body_sub_type = r.i32()
        age_gender = r.u32()
        if not 0 < body_type < 512 or age_gender == 0 or age_gender > 0xFFFFFF:
            return None
        return CaspInfo(version, name, body_type, body_sub_type, age_gender)
    except (struct.error, IndexError):
        return None
