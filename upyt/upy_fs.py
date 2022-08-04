"""
Higher-level utilities for (relatively efficiently) manipulating the filesystem
on a MicroPython device via its repl.
"""

from typing import Iterable, Iterator, Optional, Callable

import os

from serial import Serial

from contextlib import contextmanager

from textwrap import dedent

from binascii import hexlify, unhexlify

from hashlib import sha256

from difflib import SequenceMatcher

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

def data_to_writes(data: bytes, block_size: int = 512) -> Iterator[tuple[str, int]]:
    """
    Given some bytes to write to a file using calls to a function named 'w'
    in blocks of up to block_size bytes, generates a series of Python
    snippets to do the job, using hex or Python string representations
    depending on what is most efficient.
    
    Specifically, yields a series of (python_snippet, bytes_written)
    tuples.

    The function 'uh' is expected to be an alias to unhexlify.
    """
    while data:
        block = data[:block_size]
        data = data[block_size:]
        
        as_bytes = f"w({block!r})"
        len_as_bytes = len(as_bytes)
        len_as_hex = len('w(uh(b""))') + (len(block) * 2)
        
        # Write using whichever format is most efficient for this block
        if len_as_bytes < len_as_hex:
            yield as_bytes, len(block)
        else:
            yield f"w(uh({hexlify(block)!r}))", len(block)


def combine_sm_operations(
    operations: Iterator[tuple[str, int, int, int, int]],
    equal_overhead: int = 0,
    seek_overhead: int = 0,
) -> Iterator[tuple[str, int, int, int, int]]:
    """
    Filters/modifies a SequenceMatcher's opcodes sequence to leave only insert
    and equal operations (with all non-referenced data being implicitly
    considered deleted). Further, insert only defines j1 and j2 and equal only
    defines i1 and i2.
    
    Attempts to coallesce equivalent operations (e.g. insert followed by
    replace).
    
    The equal_overhead parameter gives the overhead of inserting an 'equal'
    operation vs just adding more values to an already ongoing 'insert'. This
    is used to decide whether to switch from an insertion to an equal.
    
    The seek_overhead parameter indicates additional cost to add to
    equal_overhead to account for seeking past deleted content.
    """
    # The most recent opcode to be read (if an insertion, this might be
    # extended as a result of merging adjacent insertion or equal operations)
    #
    # We begin with a 'fake' empty insertion. This enables us to handle the
    # case where the first real operation is a very short 'equal' followed by
    # an insertion without having to code up the logic for merging an insertion
    # with an equal (as opposed to the other way around which we do implement).
    cur_opcode_is_real = False
    cur_opecode = "insert"
    cur_i1 = None
    cur_i2 = None
    cur_j1 = 0
    cur_j2 = 0
    
    # The implicit index of the next value to read from the old input array
    # (i.e. for which no seek would be required).
    read_offset = 0
    
    for opcode, i1, i2, j1, j2 in operations:
        if opcode in ("insert", "replace"):
            # Replace is equiv to insert+delete and in our case we don't care
            # about deletions, therefore replace = insert for our purposes.
            opcode = "insert"
            
            if cur_opecode == opcode:
                assert cur_j2 == j1  # Sanity check
                cur_j2 = j2
                cur_opcode_is_real = True
            else:
                if cur_opcode_is_real:
                    yield (cur_opecode, cur_i1, cur_i2, cur_j1, cur_j2)
                cur_opcode_is_real = True
                cur_opecode = opcode
                cur_i1, cur_i2, cur_j1, cur_j2 = None, None, j1, j2
        elif opcode == "equal":
            # NB: We should never encounter two 'equal' operations in a row so
            # we'll not bother trying to merge them here.
            
            overhead = equal_overhead + (seek_overhead if read_offset != i1 else 0)
            
            if cur_opecode == "insert" and i2 - i1 <= overhead:
                # The ongoing operation is an insertion and it would be cheaper
                # to add the literal equal bytes to that than to switch to an
                # 'equal' here.
                assert cur_j2 == j1  # Sanity check
                cur_j2 = j2
                cur_opcode_is_real = True
            else:
                # Either the previous operation wasn't an insertion or this
                # equal block is large enough that we should emit it as an
                # equal directly.
                if cur_opcode_is_real:
                    yield (cur_opecode, cur_i1, cur_i2, cur_j1, cur_j2)
                cur_opcode_is_real = True
                cur_opecode = opcode
                cur_i1, cur_i2, cur_j1, cur_j2 = i1, i2, None, None
                read_offset = i2
        elif opcode == "delete":
            # Deletions are considered a no-operation. Note that we don't end
            # the previously running operation since the only operation we
            # continue are inserts and these are not sensitive to deletion.
            pass
        else:
            assert False, f"Unexpected opcode: {opcode}"
    
    # Flush any remaining operation
    if cur_opcode_is_real:
        yield (cur_opecode, cur_i1, cur_i2, cur_j1, cur_j2)


