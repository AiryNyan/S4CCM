//! 미리보기 이미지 추출·저장.

use std::collections::HashMap;
use std::io::Cursor;

use image::{DynamicImage, GrayImage, ImageBuffer, ImageReader, Rgb, RgbImage, Rgba, RgbaImage};
use parking_lot::Mutex;

use crate::dbpf::{IndexEntry, Package};
use crate::error::{Error, Result};
use crate::pyos;

pub const THUMB_TYPES: [u32; 5] = [0x3C1AF1F2, 0x5B282D45, 0x3C2A8647, 0xCD9DE247, 0x2F7D0004];
pub const THUMB_SIZE: u32 = 256;
pub const THUMB_QUALITY: u8 = 80;
pub const THUMB_BG: [u8; 3] = [236, 236, 240];
pub const THUMB_EXT: &str = ".jpg";

/// 내용으로 형식을 판별해 연다 (`PIL.Image.open` 처럼 확장자는 보지 않는다).
pub fn open_bytes(data: &[u8]) -> Result<DynamicImage> {
    Ok(ImageReader::new(Cursor::new(data)).with_guessed_format()?.decode()?)
}

pub fn open_path(path: &str) -> Result<DynamicImage> {
    open_bytes(&std::fs::read(path)?)
}

fn find(hay: &[u8], needle: &[u8], end: usize) -> Option<usize> {
    let end = end.min(hay.len());
    memchr::memmem::find(&hay[..end], needle)
}

/// 반올림: 정확히 절반이면 짝수 쪽 (Python `round`).
pub fn py_round(x: f64) -> f64 {
    let r = x.round();
    if (x - x.trunc()).abs() == 0.5 {
        2.0 * (x / 2.0).round()
    } else {
        r
    }
}

#[derive(Clone, Copy)]
pub enum Filter {
    Bicubic,
    Lanczos,
}

impl Filter {
    fn to_image(self) -> image::imageops::FilterType {
        match self {
            Filter::Bicubic => image::imageops::FilterType::CatmullRom,
            Filter::Lanczos => image::imageops::FilterType::Lanczos3,
        }
    }
}

/// 알파를 미리 곱한 상태로 리샘플한다(투명 영역 색이 가장자리에 번지지 않게, Pillow 와 같은 방식).
pub fn resize_rgba(img: &RgbaImage, w: u32, h: u32, filter: Filter) -> RgbaImage {
    if img.width() == w && img.height() == h {
        return img.clone();
    }
    // image 의 float 리샘플은 0..1 범위를 전제로 하므로 그 범위로 맞춘다.
    let mut pre: ImageBuffer<Rgba<f32>, Vec<f32>> = ImageBuffer::new(img.width(), img.height());
    for (src, dst) in img.pixels().zip(pre.pixels_mut()) {
        let a = src[3] as f32 / 255.0;
        let c = |v: u8| v as f32 / 255.0 * a;
        *dst = Rgba([c(src[0]), c(src[1]), c(src[2]), a]);
    }
    let out = image::imageops::resize(&pre, w, h, filter.to_image());
    let mut res = RgbaImage::new(w, h);
    for (src, dst) in out.pixels().zip(res.pixels_mut()) {
        let a = src[3].clamp(0.0, 1.0);
        let un = |c: f32| if a > 0.0 { (c / a * 255.0).round().clamp(0.0, 255.0) as u8 } else { 0 };
        *dst = Rgba([un(src[0]), un(src[1]), un(src[2]), (a * 255.0).round() as u8]);
    }
    res
}

/// JPEG 뒤에 ALFA 청크로 알파가 따로 붙은 심즈 썸네일.
pub fn jpeg_with_alfa(data: &[u8]) -> Result<DynamicImage> {
    let img = open_bytes(data)?;
    if let Some(i) = find(data, b"ALFA", 256) {
        if let Some(rest) = data.get(i + 8..) {
            if let Ok(alpha) = open_bytes(rest) {
                let mut alpha: GrayImage = alpha.to_luma8();
                if alpha.dimensions() != (img.width(), img.height()) {
                    alpha = image::imageops::resize(
                        &alpha,
                        img.width(),
                        img.height(),
                        image::imageops::FilterType::CatmullRom,
                    );
                }
                let rgb = img.to_rgb8();
                let mut out = RgbaImage::new(rgb.width(), rgb.height());
                for ((p, a), o) in rgb.pixels().zip(alpha.pixels()).zip(out.pixels_mut()) {
                    *o = Rgba([p[0], p[1], p[2], a[0]]);
                }
                return Ok(DynamicImage::ImageRgba8(out));
            }
        }
    }
    Ok(img)
}

