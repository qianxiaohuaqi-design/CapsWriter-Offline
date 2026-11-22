"""Conservative hardware advice without benchmarking or GPU-vendor guessing."""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path


def inspect_hardware(model_root: Path | str | None = None) -> dict:
    providers = []
    detection_error = None
    try:
        import onnxruntime
        providers = list(onnxruntime.get_available_providers())
    except Exception:
        detection_error = "尚未检测到 ONNX Runtime；安装运行依赖后再检查。"
    memory_bytes = None
    try:
        import psutil
        memory_bytes = int(psutil.virtual_memory().total)
    except (ImportError, OSError):
        try:
            memory_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (AttributeError, OSError, ValueError):
            pass
    free_bytes = None
    if model_root:
        probe = Path(model_root).expanduser().absolute()
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            free_bytes = shutil.disk_usage(probe).free
        except OSError:
            pass
    return {
        "platform": platform.system(), "architecture": platform.machine(),
        "cpu_count": os.cpu_count(), "memory_bytes": memory_bytes,
        "free_bytes": free_bytes, "onnx_providers": providers,
        "recommended_model_id": "sensevoice-small-fp16",
        "recommended_backend": "CPU", "detection_error": detection_error,
        "reason": "SenseVoice 体积较小，先使用 CPU 保守配置；检测到加速后端时可在高级设置中选择。性能以本机实际加载和录音为准。",
        "accelerated_backend_available": any(p in providers for p in ("DmlExecutionProvider", "CUDAExecutionProvider")),
    }
