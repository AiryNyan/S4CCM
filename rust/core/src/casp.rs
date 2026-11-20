//! CAS 파츠(CASP) 리소스에서 이름·BodyType 읽기.

#[derive(Debug, Clone, PartialEq)]
pub struct CaspInfo {
    pub version: u32,
    pub name: String,
    pub body_type: i32,
    pub body_sub_type: i32,
    pub age_gender: u32,
}

struct Reader<'a> {
    d: &'a [u8],
    p: usize,
}

impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Option<&'a [u8]> {
        let s = self.d.get(self.p..self.p.checked_add(n)?)?;
        self.p += n;
        Some(s)
    }

    fn u8(&mut self) -> Option<u8> {
        self.take(1).map(|s| s[0])
    }

    fn u16(&mut self) -> Option<u16> {
        self.take(2).map(|s| u16::from_le_bytes([s[0], s[1]]))
    }

    fn u32(&mut self) -> Option<u32> {
        self.take(4).map(|s| u32::from_le_bytes([s[0], s[1], s[2], s[3]]))
    }

    fn i32(&mut self) -> Option<i32> {
        self.u32().map(|v| v as i32)
    }

    /// 범위를 벗어나도 실패하지 않는다(다음 읽기에서 실패).
    fn skip(&mut self, n: usize) {
        self.p = self.p.saturating_add(n);
    }

    fn be_unicode(&mut self) -> Option<String> {
        let mut length: u64 = 0;
        let mut shift: u32 = 0;
        loop {
            let b = self.u8()?;
            if shift < 64 {
                length |= ((b & 0x7F) as u64) << shift;
            } else if b & 0x7F != 0 {
                length = u64::MAX;
            }
            if b & 0x80 == 0 {
                break;
            }
            shift += 7;
        }
        let len = usize::try_from(length).unwrap_or(usize::MAX);
        let start = self.p.min(self.d.len());
        let end = self.p.saturating_add(len).min(self.d.len());
        let raw = &self.d[start..end];
        self.p = self.p.saturating_add(len);
        Some(decode_utf16_be(raw))
    }
}

/// `bytes.decode("utf-16-be", errors="replace")`
fn decode_utf16_be(raw: &[u8]) -> String {
    let units = raw.chunks_exact(2).map(|c| u16::from_be_bytes([c[0], c[1]]));
    let mut s: String = char::decode_utf16(units).map(|r| r.unwrap_or('\u{FFFD}')).collect();
    if raw.len() % 2 == 1 {
        s.push('\u{FFFD}');
    }
    s
}

pub fn parse_casp(data: &[u8]) -> Option<CaspInfo> {
    let version = if data.len() >= 4 { u32::from_le_bytes([data[0], data[1], data[2], data[3]]) } else { 0 };
    let extras: &[usize] = if version >= 51 {
        &[14, 2, 0]
    } else if version == 50 {
        &[2, 0, 14]
    } else {
        &[0]
    };
    extras.iter().find_map(|&extra| parse(data, extra))
}

fn parse(data: &[u8], extra: usize) -> Option<CaspInfo> {
    let mut r = Reader { d: data, p: 0 };
    let version = r.u32()?;
    r.u32()?;
    r.u32()?;
    let name = r.be_unicode()?;
    r.skip(4);
    r.u16()?;
    r.u32()?;
    r.u32()?;
    r.u8()?;
    if version >= 39 {
        r.u8()?;
    }
    r.skip(8);
    if version >= 41 {
        r.skip(8);
    }
    r.skip(if version > 36 { 8 } else { 4 });
    r.skip(extra);
    let tag_count = r.u32()?;
    if tag_count > 2000 {
        return None;
    }
    r.skip(tag_count as usize * if version >= 37 { 6 } else { 4 });
    r.u32()?;
    r.u32()?;
    r.u32()?;
    if version >= 43 {
        r.u32()?;
    }
    r.u8()?;
    let body_type = r.i32()?;
    let body_sub_type = r.i32()?;
    let age_gender = r.u32()?;
    if !(0 < body_type && body_type < 512) || age_gender == 0 || age_gender > 0xFF_FFFF {
        return None;
    }
    Some(CaspInfo { version, name, body_type, body_sub_type, age_gender })
}
