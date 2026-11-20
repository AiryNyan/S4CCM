from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO, Iterator

COMP_NONE = 0x0000
COMP_ZLIB = 0x5A42
COMP_REFPACK = 0xFFFF
COMP_STREAMABLE = 0xFFFE
COMP_DELETED = 0xFFE0


class DBPFError(Exception):
    pass


@dataclass(frozen=True)
class IndexEntry:
    type: int
    group: int
    instance: int
    offset: int
    file_size: int
    mem_size: int
    compression: int

    @property
    def tgi(self) -> str:
        return f"{self.type:08X}:{self.group:08X}:{self.instance:016X}"


def refpack_decompress(data: bytes) -> bytes:
    if len(data) < 2 or data[1] != 0xFB:
        raise DBPFError("not RefPack data")
    flags = data[0]
    if flags & 0x80:
        size = int.from_bytes(data[2:6], "big")
        pos = 6
    else:
        size = int.from_bytes(data[2:5], "big")
        pos = 5
    if flags & 0x01:
        pos += 4 if flags & 0x80 else 3

    out = bytearray(size)
    o = 0
    n = len(data)
    while pos < n:
        b0 = data[pos]
        if b0 < 0x80:
            b1 = data[pos + 1]
            pos += 2
            plain = b0 & 0x03
            copy = ((b0 & 0x1C) >> 2) + 3
            dist = ((b0 & 0x60) << 3) + b1 + 1
        elif b0 < 0xC0:
            b1, b2 = data[pos + 1], data[pos + 2]
            pos += 3
            plain = (b1 >> 6) & 0x03
            copy = (b0 & 0x3F) + 4
            dist = ((b1 & 0x3F) << 8) + b2 + 1
        elif b0 < 0xE0:
            b1, b2, b3 = data[pos + 1], data[pos + 2], data[pos + 3]
            pos += 4
            plain = b0 & 0x03
            copy = ((b0 & 0x0C) << 6) + b3 + 5
            dist = ((b0 & 0x10) << 12) + (b1 << 8) + b2 + 1
        elif b0 < 0xFC:
            pos += 1
            plain = ((b0 & 0x1F) << 2) + 4
            out[o:o + plain] = data[pos:pos + plain]
            o += plain
            pos += plain
            continue
        else:
            pos += 1
            plain = b0 & 0x03
            out[o:o + plain] = data[pos:pos + plain]
            o += plain
            break

        out[o:o + plain] = data[pos:pos + plain]
        o += plain
        pos += plain
        src = o - dist
        if dist >= copy:
            out[o:o + copy] = out[src:src + copy]
            o += copy
        else:
            for i in range(copy):
                out[o + i] = out[src + i]
            o += copy
    return bytes(out[:o])


class Package:

    def __init__(self, path: str):
        self.path = path
        self._f: BinaryIO = open(path, "rb")
        try:
            self.entries = self._read_index()
        except (struct.error, ValueError, OverflowError) as ex:
            self._f.close()
            raise DBPFError(f"손상된 패키지 (인덱스를 읽을 수 없음): {ex}") from ex
        except Exception:
            self._f.close()
            raise

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "Package":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _read_index(self) -> list[IndexEntry]:
        f = self._f
        header = f.read(96)
        if len(header) < 96 or header[:4] != b"DBPF":
            raise DBPFError("not a DBPF file")
        major, minor = struct.unpack_from("<II", header, 4)
        if major != 2:
            raise DBPFError(f"unsupported DBPF version {major}.{minor}")
        count = struct.unpack_from("<I", header, 36)[0]
        index_pos = struct.unpack_from("<I", header, 64)[0]
        if index_pos == 0:
            index_pos = struct.unpack_from("<I", header, 40)[0]
        if count == 0:
            return []

        f.seek(index_pos)
        flags = struct.unpack("<I", f.read(4))[0]
        const_type = struct.unpack("<I", f.read(4))[0] if flags & 1 else None
        const_group = struct.unpack("<I", f.read(4))[0] if flags & 2 else None
        const_ihi = struct.unpack("<I", f.read(4))[0] if flags & 4 else None

        entries: list[IndexEntry] = []
        for _ in range(count):
            t = const_type if const_type is not None else struct.unpack("<I", f.read(4))[0]
            g = const_group if const_group is not None else struct.unpack("<I", f.read(4))[0]
            ihi = const_ihi if const_ihi is not None else struct.unpack("<I", f.read(4))[0]
            ilo, offset, fsize, msize = struct.unpack("<IIII", f.read(16))
            comp = COMP_NONE
            if fsize & 0x80000000:
                comp, _committed = struct.unpack("<HH", f.read(4))
                fsize &= 0x7FFFFFFF
            entries.append(IndexEntry(t, g, (ihi << 32) | ilo, offset, fsize, msize, comp))
        return entries

    def by_type(self, *types: int) -> Iterator[IndexEntry]:
        ts = set(types)
        return (e for e in self.entries if e.type in ts)

    def read_head(self, entry: IndexEntry, n: int) -> bytes:
        if entry.compression == COMP_NONE:
            self._f.seek(entry.offset)
            return self._f.read(min(n, entry.file_size))
        if entry.compression == COMP_ZLIB:
            self._f.seek(entry.offset)
            raw = self._f.read(min(entry.file_size, 4096))
            return zlib.decompressobj().decompress(raw, n)
        return self.read(entry)[:n]

    def read(self, entry: IndexEntry) -> bytes:
        if entry.compression == COMP_DELETED:
            return b""
        self._f.seek(entry.offset)
        raw = self._f.read(entry.file_size)
        if entry.compression == COMP_NONE:
            return raw
        try:
            if entry.compression == COMP_ZLIB:
                return zlib.decompress(raw)
            if entry.compression in (COMP_REFPACK, COMP_STREAMABLE):
                return refpack_decompress(raw)
        except (zlib.error, IndexError, ValueError) as ex:
            raise DBPFError(f"손상된 리소스 {entry.tgi}: {ex}") from ex
        raise DBPFError(f"unknown compression 0x{entry.compression:04X}")
