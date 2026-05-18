"""
Configuration utility for USR-TCP232-306 gateway
Attempts to identify and reconfigure the serial gateway
"""
import serial
import time


def send_command(port: str, command: bytes, label: str):
    """Send command and capture response."""
    print(f"\n[CMD] {label}")
    print(f"      TX: {command.hex()}")

    try:
        ser = serial.Serial(port, baudrate=38400, timeout=2)
        ser.write(command)
        time.sleep(0.5)

        response = ser.read(512)
        if response:
            print(f"      RX: {response[:50].hex()}... ({len(response)} bytes)")
            # Try to decode ASCII portion
            ascii_part = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in response)
            print(f"      ASCII: {ascii_part[:60]}...")
        else:
            print(f"      RX: [TIMEOUT - no response]")

        ser.close()
        return response

    except Exception as e:
        print(f"      [ERROR] {e}")
        return None


def main():
    print("=" * 70)
    print("USR-TCP232-306 Gateway Configuration Utility")
    print("=" * 70)

    port = "COM4"

    print(f"\nTesting on {port}...")
    print("\nAttempting various gateway commands...\n")

    # Try different command formats
    commands = [
        # MODBUS standard read
        (bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x0A, 0x44, 0x09]), "MODBUS FC03 (read holding regs)"),

        # AT commands (some gateways use these)
        (b"AT\r\n", "AT command (modem mode)"),
        (b"+++", "Escape sequence"),

        # Info/status requests
        (b"INFO\r\n", "INFO request"),
        (b"STATUS\r\n", "STATUS request"),
        (b"VERSION\r\n", "VERSION request"),

        # Gateway-specific
        (b"\x1B\x01INFO\r", "Escape + INFO"),
        (bytes([0x55, 0xAA, 0x01, 0x00]), "Possible gateway header"),
    ]

    for cmd, label in commands:
        send_command(port, cmd, label)
        time.sleep(1)

    print("\n" + "=" * 70)
    print("RECOMMENDATIONS:")
    print("=" * 70)
    print("""
1. Check USR-TCP232-306 configuration:
   - Access web interface at 192.168.0.7
   - Verify serial port settings (baud rate, parity, stop bits)
   - Check if MODBUS RTU mode is enabled

2. Possible solutions:
   - Device may need firmware update for MODBUS RTU support
   - Serial gateway may need reconfiguration via HTTP interface
   - Actual Wavin device may require direct connection (not through gateway)

3. Alternative approach:
   - Use HTTP interface of the gateway (already working)
   - Gateway may have web API for controlling Wavin device

4. Verify connection:
   - Check RS-485 wiring between gateway and Wavin device
   - Verify slave address in Wavin device settings
   - Ensure termination resistors on RS-485 bus
""")


if __name__ == "__main__":
    main()
