//! 심즈 4 `.package`(DBPF 2.x) 읽기.

use std::fs::File;
use std::io::{BufReader, Read, Seek, SeekFrom};

use flate2::{Decompress, FlushDecompress, Status};

pub const COMP_NONE: u16 = 0x0000;
pub const COMP_ZLIB: u16 = 0x5A42;
pub const COMP_REFPACK: u16 = 0xFFFF;
pub const COMP_STREAMABLE: u16 = 0xFFFE;
pub const COMP_DELETED: u16 = 0xFFE0;

/// 패키지를 읽지 못한 이유. `Io` 는 파일 자체 문제, `Format` 은 내용 문제.
#[derive(Debug, Clone)]
pub enum DbpfError {
    Io(String),
    Format(String),
}

impl std::fmt::Display for DbpfError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DbpfError::Io(s) | DbpfError::Format(s) => f.write_str(s),
        }
    }
}

impl From<std::io::Error> for DbpfError {
    fn from(e: std::io::Error) -> Self {
        DbpfError::Io(e.to_string())
    }
}

impl From<DbpfError> for crate::error::Error {
    fn from(e: DbpfError) -> Self {
        crate::error::Error(e.to_string())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct IndexEntry {
    pub type_: u32,
    pub group: u32,
    pub instance: u64,
    pub offset: u32,
    pub file_size: u32,
    pub mem_size: u32,
    pub compression: u16,
}

impl IndexEntry {
    pub fn tgi(&self) -> String {
        format!("{:08X}:{:08X}:{:016X}", self.type_, self.group, self.instance)
    }
}

fn fmt_err(s: impl Into<String>) -> DbpfError {
    DbpfError::Format(s.into())
}

pub fn refpack_decompress(data: &[u8]) -> Result<Vec<u8>, DbpfError> {
    if data.len() < 2 || data[1] != 0xFB {
        return Err(fmt_err("not RefPack data"));
    }
    let oob = || fmt_err("index out of range");
    let be = |s: &[u8]| s.iter().fold(0usize, |a, &b| (a << 8) | b as usize);
    let flags = data[0];
    let (size, mut pos) = if flags & 0x80 != 0 {
        (be(&data[2..data.len().min(6)]), 6usize)
    } else {
        (be(&data[2..data.len().min(5)]), 5usize)
    };
    if flags & 0x01 != 0 {
        pos += if flags & 0x80 != 0 { 4 } else { 3 };
    }

    let mut out = vec![0u8; size];
    let mut o = 0usize;
    let n = data.len();
    let get = |i: usize| data.get(i).copied().ok_or_else(oob);
    // out[o..o+plain] = data[pos..pos+plain]
    let put_plain = |out: &mut Vec<u8>, o: &mut usize, pos: usize, plain: usize| -> Result<(), DbpfError> {
        let src = data.get(pos..pos + plain).ok_or_else(oob)?;
        let dst = out.get_mut(*o..*o + plain).ok_or_else(oob)?;
        dst.copy_from_slice(src);
        *o += plain;
        Ok(())
    };
    while pos < n {
        let b0 = data[pos] as usize;
        let (plain, copy, dist);
        if b0 < 0x80 {
            let b1 = get(pos + 1)? as usize;
            pos += 2;
            plain = b0 & 0x03;
            copy = ((b0 & 0x1C) >> 2) + 3;
            dist = ((b0 & 0x60) << 3) + b1 + 1;
        } else if b0 < 0xC0 {
            let (b1, b2) = (get(pos + 1)? as usize, get(pos + 2)? as usize);
            pos += 3;
            plain = (b1 >> 6) & 0x03;
            copy = (b0 & 0x3F) + 4;
            dist = ((b1 & 0x3F) << 8) + b2 + 1;
        } else if b0 < 0xE0 {
            let (b1, b2, b3) = (get(pos + 1)? as usize, get(pos + 2)? as usize, get(pos + 3)? as usize);
            pos += 4;
            plain = b0 & 0x03;
            copy = ((b0 & 0x0C) << 6) + b3 + 5;
            dist = ((b0 & 0x10) << 12) + (b1 << 8) + b2 + 1;
        } else if b0 < 0xFC {
            pos += 1;
            let plain = ((b0 & 0x1F) << 2) + 4;
            put_plain(&mut out, &mut o, pos, plain)?;
            pos += plain;
            continue;
        } else {
            pos += 1;
            let plain = b0 & 0x03;
            put_plain(&mut out, &mut o, pos, plain)?;
            break;
        }

        put_plain(&mut out, &mut o, pos, plain)?;
        pos += plain;
        if dist > o || o + copy > out.len() {
            return Err(oob());
        }
        let src = o - dist;
        if dist >= copy {
            out.copy_within(src..src + copy, o);
        } else {
            for i in 0..copy {
                out[o + i] = out[src + i];
            }
        }
        o += copy;
    }
    out.truncate(o);
    Ok(out)
}

pub struct Package {
    pub path: String,
    file: File,
    pub entries: Vec<IndexEntry>,
}

/// Python `struct.unpack` 이 짧은 버퍼에서 내는 메시지와 같은 모양으로 만든다.
struct IndexReader<R: Read> {
    r: R,
}

impl<R: Read> IndexReader<R> {
    fn bytes<const N: usize>(&mut self) -> Result<[u8; N], DbpfError> {
        let mut buf = [0u8; N];
        let mut got = 0;
        while got < N {
            match self.r.read(&mut buf[got..]) {
                Ok(0) => break,
                Ok(k) => got += k,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
                Err(e) => return Err(DbpfError::Io(e.to_string())),
            }
        }
        if got < N {
            return Err(fmt_err(format!(
                "손상된 패키지 (인덱스를 읽을 수 없음): unpack requires a buffer of {N} bytes"
            )));
        }
        Ok(buf)
    }

    fn u32(&mut self) -> Result<u32, DbpfError> {
        Ok(u32::from_le_bytes(self.bytes::<4>()?))
    }
}

fn le32(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

impl Package {
    pub fn open(path: &str) -> Result<Package, DbpfError> {
        let mut file = File::open(path).map_err(|e| DbpfError::Io(e.to_string()))?;
        let entries = Self::read_index(&mut file)?;
        Ok(Package { path: path.to_string(), file, entries })
    }

    fn read_index(f: &mut File) -> Result<Vec<IndexEntry>, DbpfError> {
        let mut header = [0u8; 96];
        let mut got = 0;
        while got < 96 {
            let k = f.read(&mut header[got..])?;
            if k == 0 {
                break;
            }
            got += k;
        }
        if got < 96 || &header[..4] != b"DBPF" {
            return Err(fmt_err("not a DBPF file"));
        }
        let (major, minor) = (le32(&header, 4), le32(&header, 8));
        if major != 2 {
            return Err(fmt_err(format!("unsupported DBPF version {major}.{minor}")));
        }
        let count = le32(&header, 36);
        let mut index_pos = le32(&header, 64);
        if index_pos == 0 {
            index_pos = le32(&header, 40);
        }
        if count == 0 {
            return Ok(Vec::new());
        }

        f.seek(SeekFrom::Start(index_pos as u64))?;
        let mut r = IndexReader { r: BufReader::with_capacity(1 << 16, &mut *f) };
        let flags = r.u32()?;
        let const_type = if flags & 1 != 0 { Some(r.u32()?) } else { None };
        let const_group = if flags & 2 != 0 { Some(r.u32()?) } else { None };
        let const_ihi = if flags & 4 != 0 { Some(r.u32()?) } else { None };

        let mut entries = Vec::with_capacity((count as usize).min(1 << 20));
        for _ in 0..count {
            let t = match const_type {
                Some(v) => v,
                None => r.u32()?,
            };
            let g = match const_group {
                Some(v) => v,
                None => r.u32()?,
            };
            let ihi = match const_ihi {
                Some(v) => v,
                None => r.u32()?,
            };
            let b = r.bytes::<16>()?;
            let (ilo, offset, mut fsize, msize) = (le32(&b, 0), le32(&b, 4), le32(&b, 8), le32(&b, 12));
            let mut comp = COMP_NONE;
            if fsize & 0x8000_0000 != 0 {
                let c = r.bytes::<4>()?;
                comp = u16::from_le_bytes([c[0], c[1]]);
                fsize &= 0x7FFF_FFFF;
            }
            entries.push(IndexEntry {
                type_: t,
                group: g,
                instance: ((ihi as u64) << 32) | ilo as u64,
                offset,
                file_size: fsize,
                mem_size: msize,
                compression: comp,
            });
        }
        Ok(entries)
    }

    pub fn by_type<'a>(&'a self, types: &'a [u32]) -> impl Iterator<Item = &'a IndexEntry> + 'a {
        self.entries.iter().filter(move |e| types.contains(&e.type_))
    }

    fn read_raw(&mut self, offset: u32, len: usize) -> Result<Vec<u8>, DbpfError> {
        self.file.seek(SeekFrom::Start(offset as u64))?;
        let mut buf = Vec::with_capacity(len.min(1 << 26));
        (&mut self.file).take(len as u64).read_to_end(&mut buf)?;
        Ok(buf)
    }

    /// 리소스 앞부분 `n` 바이트만 (텍스처 크기 확인용).
    pub fn read_head(&mut self, entry: &IndexEntry, n: usize) -> Result<Vec<u8>, DbpfError> {
        if entry.compression == COMP_NONE {
            return self.read_raw(entry.offset, n.min(entry.file_size as usize));
        }
        if entry.compression == COMP_ZLIB {
            let raw = self.read_raw(entry.offset, (entry.file_size as usize).min(4096))?;
            let mut d = Decompress::new(true);
            let mut out = vec![0u8; n];
            let mut produced = 0usize;
            loop {
                let before_in = d.total_in();
                let before_out = d.total_out();
                let status = d
                    .decompress(&raw[d.total_in() as usize..], &mut out[produced..], FlushDecompress::None)
                    .map_err(|e| fmt_err(format!("zlib: {e}")))?;
                produced += (d.total_out() - before_out) as usize;
                if produced >= n || matches!(status, Status::StreamEnd) {
                    break;
                }
                if d.total_in() == before_in && d.total_out() == before_out {
                    break;
                }
            }
            out.truncate(produced);
            return Ok(out);
        }
        let mut all = self.read(entry)?;
        all.truncate(n);
        Ok(all)
    }

    pub fn read(&mut self, entry: &IndexEntry) -> Result<Vec<u8>, DbpfError> {
        if entry.compression == COMP_DELETED {
            return Ok(Vec::new());
        }
        let raw = self.read_raw(entry.offset, entry.file_size as usize)?;
        if entry.compression == COMP_NONE {
            return Ok(raw);
        }
        if entry.compression == COMP_ZLIB {
            let mut out = Vec::with_capacity((entry.mem_size as usize).min(1 << 28));
            let mut d = flate2::read::ZlibDecoder::new(&raw[..]);
            return match d.read_to_end(&mut out) {
                Ok(_) => Ok(out),
                Err(e) => Err(fmt_err(format!("손상된 리소스 {}: {e}", entry.tgi()))),
            };
        }
        if entry.compression == COMP_REFPACK || entry.compression == COMP_STREAMABLE {
            return refpack_decompress(&raw).map_err(|e| match e {
                DbpfError::Format(s) if s == "not RefPack data" => DbpfError::Format(s),
                other => fmt_err(format!("손상된 리소스 {}: {other}", entry.tgi())),
            });
        }
        Err(fmt_err(format!("unknown compression 0x{:04X}", entry.compression)))
    }
}
