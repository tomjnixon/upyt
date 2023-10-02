"""
Soft-reset a MicroPython device.
"""

import sys

from argparse import ArgumentParser, Namespace

from upyt.connection import Connection
from upyt.upy_repl import expect, interrupt_and_enter_repl, soft_reset_directly_into_repl


def add_arguments(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--repl",
        "-r",
        action="store_true",
        default=False,
        help="""
            If given, force the device to reset into a REPL, without running
            main.py (if it exists).
        """,
    )


def main(args: Namespace):
    with Connection.from_specification(args.device) as conn:
        interrupt_and_enter_repl(conn)
        if args.repl:
            soft_reset_directly_into_repl(conn)
        else:
            conn.write(b"\x04")  # ctrl+d
            expect(conn, b"\r\n")
