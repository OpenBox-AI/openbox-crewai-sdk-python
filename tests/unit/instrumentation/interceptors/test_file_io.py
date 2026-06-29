from __future__ import annotations

import builtins
import os
from unittest.mock import MagicMock

import pytest

from openbox.core.errors import GovernanceBlockedError
from openbox.core.types import GovernanceResponse, Verdict
from openbox.instrumentation.interceptors import file_io

from ..conftest import active_span as _active_span
from ..conftest import install_runtime


def _install_runtime(**kwargs):
    kwargs.setdefault("activity_type", "file_operation")
    return install_runtime(**kwargs)


@pytest.fixture(autouse=True)
def _teardown_file_io():
    yield
    file_io.teardown_file_io_instrumentation()


@pytest.fixture
def governed_file_io() -> tuple[MagicMock, MagicMock]:
    sp, gc = _install_runtime()
    file_io.setup_file_io_instrumentation()
    return sp, gc


@pytest.fixture
def tmp_file(tmp_path) -> str:
    p = tmp_path / "data.txt"
    p.write_text("hello world")
    return str(p)


class TestModeIsWrite:
    @pytest.mark.parametrize("mode", ["r", "rb", "rt"])
    def test_read_modes_are_not_writes(self, mode):
        assert file_io._mode_is_write(mode) is False

    @pytest.mark.parametrize("mode", ["w", "wb", "a", "ab", "x", "r+", "w+b"])
    def test_write_append_exclusive_and_plus_modes_are_writes(self, mode):
        assert file_io._mode_is_write(mode) is True


class TestShouldIgnoreFilePath:
    @pytest.mark.parametrize(
        "path",
        [
            "/tmp/data.txt",
            "/Users/x/Projects/repo/src/file.py",
            "/private/var/folders/abc/T/tmpxyz/data.txt",
        ],
    )
    def test_user_file_is_not_ignored(self, path):
        assert file_io._should_ignore_file_path(path) is False

    def test_crewai_lock_is_ignored(self):
        assert file_io._should_ignore_file_path("/tmp/crewai:foo.lock") is True

    @pytest.mark.parametrize(
        "path",
        [
            "openbox_logs/agent_abc.log",
            "/Users/x/openbox_logs/agent_def.log",
            "./openbox_logs/agent_xyz.log",
        ],
    )
    def test_sdk_debug_log_is_ignored(self, path):
        assert file_io._should_ignore_file_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/Users/x/repo/.venv/lib/python3.11/site-packages/crewai/translations/en.json",
            "/opt/project/venv/lib/site-packages/anything.py",
            "site-packages/whatever.json",
        ],
    )
    def test_site_packages_is_ignored(self, path):
        assert file_io._should_ignore_file_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/Users/x/repo/__pycache__/foo.cpython-311.pyc",
            "/Users/x/repo/.pytest_cache/v/cache/lastfailed",
        ],
    )
    def test_python_caches_are_ignored(self, path):
        assert file_io._should_ignore_file_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/System/Library/CoreServices/SystemVersion.plist",
            "/proc/meminfo",
            "/sys/devices/system/cpu/online",
            "/dev/urandom",
        ],
    )
    def test_os_internal_paths_are_ignored(self, path):
        assert file_io._should_ignore_file_path(path) is True


class TestSetupTeardown:
    def test_setup_replaces_builtins_open(self):
        original = builtins.open
        file_io.setup_file_io_instrumentation()
        try:
            assert builtins.open is not original
            assert file_io._original_open is original
        finally:
            file_io.teardown_file_io_instrumentation()

    def test_double_setup_is_idempotent(self):
        file_io.setup_file_io_instrumentation()
        wrapped_once = builtins.open
        file_io.setup_file_io_instrumentation()
        assert builtins.open is wrapped_once

    def test_teardown_restores_builtins_open(self):
        original = builtins.open
        file_io.setup_file_io_instrumentation()
        file_io.teardown_file_io_instrumentation()
        assert builtins.open is original
        assert file_io._original_open is None

    def test_teardown_when_not_set_up_is_safe(self):
        original = builtins.open
        file_io.teardown_file_io_instrumentation()
        assert builtins.open is original


class TestTracedOpen:
    def test_returns_raw_file_outside_active_span(self, governed_file_io, tmp_file):
        f = builtins.open(tmp_file, "r")
        try:
            assert not isinstance(f, file_io.TracedFile)
        finally:
            f.close()

    def test_returns_traced_file_when_runtime_and_span_active(
        self, governed_file_io, tmp_file
    ):
        with _active_span():
            f = builtins.open(tmp_file, "r")
            try:
                assert isinstance(f, file_io.TracedFile)
            finally:
                f.close()

    def test_open_for_write_evaluates_governance_before_open(
        self, governed_file_io, tmp_path
    ):
        _, gc = governed_file_io
        path = str(tmp_path / "out.txt")
        with _active_span():
            builtins.open(path, "w").close()
        assert gc.evaluate.call_count >= 1
        first_payload = gc.evaluate.call_args_list[0].args[0]
        assert first_payload["spans"][0]["file_operation"] == "write"
        assert first_payload["spans"][0]["file_mode"] == "w"

    def test_open_for_write_blocks_on_halt(self, tmp_path):
        _install_runtime(
            response=GovernanceResponse(verdict=Verdict.HALT, reason="forbidden"),
        )
        file_io.setup_file_io_instrumentation()
        path = str(tmp_path / "blocked.txt")
        with _active_span():
            with pytest.raises(GovernanceBlockedError):
                builtins.open(path, "w")
        assert not os.path.exists(path)

    def test_open_for_read_does_not_evaluate_at_open(self, governed_file_io, tmp_file):
        _, gc = governed_file_io
        with _active_span():
            builtins.open(tmp_file, "r").close()
        assert gc.evaluate.call_count == 0


