# Wavin AHC 9000 — Home Assistant Integration

A Home Assistant custom component for controlling **Wavin AHC 9000** underfloor heating systems via a **USR-TCP232 Modbus TCP gateway**. Supports per-zone temperature control, real-time sensor readings, and valve status monitoring.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Repository Structure](#repository-structure)
3. [Home Assistant Integration](#home-assistant-integration)
   - [Entities Created Per Zone](#entities-created-per-zone)
   - [Services](#services)
   - [Configuration Flow](#configuration-flow)
   - [Data Coordinator](#data-coordinator)
4. [Protocol Details](#protocol-details)
   - [Modbus TCP Framing](#modbus-tcp-framing)
   - [Register Categories](#register-categories)
   - [Per-Zone Read Sequence](#per-zone-read-sequence)
   - [Temperature Encoding](#temperature-encoding)
5. [Client Libraries](#client-libraries)
6. [Tools](#tools)
7. [Tests](#tests)
8. [Installation](#installation)
9. [Hardware Setup](#hardware-setup)
10. [Development](#development)

---

## Architecture Overview

```
Home Assistant
     │
     │  (async polling, 30 s default)
     ▼
WavinCoordinator
     │
     │  (executor thread, blocking I/O)
     ▼
WavinClient  ──TCP──►  USR-TCP232-306 Gateway  ──RS-485──►  Wavin AHC 9000
             port 8899    (192.168.x.x)                      (slave 0x01)
```

The controller does not expose a standard Modbus TCP port; instead, a **USR-TCP232-306** serial-to-Ethernet bridge wraps the RS-485 bus. The integration speaks directly to the gateway using a **custom Modbus dialect** with function codes `0x43` (read) and `0x44` (write).

---

## Repository Structure

```
VELUX/
├── custom_components/
│   └── wavin_ahc9000/          # Home Assistant custom integration
│       ├── __init__.py         # Entry-point: setup, unload, service registration
│       ├── manifest.json       # HA metadata (domain, version, iot_class)
│       ├── const.py            # Protocol constants, register indices, helper fns
│       ├── client.py           # Modbus TCP client (socket I/O, thread-safe)
│       ├── coordinator.py      # DataUpdateCoordinator — polls all zones
│       ├── config_flow.py      # UI config wizard (host/port/zones/names)
│       ├── climate.py          # Climate entity per zone (thermostat)
│       ├── sensor.py           # Temperature sensor entities (air + floor)
│       ├── binary_sensor.py    # Valve/output status binary sensor per zone
│       ├── strings.json        # Config-flow UI labels and error strings
│       └── translations/
│           └── en.json         # English translations (mirrors strings.json)
│
├── clients/                    # Standalone client library implementations
│   ├── wavin_client.py         # HTTP REST client (alternative backend)
│   └── wavin_modbus_client.py  # Modbus RTU serial client (RS-485 direct)
│
├── tools/                      # Diagnostic and gateway utilities
│   ├── wavin_test.py           # Quick device query at 10.10.100.254:8899
│   ├── wavin_diag.py           # Passive bus monitor / protocol analyzer
│   ├── analyze_response.py     # Frame parser and temperature interpreter
│   ├── diagnose_device.py      # Serial port + slave-address discovery
│   └── gateway_config.py      # USR-TCP232-306 gateway configuration probe
│
├── tests/                      # All test suites
│   ├── test_wavin_client.py         # Unit tests — HTTP client (26 tests)
│   ├── test_wavin_modbus_client.py  # Unit tests — Modbus RTU client (39 tests)
│   ├── test_device.py               # Real device test harness (HTTP path)
│   ├── test_wavin_real_device.py    # Real device tests — Modbus RTU (COM3)
│   ├── test_wavin.py                # Integration smoke test via TCP
│   └── test_http_client.py          # HTTP connectivity test
│
├── docs/
│   └── TEST_REPORT.md          # Test execution report and gateway findings
│
├── README.md                   # This file
└── .gitignore
```

---

## Home Assistant Integration

Located in `custom_components/wavin_ahc9000/`.

### Entities Created Per Zone

For each configured zone (up to 10), the integration creates **four entities**:

| Entity Type | Name | Description | Default Enabled |
|-------------|------|-------------|-----------------|
| `climate` | `Zone N` | Thermostat — current + target temp, HEAT mode | Yes |
| `sensor` | `Zone N Air Temperature` | Air temperature from room sensor (°C) | Yes |
| `sensor` | `Zone N Floor Temperature` | Floor probe temperature (°C) | No |
| `binary_sensor` | `Zone N Valve` | `on` when heating output is active | Yes |

The floor temperature sensor is **disabled by default** because the physical floor probe is optional hardware.

### Services

Two domain-level services are registered at startup:

#### `wavin_ahc9000.set_temperature`

Set the manual setpoint for a zone identified either by display name or channel number.

```yaml
service: wavin_ahc9000.set_temperature
data:
  zone_name: "Living Room"   # OR use channel: 0
  temperature: 22.0
```

| Field | Type | Description |
|-------|------|-------------|
| `zone_name` | string | User-configured zone display name |
| `channel` | int | Zero-based channel index (0–9) |
| `temperature` | float | Target temperature in °C (5.0–35.0) |

#### `wavin_ahc9000.get_channel_info`

Fires a `wavin_ahc9000_channel_info` event on the HA event bus with a snapshot of all zones. Useful for automations that need to read raw zone data.

```yaml
service: wavin_ahc9000.get_channel_info
data: {} #todo: show a success example
```

The resulting event payload contains the full flat-dict from the coordinator (see [Data Coordinator](#data-coordinator)).

### Configuration Flow

Implemented in `config_flow.py`. Two-step UI wizard:

**Step 1 — Connection** (`async_step_user`):

| Field | Default | Description |
|-------|---------|-------------|
| Host | `10.10.100.254` | IP address of USR gateway |
| Port | `8899` | TCP port on gateway |
| Slave ID | `1` | Modbus slave address of AHC 9000 |
| Number of zones | `4` | How many zones to poll (1–16) |
| Poll interval | `30 s` | How often to read all zones |

A live connection test is made before saving; if it fails, the user sees an error and can correct the settings.

**Step 2 — Options** (`WavinOptionsFlow`, two sub-steps):

1. Poll interval adjustment (without re-entering connection details).
2. Per-zone naming — one text field per zone, defaulting to `Zone N`.

### Data Coordinator

`coordinator.py` — `WavinCoordinator(DataUpdateCoordinator[dict[str, Any]])`

Polls the device on a fixed interval and stores all zone data in a single flat dict keyed by `ch{N}_{field}`:

```python
{
    "ch0_air_temp":    22.4,   # float °C, or None if sensor absent
    "ch0_floor_temp":  None,   # float °C, or None
    "ch0_desired_temp": 21.0,  # float °C
    "ch0_valve_open":  True,   # bool
    "ch0_tp_lost":     False,  # bool — True when thermostat signal lost
    "ch1_air_temp":    ...
}
```

All entities share this dict via the coordinator; no entity makes its own network calls. Writes (e.g., `async_set_temperature`) go through the coordinator, which calls the client then immediately triggers a refresh.

---

## Protocol Details

### Modbus TCP Framing

The Wavin AHC 9000 uses a **custom Modbus dialect**, not standard Modbus FC 03/06. Frames follow the standard MBAP header but with proprietary function codes.

**MBAP Header (7 bytes):**

| Bytes | Field | Value |
|-------|-------|-------|
| 0–1 | Transaction ID | Increments per request |
| 2–3 | Protocol ID | `0x0000` (standard) |
| 4–5 | Length | Number of bytes that follow |
| 6 | Unit ID | Slave address (default `0x01`) |

**Read PDU (FC `0x43`):**

```
[0x43][category][index][page][qty]
```

**Write PDU (FC `0x44`):**

```
[0x44][category][index][page][0x00][value_hi][value_lo]
```

The device echoes the full write frame back as confirmation; the client validates this echo before returning.

### Register Categories

| Code | Name | Purpose | Page field |
|------|------|---------|------------|
| `0x01` | `CAT_ELEMENTS` | Physical sensor readings | Element index (NOT zone) |
| `0x02` | `CAT_PACKED` | Per-zone setpoints (manual/comfort/eco) | Zone index (0-based) |
| `0x03` | `CAT_CHANNELS` | Per-zone valve status and control | Zone index (0-based) |
| `0x07` | `CAT_INFO` | Device identification (HW/SW version) | `0x00` |

### Per-Zone Read Sequence

Each poll cycle reads **4 registers per zone** in this order:

```
1. CAT_CHANNELS / IDX_CH_PRIMARY_ELEMENT  (cat=0x03, idx=0x02, page=zone)
      → bits[5:0]  = element_index  (pointer into CAT_ELEMENTS)
      → bit[10]    = all_tp_lost    (all thermostats offline)

2. CAT_CHANNELS / IDX_CH_TIMER_EVENT      (cat=0x03, idx=0x00, page=zone)
      → bit[4]     = valve/output on

3. CAT_PACKED   / IDX_CH_MANUAL_TEMP      (cat=0x02, idx=0x00, page=zone)
      → active setpoint (R/W)

4. CAT_ELEMENTS / IDX_ELEM_AIR_TEMP       (cat=0x01, idx=0x04, page=element_index)
   CAT_ELEMENTS / IDX_ELEM_FLOOR_TEMP     (cat=0x01, idx=0x05, page=element_index)
      → air and floor temperatures
```

The indirection through `element_index` is important: zone N's sensors are not at page N; the controller maps zones to physical sensor elements dynamically.

### Temperature Encoding

All temperatures are **signed int16 in units of 0.1 °C**:

```python
# Decode
celsius = raw_value / 10.0        # e.g. 0x00E6 = 230 → 23.0 °C

# Sentinel — sensor not wired or absent
if raw_value == 0x7FFF:
    return None

# Encode (write)
raw = int(round(celsius * 10))
```

---

## Client Libraries

Located in `clients/`. These are **standalone implementations** independent of Home Assistant, useful for scripting or alternative integrations.

### `clients/wavin_client.py` — HTTP REST Client

A `requests`-based client for a hypothetical HTTP API on the gateway. Useful if a future firmware exposes REST endpoints.

```python
from clients.wavin_client import WavinClient

client = WavinClient(host="192.168.0.7", username="admin", password="admin")
client.connect()
status = client.get_status()
client.set_temperature(22.5)
```

### `clients/wavin_modbus_client.py` — Modbus RTU Serial Client

Direct RS-485 serial communication using `pyserial`. Suitable for a setup where the computer connects directly to the Wavin bus without an Ethernet gateway.

```python
from clients.wavin_modbus_client import WavinModbusClient

client = WavinModbusClient(port="COM4", baud_rate=38400)
client.connect()
temp = client.get_dhw_temperature()
info = client.get_device_info()
client.disconnect()
```

Includes a standalone `ModbusRTU` helper class with static CRC-16-CCITT utilities (`calculate_crc16`, `validate_crc`, `add_crc`).

> **Note:** The HA integration (`custom_components/`) uses its own `client.py` (Modbus TCP, persistent socket), not these files. The `clients/` directory is for standalone scripting.

---

## Tools

Located in `tools/`. Run these directly from a terminal to probe or diagnose the device — no Home Assistant required.

### `tools/wavin_test.py`

Quick connectivity and temperature read at `10.10.100.254:8899`. Queries zones 0–3 and prints formatted temperature + setpoint values.

```
python tools/wavin_test.py
```

### `tools/wavin_diag.py`

Passive bus monitor. Captures frames on TCP port 502, parses Modbus RTU query/response pairs, and displays decoded temperature and status values.

```
python tools/wavin_diag.py
```

Runs four phases: passive listen → mirror AHC query → device name → desired temp.

### `tools/analyze_response.py`

Connects to `192.168.0.7:502`, sends specific register queries, and prints a detailed hex + decoded breakdown of each response. Useful for reverse-engineering unknown register values.

```
python tools/analyze_response.py
```

### `tools/diagnose_device.py`

Serial port discovery and slave address scanner. Tests each available COM port, then sweeps Modbus slave addresses (1–10) to identify the correct configuration.

```
python tools/diagnose_device.py
```

### `tools/gateway_config.py`

Probes the USR-TCP232-306 gateway with various configuration commands to determine the current operating mode (diagnostic vs. transparent Modbus RTU pass-through).

```
python tools/gateway_config.py
```

---

## Tests

Located in `tests/`. Run with `pytest` from the repository root.

```
pytest tests/
```

| File | Type | Tests | Description |
|------|------|-------|-------------|
| `test_wavin_modbus_client.py` | Unit | 39 | Full Modbus RTU protocol: CRC, framing, register I/O, temperature ops |
| `test_wavin_client.py` | Unit | 26 | HTTP REST client: init, connect, get/set temperature, device info |
| `test_http_client.py` | Integration | — | Live HTTP connectivity smoke test |
| `test_device.py` | Real device | — | Connects to real device via HTTP; requires `<host> <user> <pass>` args |
| `test_wavin_real_device.py` | Real device | — | Modbus RTU on real hardware; requires serial port (default: COM3) |
| `test_wavin.py` | Real device | — | TCP integration test at `10.10.100.254:8899` |

Unit test results are documented in [docs/TEST_REPORT.md](docs/TEST_REPORT.md). As of last run: **65/65 unit tests passing**.

---

## Installation

### Via HACS (recommended)

1. Add this repository as a custom repository in HACS.
2. Install **Wavin AHC 9000** from HACS.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration** and search for *Wavin AHC 9000*.

### Manual

1. Copy `custom_components/wavin_ahc9000/` into your HA `config/custom_components/` directory.
2. Restart Home Assistant.
3. Add the integration via the UI.

### Dependencies

The integration uses only Python standard library (`socket`, `struct`, `threading`) — no `pip` packages required. The `clients/` libraries require `pyserial` (Modbus RTU) and `requests` (HTTP).

---

## Hardware Setup

```
[Wavin AHC 9000]
      │  RS-485 (2-wire)
      ▼
[USR-TCP232-306 Serial Gateway]
   • Serial: 38400 bps, 8N1
   • Network port: 8899
   • Mode: Transparent serial pass-through (NOT diagnostic mode)
      │  Ethernet
      ▼
[Home Assistant host]
```

**Gateway configuration requirements:**

- Serial: **38400 bps, 8N1, no flow control**
- Mode: **Transparent / pass-through** (not diagnostic mode)
- TCP port: **8899** (configurable in integration options)

See [docs/TEST_REPORT.md](docs/TEST_REPORT.md) for gateway reconfiguration steps if the device is stuck in diagnostic mode.

---

## Development

### Running tests

```bash
# All unit tests (no hardware needed)
pytest tests/test_wavin_client.py tests/test_wavin_modbus_client.py -v

# All tests including integration stubs
pytest tests/ -v
```

### Key files to understand first

| File | Why |
|------|-----|
| `custom_components/wavin_ahc9000/const.py` | All register addresses and protocol constants |
| `custom_components/wavin_ahc9000/client.py` | Low-level socket I/O and frame construction |
| `custom_components/wavin_ahc9000/coordinator.py` | Polling logic and data model |

### Adding a new register

1. Add the index constant to `const.py`.
2. Read it in `coordinator.py → _fetch_all()` and store in the data dict.
3. Add a key constant in `const.py` (e.g., `KEY_NEW_VALUE = "new_value"`).
4. Create or update the entity in `climate.py`, `sensor.py`, or `binary_sensor.py`.

### Extending zone count

Change `DEFAULT_NUM_CHANNELS` in `const.py` or set a higher value during config flow. The coordinator and all entities loop over `range(num_channels)` dynamically — no hardcoded zone lists.
