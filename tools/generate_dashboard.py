#!/usr/bin/env python3
"""
generate_dashboard.py
─────────────────────
Reads the Wavin AHC 9000 config entry and entity registry from HA via Samba,
generates a Lovelace dashboard, and deploys it directly to HA storage so the
dashboard appears in the sidebar without any manual paste step.

Usage:
    python3 tools/generate_dashboard.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# ── HA connection ─────────────────────────────────────────────────────────────
HA_HOST = "192.168.1.72"
HA_USER = "homeassistant"
HA_PASS = "1234"
DOMAIN  = "wavin_ahc9000"

# ── Dashboard identity ────────────────────────────────────────────────────────
DASHBOARD_URL_PATH = "wavin-heating"   # URL shown in the browser
DASHBOARD_ID       = "wavin-heating"   # key used in lovelace_dashboards items
DASHBOARD_STORAGE_KEY = f"lovelace.{DASHBOARD_URL_PATH}"
DASHBOARD_TITLE    = "Underfloor Heating"
DASHBOARD_ICON     = "mdi:radiator"


# ── Samba helpers ─────────────────────────────────────────────────────────────

def _run_ps(ps_script: str) -> str:
    """Write a PS script to a temp file and execute it; return stdout."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as f:
        f.write(ps_script)
        ps_path = f.name

    win_ps = subprocess.run(
        ["wslpath", "-w", ps_path], capture_output=True, text=True
    ).stdout.strip()

    result = subprocess.run(
        ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", win_ps],
        capture_output=True, text=True,
    )
    os.unlink(ps_path)

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "PowerShell error")
    return result.stdout


def _samba_mount_block(drive: str = "WADASH") -> str:
    return f"""
$pass = ConvertTo-SecureString '{HA_PASS}' -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential('{HA_USER}', $pass)
if (Get-PSDrive -Name {drive} -ErrorAction SilentlyContinue) {{ Remove-PSDrive -Name {drive} }}
New-PSDrive -Name {drive} -PSProvider FileSystem -Root '\\\\{HA_HOST}\\config' -Credential $cred | Out-Null
"""


def samba_read(remote_path: str) -> str:
    """Read a UTF-8 text file from the HA Samba share.

    Copies the remote file to a local temp path so Python can read it
    directly — avoids PowerShell stdout encoding issues with UTF-8 content.
    """
    rp = remote_path.replace("/", "\\")
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    win_tmp = subprocess.run(
        ["wslpath", "-w", tmp.name], capture_output=True, text=True
    ).stdout.strip()

    ps = _samba_mount_block() + f"""
Copy-Item -Path 'WADASH:\\{rp}' -Destination '{win_tmp}' -Force
Remove-PSDrive -Name WADASH
"""
    _run_ps(ps)
    content = Path(tmp.name).read_text(encoding="utf-8-sig")  # strips BOM if present
    os.unlink(tmp.name)
    return content


def samba_write(remote_path: str, content: str) -> None:
    """Write text content to a file on the HA Samba share."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tmp", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp_path = f.name

    win_tmp = subprocess.run(
        ["wslpath", "-w", tmp_path], capture_output=True, text=True
    ).stdout.strip()

    rp = remote_path.replace("/", "\\")
    ps = _samba_mount_block() + f"""
