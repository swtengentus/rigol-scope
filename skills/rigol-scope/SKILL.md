---
name: rigol-scope
description: How to operate a Rigol oscilloscope through the rigol-scope MCP tools safely and get trustworthy numbers. Use whenever the user asks to measure, capture, screenshot, trigger on, configure or characterise a signal with their Rigol scope (DHO804, DHO800/900, DS1000Z, MSO5000), or asks what is on the scope.
---

# Operating a Rigol scope through the rigol-scope tools

If the tools are missing or `get_connection_info` reports an error, use the `rigol-scope-setup` skill
first.

## Before changing anything

1. `get_connection_info` - confirms a real (not simulated) instrument and whether the server is
   read-only.
2. `get_settings` - the current setup is usually the user's own front-panel work. Note what you are
   about to change and tell the user afterwards (or restore it if they did not ask for the change).
3. Never call `autoscale` unless asked: it rewrites every setting.

## Getting a signal on screen

A measurement of `null`, or `clipped_fraction` > 0 in a capture, means the trace does not fit. Fix the
view before trusting any number:

- **Vertical:** the screen shows 8 divisions. Choose `scale_v_per_div` so the signal spans 4 to 6 of
  them and set `offset_v` to minus the signal's midpoint (a 0 to 3 V square wave: 0.5 V/div, offset
  -1.5 V).
- **Horizontal:** 10 divisions. For a clean frequency reading show 2 to 5 periods
  (1 kHz -> 200 us/div).
- **Trigger:** set the level inside the signal (its midpoint), on the channel carrying it. Status `TD`
  means triggered; `AUTO` means free-running without a trigger; `WAIT` means armed and nothing yet.
- **Probe ratio:** `probe_ratio` must match the physical probe. A 10x probe with the channel at 1x
  reads ten times low. Ask the user which probe is fitted rather than guess.
- After changing scale, timebase or acquisition settings, let the scope acquire for a moment before
  measuring; a reading taken immediately can be stale.

## Measuring

- `measure` returns the scope's own measurements. Good general set: `vpp`, `vmax`, `vmin`,
  `frequency`, `period`.
- **On the DHO family do not ask for `vtop`, `vbase`, `vamp` or `overshoot`.** Querying VTOP froze a
  DHO804's remote interface until it was power-cycled; the server refuses these four. Get top/base
  levels and overshoot from `capture_waveform` data instead.
- Each item measured is added to the scope's on-screen result panel. Mention it if the user cares about
  their display.
- Cross-check anything important against `capture_waveform` statistics (`min_v`, `max_v`,
  `peak_to_peak_v`, `mean_v`, `rms_v`).

## Capturing waveforms

- `mode="screen"`: the 1000 displayed points (1200 on a DS1000Z). Fast, works while running.
- `mode="memory"`: the whole record. The scope must be stopped. Sequence: set the memory depth with
  `set_acquisition` while running, let it run a second, `stop` (or `single` with a wait), capture,
  then `run` again. Stopping immediately after a depth change leaves the memory empty.
- Resolution: on the DHO family the default is 16-bit (`code_bits: 16`), which preserves the 12-bit
  ADC. `sample_format="byte"` is 8-bit and faster. Other families are 8-bit only.
- The tool returns a downsampled min/max trace (`max_points`) plus statistics. For analysis of every
  point pass `save_path` (a new `.csv` file; existing files are never overwritten) and process the file.
- Deep-memory reads are capped by `max_memory_points` (default 1.2 M). Going beyond needs the server
  restarted with a higher `--limit`; do not try to work around it.

## Acquisition settings (`set_acquisition`)

- Types on the DHO800/900: `normal`, `peak`, `average`, `ultra`. `high_resolution` is rejected.
- `averages` must be a power of two and only matters in `average`.
- Memory depth: 1 k, 10 k, 100 k, 1 M, 10 M, 25 M points or `auto`; valid values depend on the model
  and how many channels are on. Change it while running.
- **Known firmware trap (DHO804, 00.01.05):** once in `average` the scope may silently refuse to go
  back to `normal` or `peak`. The tool detects this and returns an error saying so. Do not retry in a
  loop: tell the user to switch it on the front panel (Acquire menu) or power-cycle the scope. Warn the
  user of this before putting the scope into `average` if they did not ask for averaging explicitly.

## Triggers

- `set_trigger`: edge trigger (source, level, slope, sweep).
- `set_pulse_trigger` (DHO only): pulses wider than, narrower than, or within a width window.
- `set_timeout_trigger` (DHO only): signal idle for longer than a time after an edge.
- `set_trigger_options`: coupling (dc, ac, lf_reject, hf_reject), holdoff, noise reject; they persist
  across trigger types.
- Sweep `auto` free-runs when nothing triggers; `normal` waits; `single` captures once and stops.
  For a one-off event use `single` with `wait_s`, and `force_trigger` if it never fires.

## Screenshots

`screenshot` returns the PNG to you and saves it. Look at it: it is the quickest check that what you
measured is what the user sees. Pass `save_path` (a new `.png`) when the user wants the file somewhere
specific; otherwise it goes to the system temp folder.

## When the scope stops answering

If one tool times out, call `get_connection_info` once. If that also fails, the scope's remote
interface has frozen. Do not keep retrying: ask the user to power-cycle the scope, wait about 40
seconds for it to come back on USB, then call `reconnect`. Settings changed shortly before a freeze may
not survive the power cycle, so read `get_settings` again afterwards.

## Reporting

State values with units and the conditions they depend on: probe ratio, coupling, acquisition type,
bandwidth limit, and whether the number came from the scope's measurement or from captured samples.
Say plainly when a value is provisional (clipped trace, fewer than one period on screen, averaging
still settling).
