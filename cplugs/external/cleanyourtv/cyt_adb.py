"""The device side: ADB over TCP in pure Python (vendored adb_shell, as upstream tools/tv.py does; no adb binary, no adb server).

Keys: one per TV, under the plugin's own data folder, mode 0600, never the user's own adb key. Nothing here touches a TV unless a caller asks.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Optional

ADB_PORT = 5555
CONNECT_TIMEOUT_S = 30.0
READ_TIMEOUT_S = 120.0


class DeviceError(Exception):
    """The TV could not be reached, or the ADB link failed. The message is plain text for the owner."""


class _NoPubkey(Exception):
    """Raised by the probe signer instead of sending the public key (so a revoked key never triggers the TV's allow prompt)."""


def make_key(key_path: Path) -> None:
    """Create a fresh RSA ADB key pair at key_path (+ .pub), folder 0700, files 0600. No-op when it exists. Inputs: key_path."""
    key_path = Path(key_path)
    key_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(key_path.parent, 0o700)
    if key_path.exists():
        os.chmod(key_path, 0o600)
        return
    from adb_shell.auth.keygen import keygen  # noqa: PLC0415  (needs the app's cryptography)
    old = os.umask(0o077)
    try:
        keygen(str(key_path))
    finally:
        os.umask(old)
    os.chmod(key_path, 0o600)
    os.chmod(str(key_path) + ".pub", 0o600)


def _signer(key_path: Path):
    """The signer for a key pair. Inputs: key_path. Output: PythonRSASigner."""
    from adb_shell.auth.sign_pythonrsa import PythonRSASigner  # noqa: PLC0415
    return PythonRSASigner(Path(str(key_path) + ".pub").read_text(), Path(key_path).read_text())


class AdbTcpDevice:
    """One authorized ADB-over-TCP connection to a TV. Inputs: ip; port; key_path. Use as a context manager."""

    def __init__(self, ip: str, port: int, key_path: Path) -> None:
        """Connect now; raises DeviceError. The key is only ever the TV's own."""
        from adb_shell.adb_device import AdbDeviceTcp  # noqa: PLC0415
        self._dev = AdbDeviceTcp(ip, port, default_transport_timeout_s=CONNECT_TIMEOUT_S)
        try:
            self._dev.connect(rsa_keys=[_signer(key_path)], transport_timeout_s=CONNECT_TIMEOUT_S, auth_timeout_s=60)
        except Exception as exc:  # noqa: BLE001  adb_shell raises its own family plus OSError; all mean "could not connect"
            raise DeviceError(f"could not connect to {ip}:{port} ({type(exc).__name__}); is network debugging on, and did you tap allow?") from exc

    def shell(self, command: str) -> str:
        """Run one shell command on the TV. Output: its text."""
        try:
            return self._dev.shell(command, read_timeout_s=READ_TIMEOUT_S) or ""
        except Exception as exc:  # noqa: BLE001
            raise DeviceError(f"the tv did not answer ({type(exc).__name__})") from exc

    def reboot(self) -> None:
        """Ask the TV to reboot (native, waits for ADB's OKAY)."""
        try:
            self._dev.reboot()
        except Exception as exc:  # noqa: BLE001
            raise DeviceError(f"reboot request failed ({type(exc).__name__})") from exc

    def close(self) -> None:
        """Close the link."""
        try:
            self._dev.close()
        except Exception:  # noqa: BLE001, S110  closing a dead link may raise; nothing to do about it
            pass

    def __enter__(self) -> "AdbTcpDevice":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def verify_revoked(ip: str, port: int, key_path: Path) -> Optional[bool]:
    """Is this key NO LONGER trusted by the TV? Offers the signature only, never the public key.

    Inputs: ip; port; key_path. Output: True (TV re-challenged: revoked), False (TV accepted the signature: still authorized),
    None (could not determine; counts as not verified).
    """
    from adb_shell.adb_device import AdbDeviceTcp  # noqa: PLC0415
    inner = _signer(key_path)

    class Probe:
        def Sign(self, data):  # noqa: N802  (adb_shell's signer API)
            return inner.Sign(data)

        def GetPublicKey(self):  # noqa: N802
            raise _NoPubkey()

    dev = AdbDeviceTcp(ip, port, default_transport_timeout_s=15.0)
    try:
        dev.connect(rsa_keys=[Probe()], transport_timeout_s=15.0, auth_timeout_s=5)
        return False
    except _NoPubkey:
        return True
    except Exception:  # noqa: BLE001  anything else (refused, timeout, protocol) is "could not determine"
        return None
    finally:
        try:
            dev.close()
        except Exception:  # noqa: BLE001, S110
            pass


def probe_port(ip: str, port: int, timeout_s: float = 3.0) -> str:
    """TCP connect check, upstream adb-closed.py semantics. Output: 'open', 'closed' (refused or silent) or 'unknown' (could not check)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout_s):
            return "open"
    except ConnectionRefusedError:
        return "closed"
    except socket.timeout:
        return "closed"
    except OSError:
        return "unknown"