$dst = 'WADASH:\\{rp}'
$dir = Split-Path -Parent $dst
if (-not (Test-Path $dir)) {{ New-Item -ItemType Directory -Path $dir | Out-Null }}
Copy-Item -Path '{win_tmp}' -Destination $dst -Force
Remove-PSDrive -Name WADASH
"""
    _run_ps(ps)
    os.unlink(tmp_path)


# ── Config entry / entity registry readers ───────────────────────────────────

def get_wavin_entry() -> dict:
    """Return the Wavin AHC 9000 config entry dict."""
    raw = samba_read(".storage/core.config_entries")
    data = json.loads(raw)
    for entry in data["data"]["entries"]:
        if entry["domain"] == DOMAIN:
            return entry
    raise RuntimeError("Wavin AHC 9000 config entry not found in HA storage.")


def get_wavin_entities(entry_id: str) -> dict[str, str]:
    """Return a mapping of unique_id → entity_id for all Wavin entities."""
    raw = samba_read(".storage/core.entity_registry")
    data = json.loads(raw)
    mapping: dict[str, str] = {}
    for ent in data["data"]["entities"]:
        if ent.get("config_entry_id") == entry_id:
            uid = ent.get("unique_id", "")
            eid = ent.get("entity_id", "")
            if uid and eid:
                mapping[uid] = eid
    return mapping


# ── Dashboard generator ───────────────────────────────────────────────────────

def _entity(uid_map: dict[str, str], entry_id: str, suffix: str) -> str | None:
    """Resolve entity_id from the unique_id pattern used by the integration."""
    return uid_map.get(f"{entry_id}_{suffix}")


def build_dashboard(entry: dict, uid_map: dict[str, str]) -> dict:
    """Build the Lovelace dashboard config dict."""
    entry_id = entry["entry_id"]
    raw_groups: dict = entry["data"].get("thermostat_groups", {})
    channel_names: dict[str, str] = {
        **entry["data"].get("channel_names", {}),
        **entry.get("options", {}).get("channel_names", {}),
    }

    if not raw_groups:
        raise RuntimeError(
            "No thermostat_groups in config entry — restart HA once so "
            "auto-discovery can run, then re-run this script."
        )

    thermostat_cards = []
    for primary_ch_str in sorted(raw_groups, key=lambda k: int(k)):
        primary_ch = int(primary_ch_str)
        zone_name = channel_names.get(primary_ch_str, f"Zone {primary_ch + 1}")

        # Resolve actual entity IDs from the HA entity registry.
        climate_id  = _entity(uid_map, entry_id, f"climate_ch{primary_ch}")
        valve_id    = _entity(uid_map, entry_id, f"valve_switch_ch{primary_ch}")
        air_id      = _entity(uid_map, entry_id, f"sensor_ch{primary_ch}_air_temp")
        comfort_id  = _entity(uid_map, entry_id, f"number_ch{primary_ch}_comfort_temp")
        eco_id      = _entity(uid_map, entry_id, f"number_ch{primary_ch}_eco_temp")

        if not climate_id:
            print(f"  ⚠  No climate entity found for channel {primary_ch} — skipping.")
            continue

        entity_rows = []
        if valve_id:
            entity_rows.append({"entity": valve_id, "name": "Heating active", "icon": "mdi:radiator"})
        if air_id:
            entity_rows.append({"entity": air_id, "name": "Air temperature"})
        if comfort_id:
            entity_rows.append({"entity": comfort_id, "name": "Comfort limit"})
        if eco_id:
            entity_rows.append({"entity": eco_id, "name": "Eco limit"})

        card: dict = {
            "type": "vertical-stack",
            "cards": [
                {"type": "thermostat", "entity": climate_id, "min_temp": 0, "max_temp": 100},
            ],
        }
        if entity_rows:
            card["cards"].append({"type": "entities", "entities": entity_rows})

        thermostat_cards.append(card)

    if not thermostat_cards:
        raise RuntimeError("No thermostat cards could be generated — check entity registry.")

    # Masonry view — HA auto-arranges cards into responsive columns.
    # Works for any number of thermostats without a fixed horizontal-stack.
    return {
        "title": DASHBOARD_TITLE,
        "views": [
            {
                "title": "Heating",
                "path": "heating",
                "icon": DASHBOARD_ICON,
                "cards": thermostat_cards,
            }
        ],
    }


# ── HA storage writers ────────────────────────────────────────────────────────

def deploy_lovelace_config(dashboard_config: dict) -> None:
    """Write the dashboard config to HA's Lovelace storage."""
    storage = {
        "version": 1,
        "minor_version": 1,
        "key": DASHBOARD_STORAGE_KEY,
        "data": {"config": dashboard_config},
    }
    samba_write(
        f".storage/{DASHBOARD_STORAGE_KEY}",
        json.dumps(storage, indent=2),
    )
    print(f"  ✓  Lovelace config written → .storage/{DASHBOARD_STORAGE_KEY}")


def register_dashboard() -> None:
    """Add the Wavin dashboard to HA's dashboard registry if not already present."""
    raw = samba_read(".storage/lovelace_dashboards")
    registry = json.loads(raw)

    items: list[dict] = registry["data"].get("items", [])
    existing_ids = {item["id"] for item in items}

    if DASHBOARD_ID not in existing_ids:
        items.append({
            "id": DASHBOARD_ID,
            "title": DASHBOARD_TITLE,
            "icon": DASHBOARD_ICON,
            "url_path": DASHBOARD_URL_PATH,
            "require_admin": False,
            "mode": "storage",
            "show_in_sidebar": True,
        })
        registry["data"]["items"] = items
        samba_write(".storage/lovelace_dashboards", json.dumps(registry, indent=2))
        print("  ✓  Dashboard registered in sidebar")
    else:
        print("  ✓  Dashboard already registered — config updated in place")


def save_local_yaml(dashboard_config: dict, path: Path) -> None:
    """Save a human-readable YAML copy of the dashboard locally."""
    def _yaml_val(v: object, indent: int) -> str:
        pad = "  " * indent
        if isinstance(v, dict):
            lines = ["{"]
            for k, val in v.items():
                lines.append(f"{pad}  {k}: {_yaml_val(val, indent + 1)}")
            lines.append(f"{pad}}}")
            return "\n".join(lines)
        if isinstance(v, list):
            if not v:
                return "[]"
            lines = []
            for item in v:
                lines.append(f"{pad}- {_yaml_val(item, indent + 1).lstrip()}")
            return "\n" + "\n".join(lines)
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        return f'"{v}"' if (" " in str(v) or ":" in str(v) or not str(v)) else str(v)

    # Use a simple recursive YAML dumper
    try:
        import yaml  # type: ignore
        content = yaml.dump(dashboard_config, default_flow_style=False, allow_unicode=True)
    except ImportError:
        # Fallback: write as JSON (valid YAML superset)
        content = json.dumps(dashboard_config, indent=2)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  ✓  Local copy saved → {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Reading Wavin config entry from HA...")
    entry = get_wavin_entry()
    entry_id = entry["entry_id"]
    print(f"  entry_id: {entry_id}")

    print("Reading entity registry from HA...")
    uid_map = get_wavin_entities(entry_id)
    print(f"  {len(uid_map)} Wavin entities found")

    print("Building dashboard...")
    dashboard = build_dashboard(entry, uid_map)
    n = len(dashboard["views"][0]["cards"])
    print(f"  {n} thermostat(s) → {n} card(s) generated")

    print("Deploying to HA...")
    deploy_lovelace_config(dashboard)
    register_dashboard()

    repo_root = Path(__file__).parent.parent
    save_local_yaml(dashboard, repo_root / "dashboard" / "wavin_heating.yaml")

    print()
    print("Done. Refresh your browser — the 'Underfloor Heating' dashboard")
    print(f"should appear in the HA sidebar at /{DASHBOARD_URL_PATH}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
