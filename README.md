# rigol-scope: a Claude Code plugin for Rigol oscilloscopes

Install it, plug in the scope, and ask Claude things like "what is on the scope?", "measure CH1" or
"capture 1 M points of CH2 at 16-bit and save them". Verified on a DHO804 over USB; see
[server/README.md](server/README.md) for hardware status and known firmware quirks.

| Part | Where | Purpose |
|---|---|---|
| MCP server | [server/](server/) | The tools: settings, acquisition, triggers, measurements, capture, screenshot |
| Setup skill | [skills/rigol-scope-setup](skills/rigol-scope-setup/SKILL.md) | Gets a new computer talking to the scope (uv, drivers, link test) |
| Usage skill | [skills/rigol-scope](skills/rigol-scope/SKILL.md) | How to drive the scope safely and what to avoid |

## Install on a new computer

1. Install [uv](https://docs.astral.sh/uv/) if it is not there (the setup skill can do this for you).
2. Add this repository as a marketplace and install the plugin:

   ```
   claude plugin marketplace add swtengentus/rigol-scope
   claude plugin install rigol-scope@rigol-scope
   ```

3. Start Claude Code and say: **"Set up my Rigol scope."**

The server needs no address: it finds a Rigol on USB by itself. The first start downloads its Python
dependencies into the plugin's data folder and can take a minute.

## What still needs a human

- **Windows USB driver.** The scope needs either a VISA runtime (NI-VISA or similar) or a WinUSB
  binding made with Zadig. Both need administrator rights. Claude will tell you which applies and wait
  for your go-ahead. macOS needs only `libusb`; Linux needs `libusb` and a udev rule.
- **LAN instead of USB** needs no driver, only the scope's IP address in the plugin's options.

## Options

Set with `/plugin configure rigol-scope@rigol-scope` (or `claude plugin configure`), then restart
Claude Code or run `/reload-plugins`.

| Option | Default | Meaning |
|---|---|---|
| `scope_address` | blank | Blank finds the scope on USB. `TCPIP0::<ip>::INSTR` or `tcp://<ip>:5555` for LAN. |
| `scope_serial` | blank | Chooses one scope when several Rigols are on USB. |
| `read_only` | false | Hides every tool that changes the scope's setup. |

## Other MCP clients (Claude Desktop chat, Cursor, ...)

Plugins load in Claude Code. Elsewhere, clone this repository and register the server directly:

```json
{
  "mcpServers": {
    "rigol-scope": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/clone/server", "rigol-scope-mcp"]
    }
  }
}
```

Add `"--address", "TCPIP0::<ip>::INSTR"` or `"--read-only"` to `args` as needed.

## Licence

Apache-2.0. The server is a fork of `labmcp-rigol-scope`; see [server/NOTICE](server/NOTICE).
