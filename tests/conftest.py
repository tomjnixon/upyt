import pytest

from upyt.connection import Connection
from upyt.upy_fs import FilesystemAPI, upy_filesystem


def pytest_addoption(parser):
    parser.addoption(
        "--board",
        type=str,
        help="""
            Run live hardware tests on the board attached to the specified
            serial port.
        """,
    )


@pytest.fixture(scope='session')
def board(request):
    return request.config.getoption('board', skip=True)

@pytest.fixture(scope='session')
def ser(board) -> Connection:
    with Connection.from_specification(board) as ser:
        yield ser

@pytest.fixture
def fs(ser: Connection) -> FilesystemAPI:
    with upy_filesystem(ser) as fs:
        yield fs

@pytest.fixture
def dev_tmpdir(fs: FilesystemAPI) -> str:
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
