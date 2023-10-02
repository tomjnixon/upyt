from argparse import ArgumentParser

import os

from importlib import import_module


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
    
    subparsers = parser.add_subparsers(title="commands", required=True)
    
    subcommands = [
        (["terminal", "term", "t"], "a serial terminal for MicroPython."),
        (["sync"], "efficiently synchronise a directory to the device"),
        ("ls", "list files and directories"),
        ("mkdir", "create a directory"),
        ("rm", "remove files and directories"),
        ("cat", "read (and concatenate) files"),
        ("cp", "copy files to and from the device"),
    ]
    
    for command, help_text in subcommands:
        if isinstance(command, str):
            command = [command]
        
        command_module = import_module(f"upyt.cli.{command[0]}")
        subparser = subparsers.add_parser(
            command[0],
            help=help_text,
            description=command_module.__doc__,
            aliases=command[1:],
        )
        command_module.add_arguments(subparser)
        subparser.set_defaults(cmd=command_module.main)
    
    args = parser.parse_args()
    
    if args.device is None:
        parser.error("--device is required if UPYT_DEVICE is not set")
    
    args.cmd(args)


if __name__ == "__main__":
    main()
