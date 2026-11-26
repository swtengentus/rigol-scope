"""SCPI driver for Rigol digital oscilloscopes.

Only commands present, with the same syntax, in the programming guide of each supported family
are used. Where the families differ (autoscale command, screenshot query, number of points on
screen, probe ratios) the difference is captured in a :class:`ScopeProfile`.

References (Rigol programming guides):

* MSO1000Z/DS1000Z Series Programming Guide, PGA19109-1110 (Jul. 2018),
  https://www.bitsavers.org/test_equipment/rigol/DS1000Z/PGA19109-1110_MSO1000Z_DS1000Z_Series_Digital_Oscilloscope_Programming_Guide_201807.pdf
* DS1000Z-E Series Programming Guide, PGA27100-1110,
  https://www.batronix.com/files/Rigol/Oszilloskope/DS1000Z-E/DS1000Z-E-ProgrammingGuide.pdf
* MSO5000 Series Programming Guide, PGA25104-1110 (May 2020),
  https://www.batronix.com/files/Rigol/Oszilloskope/MSO5000/MSO5000_ProgrammingGuide_EN-V2.0.pdf
* DHO800/DHO900 Programming Guide (2024),
  https://download.rigol.com/en/Manual/Digital%20Oscilloscope/DHO900/DHO800900_ProgrammingGuide_EN.pdf
* DHO1000/DHO4000 Programming Guide, PGA34101-1110,
  https://www.batronix.com/files/Rigol/Oszilloskope/DHO1000/dho10004000_programmingguide_en.pdf

Waveform data (all guides): ``:WAVeform:DATA?`` in BYTE format returns an IEEE 488.2 block
``#9<9-digit length><bytes>`` followed by ``\\n``; volts = (byte - YORigin - YREFerence) x
YINCrement and time = (i - XREFerence) x XINCrement + XORigin, with the parameters from
``:WAVeform:PREamble?``.
"""

from __future__ import annotations

import math
import re
import struct
import time
from dataclasses import dataclass
from typing import Any, Literal

from labmcp import InstrumentConnectionError, InstrumentProtocolError, Transport
from labmcp.scpi import SCPIDriver

_DS_PROBES = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)
_HIGH_PROBES = (2000, 5000, 10000, 20000, 50000)


@dataclass(frozen=True)
class ScopeProfile:
    family: str
    guide: str
    autoscale_command: str
    screenshot_query: str
    screenshot_format: Literal["png", "bmp"]
    #: Points returned by :WAVeform:DATA? in NORMal (screen) mode; also 100 x horizontal divisions.
    screen_points: int
    probe_ratios: tuple[float, ...]
    #: Points per :WAVeform:DATA? when reading deep memory in BYTE format. 250 000 is the
    #: documented DS1000Z maximum; the other guides allow batched reads but give no maximum.
    memory_batch_points: int = 250_000
    #: True where :WAVeform:FORMat WORD has been checked on hardware: unsigned 16-bit, little-endian
    #: (verified on a DHO804, firmware 00.01.05, against the scope's own ASCii volts).
    word_format: bool = False
    #: True where the pulse-width / timeout trigger commands used here were checked on hardware.
    extended_triggers: bool = False


DS1000Z = ScopeProfile(
    "DS1000Z", "MSO1000Z/DS1000Z Programming Guide PGA19109-1110 (DS1000Z-E: PGA27100-1110)",
    ":AUToscale", ":DISPlay:DATA? ON,OFF,PNG", "png", 1200, _DS_PROBES,
)
MSO5000 = ScopeProfile(
    "MSO5000", "MSO5000 Programming Guide PGA25104-1110",
    ":AUToscale", ":DISPlay:DATA?", "bmp", 1000, (0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005) + _DS_PROBES + _HIGH_PROBES,
)
DHO = ScopeProfile(
    "DHO", "DHO800/DHO900 Programming Guide; DHO1000/DHO4000 Programming Guide PGA34101-1110",
    ":AUToset", ":DISPlay:DATA? PNG", "png", 1000, (0.001, 0.002, 0.005) + _DS_PROBES + _HIGH_PROBES,
    word_format=True, extended_triggers=True,
)
PROFILES = {"DS1000Z": DS1000Z, "MSO5000": MSO5000, "DHO": DHO}
_MODEL_PATTERNS = (
    (re.compile(r"^(DS|MSO)1\d{3}Z", re.IGNORECASE), DS1000Z),
    (re.compile(r"^MSO5\d{3}", re.IGNORECASE), MSO5000),
    (re.compile(r"^DHO(8|9)\d{2}", re.IGNORECASE), DHO),
    (re.compile(r"^DHO(1|4)\d{3}", re.IGNORECASE), DHO),
)

