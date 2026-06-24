"""
Set the Wavin AHC 9000 setpoint from the command line.

Temperature is always set per-thermostat: the tool first scans the device to
discover which channels share a thermostat, then writes the setpoint to every
channel in that group uniformly.

Usage:
    python tools/set_setpoint.py 22.0              # set channel 1 thermostat to 22.0 C
    python tools/set_setpoint.py 22.0 --channel 7  # set thermostat on channel 7 (1-based)
    python tools/set_setpoint.py --read             # read current setpoints, no write

Protocol:
    FC 0x43 read  — CAT=0x02 IDX=0x00 page=<channel> qty=1
    FC 0x44 write — CAT=0x02 IDX=0x00 page=<channel> val=<temp*10>
"""
import argparse
import socket
import struct
import sys
import time
from typing import Optional

HOST  = "10.10.100.254"
PORT  = 8899
SLAVE = 0x01

FC_READ  = 0x43
FC_WRITE = 0x44
CAT_PACKED               = 0x02
CAT_CHANNELS             = 0x03
IDX_MANUAL_TEMP          = 0x00
IDX_CH_PRIMARY_ELEMENT   = 0x02
PRIMARY_ELEMENT_IDX_MASK = 0x003F
MAX_CHANNELS             = 16
SENSOR_NA                = 0x7FFF
MIN_TEMP                 = 5.0
MAX_TEMP                 = 35.0


# ── MBAP helpers ──────────────────────────────────────────────────────────────

_tid = 0

def _next_tid() -> int:
    global _tid
    _tid = (_tid + 1) & 0xFFFF
    return _tid

def _build_read(page: int, qty: int = 1,
                cat: int = CAT_PACKED, idx: int = IDX_MANUAL_TEMP) -> tuple[bytes, int]:
    tid = _next_tid()
    pdu = bytes([FC_READ, cat, idx, page, qty])
    frame = struct.pack(">HHHB", tid, 0, 1 + len(pdu), SLAVE) + pdu
    return frame, tid

def _build_write(page: int, raw_val: int) -> tuple[bytes, int]:
    tid = _next_tid()
    pdu = bytes([
        FC_WRITE, CAT_PACKED, IDX_MANUAL_TEMP, page, 0x00,
        (raw_val >> 8) & 0xFF,
        raw_val & 0xFF,
    ])
    frame = struct.pack(">HHHB", tid, 0, 1 + len(pdu), SLAVE) + pdu
    return frame, tid


# ── Socket send/receive ───────────────────────────────────────────────────────

def _recv_mbap(sock: socket.socket, tid: int, expected_fc: int,
               timeout: float = 2.0) -> Optional[bytes]:
    """Wait for an MBAP response matching tid and fc, return its payload bytes."""
    tid_bytes = struct.pack(">H", tid)
    raw = b""
    deadline = time.monotonic() + timeout
    sock.settimeout(0.2)
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(512)
            if chunk:
                raw += chunk
                i = 0
                while i + 8 <= len(raw):
                    if raw[i:i+2] == tid_bytes and raw[i+7] == expected_fc:
                        length = struct.unpack(">H", raw[i+4:i+6])[0]
                        end = i + 6 + length
                        if len(raw) >= end:
                            return raw[i+7:end]  # FC byte onwards
                    i += 1
        except socket.timeout:
            pass
    return None


def read_setpoint(sock: socket.socket, channel: int) -> Optional[float]:
    frame, tid = _build_read(page=channel)
    sock.sendall(frame)
    payload = _recv_mbap(sock, tid, FC_READ)
    if payload is None or len(payload) < 3:
        return None
    bc = payload[1]
    if bc < 2:
        return None
    raw = struct.unpack(">H", payload[2:4])[0]
    if raw == SENSOR_NA:
        return None
    signed = raw if raw < 0x8000 else raw - 0x10000
    return round(signed / 10.0, 1)


def write_setpoint(sock: socket.socket, channel: int, temp: float) -> bool:
    raw_val = int(round(temp * 10)) & 0xFFFF
    frame, tid = _build_write(page=channel, raw_val=raw_val)
    sock.sendall(frame)
    payload = _recv_mbap(sock, tid, FC_WRITE, timeout=2.0)
    return payload is not None


# ── Thermostat group detection ────────────────────────────────────────────────

