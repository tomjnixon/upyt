"""
Delete files and directories on a MicroPython device.
"""

import sys

from argparse import ArgumentParser, Namespace

from upyt.connection import Connection
from upyt.upy_terminal import interrupt_and_enter_repl
from upyt.upy_fs import upy_filesystem, PathType
from upyt.cli.hybrid_filesystem_api import HybridFilesystemAPI


def add_arguments(parser: ArgumentParser) -> None:
    parser.add_argument(
        "path",
        nargs="+",
        help="""
            The files or directories to delete. Acts recursively. Prefix with
            ':' for device paths.
        """,
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        default=False,
        help="""
            If given, delete directories and their contents recursively.
            Otherwise, will only delete files.
        """
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        default=False,
        help="""
            Suppress errors when deleting non-existant files.
        """
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
                filetype = hfs.get_type(path)
                
                if filetype == PathType.absent:
                    if not args.force:
                        print(f"error: file not found: {path}", file=sys.stderr)
                        sys.exit(1)
                elif filetype.is_dir():
                    if args.recursive:
                        hfs.remove_recursive(path)
                    else:
                        print(
                            f"error: cannot delete directory: {path} (try --recursive)",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                elif filetype.is_file():
                    hfs.remove_recursive(path)
            hfs.sync()