#: Single-source measurement items that have the same name in all supported guides.
MEASUREMENTS: dict[str, tuple[str, str]] = {
    "vmax": ("VMAX", "V"),
    "vmin": ("VMIN", "V"),
    "vpp": ("VPP", "V"),
    "vtop": ("VTOP", "V"),
    "vbase": ("VBASe", "V"),
    "vamp": ("VAMP", "V"),
    "vavg": ("VAVG", "V"),
    "vrms": ("VRMS", "V"),
    "overshoot": ("OVERshoot", "ratio"),
    "preshoot": ("PREShoot", "ratio"),
    "period": ("PERiod", "s"),
    "frequency": ("FREQuency", "Hz"),
    "rise_time": ("RTIMe", "s"),
    "fall_time": ("FTIMe", "s"),
    "positive_width": ("PWIDth", "s"),
    "negative_width": ("NWIDth", "s"),
    "positive_duty": ("PDUTy", "ratio"),
    "negative_duty": ("NDUTy", "ratio"),
}
#: Items not queried on the DHO family: VTOP hung a DHO804; the other three were in the only other call that did.
_DHO_HANG_SUSPECTS = frozenset({"vtop", "vbase", "vamp", "overshoot"})
#: Rigol (and SCPI) return 9.9E37 when a parameter cannot be measured.
INVALID_THRESHOLD = 9.0e37

_COUPLINGS = {"AC", "DC", "GND"}
_SLOPES = {"rising": "POSitive", "falling": "NEGative", "either": "RFALl"}
_SWEEPS = {"auto": "AUTO", "normal": "NORMal", "single": "SINGle"}
_ACQ_TYPES = {
    "normal": "NORMal", "peak": "PEAK", "average": "AVERages", "ultra": "ULTRa", "high_resolution": "HRESolution",
}
_TRIGGER_COUPLINGS = {"dc": "DC", "ac": "AC", "lf_reject": "LFReject", "hf_reject": "HFReject"}
_POLARITIES = {"positive": "POSitive", "negative": "NEGative"}
_PULSE_WHEN = {"greater": "GREater", "less": "LESS", "within": "GLESs"}


def detect_profile(model: str, override: str | None = None) -> ScopeProfile:
    if override:
        key = override.strip().upper()
        if key not in PROFILES:
            raise InstrumentConnectionError(f"Unknown profile {override!r}; use one of {', '.join(PROFILES)}.")
        return PROFILES[key]
    for pattern, profile in _MODEL_PATTERNS:
        if pattern.match(model.strip()):
            return profile
    raise InstrumentConnectionError(
        f"Model {model!r} is not a supported Rigol oscilloscope family (DS1000Z/MSO1000Z, DS1000Z-E, MSO5000, "
        "DHO800/900/1000/4000). If its programming guide matches one of them, force it with "
        "--option profile=DS1000Z|MSO5000|DHO."
    )


def channel_count(model: str) -> int:
    """Analog channels from the model number: the last digit (DS1104Z -> 4, DHO802 -> 2)."""
    m = re.search(r"(\d+)", model)
    n = int(m[1][-1]) if m else 4
    return n if n in (2, 4) else 4