def scan_thermostat_groups(sock: socket.socket) -> dict[int, list[int]]:
    """Read IDX_CH_PRIMARY_ELEMENT for all channels; return {primary_ch: [all_chs]}."""
    by_elem: dict[int, list[int]] = {}
    for ch in range(MAX_CHANNELS):
        frame, tid = _build_read(page=ch, cat=CAT_CHANNELS, idx=IDX_CH_PRIMARY_ELEMENT)
        sock.sendall(frame)
        payload = _recv_mbap(sock, tid, FC_READ)
        if payload is None or len(payload) < 4:
            continue
        raw = struct.unpack(">H", payload[2:4])[0]
        element_idx = raw & PRIMARY_ELEMENT_IDX_MASK
        if element_idx > 0:
            by_elem.setdefault(element_idx, []).append(ch)
    return {min(chs): sorted(chs) for chs in by_elem.values()}


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Wavin AHC 9000 setpoint tool")
    parser.add_argument("temp", nargs="?", type=float,
                        help="Target temperature in °C (e.g. 22.0)")
    parser.add_argument("--channel", type=int, default=1,
                        help="Channel number, 1-based (default: 1). "
                             "All channels sharing the same thermostat are written together.")
    parser.add_argument("--read", action="store_true",
                        help="Read current setpoints for all thermostats, no write")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    if not args.read and args.temp is None:
        parser.error("Provide a temperature value, or use --read")

    if args.temp is not None and not (MIN_TEMP <= args.temp <= MAX_TEMP):
        parser.error(f"Temperature must be between {MIN_TEMP} and {MAX_TEMP} °C")

    channel = args.channel - 1  # convert to 0-based

    print(f"Connecting to {args.host}:{args.port} ...")
    try:
        sock = socket.create_connection((args.host, args.port), timeout=5.0)
    except OSError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print("Scanning thermostat groups ...")
    groups = scan_thermostat_groups(sock)

    if not groups:
        print("No active thermostats found — check connection and device state.")
        sock.close()
        sys.exit(1)

    # Resolve the requested channel to its thermostat group.
    # If the user specifies a non-primary channel we still find the right group.
    primary = next(
        (p for p, chs in groups.items() if channel in chs),
        None,
    )
    if primary is None and not args.read:
        print(f"ERROR: Channel {args.channel} has no active thermostat.")
        sock.close()
        sys.exit(1)

    # ── Read-only mode ─────────────────────────────────────────────────────
    if args.read:
        print(f"\nCurrent setpoints (per thermostat):")
        for p_ch, g_chs in sorted(groups.items()):
            val = read_setpoint(sock, p_ch)
            chs_str = ", ".join(str(c + 1) for c in g_chs)
            val_str = f"{val:.1f} °C" if val is not None else "(no response)"
            print(f"  Channel {p_ch + 1:<3}  circuits [{chs_str}]  →  {val_str}")
        sock.close()
        return

    # ── Read current, write to entire group, confirm ───────────────────────
    group = groups[primary]
    group_str = ", ".join(str(c + 1) for c in group)
    MAX_ATTEMPTS = 4
    print(f"\nChannel {primary + 1}  (circuits: {group_str})")

    before = read_setpoint(sock, primary)
    print(f"  Current setpoint : {before:.1f} °C" if before is not None else "  Current setpoint : (no response)")
    print(f"  Target           : {args.temp:.1f} °C  (raw={int(round(args.temp * 10))})")
    print(f"  Circuits to write: {group_str}")

    confirmed = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n  [Attempt {attempt}/{MAX_ATTEMPTS}]")

        for ch in group:
            ok = write_setpoint(sock, ch, args.temp)
            print(f"    Circuit {ch + 1} write : {'OK' if ok else 'no echo'}")

        time.sleep(0.5)
        after = read_setpoint(sock, primary)
        if after is not None:
            print(f"    Readback (ch {primary + 1}): {after:.1f} °C")
        else:
            print(f"    Readback (ch {primary + 1}): (no response)")

        if after == args.temp:
            confirmed = True
            break

        if attempt < MAX_ATTEMPTS:
            print(f"    Mismatch — retrying in 1 s ...")
            time.sleep(1.0)

    print()
    if confirmed:
        print(f"  Setpoint confirmed at {args.temp:.1f} °C after {attempt} attempt(s).")
    else:
        print(f"  FAILED: could not confirm setpoint after {MAX_ATTEMPTS} attempts.")
        print(f"  Last readback: {after:.1f} °C" if after is not None else "  Last readback: (no response)")
        sys.exit(1)

    sock.close()


if __name__ == "__main__":
    main()
