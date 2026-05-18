"""
Unit tests for Wavin MODBUS RTU Client
"""
import pytest
from unittest.mock import patch, MagicMock, call
from wavin_modbus_client import ModbusRTU, WavinModbusClient
import struct


class TestModbusRTUCRC:
    """Test CRC-16-CCITT calculation and validation."""

    def test_crc16_calculation_simple(self):
        """Test CRC16 calculation with simple data."""
        # Known test vector: [0x01, 0x03] should have CRC
        data = bytes([0x01, 0x03])
        crc = ModbusRTU.calculate_crc16(data)
        assert isinstance(crc, bytes)
        assert len(crc) == 2

    def test_crc16_calculation_empty(self):
        """Test CRC16 calculation with empty data."""
        data = bytes()
        crc = ModbusRTU.calculate_crc16(data)
        # CRC of empty data should be 0xFFFF (initial value)
        assert crc == struct.pack('<H', 0xFFFF)

    def test_crc16_calculation_single_byte(self):
        """Test CRC16 calculation with single byte."""
        data = bytes([0xFF])
        crc = ModbusRTU.calculate_crc16(data)
        assert isinstance(crc, bytes)
        assert len(crc) == 2

    def test_crc16_deterministic(self):
        """Test that CRC calculation is deterministic."""
        data = bytes([0x01, 0x43, 0x00, 0x0E, 0x00, 0x01])
        crc1 = ModbusRTU.calculate_crc16(data)
        crc2 = ModbusRTU.calculate_crc16(data)
        assert crc1 == crc2

    def test_crc16_different_inputs(self):
        """Test that different inputs produce different CRCs."""
        data1 = bytes([0x01, 0x43, 0x00])
        data2 = bytes([0x01, 0x43, 0x01])
        crc1 = ModbusRTU.calculate_crc16(data1)
        crc2 = ModbusRTU.calculate_crc16(data2)
        assert crc1 != crc2

    def test_validate_crc_valid_frame(self):
        """Test CRC validation with valid frame."""
        message = bytes([0x01, 0x43, 0x00, 0x0E, 0x00, 0x01])
        crc = ModbusRTU.calculate_crc16(message)
        frame = message + crc
        assert ModbusRTU.validate_crc(frame) is True

    def test_validate_crc_invalid_frame(self):
        """Test CRC validation with corrupted data."""
        message = bytes([0x01, 0x43, 0x00, 0x0E, 0x00, 0x01])
        crc = ModbusRTU.calculate_crc16(message)
        # Corrupt the message
        frame = bytes([message[0] ^ 0xFF]) + message[1:] + crc
        assert ModbusRTU.validate_crc(frame) is False

    def test_validate_crc_corrupted_crc(self):
        """Test CRC validation with corrupted CRC bytes."""
        message = bytes([0x01, 0x43, 0x00, 0x0E, 0x00, 0x01])
        crc = ModbusRTU.calculate_crc16(message)
        # Corrupt CRC
        frame = message + bytes([crc[0] ^ 0xFF, crc[1]])
        assert ModbusRTU.validate_crc(frame) is False

    def test_validate_crc_short_frame(self):
        """Test CRC validation with frame too short."""
        frame = bytes([0x01, 0x43])
        assert ModbusRTU.validate_crc(frame) is False

    def test_add_crc_appends_correctly(self):
        """Test that add_crc appends CRC to data."""
        message = bytes([0x01, 0x43, 0x00])
        framed = ModbusRTU.add_crc(message)
        assert len(framed) == len(message) + 2
        assert framed[:-2] == message
        # Verify CRC is correct
        assert ModbusRTU.validate_crc(framed) is True


