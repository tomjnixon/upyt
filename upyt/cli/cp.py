"""
Copy files and directories.
"""

from typing import Iterator, Iterable

import sys

from argparse import ArgumentParser, Namespace

from pathlib import Path

from upyt.connection import Connection
from upyt.upy_terminal import interrupt_and_enter_repl
from upyt.upy_fs import upy_filesystem, FilesystemAPI
from upyt.cli.hybrid_filesystem_api import HybridFilesystemAPI


def split_source(source: str) -> tuple[str, str]:
    """
    Given a commandline source name (e.g. '-', 'foo/bar' or ':foo/bar') split
    it into a (prefix_plus_dirname, name) pair.
    
    Here prefix_plus_dirname contains the ':' if any plus the base directory
    name (e.g. 'foo/' in these examples).
    
    Also normalises empty paths to "." and strips trailing slashes.
    """
    # You can never be sure there aren't Windows users about...
    source = source.replace("\\", "/")
    
    prefix = ""
    if source.startswith(":"):
        prefix = ":"
        source = source[1:]
    
    # Special case for empty string == cwd
    if source == "":
        source = "."
    
    # Normalize-out trailing slashes (special case for root dir)
    if source != "/":
        source = source.rstrip("/")
    
    base_dir, slash, name = source.rpartition("/")
    
    return (prefix + base_dir + slash, name)


class RecursionNotAllowedError(Exception):
    """Thrown when attempting to copy a directory without recursion enabled."""


def read_sources(
    hfs: HybridFilesystemAPI,
    sources: list[str],
    recursive: bool = False,
) -> Iterator[tuple[str, bytes | None]]:
    """
    Given a list of sources to copy from, return the file names and their
    contents. Directories are listed with contents of None
    """
    # Split sources into their leading components (whose names will not be used
    # in the destination) and the rest.
    #
    # [(base_dir, name), ...]
    sources = [split_source(source) for source in sources]
    
    # (Potentially) recursively iterate over the sources
    while sources:
        base_dir, name = sources.pop()
        
        path = base_dir + name
        if hfs.get_type(path).is_dir():
            if recursive:
                directories, files = hfs.ls(path)
                for subpath in directories + files:
                    sources.append((base_dir, f"{name}/{subpath}"))
                yield (name, None)
            else:
                raise RecursionNotAllowedError(base_dir + name)
        else:
            content = hfs.read_file(base_dir + name)
            yield (name, content)


def write_single_file_to_destination(
    hfs: HybridFilesystemAPI,
    file: tuple[str, bytes],
    destination: str,
) -> None:
    """
    Write a single file (as read by :py:func:`read_sources`) into the provided
    destination. If the destination is a directory, the file is copied into it.
    Otherwise, the file is copied to the given name.
    """
    filename, data = file
    
    if hfs.get_type(destination).is_dir():
        hfs.write_file(f"{destination}/{filename}", data)
    else:
        hfs.write_file(destination, data)


def write_multiple_files_to_existing_directory(
    hfs: HybridFilesystemAPI,
    files: Iterable[tuple[str, bytes]],
    destination: str,
) -> None:
    """
    Write a series of files (as read by :py:func:`read_sources`) into the
    provided destination directory.
    """
    # NB: Sorting ensures that directories are created before thier contents
    # and subdirectories
    for filename, data in sorted(files):
        if data is None:
            hfs.mkdir(f"{destination}/{filename}", exist_ok=True)
        else:
            hfs.write_file(f"{destination}/{filename}", data)

def write_single_directory_to_non_existing_destination(
    hfs: HybridFilesystemAPI,
    files: Iterable[tuple[str, bytes]],
    destination: str,
) -> None:
    """
    Write a series of files contained in a single directory (as read by
    :py:func:`read_sources`) into the provided non-existing destination
    directory.
    """
    for filename, data in sorted(files):
        filename = filename.partition("/")[2]
        if data is None:
            hfs.mkdir(f"{destination}/{filename}", exist_ok=True)
        else:
            hfs.write_file(f"{destination}/{filename}", data)

def cp(hfs: HybridFilesystemAPI, sources: list[str], destination: str, recursive: bool=False):
    """
    Copy files between the host and MicroPython device.
    
    Paths starting with ":" are device paths, those without are host paths. 
    
    Copying semantics approximately follow the POSIX ``cp`` command.
    """
    files = read_sources(hfs, sources, recursive)
    if len(sources) == 1 and not hfs.get_type(sources[0]).is_dir():
        write_single_file_to_destination(hfs, next(files), destination)
    elif len(sources) == 1 and not hfs.get_type(destination).is_dir():
        # NB: source is dir, as per the previous branch's test
        write_single_directory_to_non_existing_destination(hfs, files, destination)
    else:
        write_multiple_files_to_existing_directory(hfs, files, destination)



def add_arguments(parser: ArgumentParser) -> None:
    parser.add_argument(
        "source",
        nargs="+",
        help="""
            Files to copy. Prefix with ':' for device paths.
        """,
    )
    parser.add_argument(
        "destination",
        help="""
            Destination. Prefix with ':' for device paths.
        """,
    )
    parser.add_argument(
        "--recursive",
        "-r",
        "-R",
        action="store_true",
        default=False,
        help="""
            Copy directories and their contents recursively.
        """,
    )


def main(args: Namespace):
    with Connection.from_specification(args.device) as conn:
        interrupt_and_enter_repl(conn)
        with upy_filesystem(conn) as fs:
            if not (
                any(source.startswith(":") for source in args.source) or
                args.destination.startswith(":")
            ):
                print(
                    "warning: neither source nor destination on device (i.e. starting with ':')",
                    file=sys.stderr,
                )
            hfs = HybridFilesystemAPI(fs)
            cp(hfs, args.source, args.destination, args.recursive)
            hfs.sync()
