"""Find the oscilloscope without being told its address.

A server started with no ``--address`` looks for a Rigol instrument on USB (vendor ID 0x1AB1) so the
same configuration works on any computer and with any scope. Two PyVISA backends can reach a USB
instrument and which one works depends on the driver bound to it:

* ``@ivi`` - a vendor VISA library (NI-VISA, Keysight IO Libraries, R&S VISA) with its USBTMC driver;
* ``@py``  - pure-Python ``pyvisa-py`` over ``libusb`` (Linux / macOS as-is; on Windows the scope
  must be re-bound to WinUSB, e.g. with Zadig).

Both are tried, vendor VISA first.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from labmcp import InstrumentConnectionError

RIGOL_VENDOR_ID = 0x1AB1
BACKENDS = ("@ivi", "@py")
_USB_RE = re.compile(r"^USB\d*::(?P<vid>[^:]+)::(?P<pid>[^:]+)::(?P<serial>[^:]+)(?:::\d+)?::INSTR$", re.IGNORECASE)

SETUP_HINT = (
    "Check that the scope is on, that the cable is in its USB DEVICE port (the square USB-B socket on the "
    "back, not a front-panel host port) and that it is a data cable. On Windows the scope also needs a "
    "driver: either a VISA runtime such as NI-VISA (its 'USB Test and Measurement Device' driver), or "
    "WinUSB installed with Zadig for the pure-Python backend. Alternatively put the scope on the network "
    "and pass --address TCPIP0::<ip>::INSTR (or tcp://<ip>:5555), which needs no driver."
)


def _default_manager(backend: str) -> Any:
    import pyvisa

    return pyvisa.ResourceManager(backend)


def _vendor_id(resource: str) -> int | None:
    m = _USB_RE.match(resource.strip())
    if not m:
        return None
    try:
        return int(m["vid"], 0)  # "0x1AB1" or "6833"
    except ValueError:
        return None


def _list_usb(backend: str, manager: Callable[[str], Any]) -> list[str]:
    rm = manager(backend)
    try:
        return [r for r in rm.list_resources("USB?*INSTR") if _vendor_id(r) == RIGOL_VENDOR_ID]
    finally:
        try:
            rm.close()
        except Exception:  # noqa: BLE001 - best effort
            pass


def discover(serial: str | None = None, manager: Callable[[str], Any] = _default_manager) -> str:
    """Return ``<USB resource>?backend=<backend>`` for the Rigol instrument on USB.

    ``serial`` picks one when several are connected. Raises ``InstrumentConnectionError`` with setup
    advice when none (or more than one) is found.
    """
    problems: list[str] = []
    for backend in BACKENDS:
        try:
            found = _list_usb(backend, manager)
        except Exception as exc:  # noqa: BLE001 - a missing VISA library, no libusb, ...
            problems.append(f"{backend}: not usable ({type(exc).__name__}: {str(exc).splitlines()[0][:120]})")
            continue
        if serial:
            found = [r for r in found if (m := _USB_RE.match(r)) and m["serial"].upper() == serial.strip().upper()]
        if len(found) == 1:
            return f"{found[0]}?backend={backend}"
        if len(found) > 1:
            raise InstrumentConnectionError(
                f"Several Rigol instruments are on USB ({', '.join(found)}). Choose one with "
                "--option serial=<serial number> or --address <resource>."
            )
        problems.append(f"{backend}: no Rigol USB instrument" + (f" with serial {serial}" if serial else ""))
    raise InstrumentConnectionError(
        "No Rigol oscilloscope was found on USB (" + "; ".join(problems) + "). " + SETUP_HINT
    )


def resolve_backend(address: str, manager: Callable[[str], Any] = _default_manager) -> str:
    """Add ``?backend=`` to a bare USB VISA resource by finding the backend that can see it.

    Anything else (an address that already names a backend, LAN, serial, tcp://) is returned as is.
    """
    bare = address.strip()
    if bare.lower().startswith("visa://"):
        bare = bare[len("visa://") :]
    if "?" in bare or not _USB_RE.match(bare):
        return address
    for backend in BACKENDS:
        try:
            rm = manager(backend)
        except Exception:  # noqa: BLE001
            continue
        try:
            if bare.upper() in (r.upper() for r in rm.list_resources("USB?*INSTR")):
                return f"{bare}?backend={backend}"
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                rm.close()
            except Exception:  # noqa: BLE001
                pass
    return address  # let the transport report the failure for this exact resource
