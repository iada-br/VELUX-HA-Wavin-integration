"""
Wavin Device Communication Client
HTTP-based communication with Basic Authentication
"""
import requests
from requests.auth import HTTPBasicAuth
from typing import Optional, Dict, Any
import json


class WavinClient:
    """Client for communicating with Wavin smart heating devices."""

    def __init__(self, host: str, username: str = "admin", password: str = "admin", timeout: int = 5):
        """
        Initialize Wavin client.

        Args:
            host: IP address or hostname of the Wavin device (e.g., "192.168.0.7")
            username: Basic auth username
            password: Basic auth password
            timeout: Request timeout in seconds
        """
        self.host = host
        self.base_url = f"http://{host}"
        self.auth = HTTPBasicAuth(username, password)
        self.timeout = timeout

    def connect(self) -> bool:
        """
        Test connection to the Wavin device.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            response = requests.get(
                self.base_url,
                auth=self.auth,
                timeout=self.timeout,
                allow_redirects=False
            )
            return response.status_code in (200, 401, 403, 404)
        except requests.exceptions.RequestException:
            return False

    def get_status(self) -> Optional[Dict[str, Any]]:
        """
        Get device status.

        Returns:
            Dictionary with device status or None if request failed
        """
        try:
            response = requests.get(
                f"{self.base_url}/status",
                auth=self.auth,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json() if response.headers.get('content-type') == 'application/json' else response.text
        except requests.exceptions.RequestException as e:
            print(f"Error getting status: {e}")
            return None

    def get_temperature(self) -> Optional[float]:
        """
        Get current temperature reading.

        Returns:
            Temperature value or None if unavailable
        """
        try:
            response = requests.get(
                f"{self.base_url}/temperature",
                auth=self.auth,
                timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json() if response.headers.get('content-type') == 'application/json' else response.text
            return float(data) if isinstance(data, (int, float, str)) else data.get('temperature')
        except (requests.exceptions.RequestException, ValueError, TypeError):
            return None

    def set_temperature(self, target_temp: float) -> bool:
        """
        Set target temperature.

        Args:
            target_temp: Target temperature in Celsius

        Returns:
            True if successful, False otherwise
        """
        try:
            response = requests.post(
                f"{self.base_url}/temperature",
                auth=self.auth,
                json={"temperature": target_temp},
                timeout=self.timeout
            )
            return response.status_code in (200, 201, 204)
        except requests.exceptions.RequestException:
            return False

    def get_device_info(self) -> Optional[Dict[str, Any]]:
        """
        Get device information (model, version, etc.).

        Returns:
            Dictionary with device info or None if unavailable
        """
        try:
            response = requests.get(
                f"{self.base_url}/info",
                auth=self.auth,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json() if response.headers.get('content-type') == 'application/json' else response.text
        except requests.exceptions.RequestException:
            return None
