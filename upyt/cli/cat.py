"""
File reading utility.
"""

import sys

from argparse import ArgumentParser, Namespace

from upyt.connection import Connection
from upyt.upy_terminal import interrupt_and_enter_repl
from upyt.upy_fs import upy_filesystem
from upyt.cli.hybrid_filesystem_api import HybridFilesystemAPI


def add_arguments(parser: ArgumentParser) -> None:
    parser.add_argument(
        "path",
        nargs="+",
        help="""
            The file to read.
        """,
    )


def main(args: Namespace):
    with Connection.from_specification(args.device) as conn:
        interrupt_and_enter_repl(conn)
        with upy_filesystem(conn) as fs:
            if not any(path.startswith(":") for path in args.path):
                print(
                    "warning: path not on device (i.e. starting with ':')",
                    file=sys.stderr,
                )
            hfs = HybridFilesystemAPI(fs)
            for path in args.path:
                sys.stdout.buffer.write(hfs.read_file(path))
