//! 포즈 팩 아이콘을 썸네일로 쓰기 (Python 판에는 없음).
//!
//! 포즈 팩은 포즈마다 64×64 아이콘을 DDS 이미지 리소스로 넣어 두는데, 대개 EA 가 블록 순서를
//! 섞어 저장한 DST1/DST5 형식이다. 이것을 풀어서 읽는다.

use image::{DynamicImage, Rgba, RgbaImage};

use crate::dbpf::{IndexEntry, Package};
use crate::thumbs;

/// DDS 이미지 리소스.
pub const T_DDS_IMAGE: u32 = 0x00B2D882;
/// 애니메이션 클립. 이것이 든 패키지에서만 DDS 이미지를 썸네일로 쓴다 (CAS 의 DDS 는 옷감 텍스처라서).
pub const T_CLIP: u32 = 0x6B20C4F3;

fn rgb565(c: u16) -> [u8; 3] {
    let r = ((c >> 11) & 31) as u32;
    let g = ((c >> 5) & 63) as u32;
    let b = (c & 31) as u32;
    [((r * 255 + 15) / 31) as u8, ((g * 255 + 31) / 63) as u8, ((b * 255 + 15) / 31) as u8]
}

/// DXT 색 블록(8바이트) → 16 픽셀 RGBA.
fn color_block(b: &[u8], dxt1: bool) -> [[u8; 4]; 16] {
    let c0 = u16::from_le_bytes([b[0], b[1]]);
    let c1 = u16::from_le_bytes([b[2], b[3]]);
    let (p0, p1) = (rgb565(c0), rgb565(c1));
    let mix = |x: u8, y: u8, wx: u32, wy: u32, d: u32| ((x as u32 * wx + y as u32 * wy) / d) as u8;
    let mut pal = [[0u8; 4]; 4];
    pal[0] = [p0[0], p0[1], p0[2], 255];
    pal[1] = [p1[0], p1[1], p1[2], 255];
    if !dxt1 || c0 > c1 {
        pal[2] = [mix(p0[0], p1[0], 2, 1, 3), mix(p0[1], p1[1], 2, 1, 3), mix(p0[2], p1[2], 2, 1, 3), 255];
        pal[3] = [mix(p0[0], p1[0], 1, 2, 3), mix(p0[1], p1[1], 1, 2, 3), mix(p0[2], p1[2], 1, 2, 3), 255];
    } else {
        pal[2] = [mix(p0[0], p1[0], 1, 1, 2), mix(p0[1], p1[1], 1, 1, 2), mix(p0[2], p1[2], 1, 1, 2), 255];
        pal[3] = [0, 0, 0, 0];
    }
    let bits = u32::from_le_bytes([b[4], b[5], b[6], b[7]]);
    let mut out = [[0u8; 4]; 16];
    for (i, px) in out.iter_mut().enumerate() {
        *px = pal[((bits >> (2 * i)) & 3) as usize];
    }
    out
}

/// DXT5 알파 블록(8바이트) → 16 픽셀 알파.
fn alpha_block(b: &[u8]) -> [u8; 16] {
    let (a0, a1) = (b[0] as u32, b[1] as u32);
    let mut pal = [0u32; 8];
    pal[0] = a0;
    pal[1] = a1;
    if a0 > a1 {
        for i in 1..7u32 {
            pal[i as usize + 1] = ((7 - i) * a0 + i * a1) / 7;
        }
    } else {
        for i in 1..5u32 {
            pal[i as usize + 1] = ((5 - i) * a0 + i * a1) / 5;
        }
        pal[6] = 0;
        pal[7] = 255;
    }
    let mut bits: u64 = 0;
    for (k, &v) in b[2..8].iter().enumerate() {
        bits |= (v as u64) << (8 * k);
    }
    let mut out = [0u8; 16];
    for (i, a) in out.iter_mut().enumerate() {
        *a = pal[((bits >> (3 * i)) & 7) as usize] as u8;
    }
    out
}

/// EA 의 DST(블록 순서를 섞은 DXT)를 보통 DXT 블록 순서로 되돌린다.
/// DST1: [색 끝점 4바이트 × n][색 인덱스 4바이트 × n]
/// DST5: [알파 끝점 2 × n][색 끝점 4 × n][알파 인덱스 6 × n][색 인덱스 4 × n] (밉맵 전체에 걸쳐 한 번)
/// 순서는 샘플 포즈 팩 아이콘을 실제로 풀어 보고 확인했다.
fn unshuffle(data: &[u8], dst5: bool) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len());
    if dst5 {
        let n = data.len() / 16;
        let (a0, c0, a1, c1) = (0, n * 2, n * 6, n * 12);
        for i in 0..n {
            out.extend_from_slice(&data[a0 + i * 2..a0 + i * 2 + 2]);
            out.extend_from_slice(&data[a1 + i * 6..a1 + i * 6 + 6]);
            out.extend_from_slice(&data[c0 + i * 4..c0 + i * 4 + 4]);
            out.extend_from_slice(&data[c1 + i * 4..c1 + i * 4 + 4]);
        }
    } else {
        let n = data.len() / 8;
        for i in 0..n {
            out.extend_from_slice(&data[i * 4..i * 4 + 4]);
            out.extend_from_slice(&data[n * 4 + i * 4..n * 4 + i * 4 + 4]);
        }
    }
    out
}

