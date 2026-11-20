use std::fmt;

/// 코어 전체에서 쓰는 단순 오류형. 메시지는 화면에 그대로 표시된다.
#[derive(Debug, Clone)]
pub struct Error(pub String);

pub type Result<T> = std::result::Result<T, Error>;

impl Error {
    pub fn msg(s: impl Into<String>) -> Self {
        Error(s.into())
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

macro_rules! from_err {
    ($($t:ty),*) => {$(
        impl From<$t> for Error {
            fn from(e: $t) -> Self { Error(e.to_string()) }
        }
    )*};
}

from_err!(
    std::io::Error,
    rusqlite::Error,
    serde_json::Error,
    image::ImageError,
    zip::result::ZipError,
    sevenz_rust::Error,
    trash::Error,
    jpeg_encoder::EncodingError
);

impl From<String> for Error {
    fn from(s: String) -> Self {
        Error(s)
    }
}

impl From<&str> for Error {
    fn from(s: &str) -> Self {
        Error(s.to_string())
    }
}