pub fn decode(data: &[u8]) -> Option<DynamicImage> {
    if data.starts_with(b"\xff\xd8\xff") {
        return jpeg_with_alfa(data).ok();
    }
    for sig in [&b"\x89PNG"[..], &b"\xff\xd8\xff"[..]] {
        if let Some(i) = find(data, sig, 64) {
            if sig == b"\xff\xd8\xff" {
                return jpeg_with_alfa(&data[i..]).ok();
            }
            return open_bytes(&data[i..]).ok();
        }
    }
    None
}

/// `Image.thumbnail((size, size))` 가 고르는 크기.
pub fn thumbnail_size(w: u32, h: u32, size: u32) -> (u32, u32) {
    let (mut x, mut y) = (size as f64, size as f64);
    if x >= w as f64 && y >= h as f64 {
        return (w, h);
    }
    let aspect = w as f64 / h as f64;
    let pick = |number: f64, key: &dyn Fn(f64) -> f64| -> f64 {
        let (lo, hi) = (number.floor(), number.ceil());
        let best = if key(lo) <= key(hi) { lo } else { hi };
        best.max(1.0)
    };
    if x / y >= aspect {
        x = pick(y * aspect, &|n| (aspect - n / y).abs());
    } else {
        y = pick(x / aspect, &|n| if n == 0.0 { 0.0 } else { (aspect - x / n).abs() });
    }
    (x as u32, y as u32)
}

fn muldiv255(a: u32, b: u32) -> u32 {
    let tmp = a * b + 128;
    ((tmp >> 8) + tmp) >> 8
}

/// 단색 배경 위에 알파 합성 (`Image.paste(img, mask=alpha)`).
pub fn flatten(img: &RgbaImage, bg: [u8; 3]) -> RgbImage {
    let mut out = RgbImage::new(img.width(), img.height());
    for (src, dst) in img.pixels().zip(out.pixels_mut()) {
        let a = src[3] as u32;
        let ch = |i: usize| (muldiv255(bg[i] as u32, 255 - a) + muldiv255(src[i] as u32, a)).min(255) as u8;
        *dst = Rgb([ch(0), ch(1), ch(2)]);
    }
    out
}

pub fn encode_jpeg(img: &RgbImage, quality: u8, optimize: bool) -> Result<Vec<u8>> {
    let mut buf = Vec::new();
    let mut enc = jpeg_encoder::Encoder::new(&mut buf, quality);
    enc.set_sampling_factor(jpeg_encoder::SamplingFactor::F_2_2);
    enc.set_optimized_huffman_tables(optimize);
    let (w, h) = (
        u16::try_from(img.width()).map_err(|_| Error::msg("image too large"))?,
        u16::try_from(img.height()).map_err(|_| Error::msg("image too large"))?,
    );
    enc.encode(img.as_raw(), w, h, jpeg_encoder::ColorType::Rgb)?;
    Ok(buf)
}

/// 256px 이내로 줄이고 배경을 깔아 JPEG 로 저장한다.
pub fn save(img: &DynamicImage, out_path: &str) -> Result<String> {
    let rgba = img.to_rgba8();
    let (w, h) = thumbnail_size(rgba.width(), rgba.height(), THUMB_SIZE);
    let rgba = resize_rgba(&rgba, w, h, Filter::Bicubic);
    let flat = flatten(&rgba, THUMB_BG);
    pyos::makedirs(&pyos::dirname(out_path))?;
    let bytes = encode_jpeg(&flat, THUMB_QUALITY, true)?;
    std::fs::write(out_path, bytes)?;
    Ok(out_path.to_string())
}

pub fn convert_to_jpg(path: &str) -> Option<String> {
    let out = pyos::splitext(path).0 + ".jpg";
    let img = open_path(path).ok()?;
    save(&img, &out).ok()?;
    std::fs::remove_file(path).ok()?;
    Some(out)
}