/// DXT1/DXT5 와 EA 의 DST1/DST5 DDS 를 읽는다 (가장 큰 밉맵만).
pub fn decode_dds(data: &[u8]) -> Option<RgbaImage> {
    if data.len() < 128 || &data[..4] != b"DDS " {
        return None;
    }
    let height = u32::from_le_bytes(data[12..16].try_into().ok()?);
    let width = u32::from_le_bytes(data[16..20].try_into().ok()?);
    let (dxt1, shuffled) = match &data[84..88] {
        b"DXT1" => (true, false),
        b"DXT5" => (false, false),
        b"DST1" => (true, true),
        b"DST5" => (false, true),
        _ => return None,
    };
    if width == 0 || height == 0 || width > 4096 || height > 4096 {
        return None;
    }
    let body = &data[128..];
    let unit = if dxt1 { 8 } else { 16 };
    let unshuffled;
    let blocks: &[u8] = if shuffled {
        unshuffled = unshuffle(&body[..body.len() / unit * unit], !dxt1);
        &unshuffled
    } else {
        body
    };
    let (bw, bh) = (width.div_ceil(4) as usize, height.div_ceil(4) as usize);
    if blocks.len() < bw * bh * unit {
        return None;
    }
    let mut img = RgbaImage::new(width, height);
    for by in 0..bh {
        for bx in 0..bw {
            let b = &blocks[(by * bw + bx) * unit..(by * bw + bx + 1) * unit];
            let (alpha, color) =
                if dxt1 { (None, color_block(b, true)) } else { (Some(alpha_block(&b[..8])), color_block(&b[8..], false)) };
            for i in 0..16 {
                let (x, y) = ((bx * 4 + i % 4) as u32, (by * 4 + i / 4) as u32);
                if x < width && y < height {
                    let mut px = color[i];
                    if let Some(a) = alpha {
                        px[3] = a[i];
                    }
                    img.put_pixel(x, y, Rgba(px));
                }
            }
        }
    }
    Some(img)
}

/// 포즈 팩처럼 애니메이션이 든 패키지에서 아이콘 이미지를 썸네일로 저장한다.
pub fn extract(pk: &mut Package, out_path: &str) -> Option<String> {
    if pk.by_type(&[T_CLIP]).next().is_none() {
        return None;
    }
    let mut entries: Vec<IndexEntry> = pk.by_type(&[T_DDS_IMAGE]).copied().collect();
    entries.sort_by_key(|e| std::cmp::Reverse(e.mem_size));
    for e in entries.iter().take(6) {
        let Ok(data) = pk.read(e) else { continue };
        if let Some(img) = decode_dds(&data) {
            return thumbs::save(&DynamicImage::ImageRgba8(img), out_path).ok();
        }
    }
    None
}

/// 포즈 팩의 아이콘을 모두 `out_dir/01.jpg, 02.jpg …` 로 꺼낸다 (패키지 안 순서 그대로).
/// 이미 꺼내 둔 것이 있으면 그대로 돌려준다. 애니메이션이 없는 패키지는 빈 목록.
pub fn extract_all(path: &str, out_dir: &str) -> Vec<String> {
    let done = crate::pyos::join(out_dir, "done");
    if crate::pyos::exists(&done) {
        let mut names: Vec<String> = crate::pyos::listdir(out_dir)
            .unwrap_or_default()
            .into_iter()
            .filter(|n| n.to_lowercase().ends_with(".jpg"))
            .collect();
        names.sort();
        return names.iter().map(|n| crate::pyos::join(out_dir, n)).collect();
    }
    let Ok(mut pk) = Package::open(path) else { return Vec::new() };
    if pk.by_type(&[T_CLIP]).next().is_none() {
        return Vec::new();
    }
    let entries: Vec<IndexEntry> = pk.by_type(&[T_DDS_IMAGE]).copied().collect();
    if crate::pyos::makedirs(out_dir).is_err() {
        return Vec::new();
    }
    let mut out = Vec::new();
    for e in &entries {
        let Ok(data) = pk.read(e) else { continue };
        let Some(img) = decode_dds(&data) else { continue };
        let file = crate::pyos::join(out_dir, &format!("{:03}.jpg", out.len() + 1));
        if let Ok(p) = thumbs::save(&DynamicImage::ImageRgba8(img), &file) {
            out.push(p);
        }
    }
    let _ = std::fs::write(&done, b"");
    out
}