class TestTracedFileRead:
    def test_read_evaluates_governance(self, governed_file_io, tmp_file):
        _, gc = governed_file_io
        with _active_span():
            with builtins.open(tmp_file, "r") as f:
                assert f.read() == "hello world"
        assert gc.evaluate.call_count == 2
        started_payload = gc.evaluate.call_args_list[0].args[0]
        completed_payload = gc.evaluate.call_args_list[1].args[0]
        assert started_payload["spans"][0]["file_operation"] == "read"
        assert started_payload["spans"][0]["stage"] == "started"
        assert completed_payload["spans"][0]["stage"] == "completed"
        assert completed_payload["spans"][0]["span_id"] == started_payload["spans"][0]["span_id"]
        assert completed_payload["spans"][0]["duration_ns"] is not None

    def test_read_blocks_on_block_verdict(self, tmp_file):
        _install_runtime(
            response=GovernanceResponse(verdict=Verdict.BLOCK, reason="no read"),
        )
        file_io.setup_file_io_instrumentation()
        with _active_span():
            with builtins.open(tmp_file, "r") as f:
                with pytest.raises(GovernanceBlockedError):
                    f.read()


class TestTracedFileWrite:
    def test_write_evaluates_governance(self, governed_file_io, tmp_path):
        _, gc = governed_file_io
        path = str(tmp_path / "out.txt")
        with _active_span():
            with builtins.open(path, "w") as f:
                f.write("data")
        assert gc.evaluate.call_count == 4

    def test_write_blocks_on_halt_at_write_call(self, governed_file_io, tmp_path):
        _, gc = governed_file_io
        gc.evaluate.side_effect = [
            GovernanceResponse(verdict=Verdict.ALLOW),
            GovernanceResponse(verdict=Verdict.ALLOW),
            GovernanceResponse(verdict=Verdict.HALT, reason="halt write"),
        ]
        path = str(tmp_path / "out.txt")
        with _active_span():
            with builtins.open(path, "w") as f:
                with pytest.raises(GovernanceBlockedError):
                    f.write("data")


class TestTracedFileLineOps:
    def test_writelines_evaluates_governance(self, governed_file_io, tmp_path):
        _, gc = governed_file_io
        path = str(tmp_path / "out.txt")
        with _active_span():
            with builtins.open(path, "w") as f:
                f.writelines(["a\n", "b\n"])
        assert gc.evaluate.call_count == 4

    def test_readline_does_not_evaluate(self, governed_file_io, tmp_file):
        _, gc = governed_file_io
        with _active_span():
            with builtins.open(tmp_file, "r") as f:
                assert f.readline() == "hello world"
        assert gc.evaluate.call_count == 0

    def test_readlines_does_not_evaluate(self, governed_file_io, tmp_file):
        _, gc = governed_file_io
        with _active_span():
            with builtins.open(tmp_file, "r") as f:
                assert f.readlines() == ["hello world"]
        assert gc.evaluate.call_count == 0


class TestTracedFileContextManager:
    def test_context_manager_closes_file(self, governed_file_io, tmp_file):
        with _active_span():
            with builtins.open(tmp_file, "r") as f:
                assert isinstance(f, file_io.TracedFile)
            assert f._file.closed

    def test_iteration_passes_through(self, governed_file_io, tmp_path):
        path = tmp_path / "lines.txt"
        path.write_text("one\ntwo\n")
        with _active_span():
            with builtins.open(str(path), "r") as f:
                assert list(f) == ["one\n", "two\n"]

    def test_getattr_proxies_to_underlying_file(self, governed_file_io, tmp_file):
        with _active_span():
            with builtins.open(tmp_file, "r") as f:
                assert f.name == tmp_file


class TestEmitStartedEarlyExits:
    def test_no_runtime_does_nothing(self, tmp_file):
        with _active_span():
            assert file_io._emit_started(tmp_file, "r", "read") is None

    def test_no_active_span_does_nothing(self, tmp_file):
        _, gc = _install_runtime()
        assert file_io._emit_started(tmp_file, "r", "read") is None
        assert gc.evaluate.call_count == 0

    def test_no_agent_context_does_nothing(self, tmp_file):
        sp, gc = _install_runtime()
        sp.get_agent_context.return_value = None
        with _active_span():
            assert file_io._emit_started(tmp_file, "r", "read") is None
        assert gc.evaluate.call_count == 0
