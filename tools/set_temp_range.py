"""
Read or change the comfort (upper) and eco (lower) temperature limits on a
Wavin AHC 9000 channel.

NOTE: The HA integration no longer uses these device-level comfort/eco limits.
Temperature control is handled exclusively through the thermostat setpoint
(CAT=0x02 IDX=0x00). This script remains available for direct device
inspection and low-level testing only.

Usage:
    python tools/set_temp_range.py --read                   # show all channels
    python tools/set_temp_range.py --comfort 24.0           # set comfort limit, channel 1
    python tools/set_temp_range.py --eco 18.0               # set eco limit, channel 1
    python tools/set_temp_range.py --comfort 24.0 --eco 18.0 --channel 2
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

CAT_PACKED        = 0x02
IDX_COMFORT_TEMP  = 0x01   # upper limit
IDX_ECO_TEMP      = 0x02   # lower limit

MAX_CHANNELS  = 16
SENSOR_NA     = 0x7FFF
ABS_MIN       = 5.0
ABS_MAX       = 35.0
MAX_ATTEMPTS  = 4


# ── MBAP helpers ──────────────────────────────────────────────────────────────

_tid = 0

def _next_tid() -> int:
    global _tid
    _tid = (_tid + 1) & 0xFFFF
    return _tid

def _build_read(idx: int, page: int, qty: int = 1) -> tuple[bytes, int]:
    tid = _next_tid()
    pdu = bytes([FC_READ, CAT_PACKED, idx, page, qty])
    frame = struct.pack(">HHHB", tid, 0, 1 + len(pdu), SLAVE) + pdu
    return frame, tid

def _build_write(idx: int, page: int, raw_val: int) -> tuple[bytes, int]:
    tid = _next_tid()
    pdu = bytes([
        FC_WRITE, CAT_PACKED, idx, page, 0x00,
        (raw_val >> 8) & 0xFF,
        raw_val & 0xFF,
    ])
    frame = struct.pack(">HHHB", tid, 0, 1 + len(pdu), SLAVE) + pdu
    return frame, tid


# ── Socket helpers ────────────────────────────────────────────────────────────

def _recv_mbap(sock: socket.socket, tid: int, expected_fc: int,
               timeout: float = 2.0) -> Optional[bytes]:
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
                            return raw[i+7:end]
                    i += 1
        except socket.timeout:
            pass
    return None

def _raw_to_temp(raw: int) -> Optional[float]:
    if raw == SENSOR_NA:
        return None
    signed = raw if raw < 0x8000 else raw - 0x10000
    return round(signed / 10.0, 1)

def read_limit(sock: socket.socket, idx: int, channel: int) -> Optional[float]:
    frame, tid = _build_read(idx, page=channel)
    sock.sendall(frame)
    payload = _recv_mbap(sock, tid, FC_READ)
    if payload is None or len(payload) < 4:
        return None
    raw = struct.unpack(">H", payload[2:4])[0]
    return _raw_to_temp(raw)

def write_limit(sock: socket.socket, idx: int, channel: int,
                temp: float) -> bool:
    raw_val = int(round(temp * 10)) & 0xFFFF
    frame, tid = _build_write(idx, page=channel, raw_val=raw_val)
    sock.sendall(frame)
    return _recv_mbap(sock, tid, FC_WRITE, timeout=2.0) is not None


# ── Write with retry ──────────────────────────────────────────────────────────

def write_with_retry(sock: socket.socket, idx: int, channel: int,
                     temp: float, label: str) -> bool:
    print(f"\n  {label}: {temp:.1f} °C  (raw={int(round(temp * 10))})")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"    [Attempt {attempt}/{MAX_ATTEMPTS}]", end=" ")
        ok = write_limit(sock, idx, channel, temp)
        print(f"echo={'OK' if ok else 'no echo'}", end="  ")

        time.sleep(0.5)
        readback = read_limit(sock, idx, channel)
        if readback is not None:
            print(f"readback={readback:.1f} °C")
        else:
            print("readback=(no response)")

        if readback == temp:
            print(f"    Confirmed after {attempt} attempt(s).")
            return True

        if attempt < MAX_ATTEMPTS:
            print(f"    Mismatch — retrying in 1 s ...")
            time.sleep(1.0)

    print(f"    FAILED after {MAX_ATTEMPTS} attempts.")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wavin AHC 9000 — set thermostat comfort/eco temperature limits"
    )
    parser.add_argument("--comfort", type=float, metavar="TEMP",
                        help=f"Comfort (upper) limit in °C ({ABS_MIN}–{ABS_MAX})")
    parser.add_argument("--eco", type=float, metavar="TEMP",
                        help=f"Eco (lower) limit in °C ({ABS_MIN}–{ABS_MAX})")
    parser.add_argument("--channel", type=int, default=1,
                        help="Channel number, 1-based (default: 1)")
    parser.add_argument("--read", action="store_true",
                        help="Show current limits for all zones, no write")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    if not args.read and args.comfort is None and args.eco is None:
        parser.error("Provide --comfort and/or --eco, or use --read")

    for name, val in [("--comfort", args.comfort), ("--eco", args.eco)]:
        if val is not None and not (ABS_MIN <= val <= ABS_MAX):
            parser.error(f"{name} must be between {ABS_MIN} and {ABS_MAX} °C")

    if args.comfort is not None and args.eco is not None:
        if args.eco >= args.comfort:
            parser.error("--eco must be lower than --comfort")

    channel = args.channel - 1

    print(f"Connecting to {args.host}:{args.port} ...")
    try:
        sock = socket.create_connection((args.host, args.port), timeout=5.0)
    except OSError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # ── Read-only mode ────────────────────────────────────────────────────
    if args.read:
        print(f"\n{'Channel':<10} {'Eco (low)':>12}  {'Comfort (high)':>14}")
        print("-" * 40)
        for ch in range(MAX_CHANNELS):
            eco     = read_limit(sock, IDX_ECO_TEMP,     ch)
            comfort = read_limit(sock, IDX_COMFORT_TEMP, ch)
            if eco is not None or comfort is not None:
                eco_s     = f"{eco:.1f} °C"     if eco     is not None else "N/A"
                comfort_s = f"{comfort:.1f} °C" if comfort is not None else "N/A"
                print(f"Channel {ch + 1:<3}  {eco_s:>12}  {comfort_s:>14}")
        sock.close()
        return

    # ── Write mode ────────────────────────────────────────────────────────
    print(f"\nChannel {args.channel} (0-based index {channel})")

    comfort_now = read_limit(sock, IDX_COMFORT_TEMP, channel)
    eco_now     = read_limit(sock, IDX_ECO_TEMP,     channel)
    print(f"  Current comfort  : {comfort_now:.1f} °C" if comfort_now is not None else "  Current comfort  : (no response)")
    print(f"  Current eco      : {eco_now:.1f} °C"     if eco_now     is not None else "  Current eco      : (no response)")

    failed = False

    if args.comfort is not None:
        if not write_with_retry(sock, IDX_COMFORT_TEMP, channel, args.comfort, "Setting comfort"):
            failed = True

    if args.eco is not None:
        if not write_with_retry(sock, IDX_ECO_TEMP, channel, args.eco, "Setting eco"):
            failed = True

    sock.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
