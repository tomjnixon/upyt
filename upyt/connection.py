"""
Generic interface representing a connection to a MicroPython instance (e.g.
serial port, subprocess (e.g. UNIX) or network).
"""

from typing import cast, Iterator, NamedTuple


import os
import time
import socket
import struct

from base64 import b64encode, b64decode
from contextlib import contextmanager
from enum import IntEnum
from hashlib import sha1
from itertools import cycle
from selectors import DefaultSelector, EVENT_READ, EVENT_WRITE
from threading import Thread, Lock
from urllib.parse import urlparse

from serial import Serial


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
        * ``ws://10.0.0.111/?password-goes-here`` -- Create a WebReplConnection
          to the device at the given URL. The password is provided at the end
          of the URL after a `?` and is stripped from the URL before use. The
          port number 8266 (and not 80) is assumed if not given.
        """
        if spec.startswith("ws://"):
            url, query, password = spec.rpartition("?")
            if not query:
                raise ValueError(
                    "Expected '?password-here' suffix to ws:// device path"
                )
            return WebReplConnection(url, password)
        else:
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

    def flush(self) -> None:
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
        kwargs["timeout"] = 1.0
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
        # NB: Pyserial docs say this is always an int, even if its type
        # signature doesn't...
        return cast(int, self._ser.write(data))

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
        # NB: Pyserial docs say this is always a float, even if its type
        # signature doesn't...
        return cast(float, self._ser.timeout)

    @timeout.setter
    def timeout(self, value: float) -> None:
        self._ser.timeout = value


class WebReplError(Exception):
    """Base class for websocket/webrepl related errors."""


class WebReplHandshakeError(WebReplError):
    """Thrown when the WebSocket handshake fails."""


class WebReplAuthenticationError(WebReplError):
    """Thrown when the password given is incorrect."""


class WebReplFrameReservedBitsError(WebReplError):
    """Set if a websocket frame with any reserved bits set is receieved."""


class _WebsocketOpcode(IntEnum):
    continuation = 0
    text = 1
    binary = 2
    close = 8
    ping = 9
    pong = 10


class _WebsocketFrame(NamedTuple):
    fin: bool
    opcode: _WebsocketOpcode
    payload: bytes


class WebReplConnection(Connection):
    """
    A :py:class:`Connection` based on the MicroPython WebREPL protocol.

    The WebREPL protocol is intended to just be serial over websockets --
    except the design fails to account for non-UTF-8 (or simply partial UTF-8)
    content in the serial stream, sending it via 'text' websocket messages. As
    a result, we can't use an off-the-shelf WebSocket implementation as these
    (correctly) balk at this incorrect use of the protocol.

    See: https://github.com/micropython/webrepl/issues/77

    Instead, we include a minimal (compatibly non-conformant) implementation in
    this class capable of talking to MicroPython -- albeit little more.
    """

    _sock: socket.socket

    _send_lock: Lock  # Held whilst sending to the socket

    # To achieve file-like semantics for the read-half of the websocket
    # connection we use a background thread to feed data read from the
    # websocket into a pipe which we can read one byte at a time (rather than
    # one frame at a time).
    _recv_pipe: int  # File descriptor of the read end of the pipe
    _recv_thread: Thread

    _timeout: float

    def __init__(
        self,
        ws_url: str,
        password: str,
        timeout: float = 5.0,
        handshake_timeout: float = 10.0,
    ) -> None:
        # Pull out host, port and path from URL
        parsed_url = urlparse(ws_url)
        host = parsed_url.hostname
        if host is None:
            raise ValueError("Websocket URL must include host")
        port = parsed_url.port
        if port is None:
            port = 8266
        path = ws_url[len(parsed_url.scheme + "://" + parsed_url.netloc) :]

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.connect((host, port))

        self._websocket_handshake(host, path, handshake_timeout)

        self._send_lock = Lock()
        self._recv_pipe, self._recv_thread = self._recv_to_pipe()

        self.timeout = timeout

        # Authenticate
        try:
            self.read_until(b": ")
            self.write(password.encode("utf-8") + b"\n")
            response = self.read_until(b"\r\n")
            if response != b"\r\n":
                raise WebReplAuthenticationError(response)
            response = self.read_until(b"\r\n")
            if response != b"WebREPL connected\r\n":
                raise WebReplAuthenticationError(response)

            # NB: The prompt here is not a real prompt but a fixed part of the
            # message printed by WebREPL once it has authenticated
            response = self.read_until(b">>> ")
            if response != b">>> ":
                raise WebReplAuthenticationError(response)
        except:
            self.close()
            raise

    def _websocket_handshake(
        self, host: str, path: str = "/", timeout: float = 10.0
    ) -> None:
        """
        Perform a basic websocket handshake. Just conformant enough to handle
        MicroPython's WebSocket implementation...
        """
        old_timeout = self._sock.gettimeout()
        try:
            self._sock.settimeout(timeout)

            # Send client handshake
            client_key = os.urandom(16)
            client_key_b64 = b64encode(client_key)
            exp_server_key = sha1(
                client_key_b64 + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            ).digest()

            self._sock.sendall(
                (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    f"Upgrade: websocket\r\n"
                    f"Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {client_key_b64.decode('ascii')}\r\n"
                    f"Sec-WebSocket-Version: 13\r\n"
                    f"\r\n"
                ).encode("ascii")
            )

            # Wait for the server response headers
            buf = b""
            while char := self._sock.recv(1):
                buf += char
                if buf.endswith(b"\r\n\r\n"):
                    break

            # Verify server handshake
            status, *header_lines = buf.decode("ascii").rstrip().splitlines()
            if status != "HTTP/1.1 101 Switching Protocols":
                raise WebReplHandshakeError(f"Unexpected status line: {status}")
            headers = {
                line.partition(":")[0].strip().lower(): line.partition(":")[2].strip()
                for line in header_lines
            }
            if headers.get("upgrade") != "websocket":
                raise WebReplHandshakeError(
                    f"Invalid upgrade header: {headers.get('upgrade')}"
                )
            if headers.get("connection") != "Upgrade":
                raise WebReplHandshakeError(
                    f"Invalid connection header: {headers.get('connection')}"
                )
            server_key = b64decode(headers.get("sec-websocket-accept", ""))
            if server_key != exp_server_key:
                raise WebReplHandshakeError("Incorrect sec-websocket-accept key.")
        finally:
            self._sock.settimeout(old_timeout)

    def _recv_exact(self, length: int) -> bytes:
        """
        Receive exactly the specified number of bytes from the raw socket
        (blocking indefinately).
        """
        buf = b""
        while length:
            if data := self._sock.recv(length):
                buf += data
                length -= len(data)
            else:
                raise EOFError(buf)

        return buf

    def _recv_frame(self) -> _WebsocketFrame:
        """
        Receive and decode a complete websocket frame, blocking indefinately.
        """
        fin_opcode = self._recv_exact(1)
        fin = bool(fin_opcode[0] & 0x80)
        if fin_opcode[0] & 0x70 != 0:
            raise WebReplFrameReservedBitsError(fin_opcode[0])
        opcode = _WebsocketOpcode(fin_opcode[0] & 0x0F)

        mask_length = self._recv_exact(1)
        mask = bool(mask_length[0] & 0x80)
        length = mask_length[0] & 0x7F

        if length == 126:
            (length,) = struct.unpack("!H", self._recv_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._recv_exact(8))

        if mask:
            masking_key = self._recv_exact(4)
        else:
            masking_key = b"\x00"

        payload = self._recv_exact(length)
        payload = bytes(byte ^ mask for byte, mask in zip(payload, cycle(masking_key)))

        return _WebsocketFrame(fin=fin, opcode=opcode, payload=payload)

    def _send_frame(self, frame: _WebsocketFrame, use_mask: bool = True) -> None:
        """Encode and send a websocket frame."""
        length = len(frame.payload)

        out = bytes([(frame.fin << 7) | frame.opcode])

        mask_bit = use_mask << 7
        if length < 126:
            out += struct.pack("!B", mask_bit | length)
        elif length < 1 << 16:
            out += struct.pack("!BH", mask_bit | 126, length)
        else:
            out += struct.pack("!BQ", mask_bit | 127, length)

        if use_mask:
            masking_key = os.urandom(4)
            out += masking_key
        else:
            masking_key = b"\0"

        out += bytes(
            byte ^ mask for byte, mask in zip(frame.payload, cycle(masking_key))
        )

        self._sock.sendall(out)

    def _recv_to_pipe(self) -> tuple[int, Thread]:
        """
        Setup a pipe through which the bytes received inside websocket text
        messages are sent.

        Returns
        =======
        read_fd : int
            The file descriptor of the read end of the pipe. Read the bytes
            carried by WebSocket text messages through this pipe.
        thread : Thread
            The background thread which receives and decodes websocket messages
            (handling ping and close messages automatically).
        """
        read_fd, write_fd = os.pipe()

        def run() -> None:
            try:
                while True:
                    frame = self._recv_frame()
                    match frame.opcode:
                        case _WebsocketOpcode.text:
                            os.write(write_fd, frame.payload)
                        case _WebsocketOpcode.close:
                            with self._send_lock:
                                self._send_frame(
                                    _WebsocketFrame(
                                        fin=True,
                                        opcode=_WebsocketOpcode.close,
                                        payload=b"",
                                    )
                                )
                                self._sock.close()
                            break
                        case _WebsocketOpcode.ping:
                            with self._send_lock:
                                self._send_frame(
                                    _WebsocketFrame(
                                        fin=True,
                                        opcode=_WebsocketOpcode.pong,
                                        payload=frame.payload,
                                    )
                                )
                            break
                        case _:
                            raise NotImplementedError(frame.opcode)
            except (OSError, EOFError):
                pass

        thread = Thread(target=run, daemon=True)
        thread.start()

        return read_fd, thread

    def read(self, num_bytes: int) -> bytes:
        end_time = time.monotonic() + self._timeout
        with DefaultSelector() as sel:
            sel.register(self._recv_pipe, EVENT_READ)

            out = b""
            while (
                len(out) < num_bytes
                and (now := time.monotonic()) < end_time
                and sel.select(end_time - now)
                and (data := os.read(self._recv_pipe, 1))
            ):
                out += data

        return out

    def read_until(self, ending: bytes) -> bytes:
        end_time = time.monotonic() + self._timeout
        with DefaultSelector() as sel:
            sel.register(self._recv_pipe, EVENT_READ)

            out = b""
            while (
                not out.endswith(ending)
                and (now := time.monotonic()) < end_time
                and sel.select(end_time - now)
                and (data := os.read(self._recv_pipe, 1))
            ):
                out += data

        return out

    def read_buffered(self) -> bytes:
        with DefaultSelector() as sel:
            sel.register(self._recv_pipe, EVENT_READ)

            out = b""
            while sel.select(0) and (data := os.read(self._recv_pipe, 1)):
                out += data

        return out

    def write(self, data: bytes) -> int:
        with self._send_lock:
            self._send_frame(
                _WebsocketFrame(
                    fin=True,
                    opcode=_WebsocketOpcode.text,  # Yes, text...
                    payload=data,  # Yes I know this is not UTF-8!
                )
            )
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self._sock.close()

    def fileno(self) -> int:
        return self._recv_pipe

    @property
    def timeout(self) -> float:
        return self._timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        self._timeout = value
