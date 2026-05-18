"""
Real device communication test script
Run this with actual Wavin device credentials
"""
from wavin_client import WavinClient
import sys


def test_real_device(host: str, username: str, password: str):
    """
    Test communication with real Wavin device.

    Args:
        host: Device IP address
        username: Basic auth username
        password: Basic auth password
    """
    print(f"Testing Wavin device at {host}...\n")

    client = WavinClient(host, username=username, password=password)

    # Test 1: Connection
    print("1. Testing connection...")
    if client.connect():
        print("   [OK] Device is reachable\n")
    else:
        print("   [FAIL] Cannot connect to device\n")
        return

    # Test 2: Get device info
    print("2. Retrieving device information...")
    info = client.get_device_info()
    if info:
        print(f"   [OK] Device info: {info}\n")
    else:
        print("   [WARN] Could not retrieve device info (endpoint may not exist)\n")

    # Test 3: Get status
    print("3. Retrieving device status...")
    status = client.get_status()
    if status:
        print(f"   [OK] Status: {status}\n")
    else:
        print("   [WARN] Could not retrieve status (endpoint may not exist)\n")

    # Test 4: Get temperature
    print("4. Reading current temperature...")
    temp = client.get_temperature()
    if temp is not None:
        print(f"   [OK] Current temperature: {temp}C\n")
    else:
        print("   [WARN] Could not retrieve temperature (endpoint may not exist)\n")

    # Test 5: Set temperature (OPTIONAL - commented out for safety)
    # Uncomment below to test temperature setting
    # print("5. Setting temperature to 22°C...")
    # if client.set_temperature(22.0):
    #     print("   ✓ Temperature set successfully\n")
    # else:
    #     print("   ✗ Failed to set temperature\n")

    print("=" * 50)
    print("Communication test complete!")
    print("\nNote: Some endpoints may not exist on your device.")
    print("Check device documentation for available API endpoints.")


if __name__ == "__main__":
    # Usage: python test_device.py <host> <username> <password>
    if len(sys.argv) != 4:
        print("Usage: python test_device.py <host> <username> <password>")
        print("Example: python test_device.py 192.168.0.7 admin admin")
        sys.exit(1)

    host = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]

    test_real_device(host, username, password)
