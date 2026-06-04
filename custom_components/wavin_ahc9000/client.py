"""
Modbus TCP client for the Wavin AHC 9000.

Physical path:
  Home Assistant  -->  TCP  -->  USR-TCP232 (Modbus TCP gateway)
  -->  RS-485 RTU  -->  Wavin AHC 9000 AC-116 module

The USR-TCP232 runs in Modbus TCP gateway mode on port 8899:
  - Accepts Modbus TCP frames (MBAP header + PDU, no CRC)
  - Converts them to Modbus RTU on the RS-485 bus (adds CRC)
  - Strips CRC from RTU responses and returns Modbus TCP responses

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

_MODBUS_PROTOCOL = 0x0000


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
        self._tid = 0

    def _next_tid(self) -> int:
        self._tid = (self._tid + 1) & 0xFFFF
        return self._tid

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

    # ── MBAP frame builders ───────────────────────────────────────────────────

    def _wrap_mbap(self, pdu: bytes, tid: int) -> bytes:
        """Prepend 7-byte MBAP header to a PDU.

        MBAP: [TID:2][Protocol=0:2][Length:2][UnitID:1]
        Length = 1 (unit) + len(pdu).
        """
        return struct.pack(">HHHB", tid, _MODBUS_PROTOCOL, 1 + len(pdu), self._slave) + pdu

    def _build_read(self, cat: int, idx: int, page: int, qty: int) -> tuple[bytes, int]:
        """FC 0x43 read request wrapped in MBAP."""
        tid = self._next_tid()
        pdu = bytes([FC_READ, cat, idx, page, qty])
        return self._wrap_mbap(pdu, tid), tid

    def _build_write(self, cat: int, idx: int, page: int, val: int) -> tuple[bytes, int]:
        """FC 0x44 write request wrapped in MBAP."""
        tid = self._next_tid()
        val_u16 = val & 0xFFFF
        pdu = bytes([
            FC_WRITE, cat, idx, page, 0x00,
            (val_u16 >> 8) & 0xFF,
            val_u16 & 0xFF,
        ])
        return self._wrap_mbap(pdu, tid), tid

    # ── Response parser ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_read_response(
        raw: bytes, expected_bc: int, tid_bytes: bytes
    ) -> Optional[list[int]]:
        """Scan raw buffer for an MBAP FC 0x43 response matching tid_bytes.

        Wire format per frame:
          [TID:2][Protocol:2][Length:2][UnitID:1][FC=0x43:1][ByteCount:1][Data...]
        """
        i = 0
        while i + 9 <= len(raw):
            if raw[i:i + 2] == tid_bytes and raw[i + 7] == FC_READ:
                bc = raw[i + 8]
                if bc == expected_bc and len(raw) >= i + 9 + bc:
                    n = bc // 2
                    return list(struct.unpack(f">{n}H", raw[i + 9: i + 9 + bc]))
            i += 1
        return None

    # ── Register reads ────────────────────────────────────────────────────────

    def read_registers(
        self, cat: int, idx: int, page: int, qty: int = 1
    ) -> Optional[list[int]]:
        """Send FC 0x43 and return a list of qty raw uint16 values.

        Returns None on timeout or socket error. The caller should treat
        None as "data temporarily unavailable" rather than raising.
        """
        with self._lock:
            if self._sock is None:
                return None

            frame, tid = self._build_read(cat, idx, page, qty)
            expected_bc = qty * 2
            tid_bytes = struct.pack(">H", tid)

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
                        result = self._parse_read_response(raw, expected_bc, tid_bytes)
                        if result is not None:
                            return result
                    else:
                        self._connected = False
                        return None
                except socket.timeout:
                    pass
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
        """Send FC 0x44 and wait for the echo response.

        The gateway echoes back the same PDU in a Modbus TCP response.
        Returns True when the echo is received, False on timeout.
        A False return does not guarantee the write failed — the next
        coordinator poll will confirm the new value.
        """
        with self._lock:
            if self._sock is None:
                return False

            frame, _ = self._build_write(cat, idx, page, val)

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
                        # Echo response: MBAP (7 bytes) + FC(1) + 6-byte PDU tail
                        if len(raw) >= 8 and raw[7] == FC_WRITE:
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
