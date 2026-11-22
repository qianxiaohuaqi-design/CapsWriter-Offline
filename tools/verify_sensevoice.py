"""Explicit model download / CPU inference smoke test in chosen isolated folders.

Example (public sample is optional; neither network action is implicit):
  python tools/verify_sensevoice.py --data-dir .test-data --model-dir .test-models \
    --download --accept-model-license --fetch-test-audio

This verifies the existing ASR engine, not Windows input or the packaged app.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SAMPLE_URL = "https://raw.githubusercontent.com/HaujetZhao/SenseVoice-ONNX/4d7bce67e86b4049db1a58a6c2e2ecd34dfefd9c/test-fun.mp3"
SAMPLE_SHA256 = "ba5f8a9db8665afb48f1be359d4faef615dc7679165303f1e291da488469aafa"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--accept-model-license", action="store_true")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--audio", type=Path)
    inputs.add_argument("--fetch-test-audio", action="store_true")
    args = parser.parse_args(argv)
    if args.download and not args.accept_model_license:
        parser.error("--download requires --accept-model-license; review the catalog license_url first")
    data_dir = args.data_dir.expanduser().resolve()
    model_dir = args.model_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["CAPSWRITER_DATA_DIR"] = str(data_dir)
    os.environ["CAPSWRITER_MODEL_DIR"] = str(model_dir)
    from core.product.models import ModelCatalog
    catalog = ModelCatalog()
    model_id = "sensevoice-small-fp16"
    entry = catalog.get(model_id)
    if args.download and not catalog.installed_path(model_id, model_dir):
        from core.product.downloads import DownloadManager
        manager = DownloadManager(data_dir, model_dir, catalog)
        try:
            pending = next((x for x in manager.snapshot() if x["model_id"] == model_id and x["status"] in {"paused", "failed"}), None)
            job = manager.resume(pending["download_id"]) if pending else manager.start(model_id, license_accepted=True)
            while True:
                state = manager.get(job["download_id"])
                if state["status"] in {"completed", "failed", "cancelled", "paused"}:
                    if state["status"] != "completed":
                        raise RuntimeError(state.get("error") or state["status"])
                    break
                time.sleep(0.2)
        finally:
            manager.shutdown()
    verified = catalog.verify_installation(model_id, model_dir)
    report = {"model_id": model_id, "installation": verified, "archive_sha256": entry["sha256"],
              "platform": sys.platform, "windows_native_acceptance": "NOT_RUN"}
    audio = args.audio
    if args.fetch_test_audio:
        payload = urllib.request.urlopen(SAMPLE_URL, timeout=30).read(1024 * 1024)
        if hashlib.sha256(payload).hexdigest() != SAMPLE_SHA256:
            raise ValueError("公开测试音频与固定 SHA-256 不一致")
        audio = data_dir / ("test-fun-" + SAMPLE_SHA256[:12] + ".mp3")
        if audio.exists():
            if hashlib.sha256(audio.read_bytes()).hexdigest() != SAMPLE_SHA256:
                raise ValueError("测试音频目标已有不同内容；拒绝覆盖")
        else:
            with audio.open("xb") as stream:
                stream.write(payload)
        report.update(sample_url=SAMPLE_URL, sample_sha256=SAMPLE_SHA256)
    if audio:
        from core.server.engines.sensevoice_onnx.inference.schema import ASREngineConfig
        from core.server.engines.sensevoice_onnx.inference.engine import SenseVoiceInference
        from core.server.engines.sensevoice_onnx.inference.audio import load_audio
        config = catalog.configuration(model_id, model_dir)["classes"]["SenseVoiceArgs"]
        started = time.monotonic()
        engine = SenseVoiceInference(ASREngineConfig(**config))
        report["load_seconds"] = time.monotonic() - started
        samples = load_audio(str(audio))
        started = time.monotonic()
        result = engine.recognize(samples, lid="zh", itn=True)
        report.update(recognize_seconds=time.monotonic() - started, text=result.text,
                      timestamped_tokens=len(result.results), audio_seconds=len(samples) / 16000)
        if not result.text.strip():
            raise ValueError("模型已加载但测试音频没有识别文字；不能记推理通过")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
