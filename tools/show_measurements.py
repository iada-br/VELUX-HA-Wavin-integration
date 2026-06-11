#!/usr/bin/env python3
"""
Live measurement display for the Wavin AHC 9000.

Usage:
    python tools/show_measurements.py              # single snapshot
    python tools/show_measurements.py --watch      # refresh every 5 s
    python tools/show_measurements.py --host <ip> --port <port>
"""
import argparse
import os
import socket
import struct
import time
from typing import Optional

# ── Connection defaults (match integration defaults) ──────────────────────────
DEFAULT_HOST  = "192.168.1.199"
DEFAULT_PORT  = 8899
DEFAULT_SLAVE = 0x01
MAX_CHANNELS  = 16

# ── Protocol constants ────────────────────────────────────────────────────────
FC_READ               = 0x43
FC_WRITE              = 0x44
CAT_ELEMENTS          = 0x01
CAT_PACKED            = 0x02
CAT_CHANNELS          = 0x03
CAT_INFO              = 0x07
IDX_CH_PRIMARY_ELEMENT = 0x02
IDX_CH_TIMER_EVENT    = 0x00
IDX_CH_MANUAL_TEMP    = 0x00
IDX_ELEM_AIR_TEMP     = 0x04
IDX_ELEM_FLOOR_TEMP   = 0x05
IDX_INFO_DEVICE_NAME  = 0x04
IDX_INFO_HW_VER       = 0x02
IDX_INFO_SW_VER       = 0x03
PRIMARY_ELEMENT_IDX_MASK     = 0x003F
PRIMARY_ELEMENT_TP_LOST_MASK = 0x0400
TIMER_EVENT_OUTP_ON_MASK     = 0x0010
SENSOR_NA = 0x7FFF


# ── Minimal Modbus RTU-over-TCP client ───────────────────────────────────────
# The USR gateway is in Transparent mode, so we send raw Modbus RTU frames
# (with CRC) instead of Modbus TCP (MBAP) frames.

def _crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


class Client:
    def __init__(self, host: str, port: int, slave: int) -> None:
        self.host  = host
        self.port  = port
        self.slave = slave
        self._sock: Optional[socket.socket] = None

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=5.0)
        self._sock.settimeout(5.0)

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _send_read(self, cat: int, idx: int, page: int, qty: int) -> Optional[list[int]]:
        pdu = bytes([self.slave, FC_READ, cat, idx, page, qty])
        self._sock.sendall(pdu + _crc16(pdu))

        expected = 3 + qty * 2 + 2  # slave + FC + byte_count + data + CRC
        raw = b""
        deadline = time.monotonic() + 1.5
        self._sock.settimeout(0.3)
        while time.monotonic() < deadline:
            try:
                chunk = self._sock.recv(512)
                if not chunk:
                    return None
                raw += chunk
                if len(raw) >= expected:
                    break
            except socket.timeout:
                pass

        if len(raw) < 5:
            return None
        bc = raw[2]
        if len(raw) < 3 + bc + 2:
            return None
        n = bc // 2
        return list(struct.unpack(f">{n}H", raw[3: 3 + bc]))


# ── Helpers ───────────────────────────────────────────────────────────────────

def raw_to_temp(raw: int) -> Optional[float]:
    if raw == SENSOR_NA:
        return None
    signed = raw if raw < 0x8000 else raw - 0x10000
    return round(signed / 10.0, 1)


def temp_str(val: Optional[float]) -> str:
    return f"{val:5.1f} C " if val is not None else "  N/A  "


