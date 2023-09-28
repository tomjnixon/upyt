from argparse import ArgumentParser

import os

from upyt.cli import terminal


def main() -> None:
    parser = ArgumentParser(
        description="""
            A multi-tool for programming and interacting with MicroPython
            devices.
        """
    )
    parser.add_argument(
        "--device",
        "-d",
        default=os.getenv("UPYT_DEVICE"),
        help="""
            Device to connect to. For example `/dev/ttyACM0` or
            `/dev/ttyACM0:115200`. Defaults to value of UPYT_DEVICE environment
            variable, if set, required otherwise.
        """,
    )
    
    subparsers = parser.add_subparsers(required=True)
    
    terminal_parser = subparsers.add_parser(
        "terminal",
        help="A serial terminal for MicroPython.",
        aliases=["t", "term"]
    )
    terminal.add_arguments(terminal_parser)
    terminal_parser.set_defaults(cmd=terminal.main)
    
    args = parser.parse_args()
    
    if args.device is None:
        parser.error("--device is required if UPYT_DEVICE is not set")
    
    args.cmd(args)


if __name__ == "__main__":
    main()
