import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "s4ccm")
RELEASE = os.path.join(ROOT, "release")
WORK = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
ISCC = [os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Inno Setup 6\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"]

SOURCE_FILES = ("main.py", "requirements.txt")
PORTABLE_ZIP = "S4CCM.zip"
SOURCE_ZIP = "S4CCM_파이썬.zip"


def version() -> str:
    text = open(os.path.join(PKG, "__init__.py"), encoding="utf-8").read()
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def run(cmd: list[str]) -> None:
    print(">", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)


def zip_contents(folder: str, name: str) -> str:
    path = os.path.join(RELEASE, name)
    if os.path.exists(path):
        os.remove(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _dn, fn in os.walk(folder):
            for n in fn:
                full = os.path.join(dp, n)
                z.write(full, os.path.relpath(full, folder).replace("\\", "/"))
    return path


def make_source() -> None:
    out = os.path.join(WORK, "source")
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(PKG, os.path.join(out, "s4ccm"), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in SOURCE_FILES:
        shutil.copy2(os.path.join(ROOT, name), out)
    print("python source:", zip_contents(out, SOURCE_ZIP))


def make_portable() -> None:
    run([sys.executable, os.path.join(HERE, "make_icon.py")])
    sep = os.pathsep
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "S4CCM",
         "--icon", os.path.join(PKG, "assets", "icon.ico"),
         "--add-data", f"{os.path.join(PKG, 'fonts')}{sep}s4ccm/fonts",
         "--add-data", f"{os.path.join(PKG, 'assets')}{sep}s4ccm/assets",
         "--add-data", f"{os.path.join(PKG, 'data')}{sep}s4ccm/data",
         "--distpath", DIST, "--workpath", WORK, "--specpath", HERE,
         os.path.join(ROOT, "main.py")])
    print("portable:", zip_contents(os.path.join(DIST, "S4CCM"), PORTABLE_ZIP))


def make_installer(ver: str) -> None:
    iscc = next((p for p in ISCC if os.path.isfile(p)), None)
    if not iscc:
        print("Inno Setup 6 not found: installer skipped (https://jrsoftware.org/isdl.php)")
        return
    run([iscc, f"/DAppVersion={ver}", f"/DSourceDir={os.path.join(DIST, 'S4CCM')}", f"/DOutDir={RELEASE}",
         os.path.join(HERE, "installer.iss")])
    print("installer:", os.path.join(RELEASE, f"S4CCM_Setup_v{ver}.exe"))


def check_syntax() -> None:
    import ast
    bad = []
    for dp, _dn, fn in os.walk(PKG):
        for n in fn:
            if n.endswith(".py"):
                path = os.path.join(dp, n)
                try:
                    ast.parse(open(path, encoding="utf-8").read(), path)
                except SyntaxError as ex:
                    bad.append(f"{path}:{ex.lineno}: {ex.msg}")
    if bad:
        raise SystemExit("syntax error, build stopped:" + chr(10) + chr(10).join(bad))


def main() -> None:
    check_syntax()
    ver = version()
    os.makedirs(RELEASE, exist_ok=True)
    for old in os.listdir(RELEASE):
        if old.startswith("S4CCM_v"):
            target = os.path.join(RELEASE, old)
            shutil.rmtree(target) if os.path.isdir(target) else os.remove(target)
    make_source()
    make_portable()
    make_installer(ver)
    for d in (WORK, DIST):
        shutil.rmtree(d, ignore_errors=True)
    spec = os.path.join(HERE, "S4CCM.spec")
    if os.path.exists(spec):
        os.remove(spec)


if __name__ == "__main__":
    main()