def data_to_update_commands(
    old_content: bytes,
    new_content: bytes,
    block_size: int = 512,
    hasher: Optional[Callable[[bytes], None]] = None,
) -> Iterator[tuple[str, int]]:
    """
    Given the existing contents of an existing  file (old_content) and
    desired new file contents (new_content), generates a series of read and
    seek operations on an old file and write operations on a new file.
    
    Specifically, generates a series of (python_snippet, bytes_written)
    tuples where the python_snippets should be evaluated in the given
    order and will cause the given number of bytes to be written.
    
    The read function on the old file is expected to be called 'r'.
    
    The seek function on the old file is expected to be called 's'.
    
    The write function on the new file expected to be called 'w'.
    
    The old and new files are expected to be distinct.
    
    Writes of new material will be restricted to at most block_size bytes
    at a time.
    
    If a hasher is provided, it will be called on every block of
    old_content used in the patching process.
    """
    # Guess whether we'll be using hex mode for most of the data
    sample_length_as_byte_string = len(repr(new_content[:block_size])) - 3
    sample_length_as_hex_string = len(new_content[:block_size]) * 2
    probably_mostly_hex = sample_length_as_byte_string > sample_length_as_hex_string
    
    # The basic overhead computed below assumes literal values are being
    # encoded as a byte string at about one byte on the wire per useful byte.
    #
    # In the hex case, however, the overhead is effectively halved because the
    # cost of encoding each byte in hex is double the length of the byte.
    overhead_scale = 2 if probably_mostly_hex else 1
    
    # We use the SequenceMatcher utility to produce a series of edits which
    # can convert an on-disk copy of old_content into new_content. Whilst
    # this frequently produces compact patches, certain types of edit could
    # potentially be better supported by a different algorithm, for example
    # shuffling lines in a file.
    operations = combine_sm_operations(
        SequenceMatcher(None, old_content, new_content).get_opcodes(),
        # We presume that most seeks will have the same number of digits as the
        # index half way through -- which on average they will more or less.
        seek_overhead=len(f"s({len(old_content) // 2})\n") // overhead_scale,
        # We presume that (for the sake of overhead calculations which only
        # matter for short reads) reads will be short (i.e. a single digit
        # number of bytes)
        equal_overhead=len("w(r(9))\n") // overhead_scale,
    )
    
    read_offset = 0
    for operation, i1, i2, j1, j2 in operations:
        if operation in ("insert", "replace"):
            # NB: In the case of replace, we'll seek past the old content
            # when we next need read.
            yield from data_to_writes(new_content[j1:j2], block_size)
        elif operation == "equal":
            if hasher is not None:
                hasher(old_content[i1:i2])
            
            if read_offset != i1:
                read_offset = i1
                yield (f"s({i1})", 0)
            while read_offset < i2:
                length = min(i2 - read_offset, block_size)
                yield (f"w(r({length}))", length)
                read_offset += length
        elif operation == "delete":
            # This is a no-op! We'll seek pasth the old content when we
            # next read.
            pass

def batch_commands(
    command_iter: Iterable[tuple[str, int]],
    bytes_per_batch: int = 512,
    commands_per_batch: int = 20,
) -> Iterator[tuple[str, int]]:
    """
    Group a series of commands (given as (command, bytes_touched) tuples),
    group these into batches (concatenated with newlines) where each batch
    never exceeds the given number of bytes touched or number of commands
    executed.
    """
    this_batch = []
    this_batch_bytes = 0
    for command, num_bytes in command_iter:
        # If this command will not fit in the current batch, flush that batch
        # out
        if (
            this_batch_bytes + num_bytes > bytes_per_batch or
            len(this_batch) + 1 > commands_per_batch
        ):
            yield ("\n".join(this_batch), this_batch_bytes)
            this_batch = []
            this_batch_bytes = 0
        
        this_batch.append(command)
        this_batch_bytes += num_bytes
    
    # Output the final batch (if non-empty)
    if this_batch_bytes > 0:
        yield ("\n".join(this_batch), this_batch_bytes)

class UpdateError(Exception):
    """
    Thrown when :py:meth:`FilesystemAPI.patch` fails due to the file on disk
    differing from the one used on the host to apply the patch.
    """


