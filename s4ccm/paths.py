from __future__ import annotations

import ctypes
import os
import re
import uuid
from ctypes import wintypes
from dataclasses import dataclass

MARKERS = ("Options.ini", "GameVersion.txt", "Mods", "Saves")
DISABLED_DIR = "S4CCM_Disabled"
QUARANTINE_DIR = "S4CCM_Quarantine"


@dataclass
class SimsFolder:
    root: str
    marker_count: int
    cc_count: int
    mtime: float

    @property
    def mods(self) -> str:
        return os.path.join(self.root, "Mods")


def _known_folder(guid: str) -> str | None:
    if os.name != "nt":
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

    u = uuid.UUID(guid)
    g = GUID(u.time_low, u.time_mid, u.time_hi_version, (wintypes.BYTE * 8)(*u.bytes[8:]))
    out = ctypes.c_wchar_p()
    try:
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out))
    except Exception:
        return None
    if hr != 0:
        return None
    path = out.value
    ctypes.windll.ole32.CoTaskMemFree(out)
    return path


FOLDERID_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
FOLDERID_PUBLIC_DOCUMENTS = "{ED4824AF-DCE4-45A8-81E2-FC7965083634}"


def document_dirs() -> list[str]:
    dirs: list[str] = []
    for d in (_known_folder(FOLDERID_DOCUMENTS),
              os.path.join(os.environ.get("OneDrive", ""), "Documents") if os.environ.get("OneDrive") else None,
              os.path.join(os.environ.get("OneDrive", ""), "문서") if os.environ.get("OneDrive") else None,
              os.path.join(os.path.expanduser("~"), "Documents"),
              _known_folder(FOLDERID_PUBLIC_DOCUMENTS)):
        if d and os.path.isdir(d) and os.path.normcase(d) not in {os.path.normcase(x) for x in dirs}:
            dirs.append(d)
    return dirs


def count_markers(root: str) -> int:
    return sum(os.path.exists(os.path.join(root, m)) for m in MARKERS)


def _quick_cc_count(mods: str, limit: int = 20000) -> int:
    n = 0
    for _dp, _dn, fn in os.walk(mods):
        n += sum(f.lower().endswith((".package", ".ts4script")) for f in fn)
        if n > limit:
            break
    return n


def inspect(root: str) -> SimsFolder:
    mods = os.path.join(root, "Mods")
    return SimsFolder(root, count_markers(root),
                      _quick_cc_count(mods) if os.path.isdir(mods) else 0,
                      os.path.getmtime(root) if os.path.exists(root) else 0)


def find_sims_folders() -> list[SimsFolder]:
    found: list[SimsFolder] = []
    for docs in document_dirs():
        ea = os.path.join(docs, "Electronic Arts")
        if not os.path.isdir(ea):
            continue
        for name in os.listdir(ea):
            cand = os.path.join(ea, name)
            if os.path.isdir(cand) and count_markers(cand) >= 2:
                found.append(inspect(cand))
    return found


def normalize_user_choice(path: str) -> str:
    path = os.path.normpath(path.strip().strip('"'))
    if os.path.basename(path).lower() == "mods":
        return os.path.dirname(path)
    return path


def validate(root: str) -> tuple[bool, str]:
    if not os.path.isdir(root):
        return False, "폴더가 존재하지 않습니다."
    missing = [m for m in MARKERS if not os.path.exists(os.path.join(root, m))]
    if len(missing) <= 2 and os.path.isdir(os.path.join(root, "Mods")):
        return True, "정상"
    if not os.path.isdir(os.path.join(root, "Mods")):
        return False, "Mods 폴더가 없습니다."
    return False, "심즈 폴더 표시 파일이 없습니다: " + ", ".join(missing)


def _reg_install_dirs() -> list[str]:
    out: list[str] = []
    try:
        import winreg
    except ImportError:
        return out
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key in (r"SOFTWARE\Maxis\The Sims 4", r"SOFTWARE\WOW6432Node\Maxis\The Sims 4"):
            try:
                with winreg.OpenKey(hive, key) as k:
                    v, _ = winreg.QueryValueEx(k, "Install Dir")
                    out.append(v)
            except OSError:
                pass
    return out


def _steam_dirs() -> list[str]:
    out: list[str] = []
    for base in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        vdf = os.path.join(base, "steamapps", "libraryfolders.vdf")
        libs = [base]
        if os.path.isfile(vdf):
            try:
                text = open(vdf, encoding="utf-8", errors="ignore").read()
                libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', text)]
            except OSError:
                pass
        out += [os.path.join(lib, "steamapps", "common", "The Sims 4") for lib in libs]
    return out


def find_game_exe() -> str | None:
    cands = _reg_install_dirs() + [
        r"C:\Program Files\EA Games\The Sims 4",
        r"C:\Program Files (x86)\Origin Games\The Sims 4",
        r"C:\Program Files\Origin Games\The Sims 4",
    ] + _steam_dirs()
    for d in cands:
        exe = os.path.join(d, "Game", "Bin", "TS4_x64.exe")
        if os.path.isfile(exe):
            return exe
    return None
