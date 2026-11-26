---
name: rigol-scope-setup
description: Get a Rigol oscilloscope talking to this computer for the rigol-scope MCP server. Use when the user asks to set up, connect, install or troubleshoot their Rigol scope (DHO800/900, DS1000Z, MSO5000), when the rigol-scope tools are missing or failing to connect, when get_connection_info reports an error, or on a new computer where the scope has not been used before.
---

# Set up a Rigol oscilloscope for the rigol-scope MCP server

The plugin's MCP server starts by itself and finds a Rigol on USB without any address. Setup is
therefore about the machine: `uv`, a USB driver path (or LAN), and proving the link. Work in the stages
below and report at the end of each one.

**Hard rules**

- Never install drivers, run Zadig, or change a USB driver binding without the user's explicit go-ahead.
  State what will change and what it breaks first.
- Never install NI-VISA or other vendor software silently; give the user the link and let them run it.
- Prove `*IDN?` before blaming or changing the MCP server.

`SERVER` below means the plugin's `server` folder: two levels up from this skill, then `server`
(`${CLAUDE_PLUGIN_ROOT}/server`).

## Stage 1 - Is the scope visible on USB?

Skip to Stage 3b if the user wants LAN.

- **Windows:** `Get-PnpDevice -PresentOnly | Where-Object InstanceId -match 'VID_1AB1'` and read
  `DEVPKEY_Device_Service` / `DEVPKEY_Device_DriverProvider` with `Get-PnpDeviceProperty`.
- **macOS:** `system_profiler SPUSBDataType | grep -B3 -A8 -i rigol`.
- **Linux:** `lsusb -d 1ab1:` and `lsusb -t` (shows the bound driver).

Report vendor ID (`0x1AB1`), product ID, serial number and the bound driver.

If nothing enumerates, stop and tell the user. Usual causes: the cable is in a front-panel USB *host*
port instead of the rear USB *device* (USB-B) port, a charge-only cable, or the scope is still booting
(it takes about 40 s to appear after power-on, so wait and look again before concluding).

## Stage 2 - uv and the driver path

1. `uv --version`. If missing, ask, then install it the official way
   (Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`; macOS/Linux:
   `curl -LsSf https://astral.sh/uv/install.sh | sh`).
2. Work out which backend can reach the scope. There are two and, on Windows, they are mutually
   exclusive for one device:

   | Bound driver / situation | Backend | What to do |
   |---|---|---|
   | Windows, service `Usbtmc` / "USB Test and Measurement Device (IVI)", and a VISA is installed (`C:\Windows\System32\visa64.dll` exists, NI-VISA / Keysight in installed programs) | `@ivi` | Nothing. Recommended whenever a VISA is already present; do not run Zadig, it would take the scope away from the VISA and from LabVIEW / vendor tools. |
   | Windows, `WinUSB` / libusb driver | `@py` | Needs `libusb-1.0.dll` reachable (e.g. `uv pip install libusb-package` is not enough for pyusb on its own; putting the DLL next to python.exe or on PATH is). |
   | Windows, no driver or an "Unknown device" | neither yet | Recommend installing NI-VISA runtime (simplest, coexists with other lab software) or Zadig -> WinUSB (lightest, but exclusive). Explain both, ask, wait. |
   | macOS | `@py` | `brew install libusb` if missing. |
   | Linux | `@py` | `libusb-1.0-0`, plus a udev rule: `SUBSYSTEM=="usb", ATTRS{idVendor}=="1ab1", MODE="0660", GROUP="plugdev"`, reload rules, replug. If the kernel `usbtmc` module holds the device, unbind it. |

3. Tell the user which path you recommend and why, and wait for their go-ahead before any change.

## Stage 3a - Prove the link over USB

```
uv run --directory "<SERVER>" rigol-scope-mcp --check
```

It prints JSON. Success is `"connected": true` with an `instrument` block (model, serial, firmware).
With no `--address` it tries a vendor VISA first, then pyvisa-py. On failure the `error` text names what
each backend saw; act on that. Useful variants:

- several Rigols connected: add `--option serial=<serial>`;
- force a backend: `--address "USB0::0x1AB1::<pid>::<serial>::INSTR?backend=@ivi"` (or `@py`);
- no hardware at all (to test the software only): `--simulate --option sim_model=DHO804 --check`.

The first run downloads about 70 Python packages and can take a minute.

## Stage 3b - LAN instead (no driver needed)

Ask the user for the IP (scope: Utility -> IO -> LAN). Then:

```
uv run --directory "<SERVER>" rigol-scope-mcp --address TCPIP0::<ip>::INSTR --check
uv run --directory "<SERVER>" rigol-scope-mcp --address tcp://<ip>:5555 --check
```

Try VXI-11 first, the raw socket second. To make the plugin use the one that works, it goes in the
plugin's `scope_address` option: have the user run `/plugin configure rigol-scope@rigol-scope` and
enter it (changing plugin options is the user's call, so ask rather than doing it for them), then
`/reload-plugins` or restart Claude Code. LAN is untested on the author's DHO804, so say so if it fails.

## Stage 4 - Confirm inside Claude

Call `get_connection_info`, then `get_device_info`. If the tools are not present in this session the
plugin's server has not been loaded: ask the user to restart Claude Code (or run `/reload-plugins`),
then try again. Only one program can hold the USB session, so close other software using the scope
(UltraSigma, LabVIEW, a second Claude app with the same server).

## Stage 5 - Smoke test (optional, ask first because it changes the scope's setup)

With a probe on the scope's 1 kHz compensation output: `get_settings`, then `set_timebase` 200 us/div,
`set_channel` to suit the probe (1x probe: 0.5 V/div, offset -1.5 V; 10x probe set to 10x: the same),
`set_trigger` level 1.5 V, `measure` with `vpp` and `frequency`, `screenshot`, `capture_waveform`.
Expect about 1 kHz and 3 Vpp. Tell the user which settings you changed.

## If things go wrong

- **Everything times out, even `*IDN?`:** the scope's remote interface has frozen (see the
  `rigol-scope` skill for the known triggers). Only a power cycle fixes it. After power-on wait for it
  to re-enumerate, then `reconnect`.
- **"Could not open VISA resource":** another program holds it, or the wrong backend for the bound
  driver.
- **Windows "filename or extension is too long" on import:** the plugin is installed under a very long
  path; this is the 260-character limit. Enable long paths or install to a shorter location.