fn pick_best(pk: &Package, prefer_instances: &[u64]) -> Vec<IndexEntry> {
    let prefer = prefer_instances.first().copied();
    let mut entries: Vec<IndexEntry> = pk.by_type(&THUMB_TYPES).copied().collect();
    entries.sort_by(|a, b| {
        let ka = (Some(a.instance) != prefer, std::cmp::Reverse(a.mem_size));
        let kb = (Some(b.instance) != prefer, std::cmp::Reverse(b.mem_size));
        ka.cmp(&kb)
    });
    entries
}

pub fn extract_from_open(pk: &mut Package, out_path: &str, prefer_instances: &[u64]) -> Option<String> {
    let ordered = pick_best(pk, prefer_instances);
    for e in ordered.iter().take(6) {
        // 원본은 읽기 오류가 나면 바로 포기한다.
        let data = pk.read(e).ok()?;
        if let Some(img) = decode(&data) {
            return save(&img, out_path).ok();
        }
    }
    None
}

pub fn extract_from_package(path: &str, out_path: &str, prefer_instances: &[u64]) -> Option<String> {
    let mut pk = Package::open(path).ok()?;
    extract_from_open(&mut pk, out_path, prefer_instances)
}

/// 게임이 만들어 둔 `localthumbcache.package` 에서 썸네일을 찾는다.
pub struct ThumbCache {
    path: Mutex<Option<String>>,
    index: Mutex<Option<HashMap<u64, IndexEntry>>>,
}

impl ThumbCache {
    pub fn new(path: Option<String>) -> Self {
        ThumbCache { path: Mutex::new(path), index: Mutex::new(None) }
    }

    /// 캐시 파일 경로를 바꾸고 읽어 둔 색인을 버린다 (심즈 폴더가 바뀌었을 때).
    pub fn set_path(&self, path: Option<String>) {
        *self.path.lock() = path;
        *self.index.lock() = None;
    }

    pub fn path(&self) -> Option<String> {
        self.path.lock().clone()
    }

    fn load(&self) -> HashMap<u64, IndexEntry> {
        let mut index: HashMap<u64, IndexEntry> = HashMap::new();
        let path = self.path();
        let Some(path) = path.as_deref() else { return index };
        if !pyos::isfile(path) {
            return index;
        }
        if let Ok(pk) = Package::open(path) {
            for e in &pk.entries {
                if THUMB_TYPES.contains(&e.type_) {
                    match index.get(&e.instance) {
                        Some(cur) if e.mem_size <= cur.mem_size => {}
                        _ => {
                            index.insert(e.instance, *e);
                        }
                    }
                }
            }
        }
        index
    }

    pub fn extract(&self, instances: &[u64], out_path: &str) -> Option<String> {
        let mut guard = self.index.lock();
        if guard.is_none() {
            *guard = Some(self.load());
        }
        let index = guard.as_ref().unwrap();
        let hits: Vec<IndexEntry> = instances.iter().take(50).filter_map(|i| index.get(i).copied()).collect();
        if hits.is_empty() {
            return None;
        }
        let mut pk = Package::open(self.path().as_deref()?).ok()?;
        for e in hits.iter().take(3) {
            let data = pk.read(e).ok()?;
            if let Some(img) = decode(&data) {
                return save(&img, out_path).ok();
            }
        }
        None
    }
}

pub fn save_user_image_path(src: &str, out_path: &str) -> Option<String> {
    let img = open_path(src).ok()?;
    save(&img, out_path).ok()
}

pub fn save_user_image_bytes(src: &[u8], out_path: &str) -> Option<String> {
    let img = open_bytes(src).ok()?;
    save(&img, out_path).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resize_keeps_opaque_colour() {
        let img = RgbaImage::from_pixel(400, 569, Rgba([174, 149, 145, 255]));
        let out = resize_rgba(&img, 180, 256, Filter::Bicubic);
        assert_eq!(out.dimensions(), (180, 256));
        assert_eq!(*out.get_pixel(90, 128), Rgba([174, 149, 145, 255]));
        assert_eq!(thumbnail_size(400, 569, 256), (180, 256));
        assert_eq!(thumbnail_size(100, 50, 256), (100, 50));
        assert_eq!(py_round(2.5), 2.0);
        assert_eq!(py_round(3.5), 4.0);
    }
}
