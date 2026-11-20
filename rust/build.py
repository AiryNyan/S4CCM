"""S4CCM (Rust 코어 + Qt 화면) 빌드.

    python build.py            # rust/dist/S4CCM 에 실행 파일과 필요한 DLL 을 모은다
    python build.py --clean    # C++ 오브젝트를 지우고 처음부터
    python build.py --release  # 위에 더해 rust/release 에 포터블 zip 과 설치 파일을 만든다 (Inno Setup 6 필요)

필요한 것: Rust(cargo), llvm-mingw, Qt 6 (llvm-mingw 빌드). 경로는 아래 환경 변수로 바꿀 수 있다.
"""
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
QT = os.environ.get("S4CCM_QT", r"C:\Qt")
LLVM = os.environ.get("S4CCM_LLVM_MINGW", os.path.expanduser(r"~\.local\llvm-mingw-20261006-ucrt-x86_64"))
TARGET = "x86_64-pc-windows-gnullvm"
GUI = os.path.join(HERE, "gui")
OBJ = os.path.join(HERE, "target", "gui")
RUST_OUT = os.path.join(HERE, "target", TARGET, "release")
DIST = os.path.join(HERE, "dist", "S4CCM")
CXX = os.path.join(LLVM, "bin", "x86_64-w64-mingw32-clang++.exe")
WINDRES = os.path.join(LLVM, "bin", "x86_64-w64-mingw32-windres.exe")
RUNTIME = os.path.join(LLVM, "x86_64-w64-mingw32", "bin")

CXXFLAGS = ["-std=c++17", "-O2", "-DNDEBUG", "-DQT_NO_DEBUG", "-DUNICODE", "-D_UNICODE", "-finput-charset=UTF-8",
            "-Wall", "-Wno-ignored-attributes", "-Wno-deprecated-declarations",
            "-I" + os.path.join(QT, "include")] + [
    "-I" + os.path.join(QT, "include", m) for m in ("QtCore", "QtGui", "QtWidgets")]

QT_DLLS = ["Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Svg.dll"]
PLUGINS = {
    "platforms": ["qwindows.dll"],
    "iconengines": ["qsvgicon.dll"],
    "imageformats": ["qgif.dll", "qico.dll", "qjpeg.dll", "qsvg.dll", "qwebp.dll", "qtiff.dll", "qtga.dll",
                     "qwbmp.dll", "qicns.dll"],
}


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if r.returncode != 0:
        sys.stdout.write(r.stdout + r.stderr)
        raise SystemExit(f"실패: {os.path.basename(cmd[0])} {cmd[-1]}")
    return r


def newer(src_times, out):
    return not os.path.exists(out) or os.path.getmtime(out) < max(src_times)


def compile_one(src, headers_time):
    out = os.path.join(OBJ, os.path.splitext(os.path.basename(src))[0] + ".o")
    if newer([os.path.getmtime(src), headers_time], out):
        print("  컴파일", os.path.basename(src), flush=True)
        r = run([CXX, *CXXFLAGS, "-c", src, "-o", out])
        if r.stderr.strip():
            sys.stdout.write(r.stderr)
    return out


def copy_if_exists(src, dst_dir):
    if os.path.exists(src):
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, dst_dir)
        return True
    print("  (없음)", src)
    return False


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if "--clean" in sys.argv:
        shutil.rmtree(OBJ, ignore_errors=True)
    os.makedirs(OBJ, exist_ok=True)

    print("[1/4] Rust 코어")
    r = subprocess.run(["cargo", "build", "--release", "-p", "s4ccm-ffi"], cwd=HERE)
    if r.returncode != 0:
        raise SystemExit("cargo 빌드 실패")

    print("[2/4] Qt 화면 (C++)")
    sources = sorted(os.path.join(GUI, n) for n in os.listdir(GUI) if n.endswith(".cpp"))
    headers_time = max(os.path.getmtime(os.path.join(GUI, n)) for n in os.listdir(GUI) if n.endswith(".h"))
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        objects = list(pool.map(lambda s: compile_one(s, headers_time), sources))

    res = os.path.join(OBJ, "app_res.o")
    shutil.copy2(os.path.join(ROOT, "s4ccm", "assets", "icon.ico"), os.path.join(OBJ, "icon.ico"))
    shutil.copy2(os.path.join(GUI, "app.manifest"), os.path.join(OBJ, "app.manifest"))
    shutil.copy2(os.path.join(GUI, "app.rc"), os.path.join(OBJ, "app.rc"))
    run([WINDRES, "app.rc", "-O", "coff", "-o", res], cwd=OBJ)

    print("[3/4] 링크")
    os.makedirs(DIST, exist_ok=True)
    exe = os.path.join(DIST, "S4CCM.exe")
    run([CXX, *objects, res, "-L" + os.path.join(QT, "lib"), "-lQt6Widgets", "-lQt6Gui", "-lQt6Core",
         "-L" + RUST_OUT, "-ls4ccm_rs", "-lshell32", "-mwindows", "-o", exe])

    print("[4/4] 배포 폴더")
    shutil.copy2(os.path.join(RUST_OUT, "s4ccm_rs.dll"), DIST)
    for name in QT_DLLS:
        copy_if_exists(os.path.join(QT, "bin", name), DIST)
    for name in ("libc++.dll", "libunwind.dll"):
        copy_if_exists(os.path.join(RUNTIME, name), DIST)
    for folder, names in PLUGINS.items():
        for name in names:
            copy_if_exists(os.path.join(QT, "plugins", folder, name), os.path.join(DIST, folder))
    for folder in ("assets", "fonts"):
        dst = os.path.join(DIST, folder)
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(os.path.join(ROOT, "s4ccm", folder)):
            shutil.copy2(os.path.join(ROOT, "s4ccm", folder, name), dst)
    total = sum(os.path.getsize(os.path.join(dp, n)) for dp, _d, fn in os.walk(DIST) for n in fn)
    print(f"완료: {exe}  (폴더 전체 {total / 1048576:.1f} MB)")


RELEASE = os.path.join(HERE, "release")
ISCC = [os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Inno Setup 6\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"]


def version():
    import re
    text = open(os.path.join(HERE, "core", "src", "config.rs"), encoding="utf-8").read()
    return re.search(r'pub const VERSION: &str = "([^"]+)"', text).group(1)


def release():
    import zipfile
    ver = version()
    os.makedirs(RELEASE, exist_ok=True)
    zpath = os.path.join(RELEASE, "S4CCM.zip")
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dp, _dn, fn in os.walk(DIST):
            for n in sorted(fn):
                full = os.path.join(dp, n)
                z.write(full, "S4CCM/" + os.path.relpath(full, DIST).replace("\\", "/"))
    print(f"포터블: {zpath} ({os.path.getsize(zpath) / 1048576:.1f} MB)")
    iscc = next((p for p in ISCC if os.path.isfile(p)), None)
    if not iscc:
        print("Inno Setup 6 이 없어 설치 파일은 건너뜀 (https://jrsoftware.org/isdl.php)")
        return
    run([iscc, "/Q", f"/DAppVersion={ver}", f"/DSourceDir={DIST}", f"/DOutDir={RELEASE}",
         os.path.join(HERE, "installer.iss")])
    setup = os.path.join(RELEASE, f"S4CCM_Setup_v{ver}.exe")
    print(f"설치 파일: {setup} ({os.path.getsize(setup) / 1048576:.1f} MB)")


if __name__ == "__main__":
    main()
    if "--release" in sys.argv:
        release()
