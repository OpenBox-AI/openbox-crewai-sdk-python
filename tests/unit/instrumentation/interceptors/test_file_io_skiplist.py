"""Skiplist tests for file_io instrumentation."""

from __future__ import annotations

import pytest

from openbox.instrumentation.interceptors.file_io import _should_ignore_file_path


@pytest.mark.parametrize(
    "path",
    [
        "/usr/lib/python3.11/lib-dynload/_socket.cpython-311-darwin.so",
        "/Users/x/.venv/lib/extension.so",
        "/path/to/something.dylib",
        "/path/to/module.pyc",
        "/path/to/module.pyo",
    ],
)
def test_dynamic_loader_artifacts_ignored(path: str) -> None:
    assert _should_ignore_file_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/Users/x/code/data.csv",
        "/Users/x/code/config.json",
        "/tmp/user_input.txt",
    ],
)
def test_user_files_not_ignored(path: str) -> None:
    assert _should_ignore_file_path(path) is False


def test_existing_ignores_still_apply() -> None:
    assert _should_ignore_file_path("/anywhere/__pycache__/file.cpython-311.pyc") is True
    assert _should_ignore_file_path("/anywhere/site-packages/lib.py") is True
    assert _should_ignore_file_path("/dev/null") is True
    assert _should_ignore_file_path("/System/Library/Frameworks/x") is True
    assert _should_ignore_file_path("/tmp/crewai:abc.lock") is True
