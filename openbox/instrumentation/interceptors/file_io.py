"""File I/O instrumentation (disabled by default)."""

from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path
from typing import Any

from openbox.core.spans import FileSpanData, Stage
from openbox.instrumentation.interceptors._runtime import (
    evaluate_completed,
    evaluate_started,
    get_current_trace_id,
    get_runtime,
)
from openbox.utils import (
    format_span_id,
    format_trace_id,
    get_current_execution_frame,
)

logger = logging.getLogger("openbox")

_original_open: Any = None


class TracedFile:
    """Wraps file objects to intercept read/write for governance."""

    def __init__(self, file_obj: Any, file_path: str, mode: str) -> None:
        self._file = file_obj
        self._file_path = file_path
        self._mode = mode

    def read(self, *args: Any, **kwargs: Any) -> Any:
        ctx = _emit_started(self._file_path, self._mode, "read")
        data = self._file.read(*args, **kwargs)
        if ctx is not None:
            _emit_completed(ctx, self._file_path, self._mode, "read")
        return data

    def write(self, data: Any) -> Any:
        ctx = _emit_started(self._file_path, self._mode, "write")
        result = self._file.write(data)
        if ctx is not None:
            _emit_completed(ctx, self._file_path, self._mode, "write")
        return result

    def readline(self, *args: Any, **kwargs: Any) -> Any:
        return self._file.readline(*args, **kwargs)

    def readlines(self, *args: Any, **kwargs: Any) -> Any:
        return self._file.readlines(*args, **kwargs)

    def writelines(self, lines: Any) -> Any:
        ctx = _emit_started(self._file_path, self._mode, "write")
        result = self._file.writelines(lines)
        if ctx is not None:
            _emit_completed(ctx, self._file_path, self._mode, "write")
        return result

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> TracedFile:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __iter__(self) -> Any:
        return iter(self._file)

    def __next__(self) -> Any:
        return next(self._file)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._file, name)


def _emit_started(
    file_path: str, mode: str, operation: str
) -> tuple[int, str, str, int] | None:
    """Pre-IO governance gate. Raises GovernanceBlockedError on BLOCK/HALT."""
    if _should_ignore_file_path(file_path):
        return None

    trace_id = get_current_trace_id()
    rt = get_runtime()
    if trace_id is None or not rt:
        return None

    binding = rt.binding_for_trace(trace_id)
    if binding is None:
        return None

    if get_current_execution_frame() is None:
        return None

    span_id = format_span_id(secrets.randbits(64))
    trace_id_str = format_trace_id(trace_id)
    start_ns = time.time_ns()

    span_data = FileSpanData(
        stage=Stage.STARTED,
        span_id=span_id,
        trace_id=trace_id_str,
        name=f"file.{operation}",
        semantic_type=f"file_{operation}",
        start_time=start_ns,
        file_path=file_path,
        file_mode=mode,
        file_operation=operation,
        data={
            "file_path": file_path,
            "file_mode": mode,
            "file_operation": operation,
        },
    )
    evaluate_started(trace_id, span_data)
    return (trace_id, span_id, trace_id_str, start_ns)


def _emit_completed(
    ctx: tuple[int, str, str, int], file_path: str, mode: str, operation: str
) -> None:
    """Post-IO telemetry. Reuses the started span_id; never raises."""
    trace_id, span_id, trace_id_str, start_ns = ctx
    end_ns = time.time_ns()
    span_data = FileSpanData(
        stage=Stage.COMPLETED,
        span_id=span_id,
        trace_id=trace_id_str,
        name=f"file.{operation}",
        semantic_type=f"file_{operation}",
        start_time=start_ns,
        end_time=end_ns,
        duration_ns=end_ns - start_ns,
        file_path=file_path,
        file_mode=mode,
        file_operation=operation,
        data={
            "file_path": file_path,
            "file_mode": mode,
            "file_operation": operation,
        },
    )
    evaluate_completed(trace_id, span_data)


_IGNORED_PATH_PARTS: frozenset[str] = frozenset(
    {
        "site-packages",
        "__pycache__",
        ".pytest_cache",
        "openbox_logs",
    }
)

_IGNORED_PATH_PREFIXES: tuple[str, ...] = (
    "/System/",
    "/proc/",
    "/sys/",
    "/dev/",
)

# Dynamic-loader artifacts: Python bytecode and native shared libraries.
# Without this filter, instrument_file_io=True fires governance on every
# import — each .pyc / .so / .dylib resolved by the loader.
_IGNORED_PATH_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo", ".so", ".dylib")


def _should_ignore_file_path(file_path: str) -> bool:
    """Ignore known CrewAI/SDK/OS-internal files that are not user intent."""
    path = Path(file_path)
    name = path.name

    if name.startswith("crewai:") and name.endswith(".lock"):
        return True

    parts = set(path.parts)
    if parts & _IGNORED_PATH_PARTS:
        return True

    if file_path.startswith(_IGNORED_PATH_PREFIXES):
        return True

    if name.endswith(_IGNORED_PATH_SUFFIXES):
        return True

    return False


def _mode_is_write(mode: str) -> bool:
    return any(c in mode for c in ("w", "a", "x", "+"))


def setup_file_io_instrumentation() -> None:
    global _original_open
    import builtins

    if _original_open is not None:
        return

    _original_open = builtins.open

    def _traced_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        ctx = None
        if (
            get_runtime()
            and get_current_trace_id() is not None
            and _mode_is_write(mode)
        ):
            ctx = _emit_started(str(file), mode, "write")
        file_obj = _original_open(file, mode, *args, **kwargs)
        if ctx is not None:
            _emit_completed(ctx, str(file), mode, "write")
        if get_runtime() and get_current_trace_id() is not None:
            return TracedFile(file_obj, str(file), mode)
        return file_obj

    builtins.open = _traced_open  # type: ignore[assignment]


def teardown_file_io_instrumentation() -> None:
    global _original_open
    import builtins

    if _original_open is not None:
        builtins.open = _original_open  # type: ignore[assignment]
        _original_open = None