def read_measurements(c: Client) -> dict:
    """Return a dict with device info and per-channel readings."""
    result: dict = {"channels": []}

    # Device info
    hw  = c._send_read(CAT_INFO, IDX_INFO_HW_VER,      page=0, qty=1)
    sw  = c._send_read(CAT_INFO, IDX_INFO_SW_VER,      page=0, qty=1)
    dev = c._send_read(CAT_INFO, IDX_INFO_DEVICE_NAME, page=0, qty=1)
    result["hw_ver"]  = hw[0]  if hw  else None
    result["sw_ver"]  = sw[0]  if sw  else None
    result["dev_name"] = dev[0] if dev else None

    for ch in range(MAX_CHANNELS):
        prim = c._send_read(CAT_CHANNELS, IDX_CH_PRIMARY_ELEMENT, page=ch, qty=1)
        if prim is None:
            continue
        element_idx = prim[0] & PRIMARY_ELEMENT_IDX_MASK
        tp_lost     = bool(prim[0] & PRIMARY_ELEMENT_TP_LOST_MASK)

        if element_idx == 0:
            continue  # no thermostat on this channel

        timer = c._send_read(CAT_CHANNELS, IDX_CH_TIMER_EVENT, page=ch, qty=1)
        valve = bool(timer[0] & TIMER_EVENT_OUTP_ON_MASK) if timer else False

        setp = c._send_read(CAT_PACKED, IDX_CH_MANUAL_TEMP, page=ch, qty=1)
        desired = raw_to_temp(setp[0]) if setp else None

        temps = c._send_read(CAT_ELEMENTS, IDX_ELEM_AIR_TEMP, page=element_idx - 1, qty=2)
        if temps and len(temps) == 2:
            air   = raw_to_temp(temps[0])
            floor = raw_to_temp(temps[1])
        else:
            air = floor = None

        result["channels"].append({
            "ch":      ch,
            "element": element_idx,
            "air":     air,
            "floor":   floor,
            "desired": desired,
            "valve":   valve,
            "tp_lost": tp_lost,
        })

    return result


def print_table(data: dict, host: str, port: int) -> None:
    os.system("cls" if os.name == "nt" else "clear")

    hw  = data.get("hw_ver")
    sw  = data.get("sw_ver")
    dev = data.get("dev_name")
    print(f"Wavin AHC 9000  [{host}:{port}]"
          f"  device={dev}  hw={hw}  sw={sw}")
    print("-" * 72)
    print(f"  {'Zone':<8} {'Element':>7}  {'Air':>8}  {'Floor':>8}  {'Setpoint':>9}  {'Valve':>6}  {'TP Lost'}")
    print("-" * 72)

    channels = data.get("channels", [])
    if not channels:
        print("  No active zones detected.")
    else:
        for z in channels:
            valve_icon = "OPEN  " if z["valve"]   else "closed"
            tp_icon    = "LOST " if z["tp_lost"] else "OK   "
            print(
                f"  Zone {z['ch'] + 1:<3}  "
                f"  [{z['element']:>2}]     "
                f"  {temp_str(z['air'])}  "
                f"  {temp_str(z['floor'])}  "
                f"  {temp_str(z['desired'])}  "
                f"  {valve_icon}  "
                f"  {tp_icon}"
            )

    print("-" * 72)
    print(f"  Last update: {time.strftime('%H:%M:%S')}   "
          f"Active zones: {len(channels)}/{MAX_CHANNELS}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Wavin AHC 9000 live measurements")
    parser.add_argument("--host",  default=DEFAULT_HOST,  help="Gateway IP")
    parser.add_argument("--port",  default=DEFAULT_PORT,  type=int, help="TCP port")
    parser.add_argument("--slave", default=DEFAULT_SLAVE, type=int, help="Modbus slave ID")
    parser.add_argument("--watch", action="store_true",   help="Refresh every 5 s (Ctrl+C to stop)")
    parser.add_argument("--interval", default=5, type=int, help="Watch interval in seconds")
    args = parser.parse_args()

    while True:
        c = Client(args.host, args.port, args.slave)
        try:
            c.connect()
            data = read_measurements(c)
            print_table(data, args.host, args.port)
        except (OSError, socket.timeout) as e:
            print(f"Connection error: {e}")
        finally:
            c.close()

        if not args.watch:
            break
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    main()
