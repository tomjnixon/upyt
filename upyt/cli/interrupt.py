"""
Interrupt a MicroPython device, returning the interpreter to the REPL.
"""

import sys

from argparse import ArgumentParser, Namespace

from upyt.connection import Connection
from upyt.upy_repl import interrupt_and_enter_repl


def add_arguments(parser: ArgumentParser) -> None:
    pass


def main(args: Namespace):
    with Connection.from_specification(args.device) as conn:
        interrupt_and_enter_repl(conn)
