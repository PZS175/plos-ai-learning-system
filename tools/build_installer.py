"""一键构建 Windows 安装目录（PyInstaller）。

用法：
    python tools/build_installer.py            # 构建并自检
    python tools/build_installer.py --no-build # 仅自检已有产物

前置：pip install pyinstaller
产物：dist/PLOS AI/PLOS AI.exe（目录模式）
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "PLOS AI"


def build() -> int:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(ROOT / "installer" / "plos_ai.spec"),
        "--noconfirm",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build"),
    ]
    print("[build]", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


def cleanup() -> None:
    """清理自检运行产生的数据 / 配置 / 日志，保持发布产物干净。"""
    for name in ("data", "config", "logs"):
        target = DIST / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            print(f"[OK] 已清理运行产物: {name}")


def smoke_check() -> int:
    """产物自检：关键文件存在 + 插件资源随包 + 打包内 CLI 可运行。"""
    exe = DIST / "PLOS AI.exe"
    if not exe.exists():
        print("[FAIL] 缺少产物:", exe)
        return 1
    print("[OK] exe 存在:", exe)

    schema = DIST / "_internal" / "plos" / "db" / "schema.sql"
    if not schema.exists():
        print("[FAIL] 缺少 schema.sql 数据文件:", schema)
        return 1
    print("[OK] schema.sql 已随包")

    # 插件是运行时动态加载的，PyInstaller 静态分析覆盖不到，必须随包
    for relative in ("builtin", "store"):
        plugin_dir = DIST / "_internal" / "plos" / "plugins" / relative
        if not plugin_dir.is_dir():
            print("[FAIL] 缺少插件目录:", plugin_dir)
            return 1
        count = len(list(plugin_dir.glob("*/plugin.json")))
        if count == 0:
            print("[FAIL] 插件目录中没有插件:", plugin_dir)
            return 1
        print(f"[OK] 插件目录 {relative} 含 {count} 个插件")

    import os
    import re
    import tempfile

    marker = Path(tempfile.gettempdir()) / "plos_smoke_ok.txt"
    marker.unlink(missing_ok=True)
    env = dict(os.environ, PLOS_SMOKE_TEST="1")
    try:
        subprocess.run([str(exe)], env=env, cwd=str(DIST), timeout=180)
    except subprocess.TimeoutExpired:
        print("[FAIL] 冒烟自检超时：重量级导入可能缺失或死锁")
        return 1
    if not marker.exists():
        print("[FAIL] 冒烟标记未写入：导入链在打包环境断裂（查 missing hidden imports）")
        return 1

    text = marker.read_text(encoding="utf-8")
    print("[OK] 打包环境导入链完整，版本:", text)
    if "plugin_scan_failed" in text:
        print("[FAIL] 打包内插件扫描失败:", text)
        return 1
    # 试卷导出依赖（python-docx / PyMuPDF）必须在打包环境可实际出文档
    if "export_failed=" in text:
        print("[FAIL] 打包内试卷导出失败:", text)
        return 1
    if "export=ok" not in text:
        print("[FAIL] 自检未返回导出结果:", text)
        return 1
    print("[OK] 打包内试卷导出（Word/PDF）可用")
    # 图片增强依赖（OpenCV / NumPy / Pillow）必须在打包环境可实际出图
    if "enhance_failed=" in text:
        print("[FAIL] 打包内图片增强失败:", text)
        return 1
    if "enhance=ok" not in text:
        print("[FAIL] 自检未返回图片增强结果:", text)
        return 1
    print("[OK] 打包内图片增强（OpenCV）可用")
    # 全局公式渲染器必须在打包环境可用（且不依赖被排除的 matplotlib）
    if "mathhtml_failed=" in text:
        print("[FAIL] 打包内公式渲染失败:", text)
        return 1
    if "mathhtml=ok" not in text:
        print("[FAIL] 自检未返回公式渲染结果:", text)
        return 1
    print("[OK] 打包内 LaTeX 公式渲染可用")
    matched = re.search(r"builtin=(\d+)", text)
    if matched is None:
        print("[WARN] 自检未返回插件数量，跳过插件计数校验")
    elif int(matched.group(1)) < 14:
        print("[FAIL] 打包内发现的内置插件数量异常:", text)
        return 1
    else:
        print("[OK] 打包内插件资源可用：", text)
    return 0


def main() -> int:
    if "--no-build" not in sys.argv:
        rc = build()
        if rc != 0:
            print("[FAIL] PyInstaller 构建失败")
            return rc
    rc = smoke_check()
    if rc == 0:
        cleanup()
    return rc


if __name__ == "__main__":
    sys.exit(main())
