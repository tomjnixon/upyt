"""
Serial terminal CLI utility.
"""

from argparse import ArgumentParser, Namespace

from upyt.connection import Connection
from upyt.upy_terminal import serial_terminal, GREY, RESET

def add_arguments(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="""
            If given, don't print instructions for exiting the terminal on
            startup.
        """,
    )
    parser.add_argument(
        "--no-automatic-paste-mode",
        "-P",
        action="store_true",
        help="""
            If given, disable the automatic use of paste mode when multiple
            lines of text are pasted into the terminal.
        """,
    )
    parser.add_argument(
        "--no-emulate-ctrl-l",
        "-L",
        action="store_true",
        help="""
            If given, disable emulation of the Ctrl+L (clear terminal) keyboard
            shortcut.
        """,
    )


def terminal(
    conn: Connection,
    args: Namespace,
    extra_exit_on: list[str] = [],
    extra_help: str = "",
    show_help: bool = True,
) -> bytes:
    """
    Run the terminal, returning the exit sequence used to exit it.
    """
    if not args.quiet and show_help:
        print(f"{GREY}Press Ctrl+] to exit. {extra_help}{RESET}")
    
    exit_seq = serial_terminal(
        conn,
        exit_on=["\x1d"] + extra_exit_on,  # Ctrl+]
        automatic_paste_mode=not args.no_automatic_paste_mode,
        emulate_ctrl_l=not args.no_emulate_ctrl_l,
    )
    print()  # Move to new line on exit
    return exit_seq


def main(args: Namespace):
    with Connection.from_specification(args.device) as conn:
        terminal(conn, args)
