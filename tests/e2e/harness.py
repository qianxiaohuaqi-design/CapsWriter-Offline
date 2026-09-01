# coding: utf-8
"""
CapsWriter-Offline E2E Test Harness & Mock Server
=================================================

Provides:
1. MockLLMServer: Threaded HTTP/SSE server on 127.0.0.1:0 simulating OpenAI/Ollama endpoints,
   custom stream delays, slow TTFT (3-5s), HTTP status codes (401/429/500/503),
   empty responses, and socket management.
2. RecordedRequest: Captured wire-level request with headers and JSON body.
3. E2ETestHarness / E2EBaseTestCase: Base unittest fixture with mock server lifecycle,
   isolated temporary environments, and standard assertion helpers.
4. ASTCacheTestHelper / ASTCacheHelper: File-based AST parser & mtime cache tester.
5. MaskingHelper / mask_key_helper: Key masking and log security validator.
6. MicroBuffer / MicroBufferSimulator: Stream micro-buffering engine.
7. MockApp: Application mock context for MessageBuilder and component testing.
"""

from __future__ import annotations

import ast
import io
import json
import logging
import os
import shutil
import socket
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Set

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class RecordedRequest:
    """Captured HTTP request data."""
    timestamp: float
    method: str
    path: str
    headers: Dict[str, str]
    body_bytes: bytes
    json_data: Optional[Dict[str, Any]] = None

    @property
    def auth_header(self) -> str:
        return self.headers.get('authorization', self.headers.get('Authorization', ''))

    @property
    def bearer_token(self) -> str:
        auth = self.auth_header
        if auth.lower().startswith('bearer '):
            return auth[7:].strip()
        return auth


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Threaded HTTP server to handle concurrent mock requests cleanly."""
    daemon_threads = True
    allow_reuse_address = True


class MockRequestHandler(BaseHTTPRequestHandler):
    """Custom HTTP handler for deterministic LLM API mocking."""

    @property
    def mock_server(self) -> Optional['MockLLMServer']:
        return getattr(self.server, 'mock_llm_server', None)

    def log_message(self, format, *args):
        # Suppress noisy standard HTTP access logs
        pass

    def setup(self):
        super().setup()
        if self.mock_server and hasattr(self.mock_server, 'track_socket_open'):
            self.mock_server.track_socket_open(self.connection)

    def finish(self):
        try:
            if self.mock_server and hasattr(self.mock_server, 'track_socket_close'):
                self.mock_server.track_socket_close(self.connection)
        finally:
            super().finish()

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b''
        json_data = None
        if body:
            try:
                json_data = json.loads(body.decode('utf-8'))
            except Exception:
                pass

        req = RecordedRequest(
            timestamp=time.time(),
            method='POST',
            path=self.path,
            headers={k.lower(): v for k, v in self.headers.items()},
            body_bytes=body,
            json_data=json_data
        )
        if self.mock_server:
            self.mock_server.record_request(req)
            handler = self.mock_server.match_handler(self.path, 'POST')
            if handler:
                handler(self, req)
            else:
                self.mock_server.default_handler(self, req)

    def do_GET(self):
        req = RecordedRequest(
            timestamp=time.time(),
            method='GET',
            path=self.path,
            headers={k.lower(): v for k, v in self.headers.items()},
            body_bytes=b'',
            json_data=None
        )
        if self.mock_server:
            self.mock_server.record_request(req)
            handler = self.mock_server.match_handler(self.path, 'GET')
            if handler:
                handler(self, req)
            else:
                self.mock_server.default_handler(self, req)


class MockLLMServer:
    """
    In-process multi-threaded mock LLM HTTP server.
    Binds dynamically to an ephemeral port on 127.0.0.1.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.requested_port = port
        self.httpd: Optional[ThreadedHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.requests: List[RecordedRequest] = []
        self._lock = threading.Lock()
        self.routes: Dict[Tuple[str, str], Callable] = {}
        self._active_sockets = set()

        self._default_mode: str = "stream"  # "stream", "error", "empty", "raw_sse"
        self._default_tokens: List[str] = ["润色", "测试", "文本"]
        self._default_delay_s: float = 0.0
        self._default_chunk_delay_ms: float = 0.0
        self._error_status: int = 500
        self._error_message: str = "Internal Server Error"
        self._error_type: str = "server_error"
        self._raw_sse_chunks: List[str] = []

    @property
    def port(self) -> int:
        if self.httpd:
            return self.httpd.server_port
        return self.requested_port

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def start(self) -> 'MockLLMServer':
        self.httpd = ThreadedHTTPServer((self.host, self.requested_port), MockRequestHandler)
        self.httpd.mock_llm_server = self  # type: ignore
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
            self.thread = None

    def __enter__(self) -> 'MockLLMServer':
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def track_socket_open(self, sock):
        with self._lock:
            self._active_sockets.add(sock)

    def track_socket_close(self, sock):
        with self._lock:
            self._active_sockets.discard(sock)

    def get_open_socket_count(self) -> int:
        with self._lock:
            return len(self._active_sockets)

    def record_request(self, req: RecordedRequest):
        with self._lock:
            self.requests.append(req)

    def get_requests(self) -> List[RecordedRequest]:
        with self._lock:
            return list(self.requests)

    def get_last_request(self) -> Optional[RecordedRequest]:
        with self._lock:
            return self.requests[-1] if self.requests else None

    def clear_requests(self):
        with self._lock:
            self.requests.clear()

    def set_handler(self, path: str, handler: Callable, method: str = 'POST'):
        self.routes[(path, method.upper())] = handler

    def match_handler(self, path: str, method: str = 'POST') -> Optional[Callable]:
        method = method.upper()
        for (route_path, route_method), handler in self.routes.items():
            if method == route_method and path.startswith(route_path):
                return handler
        return None

    def set_streaming_response(self, tokens: List[str], delay_s: float = 0.0, chunk_delay_ms: float = 0.0):
        self._default_mode = "stream"
        self._default_tokens = list(tokens)
        self._default_delay_s = delay_s
        self._default_chunk_delay_ms = chunk_delay_ms

    def set_default_stream_tokens(self, tokens: List[str], delay_s: float = 0.0, chunk_delay_ms: float = 0.0):
        self.set_streaming_response(tokens, delay_s, chunk_delay_ms)

    def set_slow_first_token_response(self, tokens: List[str], delay_s: float = 0.5):
        self.set_streaming_response(tokens, delay_s=delay_s)

    def set_error_response(self, status_code: int = 500, message: str = "Error", error_type: str = "api_error"):
        self._default_mode = "error"
        self._error_status = status_code
        self._error_message = message
        self._error_type = error_type

    def set_empty_response(self):
        self._default_mode = "empty"

    def set_empty_stream_response(self):
        self._default_mode = "empty"

    def set_raw_sse_chunks(self, chunks: List[str], delay_s: float = 0.0):
        self._default_mode = "raw_sse"
        self._raw_sse_chunks = list(chunks)
        self._default_delay_s = delay_s

    def default_handler(self, handler: BaseHTTPRequestHandler, req: RecordedRequest):
        if self._default_mode == "stream":
            self.respond_openai_stream(
                handler,
                tokens=self._default_tokens,
                delay_s=self._default_delay_s,
                chunk_delay_ms=self._default_chunk_delay_ms
            )
        elif self._default_mode == "error":
            self.respond_error(
                handler,
                status_code=self._error_status,
                message=self._error_message,
                error_type=self._error_type
            )
        elif self._default_mode == "empty":
            self.respond_empty_stream(handler)
        elif self._default_mode == "raw_sse":
            self.respond_raw_sse(handler, self._raw_sse_chunks, delay_s=self._default_delay_s)
        else:
            self.respond_openai_stream(handler, ["默认", "输出"])

    def default_post_handler(self, handler: BaseHTTPRequestHandler, req: RecordedRequest):
        self.default_handler(handler, req)

    def default_get_handler(self, handler: BaseHTTPRequestHandler, req: RecordedRequest):
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json; charset=utf-8')
        handler.end_headers()
        handler.wfile.write(b'{"status": "ok"}')

    def respond_openai_stream(
        self,
        handler: BaseHTTPRequestHandler,
        tokens: List[str],
        delay_s: float = 0.0,
        chunk_delay_ms: float = 0.0
    ):
        if delay_s > 0:
            time.sleep(delay_s)

        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        handler.send_header('Cache-Control', 'no-cache')
        handler.send_header('Connection', 'close')
        handler.end_headers()

        for token in tokens:
            chunk = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "mock-model",
                "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}]
            }
            line = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode('utf-8')
            try:
                handler.wfile.write(line)
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            if chunk_delay_ms > 0:
                time.sleep(chunk_delay_ms / 1000.0)

        try:
            handler.wfile.write(b"data: [DONE]\n\n")
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        handler.close_connection = True

    def respond_empty_stream(self, handler: BaseHTTPRequestHandler):
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        handler.send_header('Cache-Control', 'no-cache')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        try:
            handler.wfile.write(b"data: [DONE]\n\n")
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        handler.close_connection = True

    def respond_raw_sse(self, handler: BaseHTTPRequestHandler, chunks: List[str], delay_s: float = 0.0):
        if delay_s > 0:
            time.sleep(delay_s)

        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        handler.send_header('Cache-Control', 'no-cache')
        handler.send_header('Connection', 'close')
        handler.end_headers()

        for chunk_str in chunks:
            try:
                handler.wfile.write(chunk_str.encode('utf-8'))
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
        handler.close_connection = True

    def respond_error(
        self,
        handler: BaseHTTPRequestHandler,
        status_code: int,
        message: str,
        error_type: str = "api_error"
    ):
        handler.send_response(status_code)
        handler.send_header('Content-Type', 'application/json; charset=utf-8')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        err_body = {
            "error": {
                "message": message,
                "type": error_type,
                "code": status_code
            }
        }
        handler.wfile.write(json.dumps(err_body, ensure_ascii=False).encode('utf-8'))
        handler.close_connection = True


