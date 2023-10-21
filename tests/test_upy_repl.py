from typing import Optional

import pytest

import time

from upyt.connection import Connection

from unittest.mock import Mock

from textwrap import dedent

from contextlib import contextmanager

from upyt.upy_repl import (
    MicroPythonReplError,
    expect,
    expect_endswith,
    interrupt_and_enter_repl,
    paste_exec,
    raw_mode,
    raw_paste_exec,
    soft_reset_directly_into_repl,
)


class TestExpect:
    def test_matches(self) -> None:
        ser = Mock()
        ser.read.return_value = b"yes"

        assert expect(ser, b"yes") == b"yes"

    def test_no_match(self) -> None:
        ser = Mock()
        ser.read.return_value = b"no"

        with pytest.raises(MicroPythonReplError) as exc_info:
            expect(ser, b"yes")

        assert exc_info.value.args[0] == b"no"


class TestExpectEndswIth:
    def test_matches(self) -> None:
        ser = Mock()
        ser.read_until.return_value = b"well...yes"

        assert expect_endswith(ser, b"yes") == b"well...yes"

    def test_no_match(self) -> None:
        ser = Mock()
        ser.read_until.return_value = b"well...no"

        with pytest.raises(MicroPythonReplError) as exc_info:
            expect_endswith(ser, b"yes")

        assert exc_info.value.args[0] == b"well...no"


class TestInterruptAndEnterRepl:
    def test_no_contention_next_prompt_unread(self, ser: Connection) -> None:
        interrupt_and_enter_repl(ser)

        # Confirm the REPL is working and that we've gone back to a prompt
        ser.write(b"print('hello')\r")
        assert ser.read_until(b"\r\nhello") == (
            b"print('hello')\r\n" b"hello"  # Echo  # Output
        )

        # NB: newline and prompt (>>>) still left in buffer
        assert interrupt_and_enter_repl(ser) == b"\r\n>>> "

    def test_no_contention_next_prompt_read(self, ser: Connection) -> None:
        interrupt_and_enter_repl(ser)

        # Confirm the REPL is working and that we've gone back to a prompt
        ser.write(b"print('hello')\r")
        assert ser.read_until(b"hello\r\n>>> ") == (
            b"print('hello')\r\n" b"hello\r\n" b">>> "  # Echo  # Output  # Next prompt
        )

        # NB: nothing left in buffer
        assert interrupt_and_enter_repl(ser) == b""

    def test_interrupt_process(self, ser: Connection) -> None:
        interrupt_and_enter_repl(ser)

        before = time.time()

        ser.write(b"import time; time.sleep(30)\r")

        # XXX: Due to an unknown issue with the ACM driver/pyserial/something
        # else(?), if we do not wait for the echo to come back, the attempt to
        # write a ^C character in interrupt_and_enter_repl() will be blocked
        # until the end of the sleep for reasons unknown.
        expect(ser, b"import time; time.sleep(30)\r\n")

        time.sleep(0.1)  # Ensure started before we send ctrl+c

        # Ensure we interrupt the sleep
        out = interrupt_and_enter_repl(ser)
        print(out.decode("utf-8"))
        after = time.time()

        # Make sure our ^C worked (and that we didn't end up blocked waiting
        # for the printout
        assert after - before < ser.timeout * 1.5

        assert out == (
            b"Traceback (most recent call last):\r\n"
            b'  File "<stdin>", line 1, in <module>\r\n'
            b"KeyboardInterrupt: "
        )

    def test_interrupt_process_custom_message(self, ser: Connection) -> None:
        interrupt_and_enter_repl(ser)

        before = time.time()

        # XXX: Due to an unknown issue with the ACM driver/pyserial/something
        # else(?), if we do not wait for the echo to come back, the attempt to
        # write a ^C character in interrupt_and_enter_repl() will be blocked
        # until the end of the sleep for reasons unknown.
        ser.write(b"import time\r")
        expect(ser, b"import time\r\n")
        ser.write(b"try: time.sleep(30)\r")
        expect(ser, b">>> try: time.sleep(30)\r\n")
        ser.write(b"except: print('stopped\\ndead')\r")
        expect(ser, b"... except: print('stopped\\ndead')\r\n")
        ser.write(b"\r")
        expect(ser, b"... \r\n")

        time.sleep(0.1)  # Ensure started before we send ctrl+c

        # Ensure we interrupt the sleep
        out = interrupt_and_enter_repl(ser)
        after = time.time()

        # Make sure our ^C worked (and that we didn't end up blocked waiting
        # for the printout
        assert after - before < ser.timeout * 1.5

        assert out == (b"stopped\r\n" b"dead")

    def test_double_interrupt_process(self, ser: Connection) -> None:
        interrupt_and_enter_repl(ser)

        before = time.time()

        ser.write(b"import time\r")
        expect(ser, b"import time\r\n>>> ")
        ser.write(b"try: time.sleep(30)\r")
        expect(ser, b"try: time.sleep(30)\r\n... ")
        ser.write(b"finally: time.sleep(20)\r")
        expect(ser, b"finally: time.sleep(20)\r\n... ")
        ser.write(b"\r")
        expect(ser, b"\r\n")

        time.sleep(0.1)  # Ensure started before we send ctrl+c

        # XXX: Due to an unknown issue with the ACM driver/pyserial/something
        # else(?), if we do not wait for the echo to come back, the attempt to
        # write a ^C character in interrupt_and_enter_repl() will be blocked
        # until the end of the sleep for reasons unknown.

        # Ensure we interrupt the sleep
        out = interrupt_and_enter_repl(ser)
        after = time.time()

        # Make sure our ^C worked (and that we didn't end up blocked waiting
        # for the printout
        delta = after - before
        assert delta < ser.timeout * 2.5

        assert out == (
            b"Traceback (most recent call last):\r\n"
            b'  File "<stdin>", line 2, in <module>\r\n'
            b"KeyboardInterrupt: "
        )


