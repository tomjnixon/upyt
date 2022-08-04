"""
Low-level utilities which interact with the MicroPython REPL.
"""

import random

from contextlib import contextmanager

import struct

from upyt.connection import Connection


class MicroPythonReplError(Exception):
    """
    Base exception thrown when the MicroPython REPL produces unexpected output.
    
    All exceptions include any unexpected output from the connection as their
    first argument.
    """

class NoReplError(MicroPythonReplError):
    """
    Thrown when no REPL prompt (>>>) is received when expected.
    """

class RawPasteModeNotSupportedError(MicroPythonReplError):
    """
    Thrown if the connected board is running a MicroPython version too old to
    support raw paste mode.
    """


class SomeCodeNotSentError(MicroPythonReplError):
    """
    Thrown the MicroPython board fails to accept all of the code provided to
    :py:func:`raw_paste_exec`.
    
    The first argument is th exception output (as raw bytes) and the second
    argument is the ordinary output (also as raw bytes, and likely empty). The
    third argument is the input which was never sent.
    """


def expect(conn: Connection, value: bytes) -> bytes:
    """
    Read from a connection, checking that exactly the expected value is read.
    If a timeout occurs, raises MicroPythonReplError.
    
    Returns the bytes actually read.
    """
    actual = conn.read(len(value))
    if actual != value:
        raise MicroPythonReplError(actual)
    return actual


def expect_endswith(conn: Connection, value: bytes) -> bytes:
    """
    Read from a connection until the supplied value is read. If a timeout
    occurs before this, raises MicroPythonReplError.
    
    Returns all of the bytes read.
    """
    actual = conn.read_until(value)
    if not actual.endswith(value):
        raise MicroPythonReplError(actual)
    return actual


def interrupt_and_enter_repl(conn: Connection, num_attempts: int = 2, timeout: float = 0.1) -> bytes:
    """
    Attempt to perform a keyboard interrupt to get to a REPL.
    
    Raises NoReplError if the REPL is not reached.
    
    Returns all ignored terminal output prior to the final newline and prompt
    ('>>>'), e.g.  KeyboardInterrupt exception trace-backs).
    
    Makes num_attempts attempts to interrupt the running process, waiting
    timeout seconds between each attempt. Multiple attempts are necessary if,
    for example, an exception handler also blocks.
    """
    # Flush any pending input (e.g. previous command outputs or prompts)
    unmatched_output = conn.read_buffered()
    
    # Attempt to reach a prompt follwing a keyboard interrupt
    with conn.timeout_override(timeout):
        for attempt in range(num_attempts):
            conn.write(b"\x03")
            
            # Wait for the first hint hint of a prompt
            prompt = b"\r\n>>> "
            try:
                unmatched_output += expect_endswith(conn, prompt)[:-len(prompt)]
            except MicroPythonReplError as e:
                # No sign of a prompt, try interrupting again as the exception
                # handler may need interrupting
                unmatched_output += e.args[0]
                continue
            
            # To make sure what we're seeing is not just an old prompt hanging
            # about in the buffer, produce some unique output
            random_number = random.randint(0x10, 0xFFFFFF)
            conn.write(b"0x%x\r" % random_number)
            expected_response = (
                b"0x%x\r\n"  # Echo
                b"%d\r\n" # Decimal representation
                b">>> "  # Fresh prompt
            ) % (random_number, random_number)
            try:
                unmatched_output += expect_endswith(
                    conn, expected_response
                )[:-len(expected_response)]
                # Success! We're in sync and sat waiting at a prompt!
                return unmatched_output
            except MicroPythonReplError as e:
                # Fail: we didn't see our reply, lets try agian
                unmatched_output += e.args[0]
                continue
        
        # Ran out of retries
        raise NoReplError(unmatched_output)


@contextmanager
def raw_mode(conn: Connection):
    """
    Context manager which enters raw mode on entry and leaves raw mode again on
    exit.
    """
    # Enter raw mode
    conn.write(b"\x01")  # (Ctrl+A)
    expect_endswith(conn, b"raw REPL; CTRL-B to exit\r\n>")
    
    try:
        yield
    finally:
        conn.write(
            b"\x04"  # (Ctrl+D)  End current code block (if any)
            b"\x02"  # (Ctrl+B)  Exit raw mode
        )
        expect_endswith(conn, b"\r\n>>> ")


