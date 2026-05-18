"""
Unit tests for Wavin Device Communication Client
"""
import pytest
from unittest.mock import patch, MagicMock
from requests.auth import HTTPBasicAuth
from requests.exceptions import RequestException, Timeout, ConnectionError
import json

from wavin_client import WavinClient


class TestWavinClientInitialization:
    """Test client initialization."""

    def test_client_initialization_with_defaults(self):
        """Test client initializes with default credentials."""
        client = WavinClient("192.168.0.7")
        assert client.host == "192.168.0.7"
        assert client.base_url == "http://192.168.0.7"
        assert client.timeout == 5

    def test_client_initialization_with_custom_credentials(self):
        """Test client initializes with custom credentials."""
        client = WavinClient("192.168.0.7", username="test_user", password="test_pass")
        assert client.auth.username == "test_user"
        assert client.auth.password == "test_pass"

    def test_client_initialization_with_custom_timeout(self):
        """Test client initializes with custom timeout."""
        client = WavinClient("192.168.0.7", timeout=10)
        assert client.timeout == 10


class TestWavinClientConnect:
    """Test connection functionality."""

    @patch('wavin_client.requests.get')
    def test_connect_successful(self, mock_get):
        """Test successful connection."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        client = WavinClient("192.168.0.7")
        assert client.connect() is True
        mock_get.assert_called_once()

    @patch('wavin_client.requests.get')
    def test_connect_with_401_unauthorized(self, mock_get):
        """Test connection with 401 status (expected from device)."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        client = WavinClient("192.168.0.7")
        assert client.connect() is True

    @patch('wavin_client.requests.get')
    def test_connect_timeout(self, mock_get):
        """Test connection timeout."""
        mock_get.side_effect = Timeout("Connection timed out")

        client = WavinClient("192.168.0.7")
        assert client.connect() is False

    @patch('wavin_client.requests.get')
    def test_connect_connection_error(self, mock_get):
        """Test connection refused."""
        mock_get.side_effect = ConnectionError("Connection refused")

        client = WavinClient("192.168.0.7")
        assert client.connect() is False

    @patch('wavin_client.requests.get')
    def test_connect_uses_correct_auth(self, mock_get):
        """Test connection uses provided credentials."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        client = WavinClient("192.168.0.7", username="admin", password="12345")
        client.connect()

        call_args = mock_get.call_args
        assert call_args[1]['auth'].username == "admin"
        assert call_args[1]['auth'].password == "12345"


class TestWavinClientGetStatus:
    """Test status retrieval."""

    @patch('wavin_client.requests.get')
    def test_get_status_json_response(self, mock_get):
        """Test getting status with JSON response."""
        expected_status = {"online": True, "mode": "heating"}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = expected_status
        mock_get.return_value = mock_response

        client = WavinClient("192.168.0.7")
        status = client.get_status()

        assert status == expected_status
        mock_get.assert_called_once_with(
            "http://192.168.0.7/status",
            auth=client.auth,
            timeout=5
        )

    @patch('wavin_client.requests.get')
    def test_get_status_text_response(self, mock_get):
        """Test getting status with text response."""
        expected_text = "OK"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'text/plain'}
        mock_response.text = expected_text
        mock_get.return_value = mock_response

        client = WavinClient("192.168.0.7")
        status = client.get_status()

        assert status == expected_text

    @patch('wavin_client.requests.get')
    def test_get_status_request_exception(self, mock_get):
        """Test status retrieval with request exception."""
        mock_get.side_effect = RequestException("Network error")

        client = WavinClient("192.168.0.7")
        status = client.get_status()

        assert status is None

    @patch('wavin_client.requests.get')
    def test_get_status_http_error(self, mock_get):
        """Test status retrieval with HTTP error."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = RequestException("401 Unauthorized")
        mock_get.return_value = mock_response

        client = WavinClient("192.168.0.7")
        status = client.get_status()

        assert status is None


