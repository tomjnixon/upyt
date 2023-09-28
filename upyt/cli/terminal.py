from argparse import ArgumentParser

from upyt.connection import Connection
from upyt.upy_terminal import serial_terminal, GREY, RESET

def main():
    parser = ArgumentParser(
        description="""
            A MicroPython serial terminal.
        """
    )
    parser.add_argument(
        "device",
        help="""
            The device to connect to. For example `/dev/ttyACM0` or
            `/dev/ttyACM0:115200`.
        """,
    )
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
    args = parser.parse_args()

    with Connection.from_specification(args.device) as conn:
        if not args.quiet:
            print(f"{GREY}Press Ctrl+] to exit.{RESET}")
        
        exit_seq = serial_terminal(
            conn,
            exit_on=["\x1d"],  # Ctrl+]
            automatic_paste_mode=not args.no_automatic_paste_mode,
            emulate_ctrl_l=not args.no_emulate_ctrl_l,
        )
        print()  # Move to new line on exit


if __name__ == "__main__":
    main()
