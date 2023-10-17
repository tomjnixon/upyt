import pytest

from typing import Iterator

from upyt.connection import Connection
from upyt.upy_fs import FilesystemAPI, upy_filesystem


def pytest_addoption(parser):
    parser.addoption(
        "--device",
        type=str,
        help="Run live hardware tests on the attached device.",
    )


@pytest.fixture(scope="session")
def board(request):
    return request.config.getoption("device", skip=True)


@pytest.fixture(scope="session")
def ser(board) -> Iterator[Connection]:
    with Connection.from_specification(board) as ser:
        yield ser


@pytest.fixture
def fs(ser: Connection) -> Iterator[FilesystemAPI]:
    with upy_filesystem(ser) as fs:
        yield fs


@pytest.fixture
def dev_tmpdir(fs: FilesystemAPI) -> Iterator[str]:
    name = "/test"
    try:
        fs.remove_recursive(name)
    except OSError:  # Doesn't exist
        pass

    fs.mkdir(name, parents=True)

    try:
        yield name
    finally:
        try:
            fs.remove_recursive(name)
        except OSError:
            pass


@pytest.fixture
def dev_tmpdir_repl(ser: Connection) -> Iterator[str]:
    """
    Like dev_tmpdir, but doesn't keep the device in fs mode during the test
    run.
    """
    with upy_filesystem(ser) as fs:
        name = "/test"
        try:
            fs.remove_recursive(name)
        except OSError:  # Doesn't exist
            pass

        fs.mkdir(name, parents=True)

    try:
        yield name
    finally:
        with upy_filesystem(ser) as fs:
            try:
                fs.remove_recursive(name)
            except OSError:
                pass