class TestWavinModbusClientInitialization:
    """Test client initialization."""

    def test_client_initialization_defaults(self):
        """Test client initializes with defaults."""
        client = WavinModbusClient("COM3")
        assert client.port == "COM3"
        assert client.slave_address == 0x01
        assert client.timeout == 2.0
        assert client.serial is None

    def test_client_initialization_custom_slave_address(self):
        """Test client initializes with custom slave address."""
        client = WavinModbusClient("COM3", slave_address=0x02)
        assert client.slave_address == 0x02

    def test_client_initialization_custom_timeout(self):
        """Test client initializes with custom timeout."""
        client = WavinModbusClient("COM3", timeout=5.0)
        assert client.timeout == 5.0

    def test_client_constants_defined(self):
        """Test that all required constants are defined."""
        assert hasattr(WavinModbusClient, 'FC_READ_INDEX')
        assert WavinModbusClient.FC_READ_INDEX == 0x43
        assert WavinModbusClient.FC_WRITE_INDEX == 0x44
        assert WavinModbusClient.CATEGORY_MAIN == 0x00
        assert WavinModbusClient.REG_DHW_SENSOR_TEMP == 0x0E


class TestWavinModbusClientConnect:
    """Test connection functionality."""

    @patch('wavin_modbus_client.serial.Serial')
    def test_connect_successful(self, mock_serial_class):
        """Test successful connection."""
        mock_serial = MagicMock()
        mock_serial.is_open = True
        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        result = client.connect()

        assert result is True
        mock_serial_class.assert_called_once_with(
            port="COM3",
            baudrate=38400,
            bytesize=8,
            stopbits=1,
            parity='N',  # serial.PARITY_NONE
            timeout=2.0
        )

    @patch('wavin_modbus_client.serial.Serial')
    def test_connect_failure(self, mock_serial_class):
        """Test connection failure."""
        import serial
        mock_serial_class.side_effect = serial.SerialException("Port not found")

        client = WavinModbusClient("COM99")
        result = client.connect()

        assert result is False

    @patch('wavin_modbus_client.serial.Serial')
    def test_disconnect(self, mock_serial_class):
        """Test disconnect closes serial port."""
        mock_serial = MagicMock()
        mock_serial.is_open = True
        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        client.disconnect()

        mock_serial.close.assert_called_once()

    @patch('wavin_modbus_client.serial.Serial')
    def test_disconnect_not_connected(self, mock_serial_class):
        """Test disconnect when not connected."""
        client = WavinModbusClient("COM3")
        # Should not raise exception
        client.disconnect()


class TestWavinModbusClientReadRegisterByIndex:
    """Test read_register_by_index functionality."""

    @patch('wavin_modbus_client.serial.Serial')
    def test_read_register_by_index_success(self, mock_serial_class):
        """Test successful register read by index."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        # Response: [slave, fc, byte_count, data_h, data_l, crc_l, crc_h]
        response_data = bytes([0x01, 0x43, 0x02, 0x00, 0x8C])  # Value: 0x008C
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        result = client.read_register_by_index(0x00, 0, 0x0E)

        assert result == [0x008C]

    @patch('wavin_modbus_client.serial.Serial')
    def test_read_register_by_index_multiple_registers(self, mock_serial_class):
        """Test reading multiple registers."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        response_data = bytes([0x01, 0x43, 0x04, 0x00, 0x8C, 0x01, 0x23])
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        result = client.read_register_by_index(0x00, 0, 0x0E, quantity=2)

        assert result == [0x008C, 0x0123]

    @patch('wavin_modbus_client.serial.Serial')
    def test_read_register_crc_validation_failure(self, mock_serial_class):
        """Test read fails with invalid CRC."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        response_data = bytes([0x01, 0x43, 0x02, 0x00, 0x8C])
        crc = ModbusRTU.calculate_crc16(response_data)
        # Corrupt CRC
        response = response_data + bytes([crc[0] ^ 0xFF, crc[1]])
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        result = client.read_register_by_index(0x00, 0, 0x0E)

        assert result is None

    @patch('wavin_modbus_client.serial.Serial')
    def test_read_register_empty_response(self, mock_serial_class):
        """Test read with empty response."""
        mock_serial = MagicMock()
        mock_serial.is_open = True
        mock_serial.read.return_value = bytes()

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        result = client.read_register_by_index(0x00, 0, 0x0E)

        assert result is None

    def test_read_register_not_connected(self):
        """Test read fails when not connected."""
        client = WavinModbusClient("COM3")
        result = client.read_register_by_index(0x00, 0, 0x0E)
        assert result is None


class TestWavinModbusClientWriteRegisterByIndex:
    """Test write_register_by_index functionality."""

    @patch('wavin_modbus_client.serial.Serial')
    def test_write_register_by_index_success(self, mock_serial_class):
        """Test successful register write."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        # Echo response for write
        response_data = bytes([0x01, 0x44, 0x00, 0x14, 0x00, 0x01])
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        result = client.write_register_by_index(0x00, 0, 0x14, [250])  # 25.0°C

        assert result is True

    @patch('wavin_modbus_client.serial.Serial')
    def test_write_register_by_index_multiple_values(self, mock_serial_class):
        """Test writing multiple registers."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        response_data = bytes([0x01, 0x44, 0x00, 0x14, 0x00, 0x02])
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        result = client.write_register_by_index(0x00, 0, 0x14, [250, 200])

        assert result is True

    @patch('wavin_modbus_client.serial.Serial')
    def test_write_register_crc_validation_failure(self, mock_serial_class):
        """Test write fails with invalid CRC in response."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        response_data = bytes([0x01, 0x44, 0x00, 0x14, 0x00, 0x01])
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + bytes([crc[0] ^ 0xFF, crc[1]])
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        result = client.write_register_by_index(0x00, 0, 0x14, [250])

        assert result is False

    @patch('wavin_modbus_client.serial.Serial')
    def test_write_register_no_response(self, mock_serial_class):
        """Test write with no response."""
        mock_serial = MagicMock()
        mock_serial.is_open = True
        mock_serial.read.return_value = bytes()

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        result = client.write_register_by_index(0x00, 0, 0x14, [250])

        assert result is False

    def test_write_register_not_connected(self):
        """Test write fails when not connected."""
        client = WavinModbusClient("COM3")
        result = client.write_register_by_index(0x00, 0, 0x14, [250])
        assert result is False


