"""图片前置增强服务测试。

用合成的「试卷照片」验证各能力是否真的生效（量化指标，不只看不报错）：
1. 一键清晰：纸面变白、文字保持黑、边缘锐度提升
2. 蓝色笔迹去除：蓝像素显著减少且印刷体保留
3. 黑色圈画去除：细长划线/大圈画的连通域被清掉
4. 透视校正 / 手动框选：纸张外背景被裁掉
5. 二值化：高档输出纯黑白
6. 失败与取消：一律回退（ok=False），不抛异常
7. 低配降级：走简化路径并标记 degraded
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from plos.services.image_enhance_service import (
    EnhanceOptions,
    ImageEnhanceService,
)

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)

_SAMPLE_TEXT = [
    "1．下列关于函数单调性的说法正确的是（  ）",
    "A. 增函数导数恒为正    B. 减函数导数恒为负",
    "2．求 f(x)=x²+1 的导函数。",
    "3．若 a>b，则 a²>b²。",
]


def _font(size: int = 26) -> ImageFont.FreeTypeFont:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _make_paper(
    path: Path,
    size=(1200, 900),
    *,
    shading: bool = False,
    blur: bool = False,
    blue_pen: bool = False,
    black_loop: bool = False,
    pen_across_text: bool = False,
    table_frame: bool = False,
    skew: bool = False,
) -> Path:
    """合成一张「试卷照片」：白底黑字，可选阴影 / 模糊 / 笔迹 / 倾斜。"""
    image = Image.new("RGB", size, (252, 252, 250))
    draw = ImageDraw.Draw(image)
    font = _font()
    y = 60
    for line in _SAMPLE_TEXT:
        draw.text((70, y), line, fill=(18, 18, 18), font=font)
        y += 90

    if blue_pen:
        # 蓝色手写：一段波浪线 + 一个勾
        points = [(120 + i * 12, 250 + int(18 * np.sin(i / 2.0))) for i in range(46)]
        draw.line(points, fill=(30, 40, 220), width=6)
        draw.line([(760, 150), (800, 195), (880, 90)], fill=(30, 40, 220), width=6)

    if black_loop:
        # 黑色圈画 + 手写划线：放在正文下方的空白区，避免与文字连成同一连通域
        # （笔迹与文字粘连时无法按连通域区分，另有专门用例覆盖该局限）
        draw.ellipse((60, 560, 1120, 760), outline=(25, 25, 25), width=4)
        widths = (2, 4, 6, 3, 7, 4, 2, 6, 5, 3)
        x, index = 70, 0
        while x < 1120:
            thickness = widths[index % len(widths)]
            baseline = 470 + int(3 * np.sin(index / 1.5))
            draw.line([(x, baseline), (x + 40, baseline)], fill=(25, 25, 25), width=thickness)
            x += 38
            index += 1

    if pen_across_text:
        # 手写笔迹横穿最后一行正文（真实场景：划线压到文字上）
        draw.line([(60, 345), (1130, 351)], fill=(25, 25, 25), width=6)

    if table_frame:
        # 印刷答题框：规则矩形，必须被保留
        draw.rectangle((80, 560, 1120, 780), outline=(25, 25, 25), width=3)

    array = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

    if skew:
        # 把「纸张」贴到灰色桌面上并做透视倾斜，用于验证自动校正
        canvas = np.full((size[1] + 160, size[0] + 160, 3), 120, dtype=np.uint8)
        canvas[80 : 80 + size[1], 80 : 80 + size[0]] = array
        src = np.float32(
            [[80, 80], [80 + size[0], 80], [80 + size[0], 80 + size[1]], [80, 80 + size[1]]]
        )
        dst = np.float32(
            [
                [140, 110],
                [80 + size[0] - 60, 70],
                [80 + size[0] - 20, 80 + size[1] - 40],
                [120, 80 + size[1] - 10],
            ]
        )
        matrix = cv2.getPerspectiveTransform(src, dst)
        array = cv2.warpPerspective(
            canvas, matrix,
            (canvas.shape[1], canvas.shape[0]), borderValue=(120, 120, 120),
        )

    if shading:
        # 左下角阴影：模拟单侧光源导致的不均匀亮度
        height, width = array.shape[:2]
        gradient = np.linspace(0.55, 1.0, width, dtype=np.float32)[None, :]
        gradient = np.repeat(gradient, height, axis=0)[:, :, None]
        array = np.clip(array.astype(np.float32) * gradient, 0, 255).astype(np.uint8)

    if blur:
        array = cv2.GaussianBlur(array, (0, 0), 2.6)

    cv2.imencode(".png", array)[1].tofile(str(path))
    return path


def _gray(path: Path) -> np.ndarray:
    data = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    return cv2.cvtColor(data, cv2.COLOR_BGR2GRAY)


def _blue_pixels(path: Path) -> int:
    data = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    blue, green, red = data[:, :, 0].astype(np.int16), data[:, :, 1], data[:, :, 2]
    mask = (blue - np.maximum(green, red)) > 40
    return int(mask.sum())


def _dark_pixels(path: Path, threshold: int = 110) -> int:
    """印刷体前景像素数（黑字）。"""
    return int((_gray(path) < threshold).sum())


def _sharpness(path: Path) -> float:
    """拉普拉斯方差：越大表示文字边缘越锐利。"""
    return float(cv2.Laplacian(_gray(path), cv2.CV_64F).var())


def _long_thin_components(path: Path) -> int:
    """统计细长连通域数量（长划线 / 圈画的特征）。"""
    gray = _gray(path)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    height, width = gray.shape[:2]
    found = 0
    for index in range(1, count):
        box_w, box_h = int(stats[index, 2]), int(stats[index, 3])
        area = int(stats[index, 4])
        if area < 40:
            continue
        fill = area / float(max(1, box_w * box_h))
        aspect = box_w / float(max(1, box_h))
        if (aspect > 7 and fill < 0.35 and box_h <= height * 0.35) or (
            aspect < 1 / 7 and fill < 0.35 and box_w <= width * 0.35
        ) or (box_w * box_h > 0.12 * height * width and fill < 0.12):
            found += 1
    return found


@pytest.fixture()
def enhance(tmp_path: Path):
    """固定为正常配置 + 独立输出目录，避免受宿主机硬件分级影响。"""
    return ImageEnhanceService(images_dir=tmp_path / "images", light_mode=False)


# ----------------------------------------------------------------------
# 一、一键清晰
# ----------------------------------------------------------------------

def test_auto_clear_whitens_paper_and_keeps_text_dark(enhance, tmp_path):
    """阴影照片经一键清晰后：纸面变白、文字依旧黑（不淡）。"""
    source = _make_paper(tmp_path / "shaded.png", shading=True)
    before = _gray(source)

    result = enhance.enhance(source, EnhanceOptions(auto_clear=True, deskew=False))

    assert result.ok, result.error
    after = _gray(result.output_path)
    assert np.percentile(after, 90) >= np.percentile(before, 90), "纸面应变白"
    assert np.percentile(after, 5) <= np.percentile(before, 5) + 12, "文字不应变淡"
    assert _dark_pixels(result.output_path) > 0


def test_auto_clear_sharpens_blurred_image(enhance, tmp_path):
    """模糊题目经一键清晰后边缘锐度应提升。"""
    source = _make_paper(tmp_path / "blur.png", blur=True)
    result = enhance.enhance(source, EnhanceOptions(auto_clear=True, deskew=False))

    assert result.ok, result.error
    assert _sharpness(result.output_path) > _sharpness(source), "锐化应提升拉普拉斯方差"
    assert "文字锐化" in result.applied


def test_auto_clear_single_image_finishes_quickly(enhance, tmp_path):
    """普通题图（约 1MP）处理应在数秒内完成，避免阻塞导入。"""
    source = _make_paper(tmp_path / "normal.png")
    result = enhance.enhance(source, EnhanceOptions())
    assert result.ok, result.error
    assert result.duration_ms < 8000, f"处理耗时过长：{result.duration_ms}ms"


# ----------------------------------------------------------------------
# 二、笔迹去除
# ----------------------------------------------------------------------

def test_blue_pen_removed_and_printed_text_kept(enhance, tmp_path):
    """蓝色手写被清掉，同时印刷体文字基本保留（以无笔迹的同一张卷子为基线）。"""
    clean = _make_paper(tmp_path / "clean.png")
    source = _make_paper(tmp_path / "blue.png", blue_pen=True, shading=True)
    printed_baseline = _dark_pixels(clean)
    blue_before = _blue_pixels(source)
    assert blue_before > 500, "测试图应包含足够的蓝色笔迹"

    result = enhance.enhance(
        source,
        EnhanceOptions(auto_clear=True, remove_pen=True, pen_color="blue", deskew=False),
    )

    assert result.ok, result.error
    assert "去蓝色笔迹" in result.applied
    blue_after = _blue_pixels(result.output_path)
    assert blue_after < blue_before * 0.2, f"蓝色笔迹应大幅减少：{blue_before} → {blue_after}"

    dark_after = _dark_pixels(result.output_path)
    assert dark_after > printed_baseline * 0.85, (
        f"印刷体被误删：基线 {printed_baseline} → {dark_after}"
    )


def test_black_loop_removed_and_printed_text_kept(enhance, tmp_path):
    """黑色圈画与手写划线被清掉，印刷体保持完好。"""
    clean = _make_paper(tmp_path / "clean.png")
    source = _make_paper(tmp_path / "loop.png", black_loop=True)
    printed_baseline = _dark_pixels(clean)
    thin_before = _long_thin_components(source)
    assert thin_before > 0, "测试图应包含圈画/划线笔迹"

    result = enhance.enhance(
        source,
        EnhanceOptions(auto_clear=False, remove_pen=True, pen_color="black", deskew=False),
    )

    assert result.ok, result.error
    assert "去黑色笔迹" in result.applied
    assert _long_thin_components(result.output_path) < thin_before, "圈画/划线应被清除"

    dark_after = _dark_pixels(result.output_path)
    assert dark_after > printed_baseline * 0.9, (
        f"印刷体被误删：基线 {printed_baseline} → {dark_after}"
    )


def test_stroke_touching_text_does_not_damage_other_lines(enhance, tmp_path):
    """已知局限的守卫：笔迹压在某一行文字上时，只可能影响与它粘连的那一行，
    其余正文必须完全不受影响（说明我们的判定不会扩散误删）。"""
    clean = _make_paper(tmp_path / "clean.png")
    source = _make_paper(tmp_path / "pen_on_text.png", pen_across_text=True)

    result = enhance.enhance(
        source,
        EnhanceOptions(auto_clear=False, remove_pen=True, pen_color="black", deskew=False),
    )

    assert result.ok, result.error
    # 前三行正文（y<320）没有被任何笔迹压到，必须与无笔迹基线一致
    upper = slice(0, 320)
    before = int((_gray(clean)[upper] < 110).sum())
    after = int((_gray(result.output_path)[upper] < 110).sum())
    assert after >= before * 0.98, f"远离笔迹的正文被误删：{before} → {after}"


def test_printed_table_frame_is_preserved(enhance, tmp_path):
    """印刷矩形框（答题框 / 表格线）必须保留，不能被当成手写圈画删掉。"""
    source = _make_paper(tmp_path / "table.png", table_frame=True)

    result = enhance.enhance(
        source,
        EnhanceOptions(auto_clear=False, remove_pen=True, pen_color="black", deskew=False),
    )

    assert result.ok, result.error
    # 框线像素应基本无损：统计 4 条边所在行的暗像素
    gray = _gray(result.output_path)
    top_edge = gray[560:566, 80:1120]
    assert (top_edge < 120).sum() > 800, "矩形框上边线被误删"


def test_line_jitter_discriminates_handwriting_from_printed_rule():
    """厚度抖动是区分手写划线与印刷线的关键判据。"""
    uniform = np.zeros((10, 200), dtype=np.uint8)
    uniform[3:6, :] = 1  # 厚度恒定为 3 的印刷线

    shaky = np.zeros((10, 200), dtype=np.uint8)
    for x in range(200):
        thickness = 2 + (x // 10) % 5  # 厚度反复变化的手写线
        shaky[2 : 2 + thickness, x] = 1

    quiet = ImageEnhanceService._line_jitter(uniform.astype(bool), horizontal=True)
    noisy = ImageEnhanceService._line_jitter(shaky.astype(bool), horizontal=True)

    assert quiet < 0.1, f"印刷线抖动应接近 0，实际 {quiet}"
    assert noisy > 0.3, f"手写线抖动应偏高，实际 {noisy}"


def test_pen_removal_disabled_by_default(enhance, tmp_path):
    """默认不开启笔迹去除：蓝色笔迹应原样保留。"""
    source = _make_paper(tmp_path / "blue_keep.png", blue_pen=True)
    result = enhance.enhance(source, EnhanceOptions(auto_clear=True, deskew=False))

    assert result.ok, result.error
    assert "去蓝色笔迹" not in result.applied
    assert _blue_pixels(result.output_path) > _blue_pixels(source) * 0.6


# ----------------------------------------------------------------------
# 三、透视校正与裁切
# ----------------------------------------------------------------------

def test_perspective_correction_crops_desk_background(enhance, tmp_path):
    """倾斜拍摄时自动检测纸张边缘并裁掉桌面背景。"""
    source = _make_paper(tmp_path / "skew.png", skew=True)
    before = _gray(source)

    result = enhance.enhance(source, EnhanceOptions(auto_clear=True, deskew=True))

    assert result.ok, result.error
    assert result.applied, "倾斜图应至少应用一步几何校正"
    after = _gray(result.output_path)
    assert np.percentile(after, 50) > np.percentile(before, 50), "裁掉暗桌面后中位亮度应提升"


def test_manual_quad_crops_selected_region(enhance, tmp_path):
    """手动框选区域应被裁出来，输出尺寸与框选矩形一致。"""
    source = _make_paper(tmp_path / "manual.png")
    quad = [[200, 150], [900, 160], [890, 700], [210, 690]]

    result = enhance.enhance(
        source,
        EnhanceOptions(auto_clear=False, deskew=True, manual_quad=quad),
    )

    assert result.ok, result.error
    assert "手动裁切" in result.applied
    height, width = _gray(result.output_path).shape[:2]
    assert abs(width - 690) <= 20 and abs(height - 540) <= 20, f"裁切尺寸异常：{width}x{height}"


def test_geometry_disabled_keeps_original_size(enhance, tmp_path):
    """关闭几何校正且不裁切时，输出尺寸应与原图一致。"""
    source = _make_paper(tmp_path / "plain.png")
    result = enhance.enhance(
        source, EnhanceOptions(auto_clear=True, deskew=False)
    )
    assert result.ok, result.error
    height, width = _gray(result.output_path).shape[:2]
    assert (width, height) == (1200, 900)


# ----------------------------------------------------------------------
# 四、二值化
# ----------------------------------------------------------------------

def test_binarize_high_outputs_pure_black_white(enhance, tmp_path):
    source = _make_paper(tmp_path / "bin.png")
    result = enhance.enhance(
        source, EnhanceOptions(auto_clear=True, binarize=True, strength="high", deskew=False)
    )
    assert result.ok, result.error
    levels = set(np.unique(_gray(result.output_path)).tolist())
    assert levels <= {0, 255}, f"纯二值化输出应只有黑白：{sorted(levels)[:8]}…"
    assert "纯二值化" in result.applied


def test_binarize_mid_keeps_gray_levels(enhance, tmp_path):
    """中档是「白底黑字」的高对比灰阶，用于保护图表与图片层次。"""
    source = _make_paper(tmp_path / "bin_mid.png", shading=True)
    result = enhance.enhance(
        source, EnhanceOptions(auto_clear=True, binarize=True, strength="mid", deskew=False)
    )
    assert result.ok, result.error
    assert len(np.unique(_gray(result.output_path))) > 2
    assert "白底黑字" in result.applied


# ----------------------------------------------------------------------
# 五、失败回退与取消
# ----------------------------------------------------------------------

def test_missing_file_returns_failure_without_raising(enhance, tmp_path):
    result = enhance.enhance(tmp_path / "not_exists.png", EnhanceOptions())
    assert not result.ok
    assert result.error
    assert result.output_path is None


def test_corrupted_file_returns_failure(enhance, tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image at all")
    result = enhance.enhance(broken, EnhanceOptions())
    assert not result.ok
    assert result.error


def test_cancel_returns_failure(enhance, tmp_path):
    source = _make_paper(tmp_path / "cancel.png")
    cancel = threading.Event()
    cancel.set()
    result = enhance.enhance(source, EnhanceOptions(), cancel_event=cancel)
    assert not result.ok
    assert "取消" in result.error


def test_progress_callback_reports_stages(enhance, tmp_path):
    source = _make_paper(tmp_path / "progress.png")
    seen = []
    result = enhance.enhance(
        source, EnhanceOptions(), progress=lambda pct, msg: seen.append((pct, msg))
    )
    assert result.ok, result.error
    assert seen and seen[-1][0] == 100


# ----------------------------------------------------------------------
# 六、低配降级与输出位置
# ----------------------------------------------------------------------

def test_low_spec_mode_degrades_and_marks_result(tmp_path):
    source = _make_paper(tmp_path / "low.png")
    service = ImageEnhanceService(images_dir=tmp_path / "images", light_mode=True)
    result = service.enhance(source, EnhanceOptions(auto_clear=True, deskew=False))

    assert result.ok, result.error
    assert result.degraded is True
    assert "低配简化" in result.applied


def test_output_saved_into_images_dir(enhance, tmp_path):
    source = _make_paper(tmp_path / "out.png")
    result = enhance.enhance(source, EnhanceOptions(auto_clear=True, deskew=False))

    assert result.ok, result.error
    assert result.output_path.parent == tmp_path / "images"
    assert result.output_path.stat().st_size > 0
    assert "enhanced_" in result.output_path.name


def test_large_image_downscaled_then_restored(enhance, tmp_path):
    """超大图先降采样处理（长边 1600），输出还原到尺寸上限 2048 且保持宽高比。"""
    source = _make_paper(tmp_path / "big.png", size=(3000, 2200))
    result = enhance.enhance(source, EnhanceOptions(auto_clear=True, deskew=False))

    assert result.ok, result.error
    height, width = _gray(result.output_path).shape[:2]
    assert max(width, height) == 2048, "输出长边应还原到上限 2048，而不是停在 1600 工作尺寸"
    assert abs(width / height - 3000 / 2200) < 0.02, "还原时宽高比必须保持"


def test_options_normalized_rejects_bad_strength():
    opts = EnhanceOptions(strength="crazy", pen_color="pink").normalized()
    assert opts.strength == "mid"
    assert opts.pen_color == "auto"


# ----------------------------------------------------------------------
# 七、预处理面板（离屏渲染，qapp 由 conftest 提供并全程保活）
# ----------------------------------------------------------------------

def _dialog(qapp, enhance, tmp_path, name: str = "dlg.png"):
    from plos.ui.image_enhance_dialog import ImageEnhanceDialog

    source = _make_paper(tmp_path / name)
    return source, ImageEnhanceDialog(source, service=enhance)


def _dispose_dialog(dialog, qapp) -> None:
    """停掉弹窗定时器并等后台任务收尾后再销毁，避免回调打到已释放对象。"""
    for attr in ("_autostart_timer", "_reprocess_timer"):
        timer = getattr(dialog, attr, None)
        if timer is not None:
            timer.stop()
    _drain_workers(qapp)
    dialog.deleteLater()
    qapp.processEvents()


def test_dialog_defaults_to_auto_clear(qapp, enhance, tmp_path):
    """打开面板即默认「一键清晰 + 透视校正」，笔迹去除默认关闭。"""
    source, dialog = _dialog(qapp, enhance, tmp_path)
    try:
        opts = dialog.options()
        assert opts.auto_clear is True
        assert opts.deskew is True
        assert opts.remove_pen is False
        assert opts.binarize is False
        assert opts.strength == "mid", "强度默认应为中档"
        assert dialog._manual_quad is None
    finally:
        _dispose_dialog(dialog, qapp)


def test_dialog_option_changes_reach_service_options(qapp, enhance, tmp_path):
    source, dialog = _dialog(qapp, enhance, tmp_path)
    try:
        dialog._pen_check.setChecked(True)
        dialog._pen_color_combo.setCurrentIndex(
            dialog._pen_color_combo.findData("blue")
        )
        dialog._strength_slider.setValue(2)
        dialog._binary_check.setChecked(True)

        opts = dialog.options()
        assert opts.remove_pen is True
        assert opts.pen_color == "blue"
        assert opts.strength == "high"
        assert opts.binarize is True
        assert dialog._strength_label.text() == "高"
    finally:
        _dispose_dialog(dialog, qapp)


def test_dialog_shows_result_and_confirms_processed_path(qapp, enhance, tmp_path):
    """处理成功后：状态显示耗时与步骤，确认时回传增强图路径。"""
    source, dialog = _dialog(qapp, enhance, tmp_path)
    result = enhance.enhance(source, EnhanceOptions(auto_clear=True, deskew=False))
    assert result.ok, result.error

    confirmed = []
    dialog.enhance_confirmed.connect(lambda path, steps: confirmed.append((path, steps)))
    try:
        dialog._on_result(result)

        assert dialog._result is not None
        assert "用时" in dialog._status_label.text()
        assert "处理后" in dialog._right_caption.text()

        dialog._confirm()
        assert len(confirmed) == 1
        assert confirmed[0][0] == str(result.output_path)
        assert "文字锐化" in confirmed[0][1]
    finally:
        _dispose_dialog(dialog, qapp)


def test_dialog_confirm_without_result_falls_back_to_original(qapp, enhance, tmp_path):
    """没有可用结果时点「确认使用」等同跳过，不能让用户卡住。"""
    source, dialog = _dialog(qapp, enhance, tmp_path)
    skipped = []
    dialog.enhance_skipped.connect(skipped.append)
    try:
        dialog._handle_failure("磁盘空间不足")
        dialog._confirm()
        assert skipped == [str(source)]
    finally:
        _dispose_dialog(dialog, qapp)


def test_dialog_failure_reverts_to_original_and_notifies(qapp, enhance, tmp_path):
    source, dialog = _dialog(qapp, enhance, tmp_path)
    failed = []
    dialog.enhance_failed.connect(failed.append)
    try:
        dialog._handle_failure("无法解析图片内容")

        assert dialog._result is None
        assert failed == ["无法解析图片内容"]
        assert "失败" in dialog._right_caption.text()
        assert "已使用原图" in dialog._status_label.text()
    finally:
        _dispose_dialog(dialog, qapp)


def test_dialog_skip_emits_original_path(qapp, enhance, tmp_path):
    source, dialog = _dialog(qapp, enhance, tmp_path)
    skipped = []
    dialog.enhance_skipped.connect(skipped.append)
    try:
        dialog._skip()
        assert skipped == [str(source)]
    finally:
        _dispose_dialog(dialog, qapp)


def test_dialog_cancel_emits_cancelled(qapp, enhance, tmp_path):
    source, dialog = _dialog(qapp, enhance, tmp_path)
    cancelled = []
    dialog.enhance_cancelled.connect(lambda: cancelled.append(True))
    try:
        dialog._cancel()
        assert cancelled == [True]
    finally:
        _dispose_dialog(dialog, qapp)


def test_load_preview_pixmap_tolerates_broken_file(qapp, tmp_path):
    from plos.ui.image_enhance_dialog import load_preview_pixmap

    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    assert load_preview_pixmap(broken).isNull()
    assert load_preview_pixmap(tmp_path / "missing.png").isNull()


def test_region_canvas_maps_display_coords_back_to_source(qapp, enhance, tmp_path):
    """手动框选必须换算回原图坐标，否则裁切区域会错位。"""
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QColor, QPixmap

    from plos.ui.image_enhance_dialog import _SelectionCanvas

    pixmap = QPixmap(400, 300)
    pixmap.fill(QColor("#FFFFFF"))
    canvas = _SelectionCanvas(pixmap)
    try:
        canvas.resize(800, 600)
        canvas._update_geometry()
        scale = canvas._display_scale
        origin_x, origin_y = canvas._origin
        # 在显示坐标里画一个「原图 60,40 起、100x80」的框
        canvas._selection = QRect(
            int(origin_x + 60 * scale), int(origin_y + 40 * scale),
            int(100 * scale), int(80 * scale),
        )
        box = canvas.selection_in_source()
        assert box is not None
        x, y, width, height = box
        assert abs(x - 60) <= 2, x
        assert abs(y - 40) <= 2, y
        assert abs(width - 100) <= 3, width
        assert abs(height - 80) <= 3, height
    finally:
        canvas.deleteLater()


def test_region_canvas_ignores_tiny_selection(qapp):
    """误点的极小选区不应被当成有效框选。"""
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QColor, QPixmap

    from plos.ui.image_enhance_dialog import _SelectionCanvas

    pixmap = QPixmap(400, 300)
    pixmap.fill(QColor("#FFFFFF"))
    canvas = _SelectionCanvas(pixmap)
    try:
        canvas.resize(800, 600)
        canvas._update_geometry()
        canvas._selection = QRect(10, 10, 4, 4)
        assert canvas.selection_in_source() is None
    finally:
        canvas.deleteLater()


class _FakeOCRService:
    """面板初始化只需这几个探测方法，无需真实模型。"""

    def is_image_service_available(self) -> bool:
        return False

    def get_status_message(self) -> str:
        return "未检测到视觉模型"


def _drain_workers(qapp, timeout_s: float = 5.0) -> None:
    """等线程池任务跑完再销毁面板。

    OCRPanel 初始化会把 VL 探测丢进线程池；任务未结束就 deleteLater，
    排队的信号槽会在面板 C++ 对象释放后被调用，进而崩溃。
    """
    from plos.ui.workers import ThreadPool

    deadline = time.time() + timeout_s
    while ThreadPool._active_workers and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def test_ocr_panel_degradation_message_renders(qapp):
    """VL 不可用时，状态槽必须能安全渲染出提示。

    回归：MarkdownBrowser 没有 setHtml 方法，槽里抛出的 AttributeError
    会让 PyQt 直接 abort 整个进程（表现为测试整体崩掉而不是用例失败）。
    """
    from plos.ui.ocr_panel import OCRPanel

    panel = OCRPanel(_FakeOCRService())
    try:
        panel._apply_image_service_status((False, "未检测到视觉模型"))
        assert "未检测到视觉模型" in panel.result_browser.browser.toPlainText()
        assert not panel.btn_ocr.isEnabled()
    finally:
        _drain_workers(qapp)
        panel.deleteLater()
        qapp.processEvents()


def test_ocr_panel_probe_round_trip_survives_event_loop(qapp):
    """探测线程 → 主线程槽的完整链路跑完事件循环不应崩溃。"""
    from plos.ui.ocr_panel import OCRPanel

    panel = OCRPanel(_FakeOCRService())
    try:
        _drain_workers(qapp)
        assert not panel.btn_ocr.isEnabled()
        assert panel.result_browser.browser.toPlainText()
    finally:
        panel.deleteLater()
        qapp.processEvents()


def test_ocr_panel_solve_result_renders_to_browser(qapp):
    """拍照搜题的解答结果同样走 MarkdownBrowser，必须渲染成功。"""
    from plos.ui.ocr_panel import OCRPanel

    panel = OCRPanel(_FakeOCRService())
    try:
        panel._on_solve_result("第一步：求导\n第二步：令导数为零")
        assert "解题思路与步骤" in panel.result_browser.browser.toPlainText()
    finally:
        _drain_workers(qapp)
        panel.deleteLater()
        qapp.processEvents()


def test_single_ocr_result_auto_wraps_math(qapp, monkeypatch):
    """单张识别结果落地即自动补公式标记，不必再手动点一次「一键格式化」。"""
    from plos.ui import ocr_panel as ocr_panel_module

    OCRPanel = ocr_panel_module.OCRPanel
    # 结果槽里会弹成功提示；无 Toast 宿主时会退化成模态框，测试中必须拦掉
    monkeypatch.setattr(ocr_panel_module, "show_success", lambda *a, **k: None)

    panel = OCRPanel(_FakeOCRService())
    try:
        panel._on_ocr_result("由 y=2x+1 可知答案")
        text = panel.ocr_result_edit.toPlainText()
        assert "由" in text and "可知答案" in text
        assert "\\(y=2x+1\\)" in text, "算式应被自动补上公式标记"
    finally:
        _drain_workers(qapp)
        panel.deleteLater()
        qapp.processEvents()


def test_auto_format_ocr_keeps_plain_text_stable():
    """纯文字不应被强行加标记（避免普通文本被误当公式）。"""
    from plos.ui.ocr_panel import OCRPanel

    plain = "今天天气不错，我们去图书馆看书。"
    assert OCRPanel._auto_format_ocr(plain) == plain


def test_ocr_panel_routes_import_through_preprocess_switch(qapp, tmp_path):
    """开关开启时走预处理面板；关闭时直接加载原图。"""
    from plos.ui.ocr_panel import OCRPanel

    panel = OCRPanel(_FakeOCRService())
    source = _make_paper(tmp_path / "panel.png")
    opened = []
    loaded = []
    panel._open_enhance_dialog = opened.append          # type: ignore[method-assign]
    panel._load_image = loaded.append                   # type: ignore[method-assign]
    try:
        assert panel.preprocess_check.isChecked(), "预处理开关默认开启"

        panel.import_image(source)
        assert opened == [source] and loaded == []

        panel.preprocess_check.setChecked(False)
        panel.import_image(source)
        assert loaded == [source] and len(opened) == 1
    finally:
        _drain_workers(qapp)
        panel.deleteLater()
        qapp.processEvents()


def test_ocr_panel_records_enhanced_mapping(qapp, tmp_path):
    """确认使用后必须记住「原图 → 增强图」，供入库时引用。"""
    from plos.ui.ocr_panel import OCRPanel

    panel = OCRPanel(_FakeOCRService())
    source = _make_paper(tmp_path / "map.png")
    loaded = []
    panel._load_image = loaded.append  # type: ignore[method-assign]

    class _StubDialog:
        image_path = source

    try:
        panel._enhance_dialog = _StubDialog()
        panel._on_enhance_confirmed(str(tmp_path / "enhanced_x.png"), ["背景净化", "去蓝色笔迹"])

        assert panel._enhanced_for(str(source)) == str(tmp_path / "enhanced_x.png")
        assert loaded and str(loaded[0]).endswith("enhanced_x.png")
        # 未处理过的图片原样返回
        assert panel._enhanced_for("other.png") == "other.png"
    finally:
        panel._enhance_dialog = None
        _drain_workers(qapp)
        panel.deleteLater()
        qapp.processEvents()
