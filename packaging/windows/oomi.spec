# PyInstaller spec for Oomi.exe. Build on Windows from the repo root, with oomi installed (`pip install .`):
#     pyinstaller packaging/windows/oomi.spec --noconfirm
# The result is dist/Oomi/ (Oomi.exe and its files); .github/workflows/windows.yml zips it for download.
import os

from PyInstaller.utils.hooks import collect_submodules

HERE = SPECPATH  # noqa: F821  (set by PyInstaller)
ROOT = os.path.dirname(os.path.dirname(HERE))
ICON = os.path.join(HERE, "oomi.ico")

# Everything in the package that isn't code: the window, the ghosts and docks, the certified skills
datas = []
for folder, dirs, files in os.walk(os.path.join(ROOT, "oomi")):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    datas += [(os.path.join(folder, f), os.path.relpath(folder, ROOT)) for f in files if not f.endswith((".py", ".pyc"))]

a = Analysis(  # noqa: F821
    [os.path.join(HERE, "oomi_app.py")],
    pathex=[ROOT],  # build from this checkout
    datas=datas,
    # imported inside functions; dns is what mongodb+srv:// connection strings need
    hiddenimports=collect_submodules("oomi") + collect_submodules("dns"),
    excludes=["webview", "tkinter"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    # UTF-8 mode: oomi's files are UTF-8, and Windows Python otherwise uses the old local code page
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="Oomi",
    icon=ICON if os.path.exists(ICON) else None,
    console=True,  # the window shows the address and "Press Ctrl-C to quit"; closing it quits oomi
)
coll = COLLECT(exe, a.binaries, a.datas, name="Oomi")  # noqa: F821
