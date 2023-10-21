import pytest
from unittest.mock import Mock

from typing import Any, Callable, Iterable

import time

from threading import Thread
from queue import Queue
from functools import partial
from concurrent.futures import ThreadPoolExecutor

import websockets.sync.server as ws_server

from upyt.connection import Connection, WebReplAuthenticationError


class TestConnectionFromSpecification:
    @pytest.fixture
    def mock_serial_connection(self, monkeypatch: Any) -> Mock:
        # Mock out SerialConnection
        import upyt.connection

        mock = Mock()
        monkeypatch.setattr(upyt.connection, "SerialConnection", mock)

        mock.return_value = mock

        return mock

    @pytest.mark.parametrize("name", ["COM2", "/dev/ttyACM3"])
    def test_serial_default_baudrate(
        self, name: str, mock_serial_connection: Mock
    ) -> None:
        out = Connection.from_specification(name)

        assert out is mock_serial_connection
        mock_serial_connection.assert_called_once_with(name, 9600)

    @pytest.mark.parametrize(
        "name", ["COM2", "/dev/ttyACM3", "/dev/ttyACM:with:colons:in"]
    )
    def test_serial_custom_baudrate(
        self, name: str, mock_serial_connection: Mock
    ) -> None:
        out = Connection.from_specification(f"{name}:115200")

        assert out is mock_serial_connection
        mock_serial_connection.assert_called_once_with(name, 115200)

    @pytest.fixture
    def mock_web_repl_connection(self, monkeypatch: Any) -> Mock:
        # Mock out WebReplConnection
        import upyt.connection

        mock = Mock()
        monkeypatch.setattr(upyt.connection, "WebReplConnection", mock)

        mock.return_value = mock

        return mock

    def test_web_repl_valid(self, mock_web_repl_connection: Mock) -> None:
        out = Connection.from_specification(f"ws://foo.bar/baz?pw1234")
        mock_web_repl_connection.assert_called_once_with("ws://foo.bar/baz", "pw1234")

    def test_web_repl_no_password(self, mock_web_repl_connection: Mock) -> None:
        with pytest.raises(ValueError):
            Connection.from_specification(f"ws://foo.bar/baz")


# A (rx_fn, tx_fn, ws_url) tuple
ServerFixture = tuple[Callable[[], str], Callable[[str], None], str]

# A (rx_fn, tx_fn, Connection) tuple
AuthedServerFixture = tuple[Callable[[], str], Callable[[str], None], Connection]


class TestWebReplConnection:
    @pytest.fixture
    def server(self) -> Iterable[ServerFixture]:
        """
        Create a local websocket server on a random port.

        Fixture returns a (recv_message, send_message, url) tuple.
        """
        rx_queue: Queue[str] = Queue()
        tx_queue: Queue[str] = Queue()
        connections: list[ws_server.ServerConnection] = []

        def rx_loop(connection: ws_server.ServerConnection):
            for message in connection:
                assert isinstance(message, str)
                rx_queue.put(message)

        def tx_loop(connection: ws_server.ServerConnection):
            while message := tx_queue.get():
                connection.send(message)

        def on_connect(connection: ws_server.ServerConnection) -> None:
            # NB: Only one connection supported
            if connections:
                connection.close()
                print("Error: Tried to open multiple connections")
            connections.append(connection)

            tx_thread = Thread(
                target=tx_loop, args=(connection,), daemon=True, name="tx"
            )
            tx_thread.start()

            rx_loop(connection)

            tx_thread.join()

        server = ws_server.serve(on_connect, host="127.0.0.1", port=0)
        assigned_port = server.socket.getsockname()[1]

        server_thread = Thread(target=server.serve_forever, name="server")
        try:
            server_thread.start()
            yield (
                partial(rx_queue.get, timeout=1),
                tx_queue.put,
                f"ws://127.0.0.1:{assigned_port}/",
            )
        finally:
            server.shutdown()
            tx_queue.put("")  # Terminate the TX thread
            for connection in connections:
                connection.close()
            server_thread.join()

    def test_authentication(self, server: ServerFixture) -> None:
        rx, tx, url = server

        fut = ThreadPoolExecutor().submit(Connection.from_specification, f"{url}?pw123")

        tx("Password: ")
        assert rx() == "pw123\n"
        tx("\r\nWebREPL connected\r\n>>> ")

        conn = fut.result(1)

        # Check the connection then becomes a bidirectional channel
        conn.write(b"Hello!")
        assert rx() == "Hello!"
        tx("World!")
        assert conn.read_until(b"!") == b"World!"

    def test_password_incorrect(self, server: ServerFixture) -> None:
        rx, tx, url = server

        fut = ThreadPoolExecutor().submit(Connection.from_specification, f"{url}?pw123")

        tx("Password: ")
        assert rx() == "pw123\n"
        tx("\r\nInvalid password\r\n")

        with pytest.raises(WebReplAuthenticationError):
            fut.result(1)

    @pytest.fixture
    def authed_server(self, server: ServerFixture) -> AuthedServerFixture:
        """
        Like the server fixture but returns a (rx, tx, conn) tuple which is
        ready-to-use.
        """
        rx, tx, url = server

        fut = ThreadPoolExecutor().submit(Connection.from_specification, f"{url}?pw123")

        tx("Password: ")
        assert rx() == "pw123\n"
        tx("\r\nWebREPL connected\r\n>>> ")

        conn = fut.result(1)

        return (rx, tx, conn)

    def test_read(self, authed_server: AuthedServerFixture) -> None:
        rx, tx, conn = authed_server

        tx("123")
        tx("456")

        # Can read subset of message
        assert conn.read(2) == b"12"

        # Can read accross message boundaries
        assert conn.read(2) == b"34"

        # Timeout works
        with conn.timeout_override(0.1):
            before = time.monotonic()
            assert conn.read(4) == b"56"
            after = time.monotonic()
            assert after - before >= conn.timeout

    def test_read_until(self, authed_server: AuthedServerFixture) -> None:
        rx, tx, conn = authed_server

        tx("123")
        tx("456")

        # Can read subset of message
        assert conn.read_until(b"2") == b"12"

        # Can read accross message boundaries
        assert conn.read_until(b"4") == b"34"

        # Timeout works
        with conn.timeout_override(0.1):
            before = time.monotonic()
            assert conn.read_until(b"7") == b"56"
            after = time.monotonic()
            assert after - before >= conn.timeout

    def test_read_buffered(self, authed_server: AuthedServerFixture) -> None:
        rx, tx, conn = authed_server

        tx("123")
        time.sleep(0.1)
        assert conn.read_buffered() == b"123"

    def test_write(self, authed_server: AuthedServerFixture) -> None:
        rx, tx, conn = authed_server

        # NB: We can't test writing non-UTF-8 bytestreams since the websocket
        # server we're using is actually conformant(!)
        conn.write(b"123")
        conn.write(b"456")

        assert rx() == "123"
        assert rx() == "456"
