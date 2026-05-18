import socket
import struct
import time

HOST    = "192.168.0.7"
PORT    = 502
SLAVE   = 0x01
TIMEOUT = 3.0

def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack('<H', crc)

def build_read(slave, category, index, page, qty):
    pdu = bytes([slave, 0x43, category, index, page, qty])
    return pdu + crc16(pdu)

def connect():
    """Connect and do a 3-second initial drain."""
    sock = socket.create_connection((HOST, PORT), timeout=TIMEOUT)
    sock.settimeout(0.5)
    buf = b''
    remote_closed = False
    deadline = time.time() + 3.0
    try:
        while time.time() < deadline:
            chunk = sock.recv(4096)
            if not chunk:
                remote_closed = True
                break
            buf += chunk
    except socket.timeout:
        pass
    except Exception:
        remote_closed = True

    if buf:
        print(f"  drained {len(buf)} stale bytes")
    else:
        print("  buffer was clean")

    if remote_closed:
        print("  remote closed during drain — reconnecting...")
        sock.close()
        time.sleep(1.0)
        sock = socket.create_connection((HOST, PORT), timeout=TIMEOUT)

    sock.settimeout(TIMEOUT)
    return sock

def query(sock, category, index, page, qty):
    """Drain bus noise, send query, read response in a tight 800ms window."""
    expected_bc = qty * 2
    frame = build_read(SLAVE, category, index, page, qty)

    # Drain any accumulated AHC bus traffic before sending
    sock.settimeout(0.05)
    try:
        while True:
            d = sock.recv(4096)
            if not d:
                break
    except Exception:
        pass

    # Send our query
    sock.settimeout(TIMEOUT)
    sock.sendall(frame)

    # Read response in tight window
    raw = b''
    deadline = time.time() + 0.8
    sock.settimeout(0.2)
    while time.time() < deadline:
        try:
            chunk = sock.recv(512)
            if chunk:
                raw += chunk
                for i in range(len(raw) - 4):
                    if raw[i] == SLAVE and raw[i+1] == 0x43:
                        bc = raw[i+2]
                        if bc != expected_bc:
                            continue
                        end = i + 3 + bc + 2
                        if end <= len(raw):
                            f = raw[i:end]
                            if crc16(f[:-2]) == f[-2:]:
                                payload = f[3:3+bc]
                                n = len(payload) // 2
                                vals = list(struct.unpack(f'>{n}H', payload[:n*2])) if n else []
                                return vals, raw, None
        except socket.timeout:
            break

    return None, raw, "timeout — no valid response"

QUERIES = [
    # (label,                  cat,  idx,  page, qty,  interpret)
    ("DEVICE NAME",            0x07, 0x04, 0x00,  1,   "raw"),
    ("HW VERSION",             0x07, 0x02, 0x00,  1,   "raw"),
    ("SW VERSION",             0x07, 0x03, 0x00,  1,   "raw"),
    ("STATUS L",               0x00, 0x08, 0x00,  1,   "hex"),
    ("CPU TEMPERATURE",        0x00, 0x12, 0x00,  1,   "temp"),
    ("INPUT VOLTAGE",          0x00, 0x13, 0x00,  1,   "raw"),
    ("ELEMENT 1 AIR TEMP",     0x01, 0x04, 0x00,  1,   "temp"),
    ("ELEMENT 1 FLOOR TEMP",   0x01, 0x05, 0x00,  1,   "temp"),
    ("ELEMENT 1 STATUS",       0x01, 0x08, 0x00,  1,   "hex"),
    ("CH1 DESIRED TEMP",       0x02, 0x10, 0x00,  1,   "temp"),
    ("CH1 COMFORT TEMP",       0x02, 0x01, 0x00,  1,   "temp"),
    ("CH1 ECO TEMP",           0x02, 0x02, 0x00,  1,   "temp"),
]

def interpret(vals, mode):
    if not vals:
        return "(empty)"
    v = vals[0]
    if mode == "temp":
        if v == 0x7FFF:
            return "N/A (sensor not present)"
        signed = v if v < 0x8000 else v - 0x10000
        return f"{signed / 10.0:.1f} °C  (raw={v:#06x})"
    elif mode == "hex":
        return f"0x{v:04X}  (bin={v:016b})"
    else:
        return f"{v}  (0x{v:04X})"

print("=" * 60)
print(f"Wavin AHC 9000 — {HOST}:{PORT}  slave=0x{SLAVE:02X}")
print("=" * 60)

try:
    print("Connecting and draining buffer...")
    sock = connect()
    print()

    for label, cat, idx, page, qty, mode in QUERIES:
        print(f"[{label}]")
        vals, raw_bytes, err = query(sock, cat, idx, page, qty)
        print(f"  rx hex: {raw_bytes.hex() if raw_bytes else '(nothing)'}")
        if err:
            print(f"  ERROR: {err}")
        else:
            print(f"  VALUE: {interpret(vals, mode)}")
        print()
        time.sleep(0.15)

    sock.close()

except ConnectionRefusedError:
    print(f"ERROR: Port {PORT} refused — reboot USR device")
except socket.timeout:
    print(f"ERROR: TCP connect timeout")
except Exception as e:
    print(f"ERROR: {e}")
