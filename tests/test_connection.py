import pytest
from unittest.mock import Mock

from typing import Any

from upyt.connection import Connection


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
