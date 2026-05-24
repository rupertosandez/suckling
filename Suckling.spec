# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH)
assets = [
    (str(project_root / "assets" / "logo.png"), "assets"),
    (str(project_root / "assets" / "tray_icon.ico"), "assets"),
    (str(project_root / "assets" / "tray_icon_error.ico"), "assets"),
    (str(project_root / "assets" / "tray_icon_update.ico"), "assets"),
]

a = Analysis(
    ["launcher.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=assets,
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Suckling",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "assets" / "tray_icon.ico"),
)