class MockApp:
    """Mock application context for MessageBuilder and LLM client testing."""
    def __init__(self):
        self.config = {}


class StringLogHandler(logging.Handler):
    """In-memory log handler for testing log output and sensitive key scrubbing."""
    def __init__(self):
        super().__init__()
        self.log_records: List[logging.LogRecord] = []
        self.stream = io.StringIO()

    def emit(self, record: logging.LogRecord) -> None:
        self.log_records.append(record)
        msg = self.format(record)
        self.stream.write(msg + '\n')

    def get_output(self) -> str:
        return self.stream.getvalue()

    def clear(self) -> None:
        self.log_records.clear()
        self.stream = io.StringIO()


class E2ETestHarness(unittest.TestCase):
    """
    Base test fixture for all E2E test suites (Tiers 1-4).
    Manages deterministic mock server, temp environments, and assertion helpers.
    """
    server: MockLLMServer
    log_handler: StringLogHandler
    temp_dir: tempfile.TemporaryDirectory

    @classmethod
    def setUpClass(cls):
        cls.server = MockLLMServer().start()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'server') and cls.server:
            cls.server.stop()

    def setUp(self):
        self.server.clear_requests()
        self.server.routes.clear()
        self.server.set_streaming_response(["默认", "测试", "输出"])

        self.log_handler = StringLogHandler()
        self.log_handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
        logging.getLogger().addHandler(self.log_handler)

        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        logging.getLogger().removeHandler(self.log_handler)
        self.temp_dir.cleanup()

    # --- Assertion Helpers ---

    def assertNoTimestampsInPayload(self, messages: List[Dict[str, Any]]) -> None:
        """Assert that no message in payload contains internal timestamp metadata"""
        for idx, msg in enumerate(messages):
            self.assertNotIn(
                'timestamp',
                msg,
                f"Message at index {idx} contains forbidden 'timestamp' key: {msg}"
            )
            self.assertTrue(
                'role' in msg and 'content' in msg,
                f"Message at index {idx} missing 'role' or 'content': {msg}"
            )

    def assert_clean_history_payload(self, messages: List[Dict[str, Any]]) -> None:
        self.assertNoTimestampsInPayload(messages)

    def assertValidLLMResult(
        self,
        result: Any,
        expected_status: str,
        expected_reason: Optional[str] = None
    ) -> None:
        """Verify LLMResult contract conformance"""
        self.assertTrue(hasattr(result, 'status'), "Result missing 'status' attribute")
        self.assertEqual(result.status, expected_status)
        if expected_reason is not None:
            self.assertTrue(hasattr(result, 'fallback_reason'), "Result missing 'fallback_reason'")
            self.assertEqual(result.fallback_reason, expected_reason)

    def assertMaskedCorrectly(self, original_key: str, masked_output: str) -> None:
        """Verify that masked output conforms to security guidelines"""
        self.assertFalse(
            MaskingHelper.is_key_exposed(masked_output, original_key),
            f"Raw API key '{original_key}' was exposed in '{masked_output}'"
        )
        if len(original_key) <= 8:
            self.assertEqual(masked_output, "********")
        else:
            self.assertTrue(
                masked_output.startswith(original_key[:4]) and masked_output.endswith(original_key[-4:]),
                f"Masked key '{masked_output}' does not preserve 4-char prefix/suffix"
            )

    def assert_masked_key_in_text(self, secret_key: str, text: str) -> None:
        if secret_key and len(secret_key) > 6:
            self.assertNotIn(
                secret_key,
                text,
                f"Security violation: Plaintext secret key '{secret_key}' found in text"
            )


