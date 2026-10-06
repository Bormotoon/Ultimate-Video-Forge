# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the Studio CLI."""

from pathlib import Path

root = Path(SPEC).resolve().parents[2]
resource_root = root / "studio" / "resources"

a = Analysis(
    [str(root / "studio" / "cli" / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "studio" / "gui" / "theme.qss"), "studio/gui"),
        (str(resource_root / "prompts"), "studio/resources/prompts"),
        (str(resource_root / "fonts"), "studio/resources/fonts"),
        (str(resource_root / "subtitle_editor"), "studio/resources/subtitle_editor"),
    ],
    hiddenimports=[
        "studio.resources",
        "studio.stages.worker",
        "yaml",
        "platformdirs",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
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
    name="studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
