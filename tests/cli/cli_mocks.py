import pytest

from unittest.mock import Mock

from typing import Callable, Any

from pathlib import Path
from importlib import import_module

import shlex

import upyt.connection
import upyt.upy_repl
import upyt.upy_fs
import upyt.cli

sub_modules = [
    path.with_suffix("").name
    for path in Path(upyt.cli.__file__).parent.glob("*.py")
    if not path.name.startswith("_")
]


@pytest.fixture
def mock_connection_from_specification(monkeypatch: Any) -> Mock:
    mock = Mock(__enter__=Mock())
    mock_conn = mock.return_value
    mock_conn.__enter__ = Mock(return_value=mock_conn)
    mock_conn.__exit__ = Mock(return_value=None)
    mock_conn.timeout_override.return_value.__enter__ = Mock()
    mock_conn.timeout_override.return_value.__exit__ = Mock(return_value=None)

    monkeypatch.setattr(upyt.connection.Connection, "from_specification", mock)
    return mock


CliFn = Callable[[list[str]], None]


@pytest.fixture
def cli(monkeypatch: Any, mock_connection_from_specification: Mock) -> CliFn:
    """
    A function which takes a series of arguments to pass to the CLI

    Sets up a mock device (via UPYT_DEVICE environment variable) and nops-out
    frequently-used functions which might attempt to talk to it.
    """
    monkeypatch.setenv("UPYT_DEVICE", "/dev/mock")

    interrupt_and_enter_repl = Mock(return_value=b"")
    upy_filesystem = Mock()
    upy_filesystem.return_value.__enter__ = Mock()
    upy_filesystem.return_value.__exit__ = Mock(return_value=None)

    # NB: Because we import upy_filesystem and interrupt_and_enter_repl by name
    # everywhere we have to monkeypatch it everywhere too...
    for name in sub_modules:
        import_module(f"upyt.cli.{name}")
        sub_module = getattr(upyt.cli, name)
        if hasattr(sub_module, "upy_filesystem"):
            monkeypatch.setattr(sub_module, "upy_filesystem", upy_filesystem)
        if hasattr(sub_module, "interrupt_and_enter_repl"):
            monkeypatch.setattr(
                sub_module, "interrupt_and_enter_repl", interrupt_and_enter_repl
            )

    def run(args: list[str]) -> None:
        upyt.cli.main(args)

    return run


@pytest.fixture
def conn_cli(
    ser: upyt.connection.Connection,
    mock_connection_from_specification: Mock,
    monkeypatch: Any,
) -> CliFn:
    """
    Like cli, but backed by a real, connected hardware device.
    """
    mock_connection_from_specification.return_value = ser

    monkeypatch.setenv("UPYT_DEVICE", "/dev/mock")

    def run(args: list[str]) -> None:
        upyt.cli.main(args)

    # Prevent exiting context manager closing the serial port...
    monkeypatch.setattr(ser, "close", Mock())

    return run
