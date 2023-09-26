"""
An implementation of a serial terminal for micropython devices.

**Currently this implementation is Unix-like only...**
"""

from typing import TextIO, Callable

import os
import sys
import selectors
import copy
import termios
import codecs

from contextlib import contextmanager, nullcontext

from upyt.connection import Connection
from upyt.read_proxy import ReadProxy, match, StreamFilter
from upyt.upy_repl import (
    expect,
    interrupt_and_enter_repl,
    paste_exec,
    MicroPythonReplError,
)

# ANSI escape sequences used internally
GREY = "\033[90m"
RESET = "\033[0m"

CLEAR = "\033[2J"
CURSOR_HOME = "\033[H"

BRACKETED_PASTE_ENABLE = "\033[?2004h"
BRACKETED_PASTE_DISABLE = "\033[?2004l"
BRACKETED_PASTE_BEGIN = "\033[200~"
BRACKETED_PASTE_END = "\033[201~"


@contextmanager
def terminal_mode(terminal: TextIO = sys.stdin):
    """
    For the duration of the context manager, disables echo, line buffering and
    conversion of keyboard interrupts into signals. Restores termios state on
    exit.
    """
    old = termios.tcgetattr(terminal)
    new = copy.deepcopy(old)
    
    new[3] = (
        new[3]
        # Disable canonical (e.g. the common line-buffered mode)
        & ~termios.ICANON
        # Disable local echo
        & ~termios.ECHO
        # Disable translation of interrupt keyboard sequences (e.g. Ctrl+C)
        # into signals
        & ~termios.ISIG
    )
    
    # Set the minimum read size to 1 byte
    new[6][termios.VMIN] = 1
    
    # Disable timeout while waiting for a byte
    new[6][termios.VTIME] = 0
    
    termios.tcsetattr(terminal, termios.TCSANOW, new)
    try:
        yield
    finally:
        termios.tcsetattr(terminal, termios.TCSANOW, old)


@contextmanager
def bracketed_paste_mode(stdout: TextIO = sys.stdout) -> None:
    """
    Context manager which enables bracketed paste mode in the terminal on entry
    and disables it again on exit.
    """
    stdout.write(BRACKETED_PASTE_ENABLE)
    stdout.flush()
    try:
        yield
    finally:
        stdout.write(BRACKETED_PASTE_DISABLE)
        stdout.flush()


@contextmanager
def nonblocking_file(stream: TextIO = sys.stdin):
    """
    Configures a given stream in non-blocking mode for the duration of the
    context manager, restoring the previous state on exit.
    """
    old = os.get_blocking(stream.fileno())
    os.set_blocking(stream.fileno(), False)
    try:
        yield
    finally:
        os.set_blocking(stream.fileno(), old)


@contextmanager
def nonblocking_connection(conn: Connection):
    """
    Configures a given serial connection in non-blocking mode for the duration
    of the context manager, restoring the previous state on exit.
    """
    old = conn.timeout
    conn.timeout = 0
    try:
        yield
    finally:
        conn.timeout = old


class ExitTerminal(Exception):
    """Used internally within :py:class:`raw_serial_terminal`."""
    
    @classmethod
    def make_raise_cb(cls, match_string: str) -> Callable[[], None]:
        def callback() -> None:
            raise cls(match_string)
        return callback


def raw_serial_terminal(
    conn: Connection,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    exit_on: list[str] = ["\x1d"],  # Default = Ctrl+]
) -> str:
    """
    A bare-minimum implementation of a MicroPython compatible serial terminal.
    
    Things this does:
    
    * Setup terminal in non-echo mode
    * Translate system text encoding to/from UTF-8 for the MicroPython device
    * Translate line-endings to/from CRLF for the MicroPython device
    * Setup terminal to capture Ctrl+C as a keyboard input (so that it is
      forwarded to MicroPython rather than triggering a KeyboardInterrupt
      here).
    * Restore terminal configuration on exit or if an exception is thrown.
    
    This function will gracefully exit if any of the strings in the 'exit_on'
    list are found in the stdin stream (i.e. are typed by the user). The
    matched string is returned by this function and *not* forwarded to the
    MicroPython device.
    """
    # Wraps the stdin stream such that when any of the exit strings are
    # encountered in the stream, an ExitTerminal exception is thrown containing
    # the discovered match.
    stdin = ReadProxy(stdin, [match(s, ExitTerminal.make_raise_cb(s)) for s in exit_on])
    
    try:
        with (
            terminal_mode(stdin),
            nonblocking_file(stdin),
            nonblocking_connection(conn),
            selectors.DefaultSelector() as sel,
        ):
            sel.register(conn, selectors.EVENT_READ)
            sel.register(stdin, selectors.EVENT_READ)
            
            # The MicroPython shell expects/produces UTF-8 (well actually it
            # expects ASCII, for now, but may one day accept UTF-8). We'll use an
            # incremental decoder to parse the incoming stream since we may not
            # receieve whole code points in every message.
            utf_8_decoder = codecs.lookup("utf-8").incrementaldecoder(errors="replace")
            
            while True:
                for key, _events in sel.select():
                    if key.fileobj is stdin:
                        c = stdin.read()
                        c = c.replace("\n", "\r\n")  # Use DOS-style newlines
                        conn.write(c.encode("utf-8"))
                        conn.flush()
                    elif key.fileobj is conn:
                        c = utf_8_decoder.decode(conn.read_buffered())
                        c = c.replace("\r", "")  # Restore UNIX-style newlines
                        stdout.write(c)
                        stdout.flush()
    except ExitTerminal as exc:
        return exc.args[0]