# Alias
E2EBaseTestCase = E2ETestHarness


class ASTCacheTestHelper:
    """
    Simulates and tests the AST mtime caching mechanism for get_live_client_config.
    Maintains parse count and ensures AST is only parsed when st_mtime_ns changes.
    """
    _cache: Dict[str, Tuple[int, ast.AST]] = {}
    parse_counter: int = 0
    _lock = threading.Lock()

    def __init__(self):
        self._ast_cache: Dict[str, Tuple[int, ast.AST]] = {}
        self.parse_count: int = 0
        self._lock = threading.Lock()

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._cache.clear()
            cls.parse_counter = 0

    def get_config_var(self, file_path: str | Path, var_name: str, default: Any = None) -> Any:
        file_path_str = str(file_path)
        if not os.path.exists(file_path_str):
            return default

        try:
            mtime_ns = os.stat(file_path_str).st_mtime_ns
            with self._lock:
                if file_path_str in self._ast_cache:
                    cached_mtime, cached_tree = self._ast_cache[file_path_str]
                    if cached_mtime == mtime_ns:
                        tree = cached_tree
                    else:
                        tree = None
                else:
                    tree = None

                if tree is None:
                    content = Path(file_path_str).read_text(encoding='utf-8-sig')
                    tree = ast.parse(content)
                    self.parse_count += 1
                    self._ast_cache[file_path_str] = (mtime_ns, tree)

            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.Assign, ast.AnnAssign))
                    and any(
                        isinstance(target, ast.Name) and target.id == var_name
                        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                    )
                ):
                    return ast.literal_eval(node.value)
        except Exception:
            pass
        return default

    @classmethod
    def get_config_value(cls, file_path: Path | str, var_name: str, default: Any = None) -> Any:
        file_path_str = str(file_path)
        if not os.path.exists(file_path_str):
            return default
        try:
            mtime_ns = os.stat(file_path_str).st_mtime_ns
            with cls._lock:
                if file_path_str in cls._cache:
                    cached_mtime, cached_tree = cls._cache[file_path_str]
                    if cached_mtime == mtime_ns:
                        tree = cached_tree
                    else:
                        tree = None
                else:
                    tree = None

                if tree is None:
                    cls.parse_counter += 1
                    content = Path(file_path_str).read_text(encoding='utf-8-sig')
                    tree = ast.parse(content)
                    cls._cache[file_path_str] = (mtime_ns, tree)

            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.Assign, ast.AnnAssign))
                    and any(
                        isinstance(target, ast.Name) and target.id == var_name
                        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                    )
                ):
                    return ast.literal_eval(node.value)
        except Exception:
            pass
        return default


