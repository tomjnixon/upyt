"""
Generic interface representing a connection to a MicroPython instance (e.g.
serial port, subprocess (e.g. UNIX) or network).
"""

from typing import Iterator

from contextlib import contextmanager

import time

from selectors import DefaultSelector, EVENT_READ, EVENT_WRITE

from serial import Serial

from subprocess import Popen, PIPE


class Connection:
    
    def read(self, num_bytes: int) -> bytes:
        """
        Read num_bytes bytes, returning however many bytes were read before the
        timeout.
        """
        raise NotImplementedError()
    
    def read_until(self, data: bytes) -> bytes:
        """
        Read until the given data is read, returning however much data was read
        upto and including that data or until a timeout ocurred.
        """
        raise NotImplementedError()
    
    def read_buffered(self) -> bytes:
        """
        Read any already received and buffered data immediately.
        """
        raise NotImplementedError()
    
    def write(self, data: bytes) -> int:
        """
        Write the specified bytes, returning how many were actually written.
        """
        raise NotImplementedError()
    
    def close(self) -> None:
        """
        Close the connection.
        """
        raise NotImplementedError()
    
    def __enter__(self):
        """
        When used as a context manager, will close the connection on context
        exit. Returns a reference to this object as the 'as' value.
        """
        return self
    
    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.close()
    
    @property
    def timeout(self) -> float:
        """
        Get the current read/write timeout (in seconds).
        """
        raise NotImplementedError()
    
    @timeout.setter
    def timeout(self, value: float) -> None:
        """
        Set the current read/write timeout (in seconds).
        """
        raise NotImplementedError()
    
    @contextmanager
    def timeout_override(self, value: float) -> Iterator[None]:
        """
        Context manager which temporarily overrides the timeout for this
        connection.
        """
        old_timeout = self.timeout
        try:
            self.timeout = value
            yield
        finally:
            self.timeout = old_timeout


class SerialConnection(Connection):
    """
    A :py:class:`Connection` based on a :py:class:`serial.Serial` port
    (constructor takes the same arguments).
    """
    
    _ser: Serial
    
    def __init__(self, *args, **kwargs) -> None:
        self._ser = Serial(*args, **kwargs)
    
    def read(self, num_bytes: int) -> bytes:
        return self._ser.read(num_bytes)
    
    def read_until(self, data: bytes) -> bytes:
        return self._ser.read_until(data)
    
    def read_buffered(self) -> bytes:
        if self._ser.in_waiting:
            return self._ser.read(self._ser.in_waiting)
        else:
            return b""
    
    def write(self, data: bytes) -> int:
        return self._ser.write(data)
    
    def close(self) -> None:
        self._ser.close()
    
    @property
    def timeout(self) -> float:
        return self._ser.timeout
    
    @timeout.setter
    def timeout(self, value: float) -> None:
        self._ser.timeout = value
