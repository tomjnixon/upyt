import pytest

from upyt.connection import SerialConnection


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
def ser(board):
    with SerialConnection(board, timeout=1) as ser:
        yield ser