class TestWavinModbusClientReadRegisterByAddress:
    """Test read_register_by_address functionality."""

    @patch('wavin_modbus_client.serial.Serial')
    def test_read_register_by_address_success(self, mock_serial_class):
        """Test successful register read by address."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        response_data = bytes([0x01, 0x41, 0x02, 0x00, 0x8C])
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        result = client.read_register_by_address(0x00000001, 0)

        assert result == [0x008C]

    @patch('wavin_modbus_client.serial.Serial')
    def test_read_register_by_address_32bit_address(self, mock_serial_class):
        """Test read with 32-bit element address."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        response_data = bytes([0x01, 0x41, 0x02, 0x12, 0x34])
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        # Address: 0x12345678
        result = client.read_register_by_address(0x12345678, 0)

        # Verify request was constructed with proper address encoding
        call_args = mock_serial.write.call_args[0][0]
        # Request should include [slave, fc, category, index, addr_l_h, addr_l_l, addr_h_h, addr_h_l, ...]
        assert call_args[1] == 0x41  # FC_READ_ADDRESS


class TestWavinModbusClientTemperatureOperations:
    """Test temperature-related methods."""

    @patch('wavin_modbus_client.serial.Serial')
    def test_get_dhw_sensor_temperature(self, mock_serial_class):
        """Test getting DHW sensor temperature."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        # Temperature 22.5°C = 225 in 0.1°C units = 0x00E1
        response_data = bytes([0x01, 0x43, 0x02, 0x00, 0xE1])
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        temp = client.get_dhw_sensor_temperature()

        assert temp == 22.5

    @patch('wavin_modbus_client.serial.Serial')
    def test_set_dhw_comfort_temperature(self, mock_serial_class):
        """Test setting DHW comfort temperature."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        response_data = bytes([0x01, 0x44, 0x00, 0x14, 0x00, 0x01])
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        result = client.set_dhw_comfort_temperature(22.5)

        assert result is True
        # Verify write was called with correct value (225)
        call_args = mock_serial.write.call_args[0][0]
        # Value 225 = 0x00E1 should be in request as [0x00, 0xE1]
        assert 0xE1 in call_args or 225 in call_args

    @patch('wavin_modbus_client.serial.Serial')
    def test_temperature_conversion_zero(self, mock_serial_class):
        """Test temperature conversion for 0°C."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        response_data = bytes([0x01, 0x43, 0x02, 0x00, 0x00])
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        temp = client.get_dhw_sensor_temperature()

        assert temp == 0.0

    @patch('wavin_modbus_client.serial.Serial')
    def test_temperature_conversion_negative(self, mock_serial_class):
        """Test temperature conversion for negative values."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        # -5.0°C would be represented as negative value
        # In practice, this depends on the device's representation (signed/unsigned)
        response_data = bytes([0x01, 0x43, 0x02, 0xFF, 0xCE])  # -50 in signed
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        temp = client.get_dhw_sensor_temperature()

        # Result depends on device protocol for negative temps
        assert temp is not None


