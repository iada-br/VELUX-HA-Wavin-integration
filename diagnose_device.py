"""
Diagnostic script for Wavin device discovery and communication troubleshooting
"""
from wavin_modbus_client import WavinModbusClient, ModbusRTU
import serial
import time


def test_port_availability(port: str):
    """Test if a serial port can be opened."""
    try:
        ser = serial.Serial(port, timeout=1)
        ser.close()
        return True
    except Exception as e:
        print(f"   {port}: {type(e).__name__} - {e}")
        return False


def test_modbus_communication(port: str, slave_address: int = 0x01):
    """Test MODBUS RTU communication with different settings."""
    print(f"\n[TEST] MODBUS RTU communication on {port}")
    print(f"       Slave Address: 0x{slave_address:02X}")
    print(f"       Parameters: 38400 bps, 8N1\n")

    client = WavinModbusClient(port, slave_address=slave_address, timeout=1.0)

    if not client.connect():
        print("   [FAIL] Cannot connect to serial port")
        return False

    try:
        # Try to read status register
        print("   Attempting to read Status register (0x08)...")
        result = client.read_register_by_index(0x00, 0, 0x08, quantity=1)

        if result:
            print(f"   [OK] Device responded!")
            print(f"       Status value: 0x{result[0]:04X}")
            return True
        else:
            print(f"   [TIMEOUT] No response from device (CRC validation may have failed)")
            return False

    except Exception as e:
        print(f"   [ERROR] {type(e).__name__}: {e}")
        return False

    finally:
        client.disconnect()


def test_raw_serial_communication(port: str):
    """Test raw serial communication to verify port works at all."""
    print(f"\n[TEST] Raw serial port test on {port}")

    try:
        ser = serial.Serial(port, baudrate=38400, timeout=1)
        print(f"   [OK] Port opened successfully")
        print(f"       Sending MODBUS READ request (slave 0x01, function 0x43)...")

        # Construct a simple MODBUS read request
        request = bytes([0x01, 0x43, 0x00, 0x08, 0x00, 0x01])
        frame = request + ModbusRTU.calculate_crc16(request)

        print(f"       Request frame (hex): {frame.hex()}")
        print(f"       Sending {len(frame)} bytes...")

        ser.write(frame)
        time.sleep(0.5)

        # Try to read response
        response = ser.read(256)
        if response:
            print(f"   [OK] Received {len(response)} bytes")
            print(f"       Response (hex): {response.hex()}")

            # Validate CRC
            if ModbusRTU.validate_crc(response):
                print(f"       CRC: [VALID]")
            else:
                print(f"       CRC: [INVALID]")
            return True
        else:
            print(f"   [TIMEOUT] No response received")
            return False

    except Exception as e:
        print(f"   [ERROR] {type(e).__name__}: {e}")
        return False

    finally:
        try:
            ser.close()
        except:
            pass


def main():
    print("=" * 70)
    print("Wavin Device Communication Diagnostic")
    print("=" * 70)

    # Step 1: Test port availability
    print("\n[STEP 1] Testing serial port availability...")
    ports = ['COM3', 'COM4', 'COM1', 'COM2']
    available_ports = []

    for port in ports:
        try:
            ser = serial.Serial(port, timeout=0.1)
            available_ports.append(port)
            ser.close()
            print(f"   [OK] {port}: Available")
        except Exception as e:
            print(f"   [SKIP] {port}: {type(e).__name__}")

    if not available_ports:
        print("\n[ERROR] No serial ports available!")
        return

    # Step 2: Raw serial tests
    print("\n[STEP 2] Testing raw serial communication...")
    working_ports = []
    for port in available_ports:
        if test_raw_serial_communication(port):
            working_ports.append(port)

    if not working_ports:
        print("\n[INFO] No ports responded to MODBUS. Checking with different slave addresses...")

    # Step 3: Test MODBUS with different configurations
    print("\n[STEP 3] Testing MODBUS RTU communication...")
    for port in available_ports:
        for slave_addr in [0x01, 0x02, 0x03, 0xFF]:
            if test_modbus_communication(port, slave_addr):
                print(f"\n[SUCCESS] Found device at {port} (Slave: 0x{slave_addr:02X})")
                return

    print("\n" + "=" * 70)
    print("DIAGNOSIS SUMMARY:")
    print("=" * 70)
    print(f"Available ports: {available_ports}")
    print(f"Ports with responses: {working_ports}")
    print("\nPossible issues:")
    print("1. Device not powered on or not connected to serial port")
    print("2. Serial gateway (USR-TCP232-306) not configured correctly")
    print("3. Slave address doesn't match device configuration")
    print("4. Device may require different baud rate or serial settings")
    print("5. Serial port permissions issue (another application using the port)")


if __name__ == "__main__":
    main()
