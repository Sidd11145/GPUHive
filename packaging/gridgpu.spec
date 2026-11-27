# PyInstaller spec: freezes the gridgpu CLI into one standalone binary.
# Build from the repo root:  pyinstaller packaging/gridgpu.spec  ->  dist/gridgpu[.exe]
import os

project_root = os.path.abspath(os.path.join(os.getcwd()))
entry = os.path.join(project_root, "gridgpu", "__main__.py")

a = Analysis(
    [entry],
    pathex=[project_root],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="gridgpu",
    console=True,
    onefile=True,
    strip=False,
    upx=False,
)
