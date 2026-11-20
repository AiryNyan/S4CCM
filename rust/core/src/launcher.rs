//! 게임 실행과 실행 여부 확인.

use std::process::Command;

use crate::error::Result;
use crate::pyos;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub fn launch(exe: &str, sims_dir: &str, clear_thumbcache: bool) -> Result<()> {
    if clear_thumbcache {
        let cache = pyos::join(sims_dir, "localthumbcache.package");
        if pyos::isfile(&cache) {
            trash::delete(&cache)?;
        }
    }
    Command::new(exe).current_dir(pyos::dirname(exe)).spawn()?;
    Ok(())
}

pub fn game_running() -> bool {
    let mut cmd = Command::new("tasklist");
    cmd.args(["/FI", "IMAGENAME eq TS4*", "/NH"]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let Ok(out) = cmd.output() else { return false };
    let text = String::from_utf8_lossy(&out.stdout).to_lowercase();
    text.lines().any(|line| line.trim().starts_with("ts4") && line.contains(".exe"))
}
