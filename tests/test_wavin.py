"""
Quick diagnostic script — connects to the Wavin AHC 9000 via the PUSR bridge
at 10.10.100.100:8899 and attempts to read temperature / setpoint registers.

Run with:  python test_wavin.py
"""
import socket
import struct
import time

HOST = "10.10.100.254"
PORT = 8899
SLAVE = 0x01

# -- Protocol constants --------------------------------------------------------
CMD_READ  = 0x43
CMD_WRITE = 0x44

CAT_ELEMENTS = 0x01
CAT_CHANNELS = 0x02  # per the "direkte" spec used in the integration

IDX_ELEM_AIR_TEMP   = 0x04
IDX_ELEM_FLOOR_TEMP = 0x05
IDX_CH_DESIRED_TEMP = 0x10

SENSOR_NA = 0x7FFF


# -- MBAP frame builder -------------------------------------------------------
_tid = 0

def next_tid() -> int:
    global _tid
    _tid = (_tid + 1) & 0xFFFF
    return _tid

def build_read(cat, idx, page, qty=1) -> tuple[bytes, int]:
    """Return (mbap_frame, tid)."""
    tid = next_tid()
    pdu = bytes([CMD_READ, cat, idx, page, qty])
    frame = struct.pack(">HHHB", tid, 0, 1 + len(pdu), SLAVE) + pdu
    return frame, tid


# -- Response parser ----------------------------------------------------------─
def parse_response(raw: bytes, expected_bc: int, tid: int):
    """Scan buffer for MBAP frame matching tid with correct byte count."""
    tid_bytes = struct.pack(">H", tid)
    i = 0
    while i + 9 <= len(raw):
        if raw[i:i+2] == tid_bytes and raw[i+7] == CMD_READ:
            bc = raw[i+8]
            if bc == expected_bc and len(raw) >= i + 9 + bc:
                n = bc // 2
                return list(struct.unpack(f">{n}H", raw[i+9: i+9+bc]))
        i += 1
    return None


def raw_to_temp(raw: int):
    if raw == SENSOR_NA:
        return None
    signed = raw if raw < 0x8000 else raw - 0x10000
    return round(signed / 10.0, 1)


# -- Query helper --------------------------------------------------------------
def query(sock, cat, idx, page, qty=1, timeout=1.5, label=""):
    frame, tid = build_read(cat, idx, page, qty)
    print(f"  TX {label:35s} -> {frame.hex()}  (tid={tid})")

    # pre-query drain
    sock.settimeout(0.05)
    drained = b""
    try:
        while True:
            chunk = sock.recv(512)
            if not chunk:
                break
            drained += chunk
    except socket.timeout:
        pass
    if drained:
        print(f"  drained {len(drained)} stale bytes")

    sock.settimeout(0.2)
    try:
        sock.sendall(frame)
    except OSError as e:
        print(f"  send error: {e}")
        return None

    raw = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(512)
            if chunk:
                raw += chunk
                result = parse_response(raw, qty * 2, tid)
                if result is not None:
                    print(f"  RX ({len(raw)} bytes): {raw[:32].hex()}{'...' if len(raw)>32 else ''}")
                    return result
        except socket.timeout:
            pass

    print(f"  RX timeout ({len(raw)} bytes raw: {raw[:32].hex() if raw else 'empty'})")
    return None


# -- Main ----------------------------------------------------------------------
def main():
    print(f"Connecting to {HOST}:{PORT} …")
    try:
        sock = socket.create_connection((HOST, PORT), timeout=5)
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    print("Connected. Draining initial burst (3 s) …")
    sock.settimeout(0.5)
    total_drained = 0
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            total_drained += len(chunk)
        except socket.timeout:
            pass
    print(f"Initial drain done — discarded {total_drained} bytes\n")

    for zone in range(4):  # test zones 0–3
        print(f"-- Zone {zone + 1} (page={zone}) --------------------------")

        # Air + floor temp (consecutive → qty=2)
        vals = query(sock,
                     CAT_ELEMENTS, IDX_ELEM_AIR_TEMP, page=zone, qty=2,
                     label=f"ch{zone} air+floor temp (CAT=0x01 IDX=0x04)")
        if vals:
            air   = raw_to_temp(vals[0])
            floor = raw_to_temp(vals[1])
            print(f"  air temp  = {air}  (raw 0x{vals[0]:04X})")
            print(f"  floor temp= {floor}  (raw 0x{vals[1]:04X})")
        else:
            print("  no response")

        # Desired setpoint
        vals = query(sock,
                     CAT_CHANNELS, IDX_CH_DESIRED_TEMP, page=zone, qty=1,
                     label=f"ch{zone} desired temp   (CAT=0x02 IDX=0x10)")
        if vals:
            desired = raw_to_temp(vals[0])
            print(f"  desired   = {desired}  (raw 0x{vals[0]:04X})")
        else:
            print("  no response")
        print()

    # Also try the dkjonas register addresses as a cross-check
    print("-- Cross-check: dkjonas register addresses (zone 0) --------─")
    # dkjonas: PACKED_DATA_MANUAL_TEMPERATURE = 0x00 in CAT=0x02
    vals = query(sock,
                 0x02, 0x00, page=0, qty=1,
                 label="dkjonas manual temp    (CAT=0x02 IDX=0x00)")
    if vals:
        print(f"  raw 0x{vals[0]:04X} → {raw_to_temp(vals[0])}")
    else:
        print("  no response")

    # dkjonas: CATEGORY_CHANNELS=0x03, CHANNELS_TIMER_EVENT=0x00
    vals = query(sock,
                 0x03, 0x00, page=0, qty=1,
                 label="dkjonas timer event    (CAT=0x03 IDX=0x00)")
    if vals:
        valve_on = bool(vals[0] & 0x0010)
        print(f"  raw 0x{vals[0]:04X} → valve_on={valve_on}")
    else:
        print("  no response")

    sock.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
