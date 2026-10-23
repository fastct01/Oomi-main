"""Build a self-contained, drag-to-Applications Oomi.app for Apple Silicon and Intel Macs, zipped for download.

    python3 tools/build_app.py --arm64 <python-build-standalone aarch64-apple-darwin install_only tarball> \
                               --x86_64 <python-build-standalone x86_64-apple-darwin install_only tarball> [--out dist]

What it does, all from Linux or a Mac:
  * unpacks the standalone Python builds (github.com/astral-sh/python-build-standalone), one per chip
  * installs oomi's dependencies as prebuilt macOS wheels, pinned to the versions the tests run against
  * compiles the app's small executable (macapp/launcher.c) for both chips with `zig cc` and joins them into one
    universal file, so macOS sees a real app: its own name in the menu bar and the ghost in the Dock
  * signs the bundle ad hoc with rcodesign (github.com/indygreg/apple-platform-rs). Without a sealed signature,
    macOS calls a downloaded app "damaged" instead of offering "Open Anyway".

Needs: zig (`pip install ziglang`, or on PATH) and rcodesign (on PATH, or --rcodesign).

--linux <tarball> builds the same layout around a Linux Python, to smoke-test the packaging off a Mac.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_VERSION = "3.13"
# Exactly the versions oomi's test suite runs against, so the app ships what was tested.
DEPS = ["annotated-types==0.8.0", "anthropic==1.17.0", "anyio==4.15.1", "docstring-parser==0.18.0", "h11==0.16.0",
        "httpcore2==2.13.1", "httpx2==2.13.1", "idna==3.20", "jiter==0.17.0", "markdown-it-py==4.2.0", "mdurl==0.1.2",
        "opentelemetry-api==1.45.0", "pydantic==2.13.5", "pydantic-core==2.46.5", "pygments==2.21.0", "rich==15.0.0",
        "sniffio==1.3.1", "truststore==0.10.4", "typing-extensions==4.16.0", "typing-inspection==0.4.4"]
# The native window (pywebview on WebKit, through PyObjC). macOS only.
MAC_GUI_DEPS = ["pywebview==6.2.1", "bottle==0.13.4", "pyobjc-core==12.2.2", "pyobjc-framework-Cocoa==12.2.2",
                "pyobjc-framework-Quartz==12.2.2", "pyobjc-framework-WebKit==12.2.2",
                "pyobjc-framework-Security==12.2.2", "pyobjc-framework-UniformTypeIdentifiers==12.2.2",
                "pyobjc-framework-UserNotifications==12.2.2"]
SOURCE_ONLY_DEPS = ["proxy_tools==0.1.0"]  # pure Python, published only as source
PLATFORMS = {
    "arm64": ["macosx_11_0_arm64", "macosx_10_9_universal2", "macosx_10_12_universal2", "macosx_10_13_universal2",
              "macosx_11_0_universal2"],
    "x86_64": ["macosx_10_9_x86_64", "macosx_10_12_x86_64", "macosx_10_13_x86_64", "macosx_11_0_x86_64",
               "macosx_10_9_universal2", "macosx_10_12_universal2", "macosx_10_13_universal2", "macosx_11_0_universal2"],
    "linux": ["manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux_2_28_x86_64"],
}
ZIG_TARGETS = {"arm64": "aarch64-macos.11.0", "x86_64": "x86_64-macos.11.0", "linux": "x86_64-linux-gnu.2.17"}
TRIM = ["share", "lib/pkgconfig", "lib/itcl4", "lib/tcl8", "lib/tcl8.6", "lib/tk8.6", "lib/thread2.8"]
TRIM_STDLIB = ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "turtle.py", "pydoc_data"]


def version() -> str:
    return re.search(r'__version__ = "([^"]+)"', (ROOT / "oomi" / "__init__.py").read_text()).group(1)


def unpack_python(tarball: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as tf:
        tf.extractall(dest, filter="tar")  # keeps the build's own symlinks, rejects anything escaping dest
    py = dest / "python"
    for rel in TRIM:
        shutil.rmtree(py / rel, ignore_errors=True)
    stdlib = py / "lib" / f"python{PY_VERSION}"
    for rel in TRIM_STDLIB:
        p = stdlib / rel
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    for dyn in (stdlib / "lib-dynload").glob("_tkinter*"):
        dyn.unlink()
    for cfg in stdlib.glob("config-*"):
        for a in cfg.glob("*.a"):
            a.unlink()  # static libpython: only needed to build extensions
    for lib in (py / "lib").glob("lib*.a"):
        lib.unlink()
    return py


def drop_headers(py: Path) -> None:
    shutil.rmtree(py / "include", ignore_errors=True)  # kept until the launcher is compiled against them


def install_deps(target: Path, arch: str, gui: bool) -> None:
    pip = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-compile", "--no-deps"]
    cmd = pip + ["--target", str(target), "--only-binary=:all:", "--python-version", PY_VERSION, "--implementation",
                 "cp", "--abi", f"cp{PY_VERSION.replace('.', '')}", "--abi", "abi3", "--abi", "none"]
    for plat in PLATFORMS[arch]:
        cmd += ["--platform", plat]
    subprocess.run(cmd + DEPS + (MAC_GUI_DEPS if gui else []), check=True)
    if gui:
        with tempfile.TemporaryDirectory() as tmp:  # build the pure-Python one into a staging dir, then copy it in
            names = ",".join(d.split("==")[0] for d in SOURCE_ONLY_DEPS)
            subprocess.run(pip + ["--target", tmp, "--use-pep517", f"--no-binary={names}"] + SOURCE_ONLY_DEPS, check=True)
            for item in Path(tmp).iterdir():
                dest = target / item.name
                shutil.copytree(item, dest) if item.is_dir() else shutil.copy2(item, dest)
    # --no-deps + a full pin list: fail loudly if the pins stop covering what's needed.
    names = ["anthropic", "rich"] + (["pywebview", "pyobjc-core", "proxy_tools", "bottle"] if gui else [])
    check = [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import importlib.metadata as m; "
             "[m.version(n) for n in sys.argv[2:]]", str(target)] + names
    subprocess.run(check, check=True)
    shutil.rmtree(target / "bin", ignore_errors=True)
    shutil.rmtree(target / "PyObjCTest", ignore_errors=True)  # pyobjc's own tests (with unsignable debug symbols)
    for junk in [*target.rglob("__pycache__"), *target.rglob("*.dSYM")]:
        shutil.rmtree(junk, ignore_errors=True)


def copy_oomi(dest: Path) -> None:
    shutil.copytree(ROOT / "oomi", dest / "oomi", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def write_exec(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


# -- the executable ---------------------------------------------------------------------------------------------
def find_zig(given: str | None) -> list:
    if given:
        return [given]
    if shutil.which("zig"):
        return ["zig"]
    try:
        import ziglang  # noqa: F401  (pip install ziglang)

        return [sys.executable, "-m", "ziglang"]
    except ImportError:
        raise SystemExit("zig is needed to compile the app's executable: pip install ziglang (or put zig on PATH).")


def compile_launcher(zig: list, arch: str, include: Path, out: Path) -> Path:
    cmd = zig + ["cc", "-target", ZIG_TARGETS[arch], "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(include),
                 "-o", str(out), str(ROOT / "macapp" / "launcher.c")]
    if arch == "linux":
        cmd += ["-ldl", "-s"]
    subprocess.run(cmd, check=True)
    return out


FAT_MAGIC, MH_MAGIC_64, CPU_SUBTYPE_MASK = 0xCAFEBABE, 0xFEEDFACF, 0xFF000000


def write_fat(slices: list, out: Path, align: int = 14) -> None:
    """Join single-architecture Mach-O files into one universal binary (what `lipo -create` does)."""
    step = 1 << align
    up = lambda n: -(-n // step) * step  # noqa: E731
    parts = []
    for p in slices:
        data = Path(p).read_bytes()
        magic, cputype, cpusubtype = struct.unpack("<III", data[:12])
        if magic != MH_MAGIC_64:
            raise ValueError(f"{p} is not a 64-bit Mach-O file")
        parts.append((cputype, cpusubtype & ~CPU_SUBTYPE_MASK & 0xFFFFFFFF, data))
    buf = bytearray(struct.pack(">II", FAT_MAGIC, len(parts)))
    offset = up(8 + 20 * len(parts))
    placed = []
    for cputype, cpusubtype, data in parts:
        buf += struct.pack(">IIIII", cputype, cpusubtype, offset, len(data), align)
        placed.append((offset, data))
        offset = up(offset + len(data))
    for off, data in placed:
        buf += b"\0" * (off - len(buf))
        buf += data
    out.write_bytes(bytes(buf))
    out.chmod(0o755)


def sign(app: Path, rcodesign: str | None) -> None:
    tool = rcodesign or shutil.which("rcodesign")
    if not tool:
        raise SystemExit("rcodesign is needed to seal the app (otherwise macOS says it's damaged): "
                         "get it from github.com/indygreg/apple-platform-rs/releases, or pass --rcodesign.")
    # Ad hoc: no certificate. Seals Contents (Info.plist, resources, every bundled binary) under one signature.
    r = subprocess.run([tool, "sign", str(app)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"signing failed:\n{(r.stdout + r.stderr)[-3000:]}")


def build_mac(arm64: Path, x86_64: Path, out: Path, zig: list, rcodesign: str | None) -> Path:
    ver = version()
    app = out / "Oomi.app"
    shutil.rmtree(app, ignore_errors=True)
    contents = app / "Contents"
    res = contents / "Resources"
    slices = []
    work = out / "_launcher"
    work.mkdir(parents=True, exist_ok=True)
    for arch, tarball in (("arm64", arm64), ("x86_64", x86_64)):
        print(f"• Python for {arch}")
        py = unpack_python(tarball, res / "runtime" / arch)
        print(f"• executable for {arch}")
        slices.append(compile_launcher(zig, arch, py / "include" / f"python{PY_VERSION}", work / f"Oomi-{arch}"))
        drop_headers(py)
        print(f"• libraries for {arch}")
        install_deps(res / "site-packages" / arch, arch, gui=True)
    copy_oomi(res / "app")
    shutil.copy2(ROOT / "macapp" / "boot.py", res / "boot.py")
    shutil.copy2(ROOT / "oomi" / "assets" / "oomi.icns", res / "oomi.icns")
    write_exec(res / "bin" / "oomi", (ROOT / "macapp" / "oomi-cli.sh").read_text())
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    write_fat(slices, contents / "MacOS" / "Oomi")
    shutil.rmtree(work, ignore_errors=True)
    (contents / "PkgInfo").write_text("APPL????")
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump({
            "CFBundleName": "Oomi",
            "CFBundleDisplayName": "Oomi",
            "CFBundleIdentifier": "dev.oomi.app",
            "CFBundleExecutable": "Oomi",
            "CFBundleIconFile": "oomi",
            "CFBundlePackageType": "APPL",
            "CFBundleSignature": "????",
            "CFBundleVersion": ver,
            "CFBundleShortVersionString": ver,
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleDevelopmentRegion": "en",
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.developer-tools",
            "LSArchitecturePriority": ["arm64", "x86_64"],
            "NSHighResolutionCapable": True,
            "NSSupportsAutomaticGraphicsSwitching": True,
            "NSRequiresAquaSystemAppearance": False,
            # The window talks to oomi's own server on 127.0.0.1.
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            "NSHumanReadableCopyright": "oomi: your own agent, built on Claude.",
        }, f)
    print("• signing")
    sign(app, rcodesign)
    return app


def build_linux_test(linux: Path, out: Path, zig: list) -> Path:
    """Same layout around a Linux Python, so the packaging can be run end to end off a Mac."""
    app = out / "Oomi-linux-test.app"
    shutil.rmtree(app, ignore_errors=True)
    contents = app / "Contents"
    res = contents / "Resources"
    py = unpack_python(linux, res / "runtime" / "x86_64")
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    compile_launcher(zig, "linux", py / "include" / f"python{PY_VERSION}", contents / "MacOS" / "Oomi")
    drop_headers(py)
    install_deps(res / "site-packages" / "x86_64", "linux", gui=False)
    copy_oomi(res / "app")
    shutil.copy2(ROOT / "macapp" / "boot.py", res / "boot.py")
    write_exec(res / "bin" / "oomi", (ROOT / "macapp" / "oomi-cli.sh").read_text())
    return app


def zip_app(app: Path, zip_path: Path) -> None:
    zip_path.unlink(missing_ok=True)
    # -y stores symlinks as links (the Python builds use them); unix permissions are kept, so the
    # executables stay executable when Finder unzips it.
    subprocess.run(["zip", "-q", "-r", "-y", "-X", "-9", str(zip_path), app.name], cwd=app.parent, check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm64", type=Path)
    ap.add_argument("--x86_64", type=Path)
    ap.add_argument("--linux", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--zig", help="path to zig (default: zig on PATH, or the ziglang package)")
    ap.add_argument("--rcodesign", help="path to rcodesign (default: on PATH)")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    zig = find_zig(a.zig)
    if a.linux:
        print(build_linux_test(a.linux, a.out, zig))
        return
    if not (a.arm64 and a.x86_64):
        ap.error("--arm64 and --x86_64 are both needed")
    app = build_mac(a.arm64, a.x86_64, a.out, zig, a.rcodesign)
    z = a.out / f"Oomi-{version()}-mac.zip"
    zip_app(app, z)
    size = sum(p.stat().st_size for p in app.rglob("*") if p.is_file() and not p.is_symlink())
    print(f"built {app} ({size / 1e6:.0f} MB) → {z} ({z.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    os.umask(0o022)
    main()
