"""图片前置增强服务（OCR / 错题图片导入前的预处理）。

纯 CPU 的轻量实现，只依赖 OpenCV + NumPy + Pillow，不引入任何预训练模型：
- 一键清晰：背景净化（消除阴影/不均匀光照）+ 对比度增强 + 保边降噪 + 文字边缘锐化
- 笔迹去除：蓝色笔迹按颜色阈值分离直接填充；黑色手写/圈画按连通域几何特征保守过滤
- 透视校正与裁切：自动检测纸张四边形做透视变换，检测不到时退化为小角度纠偏；支持手动框选
- 二值化：白底黑字；中低档保留灰阶层次以保护图表 / 公式 / 图片结构

性能约束（普通题截图 1~3 秒）：
- 处理在降采样后的工作尺寸上进行，输出时再还原分辨率
- 背景估计在 1/4 缩略图上做形态学运算，避免大核在全分辨率上卷积
- 不使用 fastNlMeansDenoising 等重型慢速算子

安全约束：
- 任何一步失败都不抛出到调用方之外，由上层回退原图（本模块内部按步 try/except 降级）
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence

import cv2
import numpy as np
from PIL import Image

from ..core.constants import MAX_IMAGE_DIMENSION
from ..utils.logger import get_logger
from ..utils.paths import get_images_dir

logger = get_logger("services.image_enhance")

#: 工作尺寸长边上限（超出则先降采样处理，降低 CPU 开销）
WORK_MAX_NORMAL = 1600
WORK_MAX_LIGHT = 1100
#: 输出尺寸长边上限（还原分辨率时的天花板）
OUTPUT_MAX = MAX_IMAGE_DIMENSION
#: 预览用缩略图长边
PREVIEW_MAX = 760

#: 强度档位
STRENGTHS = ("low", "mid", "high")
_STRENGTH_LABELS = {"low": "低", "mid": "中", "high": "高"}


class EnhanceCancelled(Exception):
    """用户在处理过程中主动取消。"""


@dataclass
class EnhanceOptions:
    """预处理选项。默认值即「一键清晰」。

    处理强度 strength 统一控制清晰增强、笔迹过滤与二值化的力度，
    避免面板上出现多个互相打架的强度控件。
    """

    auto_clear: bool = True
    remove_pen: bool = False
    pen_color: str = "auto"          # auto / blue / black
    strength: str = "mid"            # low / mid / high
    deskew: bool = True
    binarize: bool = False
    manual_quad: Optional[Sequence[Sequence[float]]] = None  # 原图坐标四点 [[x,y], ...]

    def normalized(self) -> "EnhanceOptions":
        """把非法档位收敛到合法值，避免上层传错导致行为不确定。"""
        return EnhanceOptions(
            auto_clear=bool(self.auto_clear),
            remove_pen=bool(self.remove_pen),
            pen_color=self.pen_color if self.pen_color in ("auto", "blue", "black") else "auto",
            strength=self.strength if self.strength in STRENGTHS else "mid",
            deskew=bool(self.deskew),
            binarize=bool(self.binarize),
            manual_quad=self.manual_quad,
        )


@dataclass
class EnhanceResult:
    """处理结果。output_path 为 None 表示未产出（调用方应回退原图）。"""

    source_path: Path
    output_path: Optional[Path]
    applied: List[str] = field(default_factory=list)
    duration_ms: int = 0
    degraded: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.output_path is not None and self.output_path.exists()


class ImageEnhanceService:
    """图片增强业务服务（无 PyQt 依赖，可在后台线程调用）。"""

    def __init__(self, images_dir: Optional[Path] = None, light_mode: Optional[bool] = None):
        self._images_dir = images_dir
        self._light_mode = light_mode

    # ------------------------------------------------------------------
    # 环境 / 路径
    # ------------------------------------------------------------------

    def light_mode(self) -> bool:
        """是否走低配降级：强制低配开关打开，或整机判定为低配。"""
        if self._light_mode is not None:
            return self._light_mode
        try:
            from ..config.hardware import (
                detect_hardware,
                get_force_low_spec_setting,
            )

            if get_force_low_spec_setting():
                self._light_mode = True
            else:
                from ..config.hardware import is_low_spec

                self._light_mode = is_low_spec(detect_hardware())
        except Exception as e:  # 检测失败按正常配置处理，不阻塞导入
            logger.warning("Detect low spec failed: %s", e)
            self._light_mode = False
        return bool(self._light_mode)

    def images_dir(self) -> Path:
        target = self._images_dir or get_images_dir()
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _output_path(self, source: Path) -> Path:
        """增强图落到 data/images，用时间戳 + 短随机串避免同名覆盖。"""
        suffix = ".png" if source.suffix.lower() == ".png" else ".jpg"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"enhanced_{stamp}_{uuid.uuid4().hex[:6]}{suffix}"
        return self.images_dir() / name

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def enhance(
        self,
        image_path: Path,
        options: Optional[EnhanceOptions] = None,
        progress: Optional[Callable[[int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> EnhanceResult:
        """按选项处理图片并落盘，返回结果（不抛异常，失败时 ok 为 False）。"""
        started = time.perf_counter()
        source = Path(image_path)
        opts = (options or EnhanceOptions()).normalized()
        light = self.light_mode()
        applied: List[str] = []

        def report(percent: int, message: str) -> None:
            if progress is not None:
                try:
                    progress(percent, message)
                except Exception:
                    pass

        def check_cancel() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise EnhanceCancelled()

        try:
            if not source.exists():
                raise FileNotFoundError(f"图片不存在：{source}")

            report(10, "正在读取图片…")
            original = self._load_bgr(source)
            if original is None:
                raise ValueError("无法解析图片内容")

            height, width = original.shape[:2]
            work_max = WORK_MAX_LIGHT if light else WORK_MAX_NORMAL
            scale = min(1.0, work_max / float(max(height, width)))
            work = (
                cv2.resize(
                    original,
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
                if scale < 1.0
                else original
            )
            check_cancel()

            # 1) 几何：手动框选优先，其次自动检测纸张边缘，最后小角度纠偏
            canvas_changed = False
            if opts.manual_quad is not None:
                report(25, "正在应用框选区域…")
                work = self._warp_by_quad(
                    work, self._scale_quad(opts.manual_quad, scale, width, height)
                )
                applied.append("手动裁切")
                canvas_changed = True
            elif opts.deskew:
                report(25, "正在校正拍摄角度…")
                work, geometry_applied = self._correct_geometry(work)
                if geometry_applied:
                    applied.append(geometry_applied)
                    # 只有裁剪/透视会改变画布尺寸；小角度纠偏不改变，仍需还原分辨率
                    canvas_changed = geometry_applied == "透视校正"
            check_cancel()

            # 2) 蓝色笔迹去除（需要原始颜色，放在灰度化之前）
            if opts.remove_pen and opts.pen_color in ("auto", "blue"):
                report(40, "正在去除蓝色笔迹…")
                work, removed = self._remove_blue_pen(work)
                if removed:
                    applied.append("去蓝色笔迹")
            check_cancel()

            # 3) 亮度通道：背景净化 + 笔迹过滤 + 清晰增强
            need_photo = opts.auto_clear or opts.binarize or opts.remove_pen
            if need_photo:
                report(55, "正在增强文字清晰度…")
                work, clear_applied, black_removed = self._photometric(
                    work, opts, light, check_cancel
                )
                applied.extend(clear_applied)
                if black_removed:
                    applied.append("去黑色笔迹")

            check_cancel()

            # 4) 还原输出分辨率并落盘（裁剪/透视已改变画布，不能再还原到原尺寸）
            report(85, "正在输出图片…")
            output = work if canvas_changed else self._resize_back(work, width, height)
            out_path = self._output_path(source)
            self._save(output, out_path)
            report(100, "处理完成")

            duration = int((time.perf_counter() - started) * 1000)
            if light:
                applied.append("低配简化")
            logger.info(
                "Enhanced image %s -> %s (%dms, applied=%s)",
                source.name, out_path.name, duration, applied,
            )
            return EnhanceResult(
                source_path=source,
                output_path=out_path,
                applied=applied,
                duration_ms=duration,
                degraded=light,
            )
        except EnhanceCancelled:
            logger.info("Enhance cancelled by user: %s", source)
            return EnhanceResult(
                source_path=source,
                output_path=None,
                applied=applied,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error="已取消处理",
            )
        except Exception as e:
            logger.error("Enhance failed for %s: %s", source, e)
            return EnhanceResult(
                source_path=source,
                output_path=None,
                applied=applied,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=str(e),
            )

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------

    @staticmethod
    def _load_bgr(path: Path) -> Optional[np.ndarray]:
        """用 PIL 读取（兼容中文路径与 EXIF 方向），转成 OpenCV BGR。"""
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
        except Exception as e:
            logger.warning("PIL load failed (%s), fallback to cv2: %s", path, e)
        data = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        return data

    @staticmethod
    def _save(image: np.ndarray, path: Path) -> None:
        """用 imencode + tofile 保存，避免中文路径下 cv2.imwrite 失败。"""
        ext = path.suffix.lower() or ".jpg"
        params = [int(cv2.IMWRITE_JPEG_QUALITY), 92] if ext in (".jpg", ".jpeg") else []
        ok, buf = cv2.imencode(ext, image, params)
        if not ok:
            raise RuntimeError("图片编码失败")
        buf.tofile(str(path))

    def _resize_back(self, image: np.ndarray, width: int, height: int) -> np.ndarray:
        """把处理结果还原到输出分辨率（长边不超过 OUTPUT_MAX）。"""
        target_w, target_h = width, height
        longest = max(width, height)
        if longest > OUTPUT_MAX:
            ratio = OUTPUT_MAX / float(longest)
            target_w, target_h = int(width * ratio), int(height * ratio)
        cur_h, cur_w = image.shape[:2]
        if (cur_w, cur_h) == (target_w, target_h):
            return image
        interpolation = cv2.INTER_CUBIC if target_w * target_h > cur_w * cur_h else cv2.INTER_AREA
        return cv2.resize(image, (target_w, target_h), interpolation=interpolation)

    # ------------------------------------------------------------------
    # 几何校正
    # ------------------------------------------------------------------

    @staticmethod
    def _scale_quad(
        quad: Sequence[Sequence[float]], scale: float, width: int, height: int
    ) -> np.ndarray:
        """把原图坐标的四点映射到工作尺寸坐标系。"""
        if scale < 1.0:
            pts = np.array(quad, dtype=np.float32) * scale
        else:
            pts = np.array(quad, dtype=np.float32)
        pts[:, 0] = np.clip(pts[:, 0], 0, max(1, int(width * scale) - 1))
        pts[:, 1] = np.clip(pts[:, 1], 0, max(1, int(height * scale) - 1))
        return pts

    def _correct_geometry(self, image: np.ndarray):
        """自动透视校正；检测不到纸张四边形时退化为小角度纠偏。

        返回 (处理后的图, 生效步骤名或空串)。找不到可靠边缘时原样返回，
        宁可不动也不能把题目裁掉。
        """
        quad = self._detect_paper_quad(image)
        if quad is not None:
            warped = self._warp_by_quad(image, quad)
            if warped is not None and warped.size:
                return warped, "透视校正"
        deskewed = self._deskew_small_angle(image)
        if deskewed is not None:
            return deskewed, "倾斜校正"
        return image, ""

    @staticmethod
    def _detect_paper_quad(image: np.ndarray) -> Optional[np.ndarray]:
        """在缩略图上找最大的四边形容器，返回工作尺寸坐标的四点。"""
        h, w = image.shape[:2]
        scale = min(1.0, 900.0 / float(max(h, w)))
        small = (
            cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            if scale < 1.0
            else image
        )
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(gray, 50, 150)
        edges = cv2.morphologyEx(
            edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
            iterations=2,
        )
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        small_area = float(small.shape[0] * small.shape[1])
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            area = cv2.contourArea(contour)
            if area < 0.25 * small_area:
                break
            approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                return (approx.reshape(4, 2).astype(np.float32) / scale)
        return None

    @staticmethod
    def _warp_by_quad(image: np.ndarray, quad: np.ndarray) -> Optional[np.ndarray]:
        """四点透视变换：把 quad 拉正成矩形。"""
        try:
            src = ImageEnhanceService._order_quad(quad)
            width = int(
                max(
                    np.linalg.norm(src[0] - src[1]),
                    np.linalg.norm(src[2] - src[3]),
                )
            )
            height = int(
                max(
                    np.linalg.norm(src[0] - src[3]),
                    np.linalg.norm(src[1] - src[2]),
                )
            )
            if width < 40 or height < 40:
                return None
            dst = np.array(
                [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                dtype=np.float32,
            )
            matrix = cv2.getPerspectiveTransform(src, dst)
            return cv2.warpPerspective(
                image, matrix, (width, height), flags=cv2.INTER_CUBIC
            )
        except Exception as e:
            logger.warning("Perspective warp failed: %s", e)
            return None

    @staticmethod
    def _order_quad(quad: np.ndarray) -> np.ndarray:
        """把任意顺序的四点整理为 左上→右上→右下→左下。"""
        pts = np.array(quad, dtype=np.float32).reshape(-1, 2)
        ordered = np.zeros((4, 2), dtype=np.float32)
        sums = pts.sum(axis=1)
        diffs = np.diff(pts, axis=1).ravel()
        ordered[0] = pts[np.argmin(sums)]
        ordered[2] = pts[np.argmax(sums)]
        ordered[1] = pts[np.argmin(diffs)]
        ordered[3] = pts[np.argmax(diffs)]
        return ordered

    @staticmethod
    def _deskew_small_angle(image: np.ndarray, max_angle: float = 8.0) -> Optional[np.ndarray]:
        """基于文本像素最小外接矩形的小角度纠偏（仅消除轻微倾斜）。"""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        coords = cv2.findNonZero(binary)
        if coords is None or len(coords) < 200:
            return None
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = 90 + angle
        if abs(angle) < 0.6 or abs(angle) > max_angle:
            return None
        h, w = gray.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
        return cv2.warpAffine(
            image, matrix, (w, h), flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )

    # ------------------------------------------------------------------
    # 亮度/颜色：一键清晰 + 笔迹去除 + 二值化
    # ------------------------------------------------------------------

    def _photometric(self, image: np.ndarray, opts: EnhanceOptions, light: bool, check_cancel):
        """在 LAB 的亮度通道上做背景净化、笔迹过滤与清晰增强。

        返回 (处理后图像, 生效步骤列表, 是否去除了黑色笔迹)。

        色彩基调只在用户启用「一键清晰」或「二值化」时才改写；
        仅勾选笔迹去除时保留原色调，只把笔迹像素填成纸面底色，
        避免用户没要求却在背后改变整张图的明暗。
        """
        applied: List[str] = []
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        luminance, a_chan, b_chan = cv2.split(lab)

        background = self._estimate_background(luminance)

        # 检测用的白底图：背景净化只在纸面（背景较亮）区域做除法，
        # 暗色图表/图片原样保留，避免被除法冲成一片白
        denoised = cv2.divide(luminance, background, scale=255)
        weight = np.clip((background.astype(np.float32) - 150.0) / 80.0, 0.0, 1.0)
        detect_base = (
            luminance.astype(np.float32) * (1.0 - weight)
            + denoised.astype(np.float32) * weight
        ).astype(np.uint8)

        tone_changed = opts.auto_clear or opts.binarize
        working = detect_base.copy() if tone_changed else luminance.copy()
        if opts.auto_clear:
            applied.append("背景净化")
        check_cancel()

        black_removed = False
        if opts.remove_pen and opts.pen_color in ("auto", "black"):
            mask = self._stroke_mask(detect_base, opts.strength)
            if mask is not None:
                working[mask > 0] = background[mask > 0]
                black_removed = True
            check_cancel()

        if opts.auto_clear:
            clip_limit = {"low": 1.6, "mid": 2.4, "high": 3.2}[opts.strength]
            clahe = cv2.createCLAHE(
                clipLimit=1.6 if light else clip_limit, tileGridSize=(8, 8)
            )
            working = clahe.apply(working)
            # 保边降噪：低配用中值滤波替代双边滤波，成本差一个数量级
            if light:
                working = cv2.medianBlur(working, 3)
            else:
                working = cv2.bilateralFilter(working, 5, 45, 45)
            # 非锐化掩模：提升文字边缘锐度
            blurred = cv2.GaussianBlur(working, (0, 0), 1.2)
            amount = {"low": 0.5, "mid": 0.9, "high": 1.3}[opts.strength]
            if light:
                amount = min(amount, 0.6)
            working = cv2.addWeighted(working, 1.0 + amount, blurred, -amount, 0)
            applied.append("对比度增强")
            applied.append("文字锐化")
            check_cancel()

        if opts.binarize:
            working = self._binarize(working, opts.strength)
            applied.append("纯二值化" if opts.strength == "high" else "白底黑字")
            if opts.strength == "high":
                # 纯黑白不保留色度：走 LAB 合并会因色彩空间往返产生 1/254 灰边
                return cv2.cvtColor(working, cv2.COLOR_GRAY2BGR), applied, black_removed

        result = cv2.cvtColor(
            cv2.merge([working, a_chan, b_chan]), cv2.COLOR_LAB2BGR
        )
        return result, applied, black_removed

    @staticmethod
    def _estimate_background(luminance: np.ndarray) -> np.ndarray:
        """在 1/4 缩略图上估纸面背景，再放大回原尺寸。

        缩略图上做形态学可把大核开销降到 1/16，是这套算法能跑进 1~3 秒的关键。
        """
        h, w = luminance.shape[:2]
        small = cv2.resize(
            luminance,
            (max(32, w // 4), max(32, h // 4)),
            interpolation=cv2.INTER_AREA,
        )
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        background = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)
        background = cv2.GaussianBlur(background, (0, 0), 5)
        background = cv2.resize(background, (w, h), interpolation=cv2.INTER_LINEAR)
        # 背景不可能比前景更暗，钳制到 1 避免整除放大噪声
        return np.maximum(background, 1)

    # ------------------------------------------------------------------
    # 笔迹去除
    # ------------------------------------------------------------------

    @staticmethod
    def _remove_blue_pen(image: np.ndarray):
        """按颜色阈值分离蓝色笔迹并填充为纸面底色。

        判据取两条并与运算：HSV 蓝色区间，或 B 通道显著高于 R/G（
        抗扫描偏色，深蓝圆珠笔也能命中）。保留印刷体：黑色文字三通道接近，
        不会被任何一条命中。
        """
        try:
            bgr = image.astype(np.int16)
            b_ch, g_ch, r_ch = bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2]
            hsv_mask = cv2.inRange(
                cv2.cvtColor(image, cv2.COLOR_BGR2HSV),
                np.array((85, 50, 0), dtype=np.uint8),
                np.array((145, 255, 255), dtype=np.uint8),
            )
            dominance = (b_ch - np.maximum(g_ch, r_ch)) > 25
            mask = cv2.bitwise_and(hsv_mask, (dominance * 255).astype(np.uint8))
            if int(cv2.countNonZero(mask)) < 40:
                return image, False

            # 轻微膨胀覆盖抗锯齿边缘，再按纸面背景填充（避免留下白斑）
            mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            background = ImageEnhanceService._estimate_background(gray)
            result = image.copy()
            selection = mask > 0
            result[selection] = np.repeat(background[selection][:, None], 3, axis=1)
            return result, True
        except Exception as e:
            logger.warning("Remove blue pen failed: %s", e)
            return image, False

    @staticmethod
    def _rect_frame_boxes(binary: np.ndarray) -> List[tuple]:
        """找出「规则矩形框」的包围盒（表格边线 / 答题框），这些必须保留。

        判据：最小外接矩形的填充率接近 1（正矩形），而手绘圈画是椭圆形的，
        填充率约 π/4≈0.785，据此可以把印刷表格框和手写圈画区分开。
        """
        boxes: List[tuple] = []
        try:
            contours, _ = cv2.findContours(
                binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
            )
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < 400:
                    continue
                (_, _), (rect_w, rect_h), _ = cv2.minAreaRect(contour)
                rect_area = rect_w * rect_h
                if rect_area > 0 and area / rect_area > 0.90:
                    boxes.append(tuple(int(v) for v in cv2.boundingRect(contour)))
        except Exception as e:
            logger.warning("Detect rect frames failed: %s", e)
        return boxes

    @staticmethod
    def _line_jitter(roi_mask: np.ndarray, horizontal: bool) -> float:
        """细长笔迹的厚度抖动系数：手写划线厚度不均，印刷线厚度恒定。"""
        matrix = roi_mask if horizontal else roi_mask.T
        counts = matrix.sum(axis=0)
        counts = counts[counts > 0]
        if counts.size < 8:
            return 0.0
        mean = float(counts.mean())
        return float(counts.std() / mean) if mean > 0 else 0.0

    @staticmethod
    def _stroke_mask(normalized: np.ndarray, strength: str) -> Optional[np.ndarray]:
        """检测黑色手写 / 圈画，返回待清除像素掩码（无命中返回 None）。

        本质是启发式，原则是「宁可漏删，不误删印刷体」。只识别三类特征：
        - 大圈画：包围盒大、填充率极低，且不是规则矩形（表格边框会被豁免）
        - 抖动的细长划线：厚度不均匀（手写），与厚度恒定的印刷线区分
        - 尺寸明显偏大且笔画明显偏粗的块：手写比印刷体粗
        印刷体文字包围盒很小、填充率高，三条规则都命中不了。

        已知局限：笔迹与文字物理粘连时会合并成同一个连通域，只能整体处理；
        印刷表格线与手写下划线在几何上难以区分，一律保留。
        """
        try:
            _, binary = cv2.threshold(
                normalized, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
            )
            count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
            if count <= 1:
                return None

            h, w = normalized.shape[:2]
            image_area = float(h * w)
            distance = cv2.distanceTransform(binary, cv2.DIST_L2, 3)
            foreground = distance[binary > 0]
            if foreground.size == 0:
                return None
            typical_width = float(np.median(foreground))
            keep_boxes = ImageEnhanceService._rect_frame_boxes(binary)

            remove_mask = np.zeros((h, w), dtype=np.uint8)
            removed = False
            for index in range(1, count):
                x, y, box_w, box_h, area = (
                    int(stats[index, 0]), int(stats[index, 1]),
                    int(stats[index, 2]), int(stats[index, 3]),
                    int(stats[index, 4]),
                )
                if area < 40:
                    continue
                box_area = float(max(1, box_w * box_h))
                fill = area / box_area
                aspect = box_w / float(max(1, box_h))

                roi_labels = labels[y : y + box_h, x : x + box_w]

                # 规则矩形框（表格 / 答题框）：无论填充率多低都必须保留
                is_rect_frame = any(
                    abs(bx - x) <= 4 and abs(by - y) <= 4 and abs(bw - box_w) <= 6
                    and abs(bh - box_h) <= 6
                    for bx, by, bw, bh in keep_boxes
                )

                big_loop = (
                    not is_rect_frame
                    and box_area > 0.08 * image_area
                    and fill < 0.10
                )

                jittered_line = False
                thin_limit = max(24, int(0.03 * max(h, w)))
                if aspect > 12 and min(box_w, box_h) <= thin_limit:
                    jittered_line = (
                        ImageEnhanceService._line_jitter(
                            roi_labels == index, horizontal=box_w >= box_h
                        )
                        > 0.30
                    )

                thick_stroke = False
                if strength in ("mid", "high"):
                    large_enough = (
                        box_area > 0.01 * image_area
                        or max(box_w, box_h) > 0.18 * max(h, w)
                    )
                    if large_enough and box_area < 0.06 * image_area:
                        first = {"mid": 3.0, "high": 2.2}[strength]
                        values = distance[y : y + box_h, x : x + box_w][roi_labels == index]
                        thick_stroke = (
                            values.size > 0
                            and typical_width > 0
                            and float(values.max()) > typical_width * first
                        )

                if big_loop or jittered_line or thick_stroke:
                    roi_mask = remove_mask[y : y + box_h, x : x + box_w]
                    roi_mask[roi_labels == index] = 255
                    removed = True

            return remove_mask if removed else None
        except Exception as e:
            logger.warning("Detect black strokes failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # 二值化
    # ------------------------------------------------------------------

    @staticmethod
    def _binarize(luminance: np.ndarray, strength: str) -> np.ndarray:
        """白底黑字。

        high 输出纯黑白，最适合打印/OCR；low/mid 输出高对比灰阶，
        保留图表、公式与插入图片的层次，避免结构被二值化抹平。
        """
        if strength == "high":
            block = max(15, (min(luminance.shape[:2]) // 40) | 1)
            binary = cv2.adaptiveThreshold(
                luminance, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, block, 8,
            )
            return cv2.medianBlur(binary, 3)
        low_percentile = 1.0 if strength == "mid" else 2.0
        high_percentile = 99.0 if strength == "mid" else 98.0
        low = float(np.percentile(luminance, low_percentile))
        high = float(np.percentile(luminance, high_percentile))
        if high - low < 1.0:
            return luminance
        scaled = (luminance.astype(np.float32) - low) / (high - low) * 255.0
        return np.clip(scaled, 0, 255).astype(np.uint8)
