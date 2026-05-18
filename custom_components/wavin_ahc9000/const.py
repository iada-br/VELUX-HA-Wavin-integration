"""Constants for the Wavin AHC 9000 integration."""

DOMAIN = "wavin_ahc9000"

# ── Config entry keys ────────────────────────────────────────────────────────
# CONF_HOST and CONF_PORT are imported from homeassistant.const.
CONF_SLAVE_ID = "slave_id"
CONF_NUM_CHANNELS = "num_channels"
CONF_SCAN_INTERVAL = "scan_interval"

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_HOST = "10.10.100.254"
# USR-TCP232 Modbus TCP gateway port (confirmed by live device test).
DEFAULT_PORT = 8899
DEFAULT_SLAVE_ID = 0x01
DEFAULT_NUM_CHANNELS = 4
DEFAULT_SCAN_INTERVAL = 30  # seconds

# ── Protocol function codes ───────────────────────────────────────────────────
FC_READ = 0x43   # Read by Index  (custom Wavin extension)
FC_WRITE = 0x44  # Write by Index (custom Wavin extension)

# Sentinel: register returns this when the physical sensor is absent/not wired
SENSOR_NA = 0x7FFF

# ── Register categories ───────────────────────────────────────────────────────
CAT_ELEMENTS = 0x01   # Physical sensors; page = element_index (NOT zone index)
CAT_PACKED   = 0x02   # Per-zone setpoints; page = zone index
CAT_CHANNELS = 0x03   # Per-zone control/status; page = zone index
CAT_INFO     = 0x07   # Device identification

# ── ELEMENTS (cat=0x01) register indices ─────────────────────────────────────
# Page = element_index obtained from IDX_CH_PRIMARY_ELEMENT, not the zone number.
IDX_ELEM_AIR_TEMP   = 0x04  # Signed int16, 0.1 °C; 0x7FFF = sensor absent
IDX_ELEM_FLOOR_TEMP = 0x05  # Signed int16, 0.1 °C; 0x7FFF = sensor absent

# ── PACKED DATA (cat=0x02) register indices ───────────────────────────────────
# Page = zone index (0-based).
IDX_CH_MANUAL_TEMP  = 0x00  # Signed int16, 0.1 °C — the active setpoint (R/W)
IDX_CH_COMFORT_TEMP = 0x01  # Signed int16, 0.1 °C
IDX_CH_ECO_TEMP     = 0x02  # Signed int16, 0.1 °C

# ── CHANNELS STATUS (cat=0x03) register indices ───────────────────────────────
# Page = zone index (0-based).
IDX_CH_TIMER_EVENT      = 0x00  # Bit flags; bit 4 = valve/output on
IDX_CH_PRIMARY_ELEMENT  = 0x02  # bits[5:0]=element_index; bit10=all_tp_lost

TIMER_EVENT_OUTP_ON_MASK    = 0x0010
PRIMARY_ELEMENT_IDX_MASK    = 0x003F
PRIMARY_ELEMENT_TP_LOST_MASK = 0x0400

# ── INFO (cat=0x07) register indices ─────────────────────────────────────────
IDX_INFO_HW_VER      = 0x02
IDX_INFO_SW_VER      = 0x03
IDX_INFO_DEVICE_NAME = 0x04  # Returns 116 for AC-116

# ── Socket / timing constants ─────────────────────────────────────────────────
SOCKET_CONNECT_TIMEOUT = 5.0  # seconds for TCP connect
QUERY_RESPONSE_WINDOW = 0.8   # seconds; total budget to receive a valid response
QUERY_CHUNK_TIMEOUT = 0.2     # seconds; recv() timeout inside the response window

# ── Climate limits ────────────────────────────────────────────────────────────
MIN_TEMP = 5.0
MAX_TEMP = 35.0
TEMP_STEP = 0.5

# ── Coordinator data-dict keys ────────────────────────────────────────────────
KEY_AIR_TEMP    = "air_temp"
KEY_FLOOR_TEMP  = "floor_temp"
KEY_DESIRED_TEMP = "desired_temp"
KEY_VALVE_OPEN  = "valve_open"
KEY_TP_LOST     = "tp_lost"


def ch_key(channel: int, key: str) -> str:
    """Return the flat-dict key for a per-channel value.

    Examples:
        ch_key(0, KEY_AIR_TEMP)   → 'ch0_air_temp'
        ch_key(2, KEY_FLOOR_TEMP) → 'ch2_floor_temp'
    """
    return f"ch{channel}_{key}"