def handle_bracketed_paste(
    conn: Connection,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> None:
    """
    Assuming a BRACKETED_PASTE_BEGIN sentinel has just been read from stdin,
    read the rest of the pasted value and then send it to MicroPython.
    
    For single-line pastes, simply sends the string verbatim to MicroPython.
    Upon resuming the serial terminal process, the pasted text should be echoed
    back by the MicroPython REPL.
    
    For multi-line pastes, uses paste mode (interrupting any ongoing line
    editing operation or running code). A dummy representation of the
    paste-mode echoed text is printed to stdout whilst the actual paste-mode
    echo response from the REPL is captured for flow-control purposes. The
    first bytes to be read from MicroPython after this function returns will be
    the output of the pasted code.
    """
    # Read remainder of bracketed paste
    buffer = ""
    while not buffer.endswith(BRACKETED_PASTE_END):
        buffer += stdin.read(1)
    content = buffer[:-len(BRACKETED_PASTE_END)]
    
    # NB: We consider 'single line' to mean anything where all the
    # non-whitespace occurs on a single line -- leading/trailing
    # whitespace are ignored.
    single_line = len(content.strip("\r\n").splitlines()) == 1
    
    if single_line:  # Paste as-is
        conn.write(content.replace("\n", "\r\n").encode("utf-8"))
    else:  # Use paste mode
        try:
            # Ensure we have a clean prompt (e.g. no half-finished line edits)
            interruption_output = interrupt_and_enter_repl(conn)
            stdout.write(interruption_output.decode("utf-8", "replace"))
            stdout.flush()
            
            # Paste (and print mock output to terminal)
            paste_exec(conn, content)
            stdout.write("\n")
            stdout.write("".join(f"=== {line}\n" for line in content.splitlines()))
            stdout.flush()
        except MicroPythonReplError:
            print(
                f"\n{GREY}upyt error: Failed paste using paste mode.{RESET}",
                file=stdout,
                flush=True,
            )


def handle_ctrl_l_emulation(conn: Connection, stdout: TextIO, timeout: float = 0.1) -> None:
    """
    Crudely emulate the typical clear-screen behaviour of a terminal by
    clearing the display and pressing return to get a new prompt.
    """
    # Clear the screen
    stdout.write(f"{CLEAR}{CURSOR_HOME}")
    stdout.flush()
    
    # Press enter to get a new REPL
    conn.write(b"\r\n")
    
    # Absorb the echoed newline to prevent the new prompt being printed on the
    # second line of the newly cleared screen.
    old_timeout = conn.timeout
    try:
        # We might not be in the REPL so the newline is not guaranteed to get
        # echoed back to us, hence the short timeout here.
        conn.timeout = timeout
        expect(conn, b"\r\n")
    except MicroPythonReplError:
        pass
    finally:
        conn.timeout = old_timeout


def serial_terminal(
    conn: Connection,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    exit_on: list[str] = ["\x1d"],  # Default = Ctrl+]
    automatic_paste_mode: bool = True,
    emulate_ctrl_l: bool = True,
) -> str:
    """
    Implements a serial terminal for the MicroPython REPL, with a handful of
    optional niceties to make it feel more like interacting with a normal
    Python shell.
    
    The terminal will run until a keyboard sequence listed in the `exit_on`
    argument is encountered. (By default this is Ctrl+]). This function will
    return the matched key sequence which caused it to exit.
    
    Parameters
    ==========
    conn : Connection
    stdin : TextIO
    stdout : TextIO
        The connection to the MicroPython device and (text mode) open
        stdin/stdout streams attached to the virtual terminal.
    exit_on : ["text", ...]
        A list of text sequences received from stdin to cause the terminal to
        exit. The matched sequence will not be sent to the device but will be
        returned by this function.
    automatic_paste_mode : bool
        If enabled, automatically write multi-line strings pasted into the
        terminal using the MicroPython REPL's paste mode.
    emulate_ctrl_l : bool
        If enabled, not-quite-emulates typical clear-screen-on-Ctrl+L
        behaviour.  Causes Ctrl+L to clear the terminal, moving the cursor to
        the top left, sending a 'return' keypress to cause a new prompt to be
        printed.
    """
    if automatic_paste_mode:
        paste_mode_context_manager = bracketed_paste_mode(stdout)
    else:
        paste_mode_context_manager = nullcontext()
    
    with paste_mode_context_manager:
        while True:
            exit_sequence = raw_serial_terminal(
                conn=conn,
                stdin=stdin,
                stdout=stdout,
                exit_on=(
                    ([BRACKETED_PASTE_BEGIN] if automatic_paste_mode else []) +
                    (["\x0c"] if emulate_ctrl_l else []) +
                    exit_on
                ),
            )
            
            if exit_sequence == BRACKETED_PASTE_BEGIN:
                handle_bracketed_paste(
                    conn=conn,
                    stdin=stdin,
                    stdout=stdout,
                )
            elif exit_sequence == "\x0c":  # Ctrl+L
                handle_ctrl_l_emulation(conn, stdout)
            else:
                return exit_sequence
