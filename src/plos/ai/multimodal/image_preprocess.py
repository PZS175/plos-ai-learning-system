"""Image preprocessing utilities: resize, deskew, denoise, grayscale.

Uses Pillow and OpenCV.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ...core.constants import MAX_IMAGE_DIMENSION
from ...utils.logger import get_logger

logger = get_logger("multimodal.image_preprocess")


class ImagePreprocessor:
    """Preprocessor for OCR input images."""

    def __init__(
        self,
        max_dimension: int = MAX_IMAGE_DIMENSION,
        deskew: bool = True,
        denoise: bool = True,
        grayscale: bool = False,
    ):
        self.max_dimension = max_dimension
        self.deskew = deskew
        self.denoise = denoise
        self.grayscale = grayscale

    def process(self, image_path: Path) -> Path:
        """Process an image file and save a preprocessed version.

        Args:
            image_path: Path to original image.

        Returns:
            Path to preprocessed image (may be the same if no changes).
        """
        img = Image.open(image_path).convert("RGB")
        img = self._resize(img)
        if self.deskew:
            img = self._deskew(img)
        if self.denoise:
            img = self._denoise(img)
        if self.grayscale:
            img = img.convert("L").convert("RGB")

        # Save to a temp file next to original
        suffix = image_path.suffix or ".jpg"
        pre_path = image_path.with_suffix(f".preprocessed{suffix}")
        img.save(pre_path, quality=95)
        logger.debug("Preprocessed image saved to %s", pre_path)
        return pre_path

    def _resize(self, img: Image.Image) -> Image.Image:
        width, height = img.size
        if width <= self.max_dimension and height <= self.max_dimension:
            return img
        scale = self.max_dimension / max(width, height)
        new_size = (int(width * scale), int(height * scale))
        return img.resize(new_size, Image.Resampling.LANCZOS)

    def _deskew(self, img: Image.Image) -> Image.Image:
        """Correct small skew angles using Hough line detection."""
        cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
        _, binary = cv2.threshold(cv_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        coords = np.column_stack(np.where(binary > 0))
        if len(coords) < 100:
            return img

        # Find angle via minAreaRect
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = 90 + angle

        if abs(angle) < 0.5:
            return img  # No significant skew

        # Rotate
        (h, w) = cv_img.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(
            cv_img,
            M,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        return Image.fromarray(rotated).convert("RGB")

    def _denoise(self, img: Image.Image) -> Image.Image:
        """Apply light denoising."""
        cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        denoised = cv2.fastNlMeansDenoisingColored(cv_img, None, 10, 10, 7, 21)
        return Image.fromarray(cv2.cvtColor(denoised, cv2.COLOR_BGR2RGB))

    @staticmethod
    def crop_region(image_path: Path, bbox: list) -> Image.Image:
        """Crop a region defined by a 4-point bbox [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]."""
        img = Image.open(image_path)
        pts = np.array(bbox, dtype=np.float32)
        x_min, y_min = pts.min(axis=0)
        x_max, y_max = pts.max(axis=0)
        x_min, y_min = max(0, int(x_min)), max(0, int(y_min))
        x_max, y_max = min(img.width, int(x_max)), min(img.height, int(y_max))
        return img.crop((x_min, y_min, x_max, y_max))
