"""python -m plos 启动入口。"""

from __future__ import annotations

import sys


def main() -> int:
    import os
    import tempfile
    from pathlib import Path

    # 打包冒烟自检：验证全部重量级导入在目标环境可用后立即退出
    if os.environ.get("PLOS_SMOKE_TEST") == "1":
        import plos.ui.app  # noqa: F401  触发完整导入链
        from plos import __version__

        # 顺便验证插件资源确实随包（插件是运行时动态加载的，静态分析覆盖不到）
        detail = ""
        try:
            from plos.plugins import PluginManager

            manager = PluginManager()
            discovered = manager.discover()
            builtin = sum(1 for info in discovered if info.builtin)
            store = len(manager.list_store_plugins())
            detail = f" builtin={builtin} store={store}"
        except Exception as exc:  # pragma: no cover - 自检路径
            detail = f" plugin_scan_failed={type(exc).__name__}:{exc}"

        # 验证试卷导出依赖（python-docx / PyMuPDF）在打包环境里真的能出文档
        detail += _probe_paper_export(tempfile, Path)

        # 验证图片增强依赖（OpenCV / NumPy / Pillow）在打包环境里真的能出图
        detail += _probe_image_enhance(tempfile, Path)

        # 验证全局公式渲染器在打包环境可用（不依赖 matplotlib）
        detail += _probe_math_render()

        Path(tempfile.gettempdir(), "plos_smoke_ok.txt").write_text(
            __version__ + detail, encoding="utf-8"
        )
        return 0

    from plos.ui.app import main as app_main

    return app_main()


def _probe_math_render() -> str:
    """自检用：验证 LaTeX 混排渲染器在打包环境可用且能容错。"""
    try:
        from plos.utils.math_latex import (
            MATH_HREF_SCHEME,
            latex_to_html,
            render_body,
        )

        # 正常公式：行内 + 独立，都要生成公式锚点与上下结构分式
        body = render_body("解 \\(\\frac{7}{18}\\) 得\n\\[x^2+1=5\\]", "#000", "#000")
        if MATH_HREF_SCHEME not in body or "mathf" not in body:
            raise RuntimeError("公式锚点未生成")
        if "table" not in latex_to_html("\\frac{1}{2}", block=True):
            raise RuntimeError("独立公式未渲染成上下结构")
        # 容错：错误公式必须降级为可读文本而不是空白
        broken = latex_to_html("\\frac{1}{2")
        if not broken.strip():
            raise RuntimeError("错误公式未降级")
        return " mathhtml=ok"
    except Exception as exc:  # pragma: no cover - 自检路径
        return f" mathhtml_failed={type(exc).__name__}:{exc}"


def _probe_image_enhance(tempfile, Path) -> str:
    """自检用：在临时目录真跑一遍图片增强（含二值化与透视校正）。

    只验证依赖与随包资源完整，结果写入冒烟标记供构建脚本核对。
    """
    try:
        import cv2
        import numpy as np

        from plos.services.image_enhance_service import (
            EnhanceOptions,
            ImageEnhanceService,
        )

        probe_dir = Path(tempfile.gettempdir()) / "plos_smoke_enhance"
        probe_dir.mkdir(parents=True, exist_ok=True)

        # 造一张「黑字白纸」小图：白底 + 几条黑色横线
        canvas = np.full((240, 360, 3), 248, dtype=np.uint8)
        for row in range(30, 220, 40):
            cv2.line(canvas, (30, row), (320, row), (20, 20, 20), 3)
        source = probe_dir / "smoke_source.png"
        cv2.imencode(".png", canvas)[1].tofile(str(source))

        service = ImageEnhanceService(images_dir=probe_dir, light_mode=False)
        result = service.enhance(
            source,
            EnhanceOptions(auto_clear=True, deskew=True, binarize=True, strength="mid"),
        )
        if not result.ok:
            raise RuntimeError(result.error or "增强未产出结果")
        if result.output_path.stat().st_size == 0:
            raise RuntimeError("增强结果为空文件")
        return " enhance=ok"
    except Exception as exc:  # pragma: no cover - 自检路径
        return f" enhance_failed={type(exc).__name__}:{exc}"


def _probe_paper_export(tempfile, Path) -> str:
    """自检用：在临时目录真跑一遍 Word / PDF 试卷导出。

    只验证依赖与数据文件随包完整，结果写入冒烟标记供构建脚本核对。
    """
    try:
        from plos.db import Database
        from plos.services import ErrorPaperService

        probe_dir = Path(tempfile.gettempdir()) / "plos_smoke_export"
        probe_dir.mkdir(parents=True, exist_ok=True)
        db = Database(db_path=probe_dir / "smoke.db")
        try:
            service = ErrorPaperService(db=db)
            errors = [
                {
                    "id": 1,
                    "question": "1+1=______。",
                    "answer": "2",
                    "analysis": "基础运算。",
                    "question_type": "填空题",
                    "knowledge_points": "[]",
                    "knowledge_point": "运算",
                    "image_path": None,
                }
            ]
            for fmt, suffix in (("docx", ".docx"), ("pdf", ".pdf")):
                out = service.export(errors, probe_dir / f"smoke{suffix}", fmt=fmt)
                if not out.exists() or out.stat().st_size == 0:
                    raise RuntimeError(f"{fmt} 产物为空")
        finally:
            db.close()
        return " export=ok"
    except Exception as exc:  # pragma: no cover - 自检路径
        return f" export_failed={type(exc).__name__}:{exc}"


if __name__ == "__main__":
    sys.exit(main())
