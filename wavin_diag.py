"""Passive bus monitor and AHC 9000 query diagnostic."""
from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass, field

HOST = "192.168.0.7"
PORT = 502
SLAVE = 0x01
TIMEOUT = 5.0


@dataclass
class QueryFrame:
    offset: int
    cat: int
    idx: int
    page: int
    qty: int
    raw: str


@dataclass
class ResponseFrame:
    offset: int
    byte_count: int
    values: list[int] = field(default_factory=list)
    raw: str = ""


Frame = QueryFrame | ResponseFrame


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def build_query(cat: int, idx: int, page: int, qty: int) -> bytes:
    pdu = bytes([SLAVE, 0x43, cat, idx, page, qty])
    return pdu + crc16(pdu)


def parse_frames(data: bytes) -> list[Frame]:
    frames: list[Frame] = []
    i = 0
    while i < len(data) - 4:
        if data[i] == SLAVE and data[i + 1] == 0x43:
            bc = data[i + 2]
            end = i + 3 + bc + 2
            if end <= len(data):
                chunk = data[i:end]
                if crc16(chunk[:-2]) == chunk[-2:]:
                    payload = chunk[3:3 + bc]
                    n = len(payload) // 2
                    vals = list(struct.unpack(f">{n}H", payload[:n * 2])) if n else []
                    frames.append(ResponseFrame(i, bc, vals, chunk.hex()))
                    i = end
                    continue
            if i + 8 <= len(data):
                q = data[i:i + 8]
                if crc16(q[:6]) == q[6:8]:
                    frames.append(QueryFrame(i, data[i + 2], data[i + 3], data[i + 4], data[i + 5], q.hex()))
                    i += 8
                    continue
        i += 1
    return frames


def collect(sock: socket.socket, duration: float) -> bytes:
    sock.settimeout(0.1)
    data = b""
    deadline = time.time() + duration
    while time.time() < deadline:
        try:
            data += sock.recv(4096)
        except socket.timeout:
            pass
    return data


def drain(sock: socket.socket) -> None:
    sock.settimeout(0.05)
    try:
        while sock.recv(4096):
            pass
    except Exception:
        pass
    sock.settimeout(TIMEOUT)


def print_frames(frames: list[Frame]) -> None:
    for frame in frames:
        if isinstance(frame, ResponseFrame):
            print(f"  RESPONSE bc={frame.byte_count} vals={[hex(v) for v in frame.values]}  [{frame.raw}]")
        else:
            print(f"  QUERY   cat=0x{frame.cat:02x} idx=0x{frame.idx:02x} page={frame.page} qty={frame.qty}  [{frame.raw}]")


def print_temperature_frames(frames: list[Frame]) -> None:
    for frame in frames:
        if isinstance(frame, ResponseFrame):
            v = frame.values[0] if frame.values else 0
            signed = v if v < 0x8000 else v - 0x10000
            print(f"  RESPONSE bc={frame.byte_count} val={v:#06x} → {signed / 10:.1f} °C")


def run_query(sock: socket.socket, label: str, query: bytes, duration: float) -> list[Frame]:
    print("=" * 60)
    print(label)
    print("=" * 60)
    print(f"TX: {query.hex()}")
    drain(sock)
    sock.sendall(query)
    time.sleep(0.05)
    raw = collect(sock, duration)
    print(f"RX ({len(raw)} bytes): {raw.hex()}")
    return parse_frames(raw)


def main() -> None:
    print("Connecting...")
    with socket.create_connection((HOST, PORT), timeout=TIMEOUT) as sock:
        print("Connected. Initial 3-second drain...")
        initial = collect(sock, 3.0)
        print(f"Drained {len(initial)} bytes\n")

        print("=" * 60)
        print("PHASE 1 — passive listen 3 s (no queries from us)")
        print("=" * 60)
        passive = collect(sock, 3.0)
        print(f"Captured {len(passive)} bytes: {passive.hex()}")
        passive_frames = parse_frames(passive)
        print(f"Parsed {len(passive_frames)} frames:")
        print_frames(passive_frames)
        print()

        frames2 = run_query(
            sock,
            "PHASE 2 — clone AHC query: MAIN cat=0x00 idx=0 qty=10",
            build_query(0x00, 0x00, 0x00, 10),
            1.0,
        )
        print_frames(frames2)
        print()

        frames3 = run_query(
            sock,
            "PHASE 3 — our DEVICE NAME query: INFO cat=0x07 idx=0x04 qty=1",
            build_query(0x07, 0x04, 0x00, 1),
            1.0,
        )
        print_frames(frames3)
        print()

        frames4 = run_query(
            sock,
            "PHASE 4 — CH1 DESIRED TEMP: CHANNELS cat=0x03 idx=0x10 qty=1",
            build_query(0x03, 0x10, 0x00, 1),
            3.0,
        )
        print_temperature_frames(frames4)


if __name__ == "__main__":
    main()
