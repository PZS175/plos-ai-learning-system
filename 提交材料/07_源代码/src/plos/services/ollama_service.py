"""Ollama 本地模型管理服务：列出/启动/停止模型，检测服务状态。

所有 subprocess 调用包 try-except，失败时返回空结果或 False，不抛异常。
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger("ollama_service")


class OllamaService:
    """通过 ollama CLI 管理本地模型。"""

    def __init__(self, timeout: int = 15) -> None:
        self._timeout = timeout

    def _run(self, args: List[str]) -> Optional[str]:
        """执行 ollama 命令，返回 stdout；失败返回 None。"""
        try:
            exe = shutil.which("ollama") or "ollama"
            result = subprocess.run(
                [exe] + args,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
            if result.returncode != 0:
                logger.warning("ollama %s failed: %s", args, result.stderr.strip())
                return None
            return result.stdout
        except FileNotFoundError:
            logger.warning("ollama executable not found")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("ollama %s timed out", args)
            return None
        except Exception as e:
            logger.error("ollama %s error: %s", args, e)
            return None

    def is_service_online(self) -> bool:
        """检测 Ollama 服务是否在线。"""
        out = self._run(["ps"])
        return out is not None

    def list_models(self) -> List[Dict[str, Any]]:
        """列出本地已下载模型。"""
        out = self._run(["list"])
        if not out:
            return []
        models: List[Dict[str, Any]] = []
        lines = out.strip().splitlines()
        # 第一行是表头
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            name = parts[0]
            size_str = parts[-2] + " " + parts[-1]
            models.append({
                "name": name,
                "size": size_str,
                "id": parts[1] if len(parts) > 4 else "",
            })
        return models

    def list_running(self) -> List[Dict[str, Any]]:
        """列出正在内存中运行的模型。"""
        out = self._run(["ps"])
        if not out:
            return []
        models: List[Dict[str, Any]] = []
        lines = out.strip().splitlines()
        for line in lines[1:]:
            parts = line.split()
            if not parts:
                continue
            models.append({"name": parts[0]})
        return models

    def start_model(self, model_name: str) -> bool:
        """启动（加载）模型到内存。"""
        # ollama run 在后台加载模型；这里用 < /dev/null 避免交互
        try:
            exe = shutil.which("ollama") or "ollama"
            proc = subprocess.Popen(
                [exe, "run", model_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # 不等待，让它在后台加载
            logger.info("Started loading model: %s (pid=%s)", model_name, proc.pid)
            return True
        except Exception as e:
            logger.error("Failed to start model %s: %s", model_name, e)
            return False

    def stop_model(self, model_name: str) -> bool:
        """停止模型，从内存卸载。"""
        out = self._run(["stop", model_name])
        return out is not None

    def get_vram_estimate(self, model_name: str) -> str:
        """粗略估算显存占用（基于模型名中的量化位数和参数大小）。"""
        try:
            name_lower = model_name.lower()
            # 解析参数大小，如 qwen2.5:7b → 7B
            gb = 0.0
            for token in name_lower.replace(":", " ").split():
                if token.endswith("b") and token[:-1].replace(".", "").isdigit():
                    gb = float(token[:-1])
                    break
            if gb <= 0:
                return "未知"
            # 量化估算：q4 约 0.5x, q8 约 1x, fp16 约 2x
            if "q2" in name_lower:
                factor = 0.3
            elif "q3" in name_lower:
                factor = 0.4
            elif "q4" in name_lower:
                factor = 0.5
            elif "q5" in name_lower:
                factor = 0.6
            elif "q6" in name_lower:
                factor = 0.75
            elif "q8" in name_lower:
                factor = 1.0
            else:
                factor = 2.0
            vram = gb * factor
            return f"约 {vram:.1f} GB"
        except Exception:
            return "未知"