ASTCacheHelper = ASTCacheTestHelper


class MaskingHelper:
    """Validates API Key masking algorithms across short/long keys and logs"""

    @staticmethod
    def mask_key(key: Optional[str]) -> str:
        if not key:
            return ""
        if len(key) <= 8:
            return "********"
        return f"{key[:4]}********{key[-4:]}"

    @staticmethod
    def mask_api_key(key: Optional[str]) -> str:
        return MaskingHelper.mask_key(key)

    @staticmethod
    def is_key_exposed(text: str, raw_key: str) -> bool:
        if not raw_key or len(raw_key) < 5:
            return False
        return raw_key in text


def mask_key_helper(api_key: Optional[str]) -> str:
    return MaskingHelper.mask_key(api_key)


class MockApp:
    """Mock Application instance for testing"""
    def __init__(self):
        from unittest.mock import MagicMock
        self.state = MagicMock()
        self.hotword = MagicMock()
        self.base_dir = Path(".").resolve()


class MicroBuffer:
    """
    Micro-buffering simulator for streaming output.
    Batches token chunks to minimize UI typing redraw events.
    """

    def __init__(
        self,
        batch_size: int = 10,
        flush_delay_ms: float = 20.0,
        trash_punc: str = "，。,.",
        buffer_ms: float = 20.0
    ):
        self.batch_size = batch_size
        self.flush_delay_ms = flush_delay_ms
        self.buffer_ms = buffer_ms
        self.trash_punc = trash_punc
        self.buffer: List[str] = []
        self.emitted_batches: List[str] = []
        self.output_batches: List[str] = []
        self._lock = threading.Lock()

    def _visible_len(self, s: str) -> int:
        import re
        return len(re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', s))

    def add_chunk(self, chunk: str) -> Optional[str]:
        with self._lock:
            self.buffer.append(chunk)
            current_len = sum(self._visible_len(c) for c in self.buffer)
            if current_len >= self.batch_size:
                flushed = "".join(self.buffer)
                self.buffer.clear()
                self.emitted_batches.append(flushed)
                self.output_batches.append(flushed)
                return flushed
        return None

    def push(self, chunk: str) -> None:
        self.add_chunk(chunk)

    def flush(self, strip_trash_punc: bool = True) -> str:
        with self._lock:
            if not self.buffer:
                return ""
            remaining = "".join(self.buffer)
            self.buffer.clear()
            if strip_trash_punc and self.trash_punc:
                remaining = remaining.rstrip(self.trash_punc)
            self.emitted_batches.append(remaining)
            self.output_batches.append(remaining)
            return remaining

    def get_full_output(self) -> str:
        with self._lock:
            return "".join(self.emitted_batches) + "".join(self.buffer)

    def get_full_text(self) -> str:
        return self.get_full_output()


MicroBufferSimulator = MicroBuffer


class MockStopMonitor:
    """Mock user interrupt monitor (ESC key simulator)"""
    def __init__(self, stop_at_call: int = -1):
        self.call_count = 0
        self.stop_at_call = stop_at_call
        self._stopped = False

    def reset(self) -> None:
        self.call_count = 0
        self._stopped = False

    def trigger_stop(self) -> None:
        self._stopped = True

    def should_stop(self) -> bool:
        self.call_count += 1
        if self.stop_at_call > 0 and self.call_count >= self.stop_at_call:
            self._stopped = True
        return self._stopped


class TempEnvManager:
    """Temporary sandbox context manager for isolated file-system tests"""
    def __init__(self):
        self.temp_dir: Optional[Path] = None

    def __enter__(self) -> Path:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="capswriter_e2e_"))
        return self.temp_dir

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(str(self.temp_dir), ignore_errors=True)
