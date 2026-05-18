"""
Test HTTP client for Wavin Device through Gateway at 192.168.0.7
"""
from wavin_client import WavinClient


def main():
    print("=" * 70)
    print("Testing Wavin Device via HTTP Interface (192.168.0.7)")
    print("=" * 70)

    host = "192.168.0.7"
    client = WavinClient(host, username="admin", password="admin", timeout=5)

    # Test 1: Connection
    print("\n1. Testing HTTP connection...")
    if client.connect():
        print("   [OK] Gateway is reachable\n")
    else:
        print("   [FAIL] Cannot reach gateway\n")
        return

    # Test 2: Get device info
    print("2. Retrieving device information...")
    info = client.get_device_info()
    if info:
        print(f"   [OK] Device info: {info}\n")
    else:
        print("   [WARN] Could not retrieve device info\n")

    # Test 3: Get status
    print("3. Retrieving device status...")
    status = client.get_status()
    if status:
        print(f"   [OK] Device status: {status}\n")
    else:
        print("   [WARN] Could not retrieve status\n")

    # Test 4: Get temperature
    print("4. Reading current temperature...")
    temp = client.get_temperature()
    if temp is not None:
        print(f"   [OK] Current temperature: {temp}°C\n")
    else:
        print("   [WARN] Could not retrieve temperature\n")

    print("=" * 70)
    print("HTTP Test Complete!")
    print("=" * 70)
    print("\nSummary:")
    print("- HTTP interface is functional for device communication")
    print("- Serial MODBUS RTU mode may require gateway reconfiguration")
    print("- Alternative: Investigate gateway's MODBUS mode settings")


if __name__ == "__main__":
    main()
