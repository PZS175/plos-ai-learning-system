"""端到端 OCR 测试。

生成一张测试图片，验证双路 OCR 流水线可运行，
并在无 VL 模型时自动降级为纯 PaddleOCR。

运行：
    cd plos_ai
    .venv\\Scripts\\Activate.ps1
    $env:PYTHONPATH="src"; py -m tests.e2e_ocr
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw, ImageFont

from plos.ai import ModelManager
from plos.config import detect_hardware, load_config
from plos.core.enums import OCRSource
from plos.services import OCRService
from plos.utils.logger import setup_logger


def create_test_image(path: Path, text: str = "Hello PLOS AI") -> None:
    """生成一张包含文字的测试图片。"""
    img = Image.new("RGB", (400, 120), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("msyh.ttc", 32)
    except Exception:
        font = ImageFont.load_default()
    draw.text((20, 40), text, fill="black", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def main() -> int:
    setup_logger()

    print("=" * 60)
    print("PLOS AI E2E OCR Test")
    print("=" * 60)

    config = load_config()
    hardware = detect_hardware()
    manager = ModelManager(config, hardware=hardware)

    print(f"Text available: {manager.is_text_available()}")
    print(f"Vision available: {manager.is_vision_available()}")

    image_path = Path("data/test_ocr.png")
    create_test_image(image_path, "牛顿第二定律：F = ma")
    print(f"Created test image: {image_path}")

    ocr_service = OCRService(manager)
    print(f"PaddleOCR available: {ocr_service.is_ocr_available()}")

    try:
        result = ocr_service.recognize_image(image_path, source=OCRSource.FILE)
        print(f"\nOCR text: {result.text}")
        print(f"Blocks: {len(result.blocks)}")
        print(f"VL corrected regions: {result.vl_corrected_regions}")
        print(f"Used VL fallback: {result.used_vl_fallback}")
    except Exception as e:
        print(f"ERROR: OCR failed: {e}")
        return 1

    # 测试拍照搜题（如果 VL 可用）
    if manager.is_vision_available():
        print("\nTesting photo solve...")
        try:
            answer = ocr_service.photo_solve(image_path)
            print(f"Photo solve answer preview: {answer[:200]}...")
        except Exception as e:
            print(f"Photo solve failed: {e}")
            return 1
    else:
        print("\nVL model not available, skipping photo solve test.")

    print("\nE2E OCR test passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
