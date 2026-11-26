# rigol-scope-mcp

MCP server for Rigol digital oscilloscopes over SCPI: read the setup, set channels, timebase,
acquisition and triggers, take the scope's measurements, capture calibrated waveforms (8-bit or
16-bit, screen or deep memory) and grab screenshots.

It is a fork of [`labmcp-rigol-scope`](https://github.com/K-Dense-AI/lab-instrument-mcps/tree/main/servers/engineering/rigol-oscilloscope)
0.1.2 (Apache-2.0, K-Dense and LabMCP contributors). See [NOTICE](NOTICE) for what changed.

## Hardware status

| Model | Firmware | Interface | Status |
|---|---|---|---|
| DHO804 | 00.01.05 | USB (USB-TMC) with NI-VISA 24.0 on Windows 11 | Verified, with the quirks listed below |
| DHO804 | 00.01.05 | LAN, or USB with pyvisa-py / libusb | Not yet tried |
| Other DHO800/900/1000/4000, DS1000Z / MSO1000Z, MSO5000 | | | Simulator only (inherited from upstream) |

## Run it

```bash
uv run rigol-scope-mcp --check                                   # find the Rigol on USB and identify it
uv run rigol-scope-mcp --address TCPIP0::192.168.1.50::INSTR --check   # LAN (VXI-11), no driver needed
uv run rigol-scope-mcp --address tcp://192.168.1.50:5555 --check       # LAN raw socket, no driver needed
uv run rigol-scope-mcp --simulate --option sim_model=DHO804 --check    # no hardware
```

With no `--address` the server looks for a Rigol on USB (vendor ID `0x1AB1`), trying a vendor VISA
library (`@ivi`: NI-VISA, Keysight) first and the pure-Python `pyvisa-py` backend (`@py`, needs
`libusb`) second. If several Rigols are connected add `--option serial=<serial number>`.

Other switches: `--read-only` hides every tool that changes the scope; `--limit max_memory_points=<n>`
raises the deep-memory cap (default 1 200 000 points); `--audit-log <file>` records all SCPI traffic.
The same settings can come from the environment: `LABMCP_ADDRESS`, `LABMCP_READ_ONLY`, `LABMCP_OPTIONS`,
`LABMCP_LIMITS`, `LABMCP_AUDIT_LOG`.

### USB drivers

| OS | What USB needs |
|---|---|
| Windows | Either a VISA runtime (NI-VISA or similar, which binds its USBTMC driver to the scope) **or** WinUSB installed on the scope with Zadig plus a `libusb-1.0.dll`. The two are mutually exclusive for one device. |
| macOS | `brew install libusb` |
| Linux | `libusb-1.0`, a udev rule giving your user access to vendor `1ab1`, and the kernel `usbtmc` module not holding the device |

LAN needs none of this.

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `get_connection_info` | read | Which instrument is connected, read-only state, safety limits |
| `get_device_info` | read | Model, serial, firmware, command family, sample rate, memory depth |
| `get_settings` | read | Channels, timebase, trigger (any supported type), acquisition type and averages |
| `measure` | read | The scope's automatic measurements for one channel |
| `capture_waveform` | read | Waveform in volts and seconds with statistics; optional CSV of every point |
| `screenshot` | read | PNG of the display (BMP on the MSO5000) |
| `get_command_log` | read | Recent raw SCPI traffic |
| `reconnect` | safety | Close and re-open the connection |
| `run`, `stop`, `single`, `force_trigger` | control | Acquisition control |
| `autoscale` | control | The scope's AUTO setup; overwrites the whole setup |
| `set_channel` | control | Scale, offset, coupling, probe ratio, bandwidth limit, on/off |
| `set_timebase` | control | Time per division and offset |
| `set_acquisition` | control | Acquisition type (normal, peak, average, ultra), averages, memory depth |
| `set_trigger` | control | Edge trigger: source, level, slope, sweep |
| `set_pulse_trigger` | control | Pulse-width trigger (DHO only) |
| `set_timeout_trigger` | control | Timeout trigger (DHO only) |
| `set_trigger_options` | control | Trigger coupling, holdoff, noise reject |

"read" tools still send readout commands (`*CLS` on connect, `:WAVeform:SOURce/MODE/FORMat/STARt/STOP`)
and can write `.png` / `.csv` files where asked; they never change the channel, timebase or trigger setup.

## Waveform data

- `capture_waveform(sample_format="auto")` reads 16-bit `WORD` data on the DHO family and 8-bit `BYTE`
  elsewhere. WORD is unsigned, little-endian, YREFerence 32768; checked against the scope's own ASCii
  volts on a DHO804. `code_bits` and `volts_per_code` in the result say what was transferred.
- `mode="screen"` returns the 1000 displayed points (1200 on the DS1000Z) and works while running.
- `mode="memory"` returns the whole record and needs the scope stopped. 1 M points in 16-bit takes
  about 1.4 s over USB.

## DHO804 quirks found on hardware (firmware 00.01.05)

- **`:MEASure:ITEM? VTOP` can freeze the scope's remote interface** until it is power-cycled. `measure`
  refuses `vtop`, `vbase`, `vamp` and `overshoot` on the DHO family without sending anything. Only VTOP
  is confirmed; the other three were in the one other call that froze it.
- **Leaving Average mode can fail silently.** Once in AVERages the scope may accept
  `:ACQuire:TYPE NORMal` or `PEAK` with no error and stay in AVERages. `set_acquisition` reads the type
  back and raises an error. A power cycle (or the front panel) clears it.
- **`HRESolution` is not an acquisition type** on the DHO800; the scope rejects it.
- **The waveform preamble's point count is the STARt..STOP window**, not the record length, and
  STARt/STOP can be refused while the other limit is stale. The driver selects the full record first
  and verifies the window by reading it back.
- **Large binary reads need the termination character off.** With it on, reads of 250 000-point
  batches stall over USB-TMC.
- **Deep memory is empty** if the scope is stopped before it completes an acquisition at a new memory
  depth. Run for a moment before stopping.
- **Each measured item is added to the scope's on-screen result list.**
- **Not yet exercised on hardware:** `autoscale`, `force_trigger`, and the measurement items `vavg`,
  `vrms`, `preshoot`, `fall_time`, the pulse widths and the duty cycles (in a clean run).

## Develop

```bash
uv run --group dev pytest        # 39 tests against the wire-level simulator
```
