# Changelog

## 1.0.0

- Initial release: Wavin AHC 9000 integration via USR-TCP232 Modbus TCP gateway.
- Per-zone climate entities (thermostat, air/floor temperature sensors, valve status).
- Two-step UI config flow: connection settings + zone naming.
- Options flow for poll interval and per-zone comfort/eco temperature limits.
- Domain services: `wavin_ahc9000.set_temperature`, `wavin_ahc9000.get_channel_info`.
- Supports 1–16 zones; no external pip dependencies (standard library only).
