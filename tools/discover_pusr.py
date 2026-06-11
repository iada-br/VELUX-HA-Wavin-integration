#!/usr/bin/env python3
"""
Discover the PUSR/USR-TCP232 gateway on the local network and optionally
update DEFAULT_HOST in custom_components/wavin_ahc9000/const.py.

Strategy
--------
1. Detect the local subnet from the active network interface.
2. Scan all hosts in that subnet for an open port 8899 (the Modbus TCP
   gateway port used by USR-IOT devices).
3. For each candidate, attempt a minimal Modbus connection to confirm the
   device responds like a Wavin gateway.
4. Print the result and — unless --dry-run — patch DEFAULT_HOST in const.py.

Usage
-----
    python tools/discover_pusr.py                  # auto-detect subnet, patch const.py
    python tools/discover_pusr.py --subnet 192.168.1.0/24
    python tools/discover_pusr.py --dry-run        # print only, no file changes
    python tools/discover_pusr.py --port 8899      # override gateway port
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import socket
import subprocess
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_GATEWAY_PORT = 8899
CONNECT_TIMEOUT = 1.0       # seconds per host probe
MAX_WORKERS = 64            # parallel socket probes
MODBUS_PROBE_TIMEOUT = 2.0  # seconds for the Modbus handshake check

CONST_PY = Path(__file__).parent.parent / "custom_components" / "wavin_ahc9000" / "const.py"


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def _is_wsl2() -> bool:
    """Return True when running inside WSL2."""
    try:
        data = Path("/proc/version").read_text().lower()
        return "microsoft" in data or "wsl" in data
    except OSError:
        return False


def _subnet_from_windows() -> str | None:
    """Ask Windows (via powershell.exe) for the active WiFi/Ethernet IPv4 address.

    Returns a /24 CIDR string like '192.168.1.0/24', or None on failure.
    Prefers the Wi-Fi adapter; skips virtual adapters (vEthernet, VirtualBox,
    Bluetooth) and the WSL2 bridge (172.x / 169.x).
    """
    ps_cmd = (
        "Get-NetIPAddress -AddressFamily IPv4 | "
        "Select-Object InterfaceAlias, IPAddress, PrefixOrigin | "
        "ConvertTo-Csv -NoTypeInformation"
    )
    try:
        out = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, OSError):
        return None

    # Parse CSV: "InterfaceAlias","IPAddress","PrefixOrigin"
    candidates: list[tuple[int, str]] = []  # (priority, subnet)
    for line in out.splitlines():
        line = line.strip().strip('"')
        if not line or line.startswith("InterfaceAlias"):
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        alias, ip = parts[0], parts[1]

        try:
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if addr.is_loopback or addr.is_link_local:
            continue
        if str(addr).startswith(("172.", "169.")):
            continue

        alias_lower = alias.lower()
        # Skip known virtual adapters
        if any(v in alias_lower for v in ("vethernet", "virtualbox", "vmware",
                                           "bluetooth", "loopback", "isatap")):
            continue

        # Priority: Wi-Fi first, then wired Ethernet, then anything else
        if "wi-fi" in alias_lower or "wlan" in alias_lower or "wireless" in alias_lower:
            priority = 0
        elif "ethernet" in alias_lower:
            priority = 1
        else:
            priority = 2

        net = ipaddress.IPv4Network(f"{ip}/24", strict=False)
        candidates.append((priority, str(net)))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
    return None


def detect_subnet() -> str:
    """Return the local subnet in CIDR notation (e.g. '192.168.1.0/24').

    On WSL2, queries Windows for the physical WiFi IP to avoid detecting
    the internal 172.x bridge instead of the real LAN.
    Falls back to Linux routing table and UDP socket tricks.
    """
    # WSL2: ask Windows for the real physical network
    if _is_wsl2():
        subnet = _subnet_from_windows()
        if subnet:
            return subnet

    # Strategy 1: parse `ip route` for non-default, non-172 routes
    try:
        out = subprocess.check_output(["ip", "route", "show"], text=True)
        for line in out.splitlines():
            if "default" in line:
                continue
            parts = line.split()
            if parts:
                try:
                    net = ipaddress.IPv4Network(parts[0], strict=False)
                    if (not net.is_loopback and not net.is_link_local
                            and not str(net).startswith("172.")):
                        return str(net)
                except ValueError:
                    continue
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    # Strategy 2: UDP connect trick to find the outbound IP, assume /24
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        net = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        return str(net)
    except OSError:
        pass

    raise RuntimeError(
        "Could not detect local subnet automatically. "
        "Use --subnet 192.168.x.x/24 to specify it manually."
    )


# ---------------------------------------------------------------------------
# Port probe
# ---------------------------------------------------------------------------

def probe_port(host: str, port: int) -> str | None:
    """Return host if port is open, else None."""
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
            return host
    except (OSError, ConnectionRefusedError):
        return None


def scan_subnet(subnet: str, port: int) -> list[str]:
    """Return all hosts in subnet that have port open."""
    network = ipaddress.IPv4Network(subnet, strict=False)
    hosts = list(network.hosts())
    print(f"[*] Scanning {len(hosts)} hosts in {subnet} for open port {port} …")

    found: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(probe_port, str(h), port): str(h) for h in hosts}
        done = 0
        for fut in as_completed(futures):
            done += 1
            result = fut.result()
            if result:
                found.append(result)
            # Simple progress every 64 hosts
            if done % 64 == 0:
                pct = done * 100 // len(hosts)
                print(f"    {done}/{len(hosts)} ({pct}%) …", end="\r", flush=True)

    print()  # clear progress line
    return found


# ---------------------------------------------------------------------------
# Modbus verify
# ---------------------------------------------------------------------------

def _build_wavin_probe() -> bytes:
    """Build a Wavin Read-by-Index request (FC 0x43) for device info.

    Reads 1 register from category 0x07 (INFO), index 0x02 (HW version),
    page 0x00.  The Wavin AHC 9000 gateway responds to this even when it
    ignores standard FC 0x03 requests.

    Modbus TCP MBAP header (6 bytes) + PDU (5 bytes):
        [TID:2][PID:2][LEN:2][UNIT:1][FC:1][CAT:1][IDX:1][PAGE:1][QTY:1]
    """
    tid    = (0x00, 0x01)
    pid    = (0x00, 0x00)
    length = (0x00, 0x06)   # remaining bytes: unit(1) + PDU(5)
    unit   = 0x01
    fc     = 0x43           # Wavin Read by Index
    cat    = 0x07           # CAT_INFO
    idx    = 0x02           # IDX_INFO_HW_VER
    page   = 0x00
    qty    = 0x01
    return bytes([*tid, *pid, *length, unit, fc, cat, idx, page, qty])


def verify_modbus(host: str, port: int) -> bool:
    """Return True if host:port responds to a Wavin Modbus TCP probe."""
    try:
        with socket.create_connection((host, port), timeout=MODBUS_PROBE_TIMEOUT) as s:
            s.sendall(_build_wavin_probe())
            s.settimeout(MODBUS_PROBE_TIMEOUT)
            data = s.recv(256)
            # Any response ≥ 6 bytes with matching transaction id is valid
            return len(data) >= 6 and data[0:2] == b"\x00\x01"
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Hostname helper
# ---------------------------------------------------------------------------

def reverse_lookup(ip: str) -> str:
    """Return hostname for ip, or empty string on failure."""
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# const.py patcher
# ---------------------------------------------------------------------------

def patch_default_host(ip: str, dry_run: bool) -> None:
    """Replace DEFAULT_HOST in const.py with ip."""
    if not CONST_PY.exists():
        print(f"[!] const.py not found at {CONST_PY} — skipping patch.")
        return

    original = CONST_PY.read_text()
    patched = re.sub(
        r'(DEFAULT_HOST\s*=\s*")[^"]+(")',
        rf'\g<1>{ip}\2',
        original,
    )

    if patched == original:
        current = re.search(r'DEFAULT_HOST\s*=\s*"([^"]+)"', original)
        current_val = current.group(1) if current else "unknown"
        if current_val == ip:
            print(f"[=] DEFAULT_HOST in const.py is already {ip} — no change needed.")
        else:
            print(f"[!] Regex did not match DEFAULT_HOST in const.py (current: {current_val}).")
        return

    if dry_run:
        print(f"[dry-run] Would update DEFAULT_HOST → {ip} in {CONST_PY}")
        return

    CONST_PY.write_text(patched)
    print(f"[✓] Updated DEFAULT_HOST → {ip} in {CONST_PY}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover the PUSR/USR-TCP232 gateway and patch const.py."
    )
    parser.add_argument(
        "--subnet",
        default=None,
        help="Network to scan, e.g. 192.168.0.0/24 (auto-detected if omitted).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_GATEWAY_PORT,
        help=f"Gateway TCP port to scan for (default: {DEFAULT_GATEWAY_PORT}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the discovered IP but do not modify const.py.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip Modbus handshake verification (accept any host with port open).",
    )
    args = parser.parse_args()

    # 1. Detect subnet
    subnet = args.subnet
    if subnet is None:
        try:
            subnet = detect_subnet()
            print(f"[*] Auto-detected subnet: {subnet}")
        except RuntimeError as exc:
            print(f"[!] {exc}")
            sys.exit(1)

    # 2. Scan
    candidates = scan_subnet(subnet, args.port)

    if not candidates:
        print(f"[!] No hosts found with port {args.port} open on {subnet}.")
        print("    Tip: confirm the PUSR is powered on and joined to this network,")
        print("    or use --subnet to specify the correct range.")
        sys.exit(1)

    print(f"[*] {len(candidates)} host(s) with port {args.port} open: {', '.join(candidates)}")

    # 3. Verify / pick best candidate
    gateway_ip: str | None = None

    for ip in candidates:
        hostname = reverse_lookup(ip)
        label = f"{ip} ({hostname})" if hostname else ip
        is_usr = hostname.upper().startswith("USR")

        if args.no_verify or is_usr:
            # USR-named hosts are unambiguously PUSR devices — skip Modbus probe
            ok = True
            if is_usr:
                print(f"[*] {label} — USR hostname confirmed ✓")
        else:
            print(f"[*] Verifying Modbus TCP on {label} …", end=" ", flush=True)
            ok = verify_modbus(ip, args.port)
            print("OK ✓" if ok else "no response")

        if ok:
            if gateway_ip is None or is_usr:
                gateway_ip = ip

    if gateway_ip is None:
        print("[!] None of the candidates responded to Modbus TCP.")
        print("    Use --no-verify to accept the first host with the port open.")
        sys.exit(1)

    hostname = reverse_lookup(gateway_ip)
    label = f"{gateway_ip} ({hostname})" if hostname else gateway_ip
    print(f"\n[✓] PUSR gateway found: {label}")

    # 4. Patch const.py
    patch_default_host(gateway_ip, dry_run=args.dry_run)

    print(f"\n    You can now connect to the gateway at {gateway_ip}:{args.port}")


if __name__ == "__main__":
    main()
