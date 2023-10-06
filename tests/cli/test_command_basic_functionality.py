"""
Basic sanity checks for the basic CLI commands to make sure they work at least
at a basic level...
"""

import pytest

from typing import Any
from pathlib import Path

from upyt.connection import Connection
from upyt.upy_repl import expect, interrupt_and_enter_repl, expect_endswith
from upyt.upy_fs import upy_filesystem

from trees import read_device_tree
from cli_mocks import CliFn, cli, conn_cli, mock_connection_from_specification


class TestLs:
    def test_short(self, cli: CliFn, tmp_path: Path, capsys: Any) -> None:
        (tmp_path / "dir").mkdir()
        (tmp_path / "file").touch()
        cli(["ls", str(tmp_path)])

        stdout, _stderr = capsys.readouterr()
        assert set(stdout.splitlines()) == {"dir/", "file"}

    def test_long(self, cli: CliFn, tmp_path: Path, capsys: Any) -> None:
        (tmp_path / "dir").mkdir()
        (tmp_path / "file").write_bytes(b"four")
        cli(["ls", "-l", str(tmp_path)])

        stdout, _stderr = capsys.readouterr()
        assert set(stdout.splitlines()) == {
            "       0 dir/",
            "       4 file",
        }


class TestMkdir:
    def test_single(self, cli: CliFn, tmp_path: Path) -> None:
        cli(["mkdir", str(tmp_path / "foo")])
        assert (tmp_path / "foo").is_dir()

    def test_multiple_levels(self, cli: CliFn, tmp_path: Path) -> None:
        cli(["mkdir", "-p", str(tmp_path / "foo" / "bar")])
        assert (tmp_path / "foo" / "bar").is_dir()


class TestRm:
    def test_file(self, cli: CliFn, tmp_path: Path) -> None:
        (tmp_path / "foo").touch()
        cli(["rm", str(tmp_path / "foo")])
        assert not (tmp_path / "foo").is_file()

    def test_missing_file_without_f(self, cli: CliFn, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            cli(["rm", str(tmp_path / "foo")])

    def test_missing_file_with_f(self, cli: CliFn, tmp_path: Path) -> None:
        cli(["rm", "-f", str(tmp_path / "foo")])

    def test_dir_fail_without_r(self, cli: CliFn, tmp_path: Path) -> None:
        (tmp_path / "foo").mkdir()
        with pytest.raises(SystemExit):
            cli(["rm", str(tmp_path / "foo")])
        assert (tmp_path / "foo").is_dir()

    def test_dir_with_r(self, cli: CliFn, tmp_path: Path) -> None:
        (tmp_path / "foo").mkdir()
        (tmp_path / "foo" / "bar").touch()
        cli(["rm", "-r", str(tmp_path / "foo")])
        assert not (tmp_path / "foo").is_dir()


class TestCat:
    def test_single_file(self, cli: CliFn, tmp_path: Path, capsys: Any) -> None:
        (tmp_path / "foo").write_text("well\nhello")
        cli(["cat", str(tmp_path / "foo")])

        stdout, _stderr = capsys.readouterr()
        assert stdout == "well\nhello"

    def test_multiple_files(self, cli: CliFn, tmp_path: Path, capsys: Any) -> None:
        (tmp_path / "foo").write_text("I'm foo,")
        (tmp_path / "bar").write_text(" and I'm bar.")
        cli(["cat", str(tmp_path / "foo"), str(tmp_path / "bar")])

        stdout, _stderr = capsys.readouterr()
        assert stdout == "I'm foo, and I'm bar."


class TestCp:
    def test_file(self, cli: CliFn, tmp_path: Path) -> None:
        (tmp_path / "foo").write_text("Hello")
        cli(["cp", str(tmp_path / "foo"), str(tmp_path / "bar")])
        assert (tmp_path / "bar").read_text() == (tmp_path / "foo").read_text()

    def test_dir_without_r_fails(self, cli: CliFn, tmp_path: Path) -> None:
        (tmp_path / "foo").mkdir()

        # XXX: We have to import this here otherwise our monkey patch to
        # interrupt_and_enter_repl will happen *after* the module imports its
        # own reference to the real function.
        from upyt.cli.cp import RecursionNotAllowedError

        with pytest.raises(RecursionNotAllowedError):
            cli(["cp", str(tmp_path / "foo"), str(tmp_path / "bar")])

    def test_recursion(self, cli: CliFn, tmp_path: Path) -> None:
        (tmp_path / "foo").mkdir()
        (tmp_path / "foo" / "bar").write_text("hello")

        cli(["cp", "-r", str(tmp_path / "foo"), str(tmp_path / "baz")])

        assert (tmp_path / "baz").is_dir()
        assert (tmp_path / "baz" / "bar").read_text() == "hello"


class TestSync:
    def test_basic_sync(
        self,
        ser: Connection,
        conn_cli: CliFn,
        tmp_path: Path,
        dev_tmpdir_repl: str,
    ) -> None:
        (tmp_path / "foo").write_text("Hello")
        (tmp_path / "bar").mkdir()
        (tmp_path / "bar" / "baz").write_text("World")

        conn_cli(["sync", str(tmp_path), f":{dev_tmpdir_repl}"])

        with upy_filesystem(ser) as fs:
            device_tree = read_device_tree(fs, dev_tmpdir_repl)

        assert isinstance(device_tree, dict)
        device_tree.pop(".upyt_id.txt")
        assert device_tree == {
            "foo": b"Hello",
            "bar": {"baz": b"World"},
        }

    def test_exclusions(
        self,
        ser: Connection,
        conn_cli: CliFn,
        tmp_path: Path,
        dev_tmpdir_repl: str,
    ) -> None:
        (tmp_path / ".git").mkdir()  # Excluded by default
        (tmp_path / "exclude_me").touch()  # Excluded by --exclude
        (tmp_path / "foo").write_text("Hello")

        conn_cli(["sync", "--exclude=exclude_*", str(tmp_path), f":{dev_tmpdir_repl}"])

        with upy_filesystem(ser) as fs:
            device_tree = read_device_tree(fs, dev_tmpdir_repl)

        assert isinstance(device_tree, dict)
        device_tree.pop(".upyt_id.txt")
        assert device_tree == {
            "foo": b"Hello",
        }

    def test_no_default_exclusions(
        self,
        ser: Connection,
        conn_cli: CliFn,
        tmp_path: Path,
        dev_tmpdir_repl: str,
    ) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "foo").write_text("Hello")

        conn_cli(
            ["sync", "--no-default-exclusions", str(tmp_path), f":{dev_tmpdir_repl}"]
        )

        with upy_filesystem(ser) as fs:
            device_tree = read_device_tree(fs, dev_tmpdir_repl)

        assert isinstance(device_tree, dict)
        device_tree.pop(".upyt_id.txt")
        assert device_tree == {
            ".git": {},
            "foo": b"Hello",
        }


