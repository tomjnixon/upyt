"""
A utility for synchronising a directory on the host to a MicroPython device
filesystem, built on top of :py:class:`upyt.upy_fs.FilesystemAPI`.

In an ideal world, an rsync-like scheme would be used where the device computes
hashes (and timestamps if RTCs were reliably a thing...) which the host can use
to compute deltas to send. Unfortunately typical MicroPython devices can't do
this fast enough for it to be worthwhile.

Instead, for 'fast' operation we use a host-side cache of all of the files on
the device. We then perform the diffs host-side and send them over. For
fastest (but least safe) operation, we can just blindly trust that the remote
filesystem hasn't changed since our last interaction. For safer operation we
can add checks such as enumerating the file system and checking file hashes.

To improve robsutness, even if fast modes, we drop a file onto the target
called ``.upyt_id.txt``. This contains two strings, separated by a space:

A 3-digit (decimal) 'version' number followed by a space then a 12-digit (hex)
'ID'. The version number is changed whenever this tool modifies the filesystem
on the device. Meanwhile the ID remains fixed and identifies the device itself
over time.
"""

from typing import Iterator, Callable

from pathlib import Path

import re
import random
import shutil

from upyt.upy_fs import FilesystemAPI, PathType, UpdateError


default_exclude = [
    # Python litter
    "*.pyc",
    "__pycache__",
    # Version control files
    ".git",
    ".cvs",
    ".svn",
    # Editor temporary files
    "*.tmp",
    "*.swp",
    "*~",
    # Upyt litter
    ".upyt_cache",
]


UPYT_ID_FILENAME = ".upyt_id.txt"
UPYT_CACHE_DIRNAME = ".upyt_cache"


def decode_upyt_id(content: bytes) -> tuple[int, str]:
    """
    Parse the contents of a .upyt_id.txt file, returning the encoded (version,
    device_id) pair.

    Raises ValueError if the file is invalid.
    """
    version, device_id = content.decode("ascii").split(" ")
    return (int(version), device_id)


def encode_upyt_id(version: int, device_id: str) -> bytes:
    """Encode a .upyt_id.txt file."""
    return f"{version:03d} {device_id}".encode("ascii")


def get_upyt_id(fs: FilesystemAPI, device_dir: str) -> tuple[int, str]:
    """
    Attempt to read and parse a `.upyt.txt" file from a given directory on the
    remote device. If one is not found (or is unparseable), generates a new
    one (and creates the directory, and any parent directories).

    Returns
    =======
    version : int
    device_id : str
    """
    filename = f"{device_dir}/{UPYT_ID_FILENAME}"
    try:
        return decode_upyt_id(fs.read_file(filename))
    except (OSError, ValueError):
        version = 0
        device_id = f"{random.SystemRandom().randrange(1<<48):012X}"
        fs.mkdir(device_dir, parents=True, exist_ok=True)
        fs.write_file(filename, encode_upyt_id(version, device_id))
        return (version, device_id)


def enumerate_local_files(host_dir: Path, exclude: list[str] = []) -> Iterator[Path]:
    """
    Iterate over the files and directories in the provided directory hierarchy,
    omiting any excluded items.

    Exclusion rules follow (approximately) the same pattern rules as rsync:

    * ``name`` -- Any file or directory with the name 'name'
    * ``name/`` -- Any directory with the name 'name'
    * ``foo/bar/baz`` -- Any file or directory called baz whose two immediate
      parent directories are named bar and foo.
    * ``foo.*`` -- Any file or directory whose name begins with 'foo.'
    * ``foo/**/bar`` -- Any file or directory called 'bar' which has an
      ancestor directory called 'foo'.
    * ``/foo`` -- A file or directory called foo in the root of the specified
      directory.
    """
    excluded = set()
    for exclusion in exclude:
        if exclusion.startswith("/"):
            # Treat rooted exclusions as relative to root dir
            exclusion = exclusion.lstrip("/")
        else:
            # Treat non-rooted exclusions as applying at any depth
            exclusion = f"**/{exclusion}"
        excluded.update(host_dir.glob(exclusion))

    to_visit = [host_dir]
    while to_visit:
        directory = to_visit.pop(0)

        for path in directory.iterdir():
            if path in excluded:
                continue
            if path.is_dir():
                to_visit.append(path)

            yield path


def clear_local_cache(host_dir: Path) -> None:
    """
    Remove all UPyT cache directories in a given directory.
    """
    cache_dir = host_dir / UPYT_CACHE_DIRNAME

    if cache_dir.is_dir():
        shutil.rmtree(cache_dir)


