"""
Higher-level utilities for (relatively efficiently) manipulating the filesystem
on a MicroPython device via its repl.
"""

import os

from serial import Serial

from contextlib import contextmanager

from textwrap import dedent

from binascii import hexlify, unhexlify

from upyt.upy_repl import (
    raw_mode,
    raw_paste_exec,
)


@contextmanager
def upy_filesystem(ser: Serial) -> "FilesystemAPI":
    with raw_mode(ser):
        yield FilesystemAPI(ser)


def traceback_to_oserror(traceback: str) -> None:
    """
    Convert a traceback involving an OSError into a raised OSError, if present.
    """
    if traceback:
        exception, _, message = traceback.splitlines()[-1].partition(": ")
        if exception == "OSError":
            raise OSError(message)


class FilesystemAPI:
    
    # Snippets which may be executed to define/import a useful function/module.
    #
    # {name: (script, dependencies), ...}
    _DEFINITIONS = {
        "os": ("import os", []),
        "time": ("import time", []),
        # a mkdir which includes parents and exist_ok arguments
        "mkdir": (
            """
                def mkdir(path, parents, exist_ok):
                    if parents:
                        parent = ""
                        for part in path.split("/")[1:-1]:
                            parent += "/" + part
                            try:
                                os.mkdir(parent)
                            except OSError:
                                pass
                    
                    try:
                        os.mkdir(path)
                    except OSError:
                        if not exist_ok:
                            raise
            """,
            ["os"],
        ),
        # Recursively delete
        #
        # Note that this process can take a surprisingly long time for large
        # hierarchies. As such, to avoid the serial connection timing out, this
        # function will raise a timeout error 
        "remove_recursive": (
            """
                def remove_recursive(path, timeout_ms, _timeout_at=None):
                    if _timeout_at is None:
                        _timeout_at = time.ticks_add(time.ticks_ms(), timeout_ms)
                    if os.stat(path)[0] & 0x4000:
                        for entry in os.ilistdir(path):
                            name, type = entry[:2]
                            if type & 0x4000:
                                remove_recursive(f"{path}/{name}", timeout_ms, _timeout_at)
                            else:
                                os.remove(f"{path}/{name}")
                            if time.ticks_diff(_timeout_at, time.ticks_ms()) <= 0:
                                raise Exception("Timeout")
                        os.rmdir(path)
                    else:
                        os.remove(path)
            """,
            ["os", "time"],
        ),
        # List directory and filenames within a path
        "ls": (
            """
                def ls(path):
                    directories = []
                    files = []
                    for entry in os.ilistdir(path):
                        name, type = entry[:2]
                        if type & 0x4000:
                            directories.append(name)
                        else:
                            files.append(name)
                    return (directories, files)
            """,
            ["os"],
        ),
        # Print next (approx) n-bytes worth of string literals from an iterator
        # as a Python list literal. Prints an empty list when the iterator is
        # exhausted.
        #
        # The utility of this is in printing out (possibly long) sequences of
        # of (relatively short) strings without a serial read timing out due to
        # the sheer time spent printing things.
        "pns": (
            """
                def pns(iterator, size):
                    so_far = 0
                    print("[", end="")
                    while so_far < size:
                        try:
                            value = next(iterator)
                            print(repr(value), end=",")
                            so_far += len(value)
                        except StopIteration:
                            break
                    print("]", end="")
            """,
            [],
        ),
        # Short aliases for unhexlify and hexlify
        "uh": ("from binascii import unhexlify as uh", []),
        "h": ("from binascii import hexlify as h", []),
        # Function which takes a bytes object and either returns a Python bytes
        # literal (as a string) or a Python expression calling unhexlify
        # (aliased to 'uh'), depending which is shorter.
        "bytes_to_evalable": (
            """
                def bytes_to_evalable(data):
                    as_bytes = repr(data)
                    len_as_bytes = len(as_bytes)
                    len_as_hex = len('uh(b"")') + (len(data) * 2)
                    if len_as_bytes < len_as_hex:
                        return as_bytes
                    else:
                        return f"uh({h(data)})"
            """,
            ["h"]
        ),
        # Print the next n bytes from a file using bytes_to_evalable.
        "pnb": (
            """
                def pnb(f, n):
                    print(bytes_to_evalable(f.read(n)))
            """,
            ["bytes_to_evalable"]
        ),
    }
    
    def __init__(self, ser: Serial) -> None:
        self._ser = ser
        self._defined = set()
    
    def _ensure_defined(self, name: str) -> None:
        """
        Ensure the specified definition is in scope.
        """
        if name not in self._defined:
            self._defined.add(name)
            
            definition, dependencies = FilesystemAPI._DEFINITIONS[name]
            
            # Resolve dependencies
            for dependency in dependencies:
                self._ensure_defined(dependency)
            
            # Load definition
            assert raw_paste_exec(
                self._ser,
                dedent(definition).strip(),
            ) == ("", "")
    
    def mkdir(
        self, path: str, parents: bool = False, exist_ok: bool = False,
    ) -> None:
        """
        Create a directory.
        """
        path = path.rstrip("/")
        self._ensure_defined("mkdir")
        out, err = raw_paste_exec(
            self._ser, f"mkdir({path!r}, {parents}, {exist_ok})"
        )
        traceback_to_oserror(err)
        assert err == "", err
        assert out == "", out
    
    def remove_recursive(self, path: str):
        """
        Remove a given file or directory tree recursively.
        """
        self._ensure_defined("remove_recursive")
        
        # NB: To avoid issues with remove_recursive taking so long that the
        # serial port times out, the remove_recursive process will timeout
        # after a given number of ms by throwing an Exception. As such, all we
        # need to do is continue re-running the command until we nolonger get
        # that exception.
        timeout_ms = 1000
        if self._ser.timeout is not None:
            timeout_ms = int(self._ser.timeout * 500)
        while True:
            out, err = raw_paste_exec(self._ser, f"remove_recursive({path!r}, {timeout_ms})")
            if err.endswith("\r\nException: Timeout\r\n"):
                continue
            traceback_to_oserror(err)
            assert err == "", err
            assert out == "", out
            break
    
    def ls(self, path: str, block_size: int = 512) -> tuple[list[str], list[str]]:
        """
        List the directories and files (separately) at a given path.
        """
        # NB: The process of reading the lists of directories and files is
        # slightly more complicated than just evaling the repr'd directory and
        # file lists because if these are long, the print command might take
        # long enough to trigger a timeout. Instead we use the 'pns' utility
        # function to print a few entries at a time.
        
        self._ensure_defined("ls")
        out, err = raw_paste_exec(self._ser, f"d, f = map(iter, ls({path!r}))")
        traceback_to_oserror(err)
        assert err == "", err
        assert out == "", out
        
        self._ensure_defined("pns")
        directories = []
        files = []
        for lst, name in [(directories, "d"), (files, "f")]:
            while True:
                out, err = raw_paste_exec(self._ser, f"pns({name}, {block_size})")
                assert err == "", err
                to_add = eval(out)
                if to_add:
                    lst.extend(to_add)
                else:
                    break
        
        return directories, files
    
    def write_file_raw(
        self, path: str, content: bytes, block_size: int = 512
    ) -> None:
        """
        Write a file in the most simplistic possible way: by just copying the
        whole file over, block_size bytes at a time.
        
        Internally attempts to write the file using whichever mode is most
        efficient: Python bytes literals or hex strings. For mainly text-based
        files the former is typically used (with a relatively low overhead).
        For binary files, the latter will likely be used with a roughly 2x
        overhead in data transfer.
        """
        out, err = raw_paste_exec(
            self._ser, f"f = open({path!r}, 'wb'); w = f.write"
        )
        traceback_to_oserror(err)
        assert err == "", err
        assert out == "", out
        
        while content:
            block = content[:block_size]
            content = content[block_size:]
            
            as_bytes = f"w({block!r})"
            len_as_bytes = len(as_bytes)
            len_as_hex = len('w(uh(b""))') + (len(block) * 2)
            
            # Write using whichever format is most efficient for this block
            if len_as_bytes < len_as_hex:
                assert raw_paste_exec(self._ser, as_bytes) == ("", "")
            else:
                self._ensure_defined("uh")  # = unhexlify
                assert raw_paste_exec(self._ser, f"w(uh({hexlify(block)!r}))") == ("", "")
        
        assert raw_paste_exec(self._ser, f"f.close()") == ("", "")

    def read_file_raw(
        self, path: str, block_size: int = 512
    ) -> bytes:
        """
        Read a file in the most simplistic way possible: by just copying the
        whole file, block_size bytes at a time.
        
        See :py:meth:`write_file_raw` for the analgous process and a
        description of how this is done
        """
        out, err = raw_paste_exec(
            self._ser, f"f = open({path!r}, 'rb');"
        )
        traceback_to_oserror(err)
        assert err == "", err
        assert out == "", out
        
        self._ensure_defined("pnb")
        
        data = b""
        while True:
            out, err = raw_paste_exec(self._ser, f"pnb(f, {block_size})")
            assert err == "", err
            block = eval(out, {"uh": unhexlify})
            data += block
            
            if len(block) < block_size:
                break
        
        out, err = raw_paste_exec(self._ser, "f.close()")
        assert err == "", err
        assert out == "", out
        
        return data
    
    def sync(self) -> None:
        """
        Sync the filesystem to storage, if sync is supported by the platform.
        Otherwise, does nothing.
        """
        self._ensure_defined("os")
        out, err = raw_paste_exec(self._ser, "if hasattr(os, 'sync'): os.sync()")
        assert out == "", out
        assert err == "", err
