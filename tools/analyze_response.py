"""
Analyze raw response from device to understand its protocol
"""
from wavin_modbus_client import ModbusRTU
import time
import serial


def analyze_response(data: bytes):
    """Analyze raw response data."""
    print("\n" + "=" * 70)
    print("RESPONSE ANALYSIS")
    print("=" * 70)
    print(f"Total bytes: {len(data)}")
    print(f"Hex: {data.hex()}")
    print(f"\nByte breakdown:")

    # Look for MODBUS-like patterns
    print(f"\nFirst 10 bytes (hex): {' '.join(f'{b:02X}' for b in data[:10])}")
    print(f"First 10 bytes (dec): {' '.join(f'{b:3d}' for b in data[:10])}")

    # Check for printable ASCII
    print(f"\nPrintable ASCII content:")
    ascii_str = ""
    for b in data:
        if 32 <= b <= 126:
            ascii_str += chr(b)
        else:
            ascii_str += f"[{b:02X}]"
    print(f"{ascii_str}")

    # Look for MODBUS frame patterns
    print(f"\nSearching for MODBUS patterns:")
    for i in range(len(data) - 6):
        # MODBUS frame: [slave(1), func_code(1), ...]
        slave = data[i]
        func_code = data[i + 1]

        # Valid MODBUS slave addresses: 1-247, common: 1, 247
        # Valid MODBUS function codes: 1-127
        if 1 <= slave <= 247 and 1 <= func_code <= 127:
            print(f"  Position {i:3d}: Slave=0x{slave:02X}, FC=0x{func_code:02X} "
                  f"(possible MODBUS frame start)")

    # Try to extract ASCII strings
    print(f"\nASCII strings (>4 chars):")
    current = ""
    for b in data:
        if 32 <= b <= 126:
            current += chr(b)
        else:
            if len(current) > 4:
                print(f"  '{current}'")
            current = ""
    if len(current) > 4:
        print(f"  '{current}'")


# Test with actual device data
if __name__ == "__main__":
    print("Connecting to COM4 to capture raw response...")

    try:
        ser = serial.Serial('COM4', baudrate=38400, timeout=2)
        print("[OK] Port opened")

        # Send MODBUS read request
        request = bytes([0x01, 0x43, 0x00, 0x08, 0x00, 0x01])
        crc = ModbusRTU.calculate_crc16(request)
        frame = request + crc

        print(f"[SEND] {frame.hex()} ({len(frame)} bytes)")
        ser.write(frame)

        time.sleep(1)

        # Read response
        response = ser.read(1024)
        print(f"[RECV] {len(response)} bytes")

        analyze_response(response)

        ser.close()

    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