def sync_to_device(
    fs: FilesystemAPI,
    host_dir: Path,
    device_dir: str = "/",
    exclude: list[str] = default_exclude,
    force_enumerate_files: bool = False,
    force_safe_update: bool = False,
    progress_callback: Callable[[Path, set[Path], set[Path]], None] | None = None,
) -> None:
    """
    Recursively synchronise the files and directories in the named host
    directory into the specified target directory. Extra files and directories
    already present on the device will be left untouched.

    Parameters
    ==========
    fs : FilesystemAPI
    host_dir : Path
        The directory on the host to copy files from.
    device_dir : str
        The target directory on the device. Defaults to the root.
    exclude : ["rsync-style-pattern", ...]
        A list of rsync-style patterns to use to exclude files from being
        copied to the remote device. Defaults to an exclusion list which
        eliminates many common Python and VCS junk entries.
    force_enumerate_files : bool
        If True, always enumerate the devices' filesystem to check for missing
        files. If False, only do this when the ``.upyt_id.txt`` file's version field
        indicates the filesystem differs from the local cache.
    force_safe_update : bool
        If True, always verify the checksums of updated files match the host.
        Otherwise, only do this when the ``.upyt_id.txt`` file's version field
        indicates the filesystem differs from the local cache.
    progress_callback : fn(current_file, files_to_update, all_files) or None
        If given, this callback will be called once before each file detected
        as having changed is written.
    """
    version, device_id = get_upyt_id(fs, device_dir)

    # Create cache directory (if absent)
    cache_dir = host_dir / UPYT_CACHE_DIRNAME / device_id
    cache_dir.mkdir(parents=True, exist_ok=True)

    # See if the cache is out-of-date with respect to what is on the device
    try:
        cache_version = decode_upyt_id((cache_dir / UPYT_ID_FILENAME).read_bytes())[0]
    except (OSError, ValueError):
        cache_version = None
    out_of_date_cache = cache_version != version

    # Update the remote version now (but not the cached version) so that if
    # we're interrupted/crash we'll know the cache could be out-of-sync
    version += 1
    fs.write_file(
        f"{device_dir}/{UPYT_ID_FILENAME}", encode_upyt_id(version, device_id)
    )

    # Enumerate files on the host/cache
    host_files = {
        p.relative_to(host_dir)
        for p in enumerate_local_files(host_dir, exclude + [f"/{UPYT_CACHE_DIRNAME}/"])
    }
    cached_files = {
        p.relative_to(cache_dir) for p in cache_dir.rglob("*") if p != cache_dir
    }

    # Work out which files (might) have changed based on the cache
    if out_of_date_cache or force_enumerate_files:
        # Don't use cache
        to_update = host_files
    else:
        to_update = {
            p
            for p in host_files
            if (
                # File/directory not on device yet
                p not in cached_files
                or
                # Changed from/to file/directory
                (host_dir / p).is_file() != (cache_dir / p).is_file()
                or
                # Content changed
                (
                    (host_dir / p).is_file()
                    and (cache_dir / p).is_file()
                    and (host_dir / p).read_bytes() != (cache_dir / p).read_bytes()
                )
            )
        }

    # Flush files from cache which are nolonger present (this will ensure no
    # directories we're about to insert into the cache are blocked by
    # (stale) files with the same name in the cache.
    for path in cached_files - host_files:
        full_path = cache_dir / path
        if path == Path(UPYT_ID_FILENAME):
            continue
        if full_path.is_dir():
            shutil.rmtree(full_path)
        elif full_path.is_file():
            full_path.unlink()

    # Ensure all directories exist (and are directories!)
    for path in sorted(to_update):
        if (host_dir / path).is_dir():
            device_path = f"{device_dir}/{'/'.join(path.parts)}"

            # If path is a file, delete it
            if fs.get_type(device_path) == PathType.file:
                fs.remove_recursive(device_path)

            # NB: We're working in sorted order so parents will be created
            # first as required
            fs.mkdir(device_path, exist_ok=True)

            # Add directory to cache (if not present already)
            if (cache_dir / path).is_file():
                (cache_dir / path).unlink()
            (cache_dir / path).mkdir(exist_ok=True)

    # Ensure all files exist (and are up-to-date)
    for path in sorted(to_update):
        if not (host_dir / path).is_dir():
            if progress_callback is not None:
                progress_callback(path, to_update, host_files)

            device_path = f"{device_dir}/{'/'.join(path.parts)}"

            # If path is currently a directory, delete it to make way for the
            # file
            if fs.get_type(device_path).is_dir():
                fs.remove_recursive(device_path)

            # Update/write the file
            try:
                fs.update_file(
                    device_path,
                    old_content=(cache_dir / path).read_bytes(),
                    new_content=(host_dir / path).read_bytes(),
                    safe=out_of_date_cache or force_safe_update,
                )
            except (OSError, UpdateError) as exc:
                # OSError thrown if file didn't exist in the cache (due to
                # read_bytes failing) or device (due to update_file failing).
                #
                # UpdateError thrown if the update failed (e.g. because the
                # copy on the device didn't match the cache).
                #
                # In all of these cases, we just write the file from scratch
                fs.write_file(device_path, (host_dir / path).read_bytes())

            # Add file to cache (if not present already)
            if (cache_dir / path).is_dir():
                shutil.rmtree(cache_dir / path)
            (cache_dir / path).write_bytes((host_dir / path).read_bytes())

    # Update version in cache only now we're successful (see note at beginning
    # when we update the version on the device)
    (cache_dir / UPYT_ID_FILENAME).write_bytes(encode_upyt_id(version, device_id))