class FilesystemAPI:
    
    # Snippets which may be executed to define/import a useful function/module.
    #
    # {name: (script, dependencies), ...}
    _DEFINITIONS = {
        "os": ("import os", []),
        "time": ("import time", []),
        "sha256": ("from hashlib import sha256", []),
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
        # Short alias which wraps a reader function such that all read values
        # will be added to the provided hasher.
        "make_read_and_hash": (
            """
                def make_read_and_hash(reader, hasher):
                    def wrapped_reader(*args):
                        data = reader(*args)
                        hasher.update(data)
                        return data
                    return wrapped_reader
            """,
            []
        ),
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
        # Print an unused temporary filename starting the the given prefix.
        # Does not create any files
        "get_temp_file_name": (
            """
                def get_temp_file_name(prefix):
                    i = 0
                    while True:
                        name = "{prefix}.{i}"
                        try:
                            os.stat(name)
                        except OSError:
                            print(name)
                            return
                        i += 1
            """,
            ["os"]
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
    
    def rename(self, old_path: str, new_path: str) -> None:
        """
        Rename/move a file.
        """
        self._ensure_defined("os")
        out, err = raw_paste_exec(self._ser, f"os.rename({old_path!r}, {new_path!r})")
        traceback_to_oserror(err)
        assert out == "", out
        assert err == "", err
    
    def _open_file(
        self,
        path: str,
        mode: str,
        aliases: dict[str, str] = {},
        file_object_name: str = "f"
    ) -> None:
        """
        Open the named file, checking for OSError and assigning the open file
        object to the name ``file_object_name``.
        
        Also creates aliases to named members of the file object. (e.g. {'w':
        'write'} creates an alias, 'w' to 'f.write').
        """
        defs = ";".join(
            f"{alias}={file_object_name}.{member}"
            for alias, member in aliases.items()
        )
        out, err = raw_paste_exec(
            self._ser,
            f"{file_object_name} = open({path!r}, {mode!r});{defs}",
        )
        traceback_to_oserror(err)
        assert err == "", err
        assert out == "", out
    
    def _close_file(self, file_object_name: str = "f") -> None:
        """
        Close the file ``file_object_name``.
        """
        out, err = raw_paste_exec(self._ser, f"{file_object_name}.close()")
        traceback_to_oserror(err)
        assert err == "", err
        assert out == "", out
    
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
        self._open_file(path, "wb", {"w": "write"})
        
        self._ensure_defined("uh")  # = unhexlify
        for write_block, _ in data_to_writes(content, block_size):
            assert raw_paste_exec(self._ser, write_block) == ("", "")
        
        self._close_file()

    def read_file_raw(
        self, path: str, block_size: int = 512
    ) -> bytes:
        """
        Read a file in the most simplistic way possible: by just copying the
        whole file, block_size bytes at a time.
        
        See :py:meth:`write_file_raw` for the analgous process and a
        description of how this is done
        """
        self._open_file(path, "rb")
        
        self._ensure_defined("pnb")
        
        data = b""
        while True:
            out, err = raw_paste_exec(self._ser, f"pnb(f, {block_size})")
            assert err == "", err
            block = eval(out, {"uh": unhexlify})
            data += block
            
            if len(block) < block_size:
                break
        
        self._close_file()
        
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
    
    def update_file(
        self,
        path: str,
        old_content: bytes,
        new_content: bytes,
        block_size: int = 512,
        block_transaction_limit: int = 20,
        safe: bool = True,
    ) -> None:
        """
        Attempt to update an existing file such that its contents is changed
        from ``old_content`` to ``new_content`` without transferring the whole
        file contents over the wire, just the differences.

        The ``block_size`` controls the maximum number of bytes transferred
        over the wire (or on disk) in one go.
        
        The ``block_transaction_limit`` limits the number of copy/write cycles
        which can occur in a single block.

        The ``safe`` parameter enables optional checks to verify that the
        reconstructed file does actually match ``new_content`` (i.e. that the
        file originally did contain ``old_content``, or rather a close-enough
        rendition). A :py:exc:`UpdateError` will be thrown if the check fails.
        If disabled, performance will be a little better but the resulting file
        may potentially be corrupt.
        """
        self._open_file(path, "rb", {"r": "read", "s": "seek"}, "fi")
        
        # We will construct the patch in a separate (temporary) file
        self._ensure_defined("get_temp_file_name")
        temp_path, err = raw_paste_exec(self._ser, f"get_temp_file_name({path!r})")
        assert err == "", err
        self._open_file(temp_path, "wb", {"w": "write"}, "fo")
        
        # To implement safe mode we compute a checksum of all data read from
        # the input file. If the input file has not become (too far) out of
        # sync, the same hash computed on the host should match.
        #
        # Note that we don't need to hash unread parts of the file since these
        # have on effect on the final (patched) file and so can differ without
        # consequence.
        if safe:
            self._ensure_defined("sha256")
            self._ensure_defined("make_read_and_hash")
            out, err = raw_paste_exec(
                self._ser,
                "hash = sha256(); r = make_read_and_hash(r, hash)",
            )
            assert out == "", out
            assert err == "", err
            
            read_hash = sha256()
        
        # Apply the patch
        self._ensure_defined("uh")  # = unhexlify
        for commands, _ in batch_commands(
            data_to_update_commands(
                old_content,
                new_content,
                block_size=block_size,
                hasher=read_hash.update if safe else None,
            ),
            bytes_per_batch=block_size,
            commands_per_batch=block_transaction_limit,
        ):
            out, err = raw_paste_exec(self._ser, commands)
            assert out == "", commands + "\n---\n" + out
            assert err == "", commands + "\n---\n" + err
        
        self._close_file("fi")
        self._close_file("fo")
        
        # Check integrity
        if safe:
            self._ensure_defined("h")
            out, err = raw_paste_exec(self._ser, "print(hash.digest())")
            assert err == "", err
            actual_read_hash = eval(out)
            
            if read_hash.digest() != actual_read_hash:
                self.remove_recursive(temp_path)
                raise UpdateError()
        
        # Replace the old file with the newly constructed one
        self.rename(temp_path, path)
