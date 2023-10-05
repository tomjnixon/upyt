"""
Efficiently synchronise a local directory to a MicroPython device. With
'--terminal', provides an integrated serial terminal where 'Ctrl+R' re-runs
synchronisation.
"""

from argparse import ArgumentParser, Namespace

import sys

from pathlib import Path
from functools import partial

from upyt.connection import Connection
from upyt.upy_repl import expect, interrupt_and_enter_repl
from upyt.upy_terminal import GREY, RESET
from upyt.upy_fs import upy_filesystem
from upyt.sync import sync_to_device, default_exclude

from upyt.cli import terminal


def add_arguments(parser: ArgumentParser) -> None:
    parser.add_argument(
        "source",
        help="""
            Local directory to synchronise to the device.
        """,
    )
    parser.add_argument(
        "destination",
        nargs="?",
        default=":/",
        help="""
            Location of corresponding directory on device. Must start with ':'.
            Defaults to the root of the device (i.e. ':/'). Note, existing
            files will be updated but files which are deleted on the host will
            *not* be automatically deleted on the device.
        """,
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="""
            A rsync-style exclusion pattern for paths to be excluded from sync.
            May be used multiple times. Unless --no-default-exclusions is
            given, common temporary and version control files and directories
            are excluded by default.
        """,
    )
    parser.add_argument(
        "--no-default-exclusions",
        "-E",
        action="store_true",
        default=False,
        help="""
            If given, don't exclude any temporary or version control files or
            directories by default.
        """,
    )
    parser.add_argument(
        "--force-enumerate-files",
        "-f",
        action="store_true",
        default=False,
        help="""
            If given, always scans the device to check for missing files.
        """,
    )
    parser.add_argument(
        "--force-safe-update",
        "--safe",
        "-s",
        action="store_true",
        default=False,
        help="""
            If given, always verify that modified files on the device have been
            edited correctly. Only necessary if files may have been changed on
            the device.
        """,
    )
    parser.add_argument(
        "--reset",
        "-r",
        action="store_true",
        default=False,
        help="""
            If given, reset the device after syncing (e.g. so modules are
            reloaded and new code runs). Otherwise, the device will be left at
            the REPL following file sync.
        """,
    )
    parser.add_argument(
        "--terminal",
        "-t",
        action="store_true",
        default=False,
        help="""
            If given, start the serial terminal after syncing. Synchronisation
            can be re-run at any time by pressing ctrl+r.
        """,
    )
    terminal.add_arguments(parser)


def print_progress(
    current_file: Path,
    files_to_update: set[Path],
    all_files: set[Path],
    terminal_mode: bool,
) -> None:
    if terminal_mode:
        prefix = f"{GREY}    "
        suffix = RESET
    else:
        prefix = suffix = ""

    print(f"{prefix}{current_file}...{suffix}")


def main(args: Namespace):
    # Check source/destination are local/device respectively
    if args.source.startswith(":"):
        print(
            "error: source path must be on host (i.e. not start with ':')",
            file=sys.stderr,
        )
        sys.exit(1)
    if not args.destination.startswith(":"):
        print(
            "error: destination path must be on device (i.e. start with ':')",
            file=sys.stderr,
        )
        sys.exit(1)

    # Compile exclusions list
    if args.no_default_exclusions:
        exclude = []
    else:
        exclude = default_exclude
    exclude += args.exclude

    # Select progress display
    if args.quiet:
        progress_callback = None
    else:
        progress_callback = partial(print_progress, terminal_mode=args.terminal)

    with Connection.from_specification(args.device) as conn:
        first_run = True
        while True:
            if args.terminal and not args.quiet:
                print(f"{GREY}Synchronising files:{RESET}")

            interrupt_and_enter_repl(conn)
            with upy_filesystem(conn) as fs:
                sync_to_device(
                    fs,
                    host_dir=Path(args.source),
                    device_dir=args.destination[1:],  # Strip off ':'
                    exclude=exclude,
                    force_enumerate_files=args.force_enumerate_files,
                    force_safe_update=args.force_safe_update,
                    progress_callback=progress_callback,
                )
                fs.sync()
                if not args.terminal:
                    return

            if not args.quiet:
                print(f"{GREY}Done{RESET}")

            if args.reset:
                conn.write(b"\x04")  # ctrl+d (trigger reset)
            else:
                conn.write(b"\r\n")  # Force prompt to be shown
            expect(conn, b"\r\n")

            exit_seq = terminal.terminal(
                conn,
                args,
                extra_exit_on=["\x12"],  # ctlr+r"
                extra_help="Press Ctrl+R to re-run file sync.",
                show_help=first_run,
            )
            if exit_seq != "\x12":  # != ctrl+r
                return
            first_run = False

            interrupt_message = interrupt_and_enter_repl(conn)
            if interrupt_message:
                sys.stdout.buffer.write(interrupt_message + b"\n")
