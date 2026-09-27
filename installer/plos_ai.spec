# -*- mode: python ; coding: utf-8 -*-
"""PLOS AI 打包配置（PyInstaller）。

构建：pip install pyinstaller && pyinstaller installer/plos_ai.spec --noconfirm
产物：dist/PLOS AI/PLOS AI.exe（目录模式，首次启动最快；体积换启动速度）

要点：
- PaddleOCR 相关已在服务层移除（纯 VL 实现），无需 paddle hook
- QtWebEngine（教材导入）以目录模式自带运行时
- 数据文件：schema.sql 必须随包；chromadb/pypdf 依赖较重，按需保留
- 插件目录（builtin / store）是运行时动态加载的，PyInstaller 静态分析不到，
  必须作为数据文件随包，否则打包后插件全部消失
- 插件内部用到的 plos.plugins.expr / floating 不在主程序导入链上，
  需显式加入 hiddenimports
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).parent
SRC = PROJECT_ROOT / "src"

block_cipher = None

hiddenimports = [
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtMultimedia",
    "faster_whisper",
    "chromadb",
    "pypdf",
    "fitz",
    # 试卷导出：PDF 渲染走 PyMuPDF（同时保留 fitz 兼容别名）
    "pymupdf",
    # 错题本试卷导出：Word 渲染
    "docx",
    # 插件共享模块（仅被动态加载的插件 import）
    "plos.plugins.expr",
    "plos.plugins.floating",
]

# 运行时不需要的重型可选依赖（存在也排除，缩体积）
excludes = [
    "matplotlib",
    "tkinter",
    "IPython",
    "jupyter",
]

_ICON = PROJECT_ROOT / "installer" / "app.ico"
datas = [
    (str(SRC / "plos" / "db" / "schema.sql"), "plos/db"),
    # 内置插件与插件库（动态加载，必须随包分发）
    (str(SRC / "plos" / "plugins" / "builtin"), "plos/plugins/builtin"),
    (str(SRC / "plos" / "plugins" / "store"), "plos/plugins/store"),
]
if _ICON.exists():
    datas.append((str(_ICON), "installer"))

a = Analysis(
    [str(SRC / "plos" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PLOS AI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(PROJECT_ROOT / "installer" / "app.ico") if (PROJECT_ROOT / "installer" / "app.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PLOS AI",
)