class TestWavinClientGetTemperature:
    """Test temperature retrieval."""

    @patch('wavin_client.requests.get')
    def test_get_temperature_float(self, mock_get):
        """Test getting temperature as float."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = 22.5
        mock_get.return_value = mock_response

        client = WavinClient("192.168.0.7")
        temp = client.get_temperature()

        assert temp == 22.5

    @patch('wavin_client.requests.get')
    def test_get_temperature_from_dict(self, mock_get):
        """Test getting temperature from JSON object."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = {"temperature": 21.0, "unit": "celsius"}
        mock_get.return_value = mock_response

        client = WavinClient("192.168.0.7")
        temp = client.get_temperature()

        assert temp == 21.0

    @patch('wavin_client.requests.get')
    def test_get_temperature_from_string(self, mock_get):
        """Test getting temperature from string response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'text/plain'}
        mock_response.text = "23.5"
        mock_get.return_value = mock_response

        client = WavinClient("192.168.0.7")
        temp = client.get_temperature()

        assert temp == 23.5

    @patch('wavin_client.requests.get')
    def test_get_temperature_invalid_response(self, mock_get):
        """Test getting temperature with invalid response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = {"status": "ok"}
        mock_get.return_value = mock_response

        client = WavinClient("192.168.0.7")
        temp = client.get_temperature()

        assert temp is None

    @patch('wavin_client.requests.get')
    def test_get_temperature_request_exception(self, mock_get):
        """Test temperature retrieval with request exception."""
        mock_get.side_effect = RequestException("Network error")

        client = WavinClient("192.168.0.7")
        temp = client.get_temperature()

        assert temp is None


class TestWavinClientSetTemperature:
    """Test temperature setting."""

    @patch('wavin_client.requests.post')
    def test_set_temperature_success(self, mock_post):
        """Test successfully setting temperature."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        client = WavinClient("192.168.0.7")
        result = client.set_temperature(25.0)

        assert result is True
        mock_post.assert_called_once_with(
            "http://192.168.0.7/temperature",
            auth=client.auth,
            json={"temperature": 25.0},
            timeout=5
        )

    @patch('wavin_client.requests.post')
    def test_set_temperature_created(self, mock_post):
        """Test setting temperature with 201 Created response."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_post.return_value = mock_response

        client = WavinClient("192.168.0.7")
        result = client.set_temperature(22.0)

        assert result is True

    @patch('wavin_client.requests.post')
    def test_set_temperature_no_content(self, mock_post):
        """Test setting temperature with 204 No Content response."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        client = WavinClient("192.168.0.7")
        result = client.set_temperature(20.0)

        assert result is True

    @patch('wavin_client.requests.post')
    def test_set_temperature_failure(self, mock_post):
        """Test setting temperature with error response."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_post.return_value = mock_response

        client = WavinClient("192.168.0.7")
        result = client.set_temperature(25.0)

        assert result is False

    @patch('wavin_client.requests.post')
    def test_set_temperature_request_exception(self, mock_post):
        """Test setting temperature with request exception."""
        mock_post.side_effect = RequestException("Network error")

        client = WavinClient("192.168.0.7")
        result = client.set_temperature(25.0)

        assert result is False

    @patch('wavin_client.requests.post')
    def test_set_temperature_value_validation(self, mock_post):
        """Test setting various temperature values."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        client = WavinClient("192.168.0.7")

        # Test with integer
        result = client.set_temperature(20)
        assert result is True

        # Test with negative
        result = client.set_temperature(-5)
        assert result is True

        # Test with large decimal
        result = client.set_temperature(99.99)
        assert result is True


class TestWavinClientGetDeviceInfo:
    """Test device info retrieval."""

    @patch('wavin_client.requests.get')
    def test_get_device_info_json(self, mock_get):
        """Test getting device info as JSON."""
        expected_info = {
            "model": "Wavin-AHC9000",
            "version": "1.2.3",
            "serial": "ABC123456"
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = expected_info
        mock_get.return_value = mock_response

        client = WavinClient("192.168.0.7")
        info = client.get_device_info()

        assert info == expected_info
        mock_get.assert_called_once_with(
            "http://192.168.0.7/info",
            auth=client.auth,
            timeout=5
        )

    @patch('wavin_client.requests.get')
    def test_get_device_info_request_exception(self, mock_get):
        """Test device info retrieval with request exception."""
        mock_get.side_effect = RequestException("Network error")

        client = WavinClient("192.168.0.7")
        info = client.get_device_info()

        assert info is None


class TestWavinClientIntegration:
    """Integration-style tests."""

    @patch('wavin_client.requests.get')
    @patch('wavin_client.requests.post')
    def test_full_communication_flow(self, mock_post, mock_get):
        """Test a full communication flow: connect -> get status -> set temperature."""
        # Setup mocks
        connect_response = MagicMock()
        connect_response.status_code = 200

        status_response = MagicMock()
        status_response.status_code = 200
        status_response.headers = {'content-type': 'application/json'}
        status_response.json.return_value = {"online": True}

        temp_set_response = MagicMock()
        temp_set_response.status_code = 200

        mock_get.side_effect = [connect_response, status_response]
        mock_post.return_value = temp_set_response

        client = WavinClient("192.168.0.7")

        # Connect
        assert client.connect() is True

        # Get status
        status = client.get_status()
        assert status == {"online": True}

        # Set temperature
        assert client.set_temperature(22.0) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
