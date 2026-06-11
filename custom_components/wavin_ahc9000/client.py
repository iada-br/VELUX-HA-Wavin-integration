"""
Modbus RTU-over-TCP client for the Wavin AHC 9000.

Physical path:
  Home Assistant  -->  TCP  -->  USR-TCP232 (transparent gateway)
  -->  RS-485 RTU  -->  Wavin AHC 9000 AC-116 module

The USR-TCP232 is configured in Transparent mode (Data_Transfor_Mode=0),
meaning it passes raw bytes between TCP and RS-485 without any Modbus
conversion. We therefore send complete Modbus RTU frames (with CRC) over
the TCP connection.

All public methods are synchronous and MUST be called from an executor
thread (via hass.async_add_executor_job), never from the HA event loop.
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from typing import Optional

from .const import (
    FC_READ,
    FC_WRITE,
    QUERY_CHUNK_TIMEOUT,
    QUERY_RESPONSE_WINDOW,
    SOCKET_CONNECT_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


def _crc16(data: bytes) -> bytes:
    """CRC-16/IBM as used by Modbus RTU."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


class WavinClientError(Exception):
    """Base exception for WavinClient errors."""


class CannotConnect(WavinClientError):
    """Raised when a TCP connection cannot be established or maintained."""


class WavinClient:
    """
    Persistent Modbus TCP connection to the Wavin AHC 9000 via a
    USR-TCP232 gateway.

    Thread safety
    -------------
    All socket I/O is protected by a threading.Lock so concurrent
    callers (background poll + user-triggered set_temperature) cannot
    interleave their send-receive sequences.
    """

    def __init__(self, host: str, port: int, slave_id: int) -> None:
        self._host = host
        self._port = port
        self._slave = slave_id
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._connected = False

    # ── Connection management ─────────────────────────────────────────────────

    def connect(self) -> None:
        """Open a Modbus TCP connection to the gateway."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._connected = False

        try:
            sock = socket.create_connection(
                (self._host, self._port), timeout=SOCKET_CONNECT_TIMEOUT
            )
        except (OSError, socket.timeout) as exc:
            raise CannotConnect(
                f"Cannot connect to {self._host}:{self._port}: {exc}"
            ) from exc

        sock.settimeout(SOCKET_CONNECT_TIMEOUT)
        self._sock = sock
        self._connected = True
        _LOGGER.debug("Connected to %s:%d (Modbus TCP)", self._host, self._port)

    def disconnect(self) -> None:
        """Close the socket gracefully. Safe to call multiple times."""
        with self._lock:
            self._connected = False
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def ensure_connected(self) -> None:
        """Reconnect if the socket is not currently open."""
        if not self._connected or self._sock is None:
            _LOGGER.debug("Reconnecting to %s:%d", self._host, self._port)
            self.connect()

    # ── RTU frame builders ────────────────────────────────────────────────────

    def _build_read(self, cat: int, idx: int, page: int, qty: int) -> bytes:
        """FC 0x43 read request as Modbus RTU frame (with CRC)."""
        pdu = bytes([self._slave, FC_READ, cat, idx, page, qty])
        return pdu + _crc16(pdu)

    def _build_write(self, cat: int, idx: int, page: int, val: int) -> bytes:
        """FC 0x44 write request as Modbus RTU frame (with CRC)."""
        val_u16 = val & 0xFFFF
        pdu = bytes([
            self._slave, FC_WRITE, cat, idx, page, 0x00,
            (val_u16 >> 8) & 0xFF,
            val_u16 & 0xFF,
        ])
        return pdu + _crc16(pdu)

    # ── Response parser ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_read_response(raw: bytes, expected_bc: int) -> Optional[list[int]]:
        """Parse an RTU FC 0x43 response.

        Wire format: [SLAVE:1][FC=0x43:1][ByteCount:1][Data...][CRC:2]
        """
        if len(raw) < 5:
            return None
        if raw[1] != FC_READ:
            return None
        bc = raw[2]
        if bc != expected_bc or len(raw) < 3 + bc + 2:
            return None
        n = bc // 2
        return list(struct.unpack(f">{n}H", raw[3: 3 + bc]))

    # ── Register reads ────────────────────────────────────────────────────────

    def read_registers(
        self, cat: int, idx: int, page: int, qty: int = 1
    ) -> Optional[list[int]]:
        """Send FC 0x43 RTU frame and return a list of qty raw uint16 values.

        Returns None on timeout or socket error. The caller should treat
        None as "data temporarily unavailable" rather than raising.
        """
        with self._lock:
            if self._sock is None:
                return None

            frame = self._build_read(cat, idx, page, qty)
            expected_bc = qty * 2
            expected_len = 3 + expected_bc + 2  # slave+FC+bc + data + CRC

            try:
                self._sock.sendall(frame)
            except OSError as exc:
                _LOGGER.warning("Send failed in read_registers: %s", exc)
                self._connected = False
                return None

            raw = b""
            deadline = time.monotonic() + QUERY_RESPONSE_WINDOW
            self._sock.settimeout(QUERY_CHUNK_TIMEOUT)

            while time.monotonic() < deadline:
                try:
                    chunk = self._sock.recv(512)
                    if chunk:
                        raw += chunk
                        result = self._parse_read_response(raw, expected_bc)
                        if result is not None:
                            return result
                    else:
                        self._connected = False
                        return None
                except socket.timeout:
                    if len(raw) >= expected_len:
                        break
                except OSError as exc:
                    _LOGGER.warning("Recv failed in read_registers: %s", exc)
                    self._connected = False
                    return None

            _LOGGER.debug(
                "read_registers timeout: cat=0x%02x idx=0x%02x page=%d qty=%d raw=%s",
                cat, idx, page, qty,
                raw.hex() if raw else "(empty)",
            )
            return None

    # ── Register write ────────────────────────────────────────────────────────

    def write_register(
        self, cat: int, idx: int, page: int, val: int
    ) -> bool:
        """Send FC 0x44 RTU frame and wait for the echo response.

        The device echoes back the same frame on success.
        Returns True when the echo is received, False on timeout.
        A False return does not guarantee the write failed — the next
        coordinator poll will confirm the new value.
        """
        with self._lock:
            if self._sock is None:
                return False

            frame = self._build_write(cat, idx, page, val)

            try:
                self._sock.sendall(frame)
            except OSError as exc:
                _LOGGER.warning("Send failed in write_register: %s", exc)
                self._connected = False
                return False

            raw = b""
            deadline = time.monotonic() + QUERY_RESPONSE_WINDOW
            self._sock.settimeout(QUERY_CHUNK_TIMEOUT)

            while time.monotonic() < deadline:
                try:
                    chunk = self._sock.recv(512)
                    if chunk:
                        raw += chunk
                        # RTU echo: slave(1) + FC_WRITE(1) + rest(6) + CRC(2) = 10 bytes
                        if len(raw) >= 2 and raw[1] == FC_WRITE:
                            _LOGGER.debug(
                                "Write echo OK: cat=0x%02x idx=0x%02x page=%d val=%d",
                                cat, idx, page, val,
                            )
                            return True
                    else:
                        self._connected = False
                        return False
                except socket.timeout:
                    pass
                except OSError as exc:
                    _LOGGER.warning("Recv failed in write_register: %s", exc)
                    self._connected = False
                    return False

            _LOGGER.warning(
                "write_register: no echo for cat=0x%02x idx=0x%02x page=%d val=%d"
                " (write may still have taken effect; next poll will confirm)",
                cat, idx, page, val,
            )
            return False

    # ── Convenience helpers ───────────────────────────────────────────────────

    def read_temperature(
        self, cat: int, idx: int, page: int
    ) -> Optional[float]:
        """Read one temperature register and return °C as a float."""
        result = self.read_registers(cat, idx, page, qty=1)
        if result is None:
            return None
        return raw_to_temp(result[0])

    def read_device_info(self) -> dict:
        """Read device identification registers for config-flow validation."""
        from .const import (
            CAT_INFO,
            IDX_INFO_DEVICE_NAME,
            IDX_INFO_HW_VER,
            IDX_INFO_SW_VER,
        )
        info: dict = {}
        for key, idx in (
            ("device_name", IDX_INFO_DEVICE_NAME),
            ("hw_ver", IDX_INFO_HW_VER),
            ("sw_ver", IDX_INFO_SW_VER),
        ):
            result = self.read_registers(CAT_INFO, idx, page=0, qty=1)
            info[key] = result[0] if result else None
        return info


# ── Module-level helper ────────────────────────────────────────────────────────

def raw_to_temp(raw: int) -> Optional[float]:
    """Convert a raw register uint16 to °C.

    0x7FFF → None (sensor absent or not wired).
    All other values are signed int16 in units of 0.1 °C.
    """
    if raw == 0x7FFF:
        return None
    signed = raw if raw < 0x8000 else raw - 0x10000
    return round(signed / 10.0, 1)