class TestReset:
    def test_basic_reset(
        self,
        ser: Connection,
        conn_cli: CliFn,
    ) -> None:
        ser.write(b"a = 123\r\n")
        expect(ser, b"a = 123\r\n>>> ")

        # Check variable is defined
        ser.write(b"print(a)\r\n")
        expect(ser, b"print(a)\r\n")
        expect(ser, b"123\r\n>>> ")

        conn_cli(["reset"])

        # Absorb reset message, interrupt any main.py if there was one
        interrupt_and_enter_repl(ser)

        # Check variable nolonger defined
        ser.write(b"'a' in locals()\r\n")
        expect(ser, b"'a' in locals()\r\n")
        expect(ser, b"False\r\n>>> ")

    def test_reset_to_repl(
        self,
        ser: Connection,
        conn_cli: CliFn,
    ) -> None:
        with upy_filesystem(ser) as fs:
            try:
                orig_main_py = fs.read_file("/main.py")
            except OSError:
                orig_main_py = None

            # Setup an infinite-looping main.py
            fs.write_file(
                "/main.py",
                (
                    b"import time\n"
                    b"while True:\n"
                    b"  print('...')\n"
                    b"  time.sleep(1)\n"
                ),
            )

        try:
            conn_cli(["reset", "--repl"])

            # Verify we reach the REPL, rather than our main
            ser.write(b"print(0x10)\r\n")
            expect(ser, b"print(0x10)\r\n")
            expect(ser, b"16\r\n>>> ")
        finally:
            interrupt_and_enter_repl(ser)
            with upy_filesystem(ser) as fs:
                if orig_main_py is not None:
                    fs.write_file("/main.py", orig_main_py)
                else:
                    fs.remove_recursive("/main.py")


def test_interrupt(ser: Connection, conn_cli: CliFn) -> None:
    ser.write(b"import time; time.sleep(60)\r\n")
    expect(ser, b"import time; time.sleep(60)\r\n")

    conn_cli(["interrupt"])

    # Absorb reset message, interrupt any main.py if there was one
    interrupt_and_enter_repl(ser)

    # Verify we're back in the REPL
    ser.write(b"print(0x10)\r\n")
    expect(ser, b"print(0x10)\r\n")
    expect(ser, b"16\r\n>>> ")