@dataclass
class Preamble:
    format: int  # 0 BYTE, 1 WORD, 2 ASCii
    type: int  # 0 NORMal, 1 MAXimum, 2 RAW
    points: int
    count: int
    xincrement: float
    xorigin: float
    xreference: float
    yincrement: float
    yorigin: float
    yreference: float

    @classmethod
    def parse(cls, reply: str) -> Preamble:
        parts = [p.strip() for p in reply.split(",")]
        if len(parts) != 10:
            raise InstrumentProtocolError(f"Expected 10 comma-separated values from :WAVeform:PREamble?, got {reply!r}")
        try:
            f = [float(p) for p in parts]
        except ValueError as exc:
            raise InstrumentProtocolError(f"Unparseable :WAVeform:PREamble? reply {reply!r}") from exc
        if not all(math.isfinite(x) for x in f):  # would turn every sample into NaN/inf volts
            raise InstrumentProtocolError(f"Non-finite value in :WAVeform:PREamble? reply {reply!r}")
        return cls(int(f[0]), int(f[1]), int(f[2]), int(f[3]), f[4], f[5], f[6], f[7], f[8], f[9])

    def same_scaling(self, other: Preamble) -> bool:
        """True if ``other`` has the same volts scaling and sample interval as this preamble. (XORigin
        is left out: it may drift in roll mode, and a changed offset only shifts the time axis.)"""
        keys = ("format", "xincrement", "xreference", "yincrement", "yorigin", "yreference")
        return all(getattr(self, k) == getattr(other, k) for k in keys)


@dataclass
class Waveform:
    channel: int
    mode: str
    time_s: list[float]
    volts: list[float]
    preamble: Preamble
    #: Fraction of points at the ADC limits (byte code 0 or 255): the signal is clipped there.
    clipped_fraction: float
    #: Bits per transferred sample code: 8 (BYTE) or 16 (WORD).
    code_bits: int = 8


def _num(value: float) -> str:
    return format(value, ".10g")