class TestPasteExec:
    def test_basic_functionality(self, ser: Connection) -> None:
        paste_exec(
            ser,
            (
                "print('Hello')\n"  # NL separated
                "print('World')\r\n"  # CRNL separated
                "print('£1.23')"  # Unicode
            ),
        )
        expect(ser, "Hello\r\nWorld\r\n£1.23\r\n".encode("utf-8"))  # Output
        expect(ser, b">>> ")  # New prompt

    def test_flow_control(self, ser: Connection) -> None:
        # The following snippet is long enough that not waiting for the remote
        # device to handle it will inevetably result in it loosing some bytes
        paste_exec(ser, f"print({' + '.join(map(str, range(30)))})\n" * 30)
        expect(ser, (str(sum(range(30))).encode("utf-8") + b"\r\n") * 30)  # Output
        expect(ser, b">>> ")  # New prompt


def test_raw_mode(ser: Connection) -> None:
    with raw_mode(ser):
        # Check we're actually in raw mode
        ser.write(b"print('hello')" b"\x04")  # (Ctrl+D)
        expect(ser, b"OKhello\r\n\x04\x04")

    # Check returned to non-raw mode
    ser.write(b"print('hello')\r")
    expect(ser, b"print('hello')\r\n")  # Echo
    expect(ser, b"hello\r\n")
    expect(ser, b">>> ")  # New prompt


