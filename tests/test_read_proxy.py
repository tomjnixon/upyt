import pytest

from unittest.mock import Mock
from typing import Iterator
from io import StringIO
from itertools import islice
from pathlib import Path

from upyt.read_proxy import (
    replace,
    match,
    ReadProxy,
)


class TestReplace:
    def test_replacement(self) -> None:
        seq = iter("abc12312X")
        out = replace("123", "<1-2-3>")(seq)
        for got, exp in zip(out, "abc<1-2-3>12X"):
            assert got == exp

    def test_retain_while_uncertain(self) -> None:
        seq = iter(list("abc12") + [None] + list("3"))
        out = replace("123", "<1-2-3>")(seq)
        for got, exp in zip(out, list("abc") + [None] + list("<1-2-3>")):
            assert got == exp


class TestCallOnMatch:
    def test_called(self) -> None:
        seq = "abc12312X"
        mock = Mock()
        out = match("123", mock)(seq)

        # Should not be called initially
        for exp, got in zip("abc", out):
            assert not mock.called
            assert got == exp

        # When we encounter the matched sequence we should be called
        for exp, got in zip("123", out):
            assert len(mock.mock_calls) == 1
            assert got == exp

        # And whatever follows should come out afterwards (with no further
        # calls)
        for exp, got in zip("12X", out):
            assert len(mock.mock_calls) == 1
            assert got == exp

    def test_retain_while_uncertain(self) -> None:
        seq = iter(list("abc12") + [None] + list("3"))
        mock = Mock()
        out = match("123", mock)(seq)

        for exp, got in zip(list("abc") + [None], out):
            assert not mock.called
            assert got == exp

        for exp, got in zip(list("123"), out):
            assert len(mock.mock_calls) == 1
            assert got == exp

    def test_breakout_with_exception(self) -> None:
        seq = iter("abc123456")
        mock = Mock(side_effect=NotImplementedError)
        out = match("123", mock)(seq)

        for exp, got in zip("abc", out):
            assert got == exp

        # When called, we should hit an exception as expected
        with pytest.raises(NotImplementedError):
            next(out)

        # The input should have been consumed exactly up to the end of the
        # matched sequence -- and no more!
        assert "".join(seq) == "456"


class TestReadProxy:
    def test_read_whole(self) -> None:
        f = StringIO("ABC")
        p = ReadProxy(f, [])

        assert p.read() == "ABC"
        assert p.read() == ""

    def test_read_partial(self) -> None:
        f = StringIO("ABC")
        p = ReadProxy(f, [])

        assert p.read(1) == "A"
        assert p.read(2) == "BC"
        assert p.read(3) == ""

    def test_fileno(self, tmp_path: Path) -> None:
        filename = tmp_path / "file.txt"
        filename.write_text("ABC")
        with filename.open("r") as f:
            p = ReadProxy(f, [])
            assert p.fileno() == f.fileno()

    def test_filters(self, tmp_path: Path) -> None:
        f = StringIO("ABC12312X")
        p = ReadProxy(
            f,
            [
                replace("123", "<1-2-3>"),
                replace("<", "<<<"),
                replace(">", ">>>"),
            ],
        )

        assert p.read() == "ABC<<<1-2-3>>>12X"
