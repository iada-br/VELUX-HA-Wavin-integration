"""
Real device communication test script for Wavin AHC9000 via MODBUS RTU
Tests actual serial communication through COM port
"""
from wavin_modbus_client import WavinModbusClient
import sys
import time


def test_real_device(port: str):
    """
    Test communication with real Wavin device via MODBUS RTU.

    Args:
        port: Serial port (e.g., "COM3", "COM4")
    """
    print(f"Testing Wavin MODBUS RTU device on {port}...\n")

    client = WavinModbusClient(port, slave_address=0x01, timeout=2.0)

    # Test 1: Connection
    print("1. Testing serial connection...")
    if client.connect():
        print("   [OK] Serial port opened successfully\n")
    else:
        print("   [FAIL] Cannot open serial port\n")
        return

    try:
        # Test 2: Get device status
        print("2. Reading device status...")
        status = client.get_status()
        if status:
            print(f"   [OK] Device status retrieved:")
            for key, value in status.items():
                print(f"       - {key}: {value}")
            print()
        else:
            print("   [WARN] Could not retrieve status (device may not respond)\n")

        # Test 3: Get DHW sensor temperature
        print("3. Reading DHW sensor temperature...")
        temp_dhw = client.get_dhw_sensor_temperature()
        if temp_dhw is not None:
            print(f"   [OK] DHW sensor temperature: {temp_dhw}°C\n")
        else:
            print("   [WARN] Could not read DHW temperature\n")

        # Test 4: Get inlet sensor temperature
        print("4. Reading inlet sensor temperature...")
        temp_inlet = client.get_inlet_sensor_temperature()
        if temp_inlet is not None:
            print(f"   [OK] Inlet sensor temperature: {temp_inlet}°C\n")
        else:
            print("   [WARN] Could not read inlet temperature\n")

        # Test 5: Get DHW comfort temperature
        print("5. Reading DHW comfort temperature setting...")
        temp_comfort = client.get_dhw_comfort_temperature()
        if temp_comfort is not None:
            print(f"   [OK] DHW comfort temperature: {temp_comfort}°C\n")
        else:
            print("   [WARN] Could not read comfort temperature\n")

        # Test 6: Get device info
        print("6. Retrieving device information...")
        info = client.get_device_info()
        if info:
            print(f"   [OK] Device info retrieved:")
            for key, value in info.items():
                print(f"       - {key}: {value}")
            print()
        else:
            print("   [WARN] Could not retrieve device info\n")

        # Test 7: CRC validation test (read multiple registers)
        print("7. Testing CRC validation (reading 3 registers)...")
        result = client.read_register_by_index(0x00, 0, 0x0E, quantity=3)
        if result:
            print(f"   [OK] Successfully read {len(result)} registers with valid CRC:")
            for i, val in enumerate(result):
                print(f"       - Register {i}: 0x{val:04X}")
            print()
        else:
            print("   [WARN] Could not read multiple registers (CRC validation may have failed)\n")

        print("=" * 60)
        print("Communication test complete!")
        print("\nNotes:")
        print("- All operations use MODBUS RTU protocol over serial")
        print("- CRC-16-CCITT validation is performed on all responses")
        print("- If device doesn't respond, check:")
        print("  * Serial port is correct (COM3 or COM4)")
        print("  * Slave address matches device configuration (default 0x01)")
        print("  * Serial gateway/device is properly connected")
        print("  * Baud rate is 38400 bps")

    finally:
        # Always close connection
        print("\n" + "=" * 60)
        print("Closing serial connection...")
        client.disconnect()
        print("[OK] Connection closed\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        # Default to COM3
        port = "COM3"

    print(f"Using port: {port}\n")
    test_real_device(port)