class RigolScope(SCPIDriver):
    def __init__(self, transport: Transport, profile: str | None = None) -> None:
        super().__init__(transport)
        ident = super().identify()
        if not ident["manufacturer"].upper().startswith("RIGOL"):
            raise InstrumentConnectionError(
                f"The instrument identifies as {ident['manufacturer']!r} {ident['model']!r}, not a Rigol oscilloscope."
            )
        self.ident = ident
        self.model = ident["model"]
        self.profile = detect_profile(self.model, profile)
        self.channels = channel_count(self.model)
        # Clear old errors (e.g. from front-panel use) so they are not blamed on our first command.
        self.write("*CLS")

    def identify(self) -> dict[str, str]:
        """``*IDN?`` fields plus the detected command family and channel count (cached)."""
        return {**self.ident, "family": self.profile.family, "analog_channels": str(self.channels)}

    # ------------------------------------------------------------ helpers

    def _check_channel(self, channel: int) -> None:
        if not 1 <= channel <= self.channels:
            raise InstrumentProtocolError(f"The {self.model} has analog channels 1-{self.channels}; got {channel}.")

    def send(self, *commands: str) -> None:
        """Send setting commands, then read the error queue so a rejected value is reported."""
        with self.t.lock:
            for cmd in commands:
                self.write(cmd)
            self.check_errors(context="; ".join(commands))

    def query_block(self, command: str, timeout: float | None = None) -> bytes:
        """Definite-length block query. On a VISA session the reply is read as whole messages.

        The base implementation asks for the header and the payload with separate exact-size reads.
        Over USB-TMC (seen on a DHO804 with NI-VISA) that works for small blocks but times out on a
        250 000-point memory batch, so here the message is read with ``read_raw`` instead. The
        session's termination character is switched off for the read: binary data is full of 0x0A
        bytes (every 256th 16-bit sample has one), and with it on each of them ends a read early.
        """
        res = getattr(self.t, "_res", None)
        if res is None:  # serial / TCP / simulator: byte-stream transports
            return super().query_block(command, timeout)
        t = self.t
        with t.lock:
            t.write(command)
            t._set_timeout(timeout)  # noqa: SLF001 - labmcp is pinned to 0.1.2 for this
            termination = res.read_termination
            res.read_termination = None  # read to END (EOM), not to the first 0x0A
            try:
                data = self._read_block_message(res, command)
            finally:
                res.read_termination = termination
        if t.audit:
            t.audit.record("read", data, t.description)
        return data

    def _read_block_message(self, res: Any, command: str) -> bytes:
        """Read one ``#<n><length><payload>`` message from a PyVISA resource and return the payload."""
        read = lambda: self.t._call(res.read_raw)  # noqa: E731, SLF001

        buf = bytearray(read())
        while len(buf) < 2:
            buf += read()
        if buf[:1] != b"#" or not buf[1:2].isdigit() or buf[1:2] == b"0":
            raise InstrumentProtocolError(f"Expected a definite-length block from {command!r}, got {bytes(buf[:16])!r}")
        ndigits = int(buf[1:2])
        while len(buf) < 2 + ndigits:
            buf += read()
        digits = bytes(buf[2 : 2 + ndigits])
        if not digits.isdigit():
            raise InstrumentProtocolError(f"Malformed binary block header from {command!r}: {bytes(buf[:16])!r}")
        end = 2 + ndigits + int(digits)
        while len(buf) < end:
            buf += read()
        return bytes(buf[2 + ndigits : end])

    def _set_window(self, start: int, stop: int) -> None:
        """Select points ``start``..``stop`` for the next ``:WAVeform:DATA?`` and confirm the scope took them.

        On the DHO a STARt/STOP can be refused (-200) while the *other* limit is still stale from an
        earlier capture at another memory depth, so the pair is written, STARt repeated, the error
        queue drained and the result checked by reading both back.
        """
        with self.t.lock:
            self.write(f":WAVeform:STARt {start}")
            self.write(f":WAVeform:STOP {stop}")
            self.write(f":WAVeform:STARt {start}")
            self.errors()
            got = (self.query_int(":WAVeform:STARt?"), self.query_int(":WAVeform:STOP?"))
        if got != (start, stop):
            raise InstrumentProtocolError(
                f"Asked the scope for waveform points {start}-{stop} but it selected {got[0]}-{got[1]}. "
                "Is the acquisition memory shorter than expected?"
            )

    # ------------------------------------------------------------ settings

    def channel_settings(self, channel: int) -> dict[str, object]:
        self._check_channel(channel)
        c = f":CHANnel{channel}"
        with self.t.lock:
            return {
                "channel": channel,
                "enabled": self.query_bool(f"{c}:DISPlay?"),
                "scale_v_per_div": self.query_float(f"{c}:SCALe?"),
                "offset_v": self.query_float(f"{c}:OFFSet?"),
                "coupling": self.query(f"{c}:COUPling?"),
                "probe_ratio": self.query_float(f"{c}:PROBe?"),
                "bandwidth_limit": self.query(f"{c}:BWLimit?"),
            }

    def timebase(self) -> dict[str, float]:
        with self.t.lock:
            return {
                "scale_s_per_div": self.query_float(":TIMebase:MAIN:SCALe?"),
                "offset_s": self.query_float(":TIMebase:MAIN:OFFSet?"),
            }

    def trigger(self) -> dict[str, object]:
        with self.t.lock:
            info: dict[str, object] = {
                "type": self.query(":TRIGger:MODE?"),
                "sweep": self.query(":TRIGger:SWEep?"),
                "status": self.trigger_status(),
            }
            if str(info["type"]).upper().startswith("EDGE"):
                info.update(
                    source=self.query(":TRIGger:EDGE:SOURce?"),
                    level_v=self.query_float(":TRIGger:EDGE:LEVel?"),
                    slope=self.query(":TRIGger:EDGE:SLOPe?"),
                )
            if self.profile.extended_triggers:
                kind = str(info["type"]).upper()
                if kind.startswith("PULS"):
                    info.update(
                        source=self.query(":TRIGger:PULSe:SOURce?"),
                        level_v=self.query_float(":TRIGger:PULSe:LEVel?"),
                        polarity=self.query(":TRIGger:PULSe:POLarity?"),
                        when=self.query(":TRIGger:PULSe:WHEN?"),
                        lower_width_s=self.query_float(":TRIGger:PULSe:LWIDth?"),
                        upper_width_s=self.query_float(":TRIGger:PULSe:UWIDth?"),
                    )
                elif kind.startswith("TIM"):
                    info.update(
                        source=self.query(":TRIGger:TIMeout:SOURce?"),
                        level_v=self.query_float(":TRIGger:TIMeout:LEVel?"),
                        slope=self.query(":TRIGger:TIMeout:SLOPe?"),
                        timeout_s=self.query_float(":TRIGger:TIMeout:TIME?"),
                    )
                info.update(
                    coupling=self.query(":TRIGger:COUPling?"),
                    holdoff_s=self.query_float(":TRIGger:HOLDoff?"),
                    noise_reject=self.query_bool(":TRIGger:NREJect?"),
                )
            return info

    def acquisition(self) -> dict[str, object]:
        with self.t.lock:
            depth: int | str = self.query(":ACQuire:MDEPth?")
            try:
                depth = int(float(depth))
            except ValueError:
                pass  # "AUTO"
            return {
                "sample_rate_sa_s": self.query_float(":ACQuire:SRATe?"),
                "memory_depth": depth,
                "type": self.query(":ACQuire:TYPE?"),
                "averages": self.query_int(":ACQuire:AVERages?"),
            }

    def set_acquisition(
        self,
        type: str | None = None,  # noqa: A002 - mirrors the SCPI node
        averages: int | None = None,
        memory_depth: int | str | None = None,
    ) -> dict[str, object]:
        """``:ACQuire:TYPE`` / ``:AVERages`` / ``:MDEPth``. Unsupported values are reported from the
        scope's error queue (each model accepts its own set of types and depths)."""
        commands = []
        if type is not None:
            if type not in _ACQ_TYPES:
                raise InstrumentProtocolError(f"Acquisition type must be one of {', '.join(_ACQ_TYPES)}.")
            commands.append(f":ACQuire:TYPE {_ACQ_TYPES[type]}")
        if averages is not None:
            if averages < 2 or averages & (averages - 1):
                raise InstrumentProtocolError("The number of averages must be a power of two (2, 4, 8, ...).")
            commands.append(f":ACQuire:AVERages {averages}")
        if memory_depth is not None:
            if isinstance(memory_depth, str):
                if memory_depth.strip().upper() != "AUTO":
                    raise InstrumentProtocolError("memory_depth must be a number of points or 'auto'.")
                commands.append(":ACQuire:MDEPth AUTO")
            else:
                commands.append(f":ACQuire:MDEPth {int(memory_depth)}")
        if commands:
            self.send(*commands)
        result = self.acquisition()
        if type is not None:
            want = _ACQ_TYPES[type].upper()
            deadline = time.monotonic() + 1.5
            while not want.startswith(str(result["type"]).upper()) and time.monotonic() < deadline:
                time.sleep(0.1)
                result = self.acquisition()
            if not want.startswith(str(result["type"]).upper()):
                # Seen on a DHO804 (firmware 00.01.05): once in AVERages the scope can accept
                # ":ACQuire:TYPE NORMal" / "PEAK" without an error and stay in AVERages.
                raise InstrumentProtocolError(
                    f"The scope accepted ':ACQuire:TYPE {_ACQ_TYPES[type]}' without an error but is still in "
                    f"{result['type']} mode. Change the acquisition mode on the front panel (Acquire menu); "
                    "other settings in this call were applied."
                )
        return result

    def trigger_status(self) -> str:
        """TD (triggered), WAIT, RUN, AUTO or STOP."""
        return self.query(":TRIGger:STATus?").upper()

    # ------------------------------------------------------------ control

    def autoscale(self) -> None:
        self.write(self.profile.autoscale_command)
        self.wait_complete(timeout=15.0)
        self.check_errors(context=self.profile.autoscale_command)

    def run(self) -> None:
        self.send(":RUN")

    def stop(self) -> None:
        self.send(":STOP")

    def force_trigger(self) -> None:
        self.send(":TFORce")

    def single(self, wait_s: float = 0.0) -> str:
        """Arm a single acquisition. With ``wait_s`` > 0, poll :TRIGger:STATus? until the scope
        has stopped (acquired) or the time runs out. Returns the last status."""
        self.send(":SINGle")
        status = self.trigger_status()
        if wait_s <= 0:
            return status
        deadline = time.monotonic() + wait_s
        armed_by = time.monotonic() + min(1.0, wait_s)
        # Wait for the scope to leave the previous STOP state (arming), then for it to stop again.
        while status == "STOP" and time.monotonic() < armed_by:
            time.sleep(0.02)
            status = self.trigger_status()
        while status != "STOP" and time.monotonic() < deadline:
            time.sleep(0.05)
            status = self.trigger_status()
        return status

    def set_channel(
        self,
        channel: int,
        enabled: bool | None = None,
        scale_v_per_div: float | None = None,
        offset_v: float | None = None,
        coupling: str | None = None,
        probe_ratio: float | None = None,
        bandwidth_limit_20mhz: bool | None = None,
    ) -> dict[str, object]:
        self._check_channel(channel)
        c = f":CHANnel{channel}"
        commands = []
        if probe_ratio is not None:  # first: the probe ratio changes the valid scale range
            if not any(abs(probe_ratio - p) <= 1e-9 * max(1.0, p) for p in self.profile.probe_ratios):
                valid = ", ".join(f"{p:g}" for p in self.profile.probe_ratios)
                raise InstrumentProtocolError(f"Probe ratio {probe_ratio:g} is not valid on the {self.profile.family}; use one of {valid}.")
            commands.append(f"{c}:PROBe {_num(probe_ratio)}")
        if scale_v_per_div is not None:
            commands.append(f"{c}:SCALe {_num(scale_v_per_div)}")
        if offset_v is not None:
            commands.append(f"{c}:OFFSet {_num(offset_v)}")
        if coupling is not None:
            if coupling.upper() not in _COUPLINGS:
                raise InstrumentProtocolError("Coupling must be AC, DC or GND.")
            commands.append(f"{c}:COUPling {coupling.upper()}")
        if bandwidth_limit_20mhz is not None:
            commands.append(f"{c}:BWLimit {'20M' if bandwidth_limit_20mhz else 'OFF'}")
        if enabled is not None:
            commands.append(f"{c}:DISPlay {'ON' if enabled else 'OFF'}")
        if commands:
            self.send(*commands)
        return self.channel_settings(channel)

    def set_timebase(self, scale_s_per_div: float | None = None, offset_s: float | None = None) -> dict[str, float]:
        commands = []
        if scale_s_per_div is not None:
            commands.append(f":TIMebase:MAIN:SCALe {_num(scale_s_per_div)}")
        if offset_s is not None:
            commands.append(f":TIMebase:MAIN:OFFSet {_num(offset_s)}")
        if commands:
            self.send(*commands)
        return self.timebase()

    def set_edge_trigger(
        self,
        source_channel: int | None = None,
        level_v: float | None = None,
        slope: Literal["rising", "falling", "either"] | None = None,
        sweep: Literal["auto", "normal", "single"] | None = None,
    ) -> dict[str, object]:
        commands = [":TRIGger:MODE EDGE"]
        if source_channel is not None:
            self._check_channel(source_channel)
            commands.append(f":TRIGger:EDGE:SOURce CHANnel{source_channel}")
        if slope is not None:
            commands.append(f":TRIGger:EDGE:SLOPe {_SLOPES[slope]}")
        if level_v is not None:
            commands.append(f":TRIGger:EDGE:LEVel {_num(level_v)}")
        if sweep is not None:
            commands.append(f":TRIGger:SWEep {_SWEEPS[sweep]}")
        self.send(*commands)
        return self.trigger()

    def set_trigger_options(
        self, coupling: str | None = None, holdoff_s: float | None = None, noise_reject: bool | None = None
    ) -> dict[str, object]:
        commands = []
        if coupling is not None:
            if coupling not in _TRIGGER_COUPLINGS:
                raise InstrumentProtocolError(f"Trigger coupling must be one of {', '.join(_TRIGGER_COUPLINGS)}.")
            commands.append(f":TRIGger:COUPling {_TRIGGER_COUPLINGS[coupling]}")
        if holdoff_s is not None:
            commands.append(f":TRIGger:HOLDoff {_num(holdoff_s)}")
        if noise_reject is not None:
            commands.append(f":TRIGger:NREJect {'ON' if noise_reject else 'OFF'}")
        if commands:
            self.send(*commands)
        return self.trigger()

    def _require_extended_triggers(self) -> None:
        if not self.profile.extended_triggers:
            raise InstrumentProtocolError(
                f"Pulse-width and timeout triggers are only implemented for the DHO family, not {self.profile.family}."
            )

    def set_pulse_trigger(
        self,
        source_channel: int | None = None,
        polarity: str | None = None,
        when: str | None = None,
        lower_width_s: float | None = None,
        upper_width_s: float | None = None,
        level_v: float | None = None,
        sweep: str | None = None,
    ) -> dict[str, object]:
        self._require_extended_triggers()
        commands = [":TRIGger:MODE PULSe"]
        if source_channel is not None:
            self._check_channel(source_channel)
            commands.append(f":TRIGger:PULSe:SOURce CHANnel{source_channel}")
        if polarity is not None:
            commands.append(f":TRIGger:PULSe:POLarity {_POLARITIES[polarity]}")
        if when is not None:
            commands.append(f":TRIGger:PULSe:WHEN {_PULSE_WHEN[when]}")
        if upper_width_s is not None:
            commands.append(f":TRIGger:PULSe:UWIDth {_num(upper_width_s)}")
        if lower_width_s is not None:
            commands.append(f":TRIGger:PULSe:LWIDth {_num(lower_width_s)}")
        if level_v is not None:
            commands.append(f":TRIGger:PULSe:LEVel {_num(level_v)}")
        if sweep is not None:
            commands.append(f":TRIGger:SWEep {_SWEEPS[sweep]}")
        self.send(*commands)
        return self.trigger()

    def set_timeout_trigger(
        self,
        source_channel: int | None = None,
        slope: str | None = None,
        timeout_s: float | None = None,
        level_v: float | None = None,
        sweep: str | None = None,
    ) -> dict[str, object]:
        self._require_extended_triggers()
        commands = [":TRIGger:MODE TIMeout"]
        if source_channel is not None:
            self._check_channel(source_channel)
            commands.append(f":TRIGger:TIMeout:SOURce CHANnel{source_channel}")
        if slope is not None:
            commands.append(f":TRIGger:TIMeout:SLOPe {_SLOPES[slope]}")
        if timeout_s is not None:
            commands.append(f":TRIGger:TIMeout:TIME {_num(timeout_s)}")
        if level_v is not None:
            commands.append(f":TRIGger:TIMeout:LEVel {_num(level_v)}")
        if sweep is not None:
            commands.append(f":TRIGger:SWEep {_SWEEPS[sweep]}")
        self.send(*commands)
        return self.trigger()

    # ------------------------------------------------------------ measurements

    def measure(self, channel: int, items: list[str]) -> dict[str, float | None]:
        """``:MEASure:ITEM? <item>,CHANnel<n>`` for each item; None where the scope returns 9.9E37."""
        self._check_channel(channel)
        out: dict[str, float | None] = {}
        with self.t.lock:
            for item in items:
                key = item.lower()
                if key not in MEASUREMENTS:
                    raise InstrumentProtocolError(f"Unknown measurement {item!r}; use one of {', '.join(MEASUREMENTS)}.")
                if self.profile.family == "DHO" and key in _DHO_HANG_SUSPECTS:
                    # A DHO804 (firmware 00.01.05) stopped answering all SCPI, until power-cycled, on
                    # ":MEASure:ITEM? VTOP,CHANnel1" and on a call that also had VAMP, VBASe and OVERshoot.
                    raise InstrumentProtocolError(
                        f"Measurement {key!r} is disabled on the DHO family: querying it has frozen a DHO804's "
                        "remote interface until a power cycle. Use vpp / vmax / vmin, or compute it from "
                        "capture_waveform. Nothing was sent to the scope."
                    )
                value = self.query_float(f":MEASure:ITEM? {MEASUREMENTS[key][0]},CHANnel{channel}")
                out[key] = None if abs(value) >= INVALID_THRESHOLD else value
        return out

    # ------------------------------------------------------------ waveforms

    def prepare_capture(
        self, channel: int, mode: Literal["screen", "memory"] = "screen", word: bool = False
    ) -> Preamble:
        """Select the source, mode and BYTE (or, with ``word``, 16-bit WORD) format and return the
        preamble (no data read yet)."""
        self._check_channel(channel)
        if word and not self.profile.word_format:
            raise InstrumentProtocolError(
                f"WORD (16-bit) waveform data is only verified on the DHO family, not {self.profile.family}; "
                "use sample_format='byte'."
            )
        with self.t.lock:
            if not self.query_bool(f":CHANnel{channel}:DISPlay?"):
                raise InstrumentProtocolError(f"CH{channel} is switched off; enable it with set_channel first.")
            if mode == "memory":
                status = self.trigger_status()
                if status != "STOP":
                    raise InstrumentProtocolError(
                        f"Deep-memory (RAW) data can only be read while the scope is stopped (trigger status is "
                        f"{status}). Call `stop` or `single` first, or use mode='screen'."
                    )
            self.send(
                f":WAVeform:SOURce CHANnel{channel}",
                f":WAVeform:MODE {'RAW' if mode == 'memory' else 'NORMal'}",
                f":WAVeform:FORMat {'WORD' if word else 'BYTE'}",
            )
            if mode == "memory" and self.profile.family == "DHO":
                # On the DHO the preamble's point count is the current STARt..STOP window, not the
                # record length: select the whole record so `points` is the memory depth.
                self._set_window(1, self.query_int(":ACQuire:MDEPth?"))
            return Preamble.parse(self.query(":WAVeform:PREamble?"))

    def read_capture(self, channel: int, preamble: Preamble, mode: Literal["screen", "memory"] = "screen") -> Waveform:
        """Read the data selected by :meth:`prepare_capture` and scale it to volts and seconds."""
        if preamble.format not in (0, 1):
            raise InstrumentProtocolError(
                f"Expected BYTE (0) or WORD (1) waveform format, the preamble reports {preamble.format}."
            )
        word = preamble.format == 1
        width = 2 if word else 1  # bytes per point
        raw = bytearray()
        with self.t.lock:
            if mode == "screen":
                total = self.profile.screen_points
                self.send(":WAVeform:STARt 1", f":WAVeform:STOP {total}")
                raw += self.query_block(":WAVeform:DATA?", timeout=10.0)
            else:
                total = preamble.points
                batch = self.profile.memory_batch_points
                for start in range(1, total + 1, batch):
                    stop = min(start + batch - 1, total)
                    if self.profile.family == "DHO":
                        self._set_window(start, stop)
                    else:
                        self.send(f":WAVeform:STARt {start}", f":WAVeform:STOP {stop}")
                    chunk = self.query_block(":WAVeform:DATA?", timeout=60.0)
                    if not chunk:
                        raise InstrumentProtocolError(
                            "The acquisition memory is empty: the scope was stopped before it finished an "
                            "acquisition at the current memory depth (e.g. right after a memory-depth, "
                            "timebase or acquisition-type change). Call `run`, wait a moment (or use "
                            "`single`), `stop`, and capture again."
                        )
                    if len(chunk) != (stop - start + 1) * width:
                        raise InstrumentProtocolError(
                            f"Asked for points {start}-{stop} ({(stop - start + 1) * width} bytes) but the scope "
                            f"returned {len(chunk)} bytes."
                        )
                    raw += chunk
            if not raw:
                raise InstrumentProtocolError("The scope returned no waveform data. Is the channel enabled?")
            # A front-panel change (V/div, offset, timebase) while the data was read would make the
            # preamble used for scaling wrong: read it again and refuse mismatched data.
            after = Preamble.parse(self.query(":WAVeform:PREamble?"))
        if not preamble.same_scaling(after):
            raise InstrumentProtocolError(
                "The scope's vertical or timebase settings changed while the waveform was being read, so it "
                "cannot be scaled reliably. Nothing was returned; capture again."
            )
        p = preamble
        if word:
            if len(raw) % 2:
                raise InstrumentProtocolError(f"WORD waveform data has an odd number of bytes ({len(raw)}).")
            codes: tuple[int, ...] | bytearray = struct.unpack(f"<{len(raw) // 2}H", raw)
        else:
            codes = raw
        top = 65535 if word else 255
        volts = [(b - p.yorigin - p.yreference) * p.yincrement for b in codes]
        times = [(i - p.xreference) * p.xincrement + p.xorigin for i in range(len(codes))]
        # The lowest and highest codes are the ADC limits in both screen (NORMal) and memory (RAW) data.
        clipped = sum(1 for b in codes if b in (0, top)) / len(codes)
        return Waveform(channel, mode, times, volts, p, clipped, 16 if word else 8)

    # ------------------------------------------------------------ screenshot

    def screenshot(self) -> tuple[bytes, str]:
        """The current display as an image (PNG, or BMP on the MSO5000)."""
        data = self.query_block(self.profile.screenshot_query, timeout=30.0)
        fmt = self.profile.screenshot_format
        magic = {"png": b"\x89PNG", "bmp": b"BM"}[fmt]
        if not data.startswith(magic):
            raise InstrumentProtocolError(f"The screenshot data does not look like a {fmt.upper()} image ({data[:8]!r}...).")
        return data, fmt
