"""
For internal use.

This module contains machinery for producing 'read proxies' which sit between
stdin and the :py:mod:`upyt.upy_terminal` terminal to handle things like
keyboard shortcuts (e.g. to quit the terminal) and intercept bracketed paste
sequences.

The :py:class:`ReadProxy` class takes a file and a
seuqnce of stream filters to apply to the stream.

A stream filter is a factory which takes an iterator-of-characters as an
argument and itself returns an iterator-of-characters following whatever
filtering/processing is needed. Iterators may generate a None (rather than a
character) if they are waiting for further input.
"""


from typing import cast, TextIO, Iterable, Iterator, Callable, AnyStr, BinaryIO, Any

from itertools import takewhile, islice


StreamFilter = Callable[[Iterable[str | None]], Iterator[str | None]]


def replace(find: str, replace: str) -> StreamFilter:
    """
    Return a stream filter which replaces all instances of 'find' with
    'replace'. Partial matches are buffered until they are determined to
    unambiguously match or not.
    """

    def replacer(sequence: Iterable[str | None]) -> Iterator[str | None]:
        # We may need to buffer the input to decide if what we've read matches the
        # find sequence
        input_buffer = ""

        for char in sequence:
            if char is None:
                yield None
            else:
                input_buffer += char
                if find == input_buffer:
                    # Found a match, produce its replacement
                    for out in replace:
                        yield out
                    input_buffer = ""
                elif not find.startswith(input_buffer):
                    # What we've buffered so far can't possibly be a match so
                    # return it as-is.
                    for out in input_buffer:
                        yield out
                    input_buffer = ""
                else:
                    # Otherwise, we've buffered the start of what might be a match,
                    # lets get more and see...
                    pass

    return replacer


def match(find: str, callback: Callable[[], None]) -> StreamFilter:
    """
    Return a stream filter which calls the provided callback whenever the
    'find' string is encountered. Like :py:func:`replace`, buffers values which
    may potentially match 'find' until the ambiguity is resolved.

    Note that the callback is called as soon as the find string has been
    encountered but prior to the string being forwarded. This means if the
    callback throws an exception, the matched characters will never be emitted.
    """

    def matcher(sequence: Iterable[str | None]) -> Iterator[str | None]:
        # We may need to buffer the input to decide if what we've read matches the
        # find sequence
        input_buffer = ""

        for char in sequence:
            if char is None:
                yield None
            else:
                input_buffer += char

                if find == input_buffer:
                    # Found what we're looking for
                    callback()

                if find == input_buffer or not find.startswith(input_buffer):
                    # Either found what we were looking for, or definitely don't
                    # have a partial match so just past it on unchanged.
                    for out in input_buffer:
                        yield out
                    input_buffer = ""

    return matcher


class ReadProxy(TextIO):
    def __init__(
        self,
        readable: TextIO,
        stream_filters: list[StreamFilter],
    ) -> None:
        """
        Create a read proxy which proxies reads from 'readable' via the series
        of stream filters provided. Filters are applied in the order
        they're provided.
        """
        self._readable = readable

        self._iterator = self._read_iter()
        for stream_filter in stream_filters:
            self._iterator = stream_filter(self._iterator)

    def _read_iter(self) -> Iterator[str | None]:
        """
        Wrap the internal file object in an iterator of characters (or None
        when no data is available for reading).
        """
        while True:
            char = self._readable.read(1)
            yield char or None

    def read(self, length=None) -> str:
        iterator = cast(
            Iterable[str],
            takewhile(lambda b: b is not None, self._iterator),
        )
        if length is not None:
            iterator = islice(iterator, length)

        return "".join(iterator)

    def fileno(self) -> int:
        return self._readable.fileno()

    # This class is only a bare-bones look-alike for a readable file for
    # internal use so all of the following are not implemented...

    @property
    def mode(self) -> str:
        raise NotImplementedError()

    @property
    def name(self) -> str:
        raise NotImplementedError()

    def close(self) -> None:
        raise NotImplementedError()

    @property
    def closed(self) -> bool:
        raise NotImplementedError()

    def flush(self) -> None:
        raise NotImplementedError()

    def isatty(self) -> bool:
        raise NotImplementedError()

    def readable(self) -> bool:
        raise NotImplementedError()

    def readline(self, limit: int = -1) -> AnyStr:
        raise NotImplementedError()

    def readlines(self, hint: int = -1) -> list[AnyStr]:
        raise NotImplementedError()

    def seek(self, offset: int, whence: int = 0) -> int:
        raise NotImplementedError()

    def seekable(self) -> bool:
        raise NotImplementedError()

    def tell(self) -> int:
        raise NotImplementedError()

    def truncate(self, size: int | None = None) -> int:
        raise NotImplementedError()

    def writable(self) -> bool:
        raise NotImplementedError()

    def write(self, s: AnyStr) -> int:
        raise NotImplementedError()

    def writelines(self, lines: Iterable[AnyStr]) -> None:
        raise NotImplementedError()

    def __exit__(self, type, value, traceback) -> None:
        raise NotImplementedError()

    @property
    def buffer(self) -> BinaryIO:
        raise NotImplementedError()

    @property
    def encoding(self) -> str:
        raise NotImplementedError()

    @property
    def errors(self) -> str | None:
        raise NotImplementedError()

    @property
    def line_buffering(self) -> bool:
        raise NotImplementedError()

    @property
    def newlines(self) -> Any:
        raise NotImplementedError()

    def __enter__(self) -> "TextIO":
        raise NotImplementedError()

    def __iter__(self) -> Iterator[str]:
        raise NotImplementedError()

    def __next__(self) -> str:
        raise NotImplementedError()
