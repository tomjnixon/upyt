"""
File-listing utility.
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
        nargs="?",
        default=":/",
        help="""
            The path to enumerate on the device. Defaults to :/. Prefix with
            ':' for device paths.
        """,
    )
    
    parser.add_argument(
        "--long",
        "-l",
        action="store_true",
        help="""
            If given, show additional details about each file.
        """,
    )


def main(args: Namespace):
    with Connection.from_specification(args.device) as conn:
        interrupt_and_enter_repl(conn)
        with upy_filesystem(conn) as fs:
            if not args.path.startswith(":"):
                print(
                    "warning: path not on device (i.e. starting with ':')",
                    file=sys.stderr,
                )
            hfs = HybridFilesystemAPI(fs)
            
            directories, files = hfs.ls(args.path)
            entries = sorted(
                (entry, typ)
                for lst, typ in [(directories, "d"), (files, "-")]
                for entry in lst
            )
            
            for name, typ in entries:
                if typ == "d":
                    name = f"{name}/"
                
                if args.long:
                    if typ != "d":
                        size = hfs.file_len(f"{args.path}/{name}")
                    else:
                        size = 0
                    print(f"{size:8d} {name}")
                else:
                    print(name)
