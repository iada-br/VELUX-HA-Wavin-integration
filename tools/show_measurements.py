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
DEFAULT_HOST  = "10.10.100.254"
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


# ── Minimal Modbus TCP client ─────────────────────────────────────────────────

class Client:
    def __init__(self, host: str, port: int, slave: int) -> None:
        self.host  = host
        self.port  = port
        self.slave = slave
        self._sock: Optional[socket.socket] = None
        self._tid  = 0

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

    def _tid_next(self) -> int:
        self._tid = (self._tid + 1) & 0xFFFF
        return self._tid

    def _send_read(self, cat: int, idx: int, page: int, qty: int) -> Optional[list[int]]:
        tid = self._tid_next()
        tid_bytes = struct.pack(">H", tid)
        pdu = bytes([FC_READ, cat, idx, page, qty])
        frame = struct.pack(">HHHB", tid, 0, 1 + len(pdu), self.slave) + pdu
        expected_bc = qty * 2

        self._sock.sendall(frame)

        raw = b""
        deadline = time.monotonic() + 1.0
        self._sock.settimeout(0.2)
        while time.monotonic() < deadline:
            try:
                chunk = self._sock.recv(512)
                if not chunk:
                    return None
                raw += chunk
                # Scan for an MBAP frame whose TID matches our query
                i = 0
                while i + 9 <= len(raw):
                    if raw[i:i+2] == tid_bytes and raw[i+7] == FC_READ:
                        bc = raw[i+8]
                        if bc == expected_bc and len(raw) >= i + 9 + bc:
                            n = bc // 2
                            return list(struct.unpack(f">{n}H", raw[i+9: i+9+bc]))
                    i += 1
            except socket.timeout:
                pass
        return None


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