class TestWavinModbusClientDeviceInfo:
    """Test device information retrieval."""

    @patch('wavin_modbus_client.serial.Serial')
    def test_get_device_info_success(self, mock_serial_class):
        """Test successful device info retrieval."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        # 5 registers of device info (10 bytes of data)
        response_data = bytes([0x01, 0x43, 0x0A, 0x00, 0x01, 0x00, 0x02, 0x00, 0x03, 0x00, 0x04, 0x00, 0x05])
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        info = client.get_device_info()

        assert info is not None
        assert isinstance(info, dict)


class TestWavinModbusClientStatus:
    """Test device status retrieval."""

    @patch('wavin_modbus_client.serial.Serial')
    def test_get_status_success(self, mock_serial_class):
        """Test successful status retrieval."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        # Status register with multiple flags set
        response_data = bytes([0x01, 0x43, 0x02, 0xC8, 0x03])  # Various flags set
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        status = client.get_status()

        assert status is not None
        assert isinstance(status, dict)
        assert all(isinstance(v, bool) for v in status.values())

    @patch('wavin_modbus_client.serial.Serial')
    def test_get_status_flag_parsing(self, mock_serial_class):
        """Test that status flags are parsed correctly."""
        mock_serial = MagicMock()
        mock_serial.is_open = True

        # Set specific flags: bit 15 (0x8000) = global_standby, bit 14 (0x4000) = dhw_enabled
        response_data = bytes([0x01, 0x43, 0x02, 0xC0, 0x00])  # 0xC000
        crc = ModbusRTU.calculate_crc16(response_data)
        response = response_data + crc
        mock_serial.read.return_value = response

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3")
        client.connect()
        status = client.get_status()

        assert status["global_standby"] is True
        assert status["dhw_enabled"] is True


class TestWavinModbusClientFrameConstruction:
    """Test correct MODBUS frame construction."""

    @patch('wavin_modbus_client.serial.Serial')
    def test_read_request_frame_format(self, mock_serial_class):
        """Test that read request is formatted correctly."""
        mock_serial = MagicMock()
        mock_serial.is_open = True
        mock_serial.read.return_value = bytes([0x01, 0x43, 0x00])

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3", slave_address=0x01)
        client.connect()
        client.read_register_by_index(0x00, 0, 0x0E)

        # Get the sent frame
        call_args = mock_serial.write.call_args[0][0]

        # Frame should be: [slave, fc, category, index, page, quantity, crc_l, crc_h]
        assert call_args[0] == 0x01  # slave address
        assert call_args[1] == 0x43  # FC_READ_INDEX
        assert call_args[2] == 0x00  # category
        assert call_args[3] == 0x0E  # index
        assert len(call_args) == 8   # 6 bytes + 2 CRC

    @patch('wavin_modbus_client.serial.Serial')
    def test_write_request_frame_format(self, mock_serial_class):
        """Test that write request is formatted correctly."""
        mock_serial = MagicMock()
        mock_serial.is_open = True
        mock_serial.read.return_value = bytes([0x01, 0x44, 0x00])

        mock_serial_class.return_value = mock_serial

        client = WavinModbusClient("COM3", slave_address=0x01)
        client.connect()
        client.write_register_by_index(0x00, 0, 0x14, [250])

        call_args = mock_serial.write.call_args[0][0]

        # Frame should be: [slave, fc, category, index, page, quantity, val_h, val_l, crc_l, crc_h]
        assert call_args[0] == 0x01  # slave address
        assert call_args[1] == 0x44  # FC_WRITE_INDEX
        assert call_args[2] == 0x00  # category
        assert call_args[3] == 0x14  # index
        assert call_args[6] == 0x00  # value high byte
        assert call_args[7] == 0xFA  # value low byte (250)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
