from __future__ import annotations

import os
import subprocess

from send2trash import send2trash


def launch(exe: str, sims_dir: str, clear_thumbcache: bool = False) -> None:
    if clear_thumbcache:
        cache = os.path.join(sims_dir, "localthumbcache.package")
        if os.path.isfile(cache):
            send2trash(cache)
    subprocess.Popen([exe], cwd=os.path.dirname(exe), close_fds=True)


def game_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq TS4*", "/NH"],
                             capture_output=True, text=True, creationflags=0x08000000).stdout
    except OSError:
        return False
    return any(line.strip().lower().startswith("ts4") and ".exe" in line.lower() for line in out.splitlines())
