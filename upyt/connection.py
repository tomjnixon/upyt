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
    
    @staticmethod
    def from_specification(spec: str) -> "Connection":
        """
        Create a new :py:class:`Connection` given a text-based specification of
        the connection details.
        
        Currently supported specifications are:
        
        * ``/path/to/serial/device`` or ``COM1`` -- Create a SerialConnection
          assuming 9600 baud.
        * ``/path/to/serial/device:9600`` or ``COM1:9600`` -- Create a
          SerialConnection at a specific baudrate.
        """
        if ":" in spec:
            port, _, baudrate_str = spec.rpartition(":")
            baudrate = int(baudrate_str)
        else:
            port = spec
            baudrate = 9600
        
        return SerialConnection(port, baudrate)
    
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
    
    def flush(self) -> int:
        """
        Flush any buffered written bytes to the device.
        """
        raise NotImplementedError()
    
    def close(self) -> None:
        """
        Close the connection.
        """
        raise NotImplementedError()
    
    def fileno(self) -> int:
        """
        Return the file descriptor index which may be waited on whilst awaiting
        data to read for this connection.
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
    
    def __init__(self, *args, timeout=1.0, **kwargs) -> None:
        self._ser = Serial(*args, timeout=timeout, **kwargs)
    
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
    
    def flush(self) -> None:
        self._ser.flush()
    
    def close(self) -> None:
        self._ser.close()
    
    def fileno(self) -> int:
        """
        Return the file descriptor index which may be waited on whilst awaiting
        data to read for this connection.
        """
        return self._ser.fileno()
    
    @property
    def timeout(self) -> float:
        return self._ser.timeout
    
    @timeout.setter
    def timeout(self, value: float) -> None:
        self._ser.timeout = value
