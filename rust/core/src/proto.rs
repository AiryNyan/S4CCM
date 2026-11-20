//! 스키마 없이 읽고 쓰는 최소 protobuf 와이어 포맷.

#[derive(Debug, Clone, PartialEq)]
pub enum Val {
    Varint(u64),
    Fixed64(u64),
    Fixed32(u32),
    Bytes(Vec<u8>),
}

impl Val {
    pub fn as_int(&self) -> Option<u64> {
        match self {
            Val::Varint(v) | Val::Fixed64(v) => Some(*v),
            Val::Fixed32(v) => Some(*v as u64),
            Val::Bytes(_) => None,
        }
    }

    pub fn as_bytes(&self) -> Option<&[u8]> {
        match self {
            Val::Bytes(b) => Some(b),
            _ => None,
        }
    }

    pub fn wire_type(&self) -> u8 {
        match self {
            Val::Varint(_) => 0,
            Val::Fixed64(_) => 1,
            Val::Bytes(_) => 2,
            Val::Fixed32(_) => 5,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Field {
    pub num: u64,
    pub val: Val,
}

impl Field {
    pub fn wt(&self) -> u8 {
        self.val.wire_type()
    }
}

/// 파싱 실패(잘린 데이터, 지원하지 않는 와이어 타입 등).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ProtoError;

pub type PResult<T> = Result<T, ProtoError>;

/// 최대 10바이트까지 허용하는 varint.
pub fn varint(d: &[u8], mut p: usize) -> PResult<(u64, usize)> {
    let mut r: u64 = 0;
    let mut s: u32 = 0;
    loop {
        let b = *d.get(p).ok_or(ProtoError)?;
        p += 1;
        r |= ((b & 0x7F) as u64).checked_shl(s).unwrap_or(0);
        if b < 0x80 {
            return Ok((r, p));
        }
        s += 7;
        if s > 63 {
            return Err(ProtoError);
        }
    }
}

/// 메시지 전체를 필드 목록으로. 잘린 데이터는 오류.
pub fn fields(d: &[u8]) -> PResult<Vec<Field>> {
    let mut p = 0usize;
    let mut out = Vec::new();
    while p < d.len() {
        let (key, np) = varint(d, p)?;
        p = np;
        let (num, wt) = (key >> 3, (key & 7) as u8);
        let val = match wt {
            0 => {
                let (v, np) = varint(d, p)?;
                p = np;
                Val::Varint(v)
            }
            1 => {
                let s = d.get(p..p + 8).ok_or(ProtoError)?;
                p += 8;
                Val::Fixed64(u64::from_le_bytes(s.try_into().unwrap()))
            }
            5 => {
                let s = d.get(p..p + 4).ok_or(ProtoError)?;
                p += 4;
                Val::Fixed32(u32::from_le_bytes(s.try_into().unwrap()))
            }
            2 => {
                let (n, np) = varint(d, p)?;
                p = np;
                let n = usize::try_from(n).map_err(|_| ProtoError)?;
                let end = p.checked_add(n).ok_or(ProtoError)?;
                let s = d.get(p..end).ok_or(ProtoError)?;
                p = end;
                Val::Bytes(s.to_vec())
            }
            _ => return Err(ProtoError),
        };
        out.push(Field { num, val });
    }
    Ok(out)
}

pub fn first<'a>(fields: &'a [Field], num: u64) -> Option<&'a Val> {
    fields.iter().find(|f| f.num == num).map(|f| &f.val)
}

pub fn first_int(fields: &[Field], num: u64) -> u64 {
    first(fields, num).and_then(|v| v.as_int()).unwrap_or(0)
}

pub fn first_bytes<'a>(fields: &'a [Field], num: u64) -> Option<&'a [u8]> {
    first(fields, num).and_then(|v| v.as_bytes())
}

/// `bytes.decode("utf-8", "replace")`; bytes 가 아니면 빈 문자열.
pub fn text(v: Option<&Val>) -> String {
    match v {
        Some(Val::Bytes(b)) => String::from_utf8_lossy(b).into_owned(),
        _ => String::new(),
    }
}

pub fn enc_varint(mut v: u64) -> Vec<u8> {
    let mut out = Vec::with_capacity(10);
    loop {
        let b = (v & 0x7F) as u8;
        v >>= 7;
        if v != 0 {
            out.push(b | 0x80);
        } else {
            out.push(b);
            return out;
        }
    }
}

pub fn encode(fields: &[Field]) -> Vec<u8> {
    let mut out = Vec::new();
    for f in fields {
        out.extend(enc_varint((f.num << 3) | f.wt() as u64));
        match &f.val {
            Val::Varint(v) => out.extend(enc_varint(*v)),
            Val::Fixed64(v) => out.extend(v.to_le_bytes()),
            Val::Fixed32(v) => out.extend(v.to_le_bytes()),
            Val::Bytes(b) => {
                out.extend(enc_varint(b.len() as u64));
                out.extend(b);
            }
        }
    }
    out
}

/// 세이브 파일용: 느슨하고(잘린 bytes 허용) 게으른 필드 반복자.
/// 원본의 제너레이터처럼 앞쪽 필드만 읽고 멈추면 뒤쪽 오류는 드러나지 않는다.
pub struct LazyFields<'a> {
    d: &'a [u8],
    p: usize,
    failed: bool,
}

pub fn lazy_fields(d: &[u8]) -> LazyFields<'_> {
    LazyFields { d, p: 0, failed: false }
}

fn loose_varint(d: &[u8], mut p: usize) -> PResult<(u64, usize)> {
    let mut r: u64 = 0;
    let mut s: u32 = 0;
    loop {
        let b = *d.get(p).ok_or(ProtoError)?;
        p += 1;
        r |= ((b & 0x7F) as u64).checked_shl(s).unwrap_or(0);
        if b < 0x80 {
            return Ok((r, p));
        }
        s += 7;
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum LVal<'a> {
    Int(u64),
    Bytes(&'a [u8]),
}

impl<'a> Iterator for LazyFields<'a> {
    type Item = PResult<(u64, u8, LVal<'a>)>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.failed || self.p >= self.d.len() {
            return None;
        }
        let d = self.d;
        let mut step = || -> PResult<(u64, u8, LVal<'a>)> {
            let (key, np) = loose_varint(d, self.p)?;
            self.p = np;
            let (num, wt) = (key >> 3, (key & 7) as u8);
            let v = match wt {
                0 => {
                    let (v, np) = loose_varint(d, self.p)?;
                    self.p = np;
                    LVal::Int(v)
                }
                1 => {
                    let s = d.get(self.p..self.p + 8).ok_or(ProtoError)?;
                    self.p += 8;
                    LVal::Int(u64::from_le_bytes(s.try_into().unwrap()))
                }
                5 => {
                    let s = d.get(self.p..self.p + 4).ok_or(ProtoError)?;
                    self.p += 4;
                    LVal::Int(u32::from_le_bytes(s.try_into().unwrap()) as u64)
                }
                2 => {
                    let (ln, np) = loose_varint(d, self.p)?;
                    self.p = np;
                    let ln = usize::try_from(ln).unwrap_or(usize::MAX);
                    let start = self.p.min(d.len());
                    let end = self.p.saturating_add(ln).min(d.len());
                    self.p = self.p.saturating_add(ln);
                    LVal::Bytes(&d[start..end])
                }
                _ => return Err(ProtoError),
            };
            Ok((num, wt, v))
        };
        let r = step();
        if r.is_err() {
            self.failed = true;
        }
        Some(r)
    }
}