class TestRawPasteExec:
    def test_basic_functionality_no_exception(self, ser: Connection) -> None:
        with raw_mode(ser):
            out, err = raw_paste_exec(
                ser,
                dedent(
                    r"""
                        print("hello\nworld!")
                        print("goodbye!")
                    """
                ),
            )
            assert err == ""
            assert out == ("hello\r\n" "world!\r\n" "goodbye!\r\n")

    def test_basic_functionality_with_exception(self, ser: Connection) -> None:
        with raw_mode(ser):
            out, err = raw_paste_exec(
                ser,
                dedent(
                    r"""
                        print("success")
                        raise Exception("Failure!")
                    """
                ),
            )
            assert out == "success\r\n"
            assert err == (
                "Traceback (most recent call last):\r\n"
                '  File "<stdin>", line 3, in <module>\r\n'
                "Exception: Failure!\r\n"
            )

    def test_input_flow_control_required(self, ser: Connection) -> None:
        with raw_mode(ser):
            out, err = raw_paste_exec(
                ser, f"print({'+'.join('1' for _ in range(1000))})"
            )
            assert err == ""
            assert out == "1000\r\n"

    def test_input_flow_control_size_multiple_of_window(self, ser: Connection) -> None:
        # XXX: Hard coded based on what the Pi Pico defaults to...
        window_size = 128

        with raw_mode(ser):
            out, err = raw_paste_exec(ser, f"print(123)".ljust(window_size * 8, "#"))
            assert err == ""
            assert out == "123\r\n"

    def test_disallow_ctrl_d_in_source(self, ser: Connection) -> None:
        with raw_mode(ser):
            with pytest.raises(ValueError):
                raw_paste_exec(ser, "oh noes\x04")


def quick_and_dirty_write_file(
    ser: Connection, file: str, value: Optional[str]
) -> None:
    interrupt_and_enter_repl(ser)

    with raw_mode(ser):
        if value is not None:
            assert (
                raw_paste_exec(
                    ser,
                    dedent(
                        f"""
                        with open({file!r}, "w") as f:
                            f.write({value!r})
                    """
                    ).strip(),
                )
                == ("", "")
            )
        else:
            assert (
                raw_paste_exec(
                    ser,
                    dedent(
                        f"""
                        import os
                        try:
                            os.remove({file!r})
                        except OSError:
                            pass
                    """
                    ).strip(),
                )
                == ("", "")
            )


def quick_and_dirty_read_file(ser: Connection, file: str) -> Optional[str]:
    interrupt_and_enter_repl(ser)

    with raw_mode(ser):
        out, err = raw_paste_exec(
            ser,
            dedent(
                f"""
                    try:
                        print(repr(open({file!r}, "r").read()))
                    except OSError:
                        print("None")
                """
            ).strip(),
        )

    assert err == ""
    return eval(out)


@contextmanager
def quick_and_dirty_override_file(ser: Connection, file: str, content: Optional[str]):
    original = quick_and_dirty_read_file(ser, file)
    quick_and_dirty_write_file(ser, file, content)
    try:
        yield
    finally:
        quick_and_dirty_write_file(ser, file, original)


@pytest.mark.parametrize(
    "original, replacement",
    [
        ("foo", "bar"),
        ("foo", None),
        (None, "foo"),
        (None, None),
    ],
)
def test_override_file(
    ser: Connection,
    original: Optional[str],
    replacement: Optional[str],
) -> None:
    quick_and_dirty_write_file(ser, "file.txt", original)

    with quick_and_dirty_override_file(ser, "file.txt", replacement):
        assert quick_and_dirty_read_file(ser, "file.txt") == replacement

    assert quick_and_dirty_read_file(ser, "file.txt") == original


class TestSoftResetDirectlyIntoRepl:
    def test_interrupt_existing_process(self, ser: Connection) -> None:
        interrupt_and_enter_repl(ser)

        # Set the thing off sleeping
        ser.write(b"import time; time.sleep(3)\r")
        expect(ser, b"import time; time.sleep(3)\r\n")

        assert soft_reset_directly_into_repl(ser) == ""

    def test_skips_main_py(self, ser: Connection) -> None:
        interrupt_and_enter_repl(ser)

        with quick_and_dirty_override_file(
            ser, "main.py", "import time; time.sleep(3)"
        ):
            # Should have no boot message
            assert soft_reset_directly_into_repl(ser) == ""

            # Should already be in the shell
            ser.write(b"\r")
            expect(ser, b"\r\n>>> ")

    def test_captures_boot_py_output(self, ser: Connection) -> None:
        interrupt_and_enter_repl(ser)

        with quick_and_dirty_override_file(ser, "boot.py", "print('booted!')"):
            # Should have no boot message
            assert soft_reset_directly_into_repl(ser) == "booted!\r\n"
