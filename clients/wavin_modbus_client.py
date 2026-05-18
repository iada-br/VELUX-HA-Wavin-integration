"""
Wavin AHC9000 MODBUS RTU Client
Communication over Modbus RTU (RS-485) via serial gateway
"""
import struct
import serial
from typing import Optional, List, Dict, Tuple
import time


class ModbusRTU:
    """MODBUS RTU protocol implementation"""

    @staticmethod
    def calculate_crc16(data: bytes) -> bytes:
        """Calculate CRC16-CCITT checksum"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return struct.pack('<H', crc)

    @staticmethod
    def validate_crc(data: bytes) -> bool:
        """Validate CRC16 of received frame"""
        if len(data) < 3:
            return False
        message = data[:-2]
        received_crc = data[-2:]
        calculated_crc = ModbusRTU.calculate_crc16(message)
        return received_crc == calculated_crc

    @staticmethod
    def add_crc(data: bytes) -> bytes:
        """Add CRC16 to frame"""
        return data + ModbusRTU.calculate_crc16(data)


class WavinModbusClient:
    """Client for Wavin AHC9000 via MODBUS RTU"""

    # Register Categories
    CATEGORY_MAIN = 0x00
    CATEGORY_ELEMENTS = 0x01
    CATEGORY_PACKED_DATA = 0x02
    CATEGORY_CHANNELS = 0x03
    CATEGORY_RELAYS = 0x04
    CATEGORY_CLOCK = 0x05
    CATEGORY_SCHEDULES = 0x06
    CATEGORY_INFO = 0x07

    # Function Codes
    FC_READ_INDEX = 0x43
    FC_READ_ADDRESS = 0x41
    FC_WRITE_INDEX = 0x44
    FC_WRITE_ADDRESS = 0x42
    FC_WRITE_MASKED_INDEX = 0x45
    FC_WRITE_MASKED_ADDRESS = 0x46

    # Main Category Registers
    REG_DHW_SENSOR_TEMP = 0x0E  # DHW Sensor Temperature (0.1°C units)
    REG_INLET_SENSOR_TEMP = 0x0F  # Inlet Sensor Temperature
    REG_DHW_COMFORT_TEMP = 0x14  # DHW Comfort Temperature
    REG_DHW_ECO_TEMP = 0x15  # DHW Eco Temperature
    REG_STATUS_L = 0x08  # Status Low

    def __init__(self, port: str, slave_address: int = 0x01, timeout: float = 2.0):
        """
        Initialize Wavin MODBUS client.

        Args:
            port: Serial port (e.g., "COM3" or "/dev/ttyUSB0")
            slave_address: MODBUS slave address (default 0x01)
            timeout: Response timeout in seconds
        """
        self.port = port
        self.slave_address = slave_address
        self.timeout = timeout
        self.serial = None

    def connect(self) -> bool:
        """Connect to Wavin device via serial port"""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=38400,
                bytesize=8,
                stopbits=1,
                parity=serial.PARITY_NONE,
                timeout=self.timeout
            )
            return self.serial.is_open
        except serial.SerialException as e:
            print(f"Connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from device"""
        if self.serial and self.serial.is_open:
            self.serial.close()

    def _send_request(self, request: bytes) -> Optional[bytes]:
        """Send MODBUS request and receive response"""
        if not self.serial or not self.serial.is_open:
            return None

        # Add CRC and send
        frame = ModbusRTU.add_crc(request)
        self.serial.write(frame)

        # Read response
        response = self.serial.read(256)
        if not response:
            return None

        # Validate CRC
        if not ModbusRTU.validate_crc(response):
            print("CRC validation failed")
            return None

        return response

    def read_register_by_index(
        self,
        category: int,
        page: int,
        index: int,
        quantity: int = 1
    ) -> Optional[List[int]]:
        """
        Read registers by Category/Page/Index.

        Args:
            category: Register category (0x00-0x07)
            page: Register page
            index: Register index
            quantity: Number of registers to read (1-22)

        Returns:
            List of register values or None on error
        """
        request = bytes([
            self.slave_address,
            self.FC_READ_INDEX,
            category,
            index,
            page,
            quantity
        ])

        response = self._send_request(request)
        if not response:
            return None

        # Parse response: [slave, fc, byte_count, data..., crc_l, crc_h]
        if len(response) < 4:
            return None

        byte_count = response[2]
        if len(response) < 3 + byte_count + 2:
            return None

        # Extract register values (MSB first)
        registers = []
        for i in range(0, byte_count, 2):
            value = (response[3 + i] << 8) | response[3 + i + 1]
            registers.append(value)

        return registers

    def write_register_by_index(
        self,
        category: int,
        page: int,
        index: int,
        values: List[int]
    ) -> bool:
        """
        Write registers by Category/Page/Index.

        Args:
            category: Register category
            page: Register page
            index: Register index
            values: List of 16-bit values to write

        Returns:
            True if successful
        """
        quantity = len(values)
        request = bytearray([
            self.slave_address,
            self.FC_WRITE_INDEX,
            category,
            index,
            page,
            quantity
        ])

        # Add register data
        for value in values:
            request.append((value >> 8) & 0xFF)
            request.append(value & 0xFF)

        response = self._send_request(bytes(request))
        return response is not None

    def read_register_by_address(
        self,
        element_address: int,
        index: int,
        quantity: int = 1
    ) -> Optional[List[int]]:
        """
        Read registers by Element Address/Index.

        Args:
            element_address: 32-bit element physical address
            index: Register index
            quantity: Number of registers to read (1-13)

        Returns:
            List of register values or None on error
        """
        address_l = element_address & 0xFFFF
        address_h = (element_address >> 16) & 0xFFFF

        request = bytes([
            self.slave_address,
            self.FC_READ_ADDRESS,
            self.CATEGORY_ELEMENTS,
            index,
            (address_l >> 8) & 0xFF,
            address_l & 0xFF,
            (address_h >> 8) & 0xFF,
            address_h & 0xFF,
            0x00,
            quantity
        ])

        response = self._send_request(request)
        if not response or len(response) < 5:
            return None

        byte_count = response[2]
        registers = []
        for i in range(0, byte_count, 2):
            if 3 + i + 1 < len(response) - 2:
                value = (response[3 + i] << 8) | response[3 + i + 1]
                registers.append(value)

        return registers

    def get_dhw_sensor_temperature(self) -> Optional[float]:
        """Get DHW sensor temperature in °C (0.1°C units)"""
        result = self.read_register_by_index(
            self.CATEGORY_MAIN, 0, self.REG_DHW_SENSOR_TEMP
        )
        if result:
            return result[0] / 10.0
        return None

    def get_inlet_sensor_temperature(self) -> Optional[float]:
        """Get inlet sensor temperature in °C"""
        result = self.read_register_by_index(
            self.CATEGORY_MAIN, 0, self.REG_INLET_SENSOR_TEMP
        )
        if result:
            return result[0] / 10.0
        return None

    def get_dhw_comfort_temperature(self) -> Optional[float]:
        """Get DHW comfort temperature setting in °C"""
        result = self.read_register_by_index(
            self.CATEGORY_MAIN, 0, self.REG_DHW_COMFORT_TEMP
        )
        if result:
            return result[0] / 10.0
        return None

    def set_dhw_comfort_temperature(self, temp_celsius: float) -> bool:
        """Set DHW comfort temperature (in 0.1°C units)"""
        temp_raw = int(temp_celsius * 10)
        return self.write_register_by_index(
            self.CATEGORY_MAIN, 0, self.REG_DHW_COMFORT_TEMP, [temp_raw]
        )

    def get_device_info(self) -> Optional[Dict[str, any]]:
        """Get device information"""
        result = self.read_register_by_index(
            self.CATEGORY_INFO, 0, 0, 5
        )
        if result and len(result) >= 5:
            hw_version = result[2] & 0x7F
            sw_version = (result[3] >> 8) & 0xFF
            device_name = result[4]
            return {
                "hw_version": f"MC110{hw_version}",
                "sw_version": f"MC610{sw_version:02X}",
                "device_name": f"AC-{device_name}"
            }
        return None

    def get_status(self) -> Optional[Dict[str, bool]]:
        """Get device status flags"""
        result = self.read_register_by_index(
            self.CATEGORY_MAIN, 0, self.REG_STATUS_L
        )
        if result:
            status = result[0]
            return {
                "global_standby": bool(status & 0x8000),
                "dhw_enabled": bool(status & 0x4000),
                "htc_enabled": bool(status & 0x2000),
                "inlet_sensor_present": bool(status & 0x1000),
                "dhw_sensor_present": bool(status & 0x0800),
                "rtc_updated": bool(status & 0x0002),
                "rtc_valid": bool(status & 0x0001)
            }
        return None