def raw_paste_exec(conn: Connection, code: str) -> tuple[str, str]:
    """
    Execute the supplied code (via raw paste mode).
    
    Must be in raw mode (see :py:func:`raw_mode`) when calling this function.
    
    Names defined by the executed code remain in scope until the surrounding
    raw mode is exited.
    
    The supplied code must not contain, or print a 0x04 character (Ctrl+D)
    otherwise the raw paste mode protocol will become out-of-sync and undefined
    behaviour will ensue.
    
    Returns two strings: the output of the executed code and any exceptions
    produced.
    
    The executed command must complete and have sent all of its output within
    the connection's timeout, otherwise an error will occur.
    """
    # Enter raw paste mode
    conn.write(
        b"\x05"  # (Ctrl+E / ENQ)
        b"A"
        b"\x01"  # (Ctrl+A)
    )  # (Ctrl+A)
    response = conn.read(2)
    if response != b"R\x01":
        raise RawPasteModeNotSupportedError(response)
    
    window_size_increment = struct.unpack("<H", conn.read(2))[0]
    window_size = window_size_increment
  
    code_utf8 = code.encode("utf-8")
    if b"\x04" in code_utf8:
        raise ValueError("Cannot eval strings containing ASCII 0x04 (ctrl+D)")
    
    # Send all of the code and wait for the window to re-open again
    #
    # NB: We need to wait for the window to reopen before sending the
    # end-of-code if we happened to use up the whole window with our final
    # block of code, otherwise the end-of-code might overrun the buffer!
    while code_utf8 or window_size == 0:
        if window_size == 0:
            response = conn.read(1)
            if response == b"\x01":
                window_size += window_size_increment
            elif response == b"\x04":
                # Device doesn't want any more data
                break
            else:
                raise MicroPythonReplError(response)
        
        # Send (up-to) the maxmimum allowed window worth of data
        written = conn.write(code_utf8[:window_size])
        code_utf8 = code_utf8[written:]
        window_size -= written
    
    # End transmission
    conn.write(b"\x04")  # (Ctrl+D)
    while True:
        response = conn.read(1)
        if response == b"\x01":
            # A window size increment, but we don't care about those anymore
            continue
        elif response == b"\x04":
            # End of transmission was acknowledged
            break
        else:
            raise MicroPythonReplError(response)
    
    # Read the response
    code_output = expect_endswith(conn, b"\x04")[:-1]
    exception_output = expect_endswith(conn, b"\x04")[:-1]
    expect_endswith(conn, b">")  # Should return to raw-repl shell
    
    if not code_utf8:
        # Success: All bytes were accepted!
        return (code_output.decode("utf-8"), exception_output.decode("utf-8"))
    else:
        # Failure: Some bytes not received
        raise SomeCodeNotSentError(exception_output, code_output, code_utf8)


def soft_reset_directly_into_repl(conn: Connection) -> str:
    """
    Interupt any running process and perform a soft reset such that the device
    boots directly into the REPL without executing main.py.
    
    Returns any output produced by boot.py as a string. Output prior to this is
    discarded.
    """
    interrupt_and_enter_repl(conn)
    
    # NB: When reset in raw mode, main.py is not executed
    with raw_mode(conn):
        # Perform a reset
        conn.write(b"\x04")  # (Ctrl + D)
        
        # Response from Raw REPL
        expect(
            conn,
            (
                b"OK\r\n"  # From Raw REPL
                b"MPY: soft reboot\r\n"  # Boot message
            ),
        )
        
        # Ignore all output from boot.py
        raw_repl_entry_message = b"raw REPL; CTRL-B to exit\r\n>"
        boot_py_output = expect_endswith(
            conn, raw_repl_entry_message
        )[:-len(raw_repl_entry_message)]
    
    return boot_py_output.decode("utf-8")
