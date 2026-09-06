"""Optional at-rest encryption for API keys stored locally (PLS-DO S2).

Without this, provider API keys sit as plain JSON text inside the `settings`
table in `~/.promptmeter/promptmeter.db` — as safe as the SQLite file itself,
no safer. On Windows, real encryption is available for free: DPAPI, reached
through `ctypes` (stdlib only — no `pip install keyring`, which this project's
zero-dependency rule forbids). It is scoped to the **current Windows user**,
never machine scope (`CRYPTPROTECT_LOCAL_MACHINE` is deliberately never
passed) — machine scope would let any account on this PC decrypt the key,
which defeats the point.

macOS and Linux have no equivalent in the standard library (the real
keychains there need a third-party package or a D-Bus call this project isn't
taking on). Keys on those platforms stay plaintext, same as before — honest
about it rather than silently only "sometimes" protecting keys. See
`providers.public_config()`'s `key_storage` field, which the Setup page reads
to say so.

DPAPI keys are tied to the Windows user account (and, per Microsoft's own
docs, in some configurations to the machine) that created them. If this
database is copied to another machine, or Windows is reinstalled, decryption
will fail — `unprotect()` returns `None` in that case rather than raising, and
the caller's job is to say "this key can't be read on this machine — re-enter
it," not to crash.
"""
from __future__ import annotations

import base64
import ctypes
import sys

AVAILABLE = sys.platform == "win32"

CRYPTPROTECT_UI_FORBIDDEN = 0x1   # never let DPAPI pop a UI prompt


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


_BLOB_P = ctypes.POINTER(_DataBlob)
_PROTOTYPED = False


def _prep() -> None:
    """Declare argtypes/restype explicitly. Without this, ctypes' default
    argument marshaling silently mis-marshals the struct pointers on some
    Python builds and CryptProtectData fails — this was caught by hand-testing
    against a real key on this machine, not assumed to just work.
    """
    global _PROTOTYPED
    if _PROTOTYPED:
        return
    crypt32 = ctypes.windll.crypt32                        # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32                       # type: ignore[attr-defined]
    sig = [_BLOB_P, ctypes.c_wchar_p, _BLOB_P, ctypes.c_void_p,
           ctypes.c_void_p, ctypes.c_uint32, _BLOB_P]
    crypt32.CryptProtectData.argtypes = sig
    crypt32.CryptProtectData.restype = ctypes.c_int
    crypt32.CryptUnprotectData.argtypes = sig
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    _PROTOTYPED = True


def _crypt32():
    _prep()
    return ctypes.windll.crypt32          # type: ignore[attr-defined]


def _kernel32():
    _prep()
    return ctypes.windll.kernel32         # type: ignore[attr-defined]


def protect(plaintext: str) -> str | None:
    """Encrypt `plaintext` for the current Windows user. Returns a base64
    string to store, or `None` if unavailable (non-Windows) or on any
    failure — the caller always needs a plaintext-storage fallback ready
    regardless of which reason it got `None` for.
    """
    if not AVAILABLE or not plaintext:
        return None
    try:
        data_in = plaintext.encode("utf-8")
        buf_in = ctypes.create_string_buffer(data_in, len(data_in))
        blob_in = _DataBlob(len(data_in), ctypes.cast(buf_in, ctypes.c_void_p))
        blob_out = _DataBlob()
        ok = _crypt32().CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
        if not ok:
            return None
        try:
            out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32().LocalFree(blob_out.pbData)
        return base64.b64encode(out).decode("ascii")
    except Exception:                                      # noqa: BLE001
        return None


def unprotect(blob_b64: str) -> str | None:
    """Decrypt a value `protect()` produced. `None` on any failure, including
    the honest, expected case of a database that moved to a different
    machine or user — DPAPI keys are not portable.
    """
    if not AVAILABLE or not blob_b64:
        return None
    try:
        raw = base64.b64decode(blob_b64)
        buf_in = ctypes.create_string_buffer(raw, len(raw))
        blob_in = _DataBlob(len(raw), ctypes.cast(buf_in, ctypes.c_void_p))
        blob_out = _DataBlob()
        ok = _crypt32().CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
        if not ok:
            return None
        try:
            out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32().LocalFree(blob_out.pbData)
        return out.decode("utf-8")
    except Exception:                                       # noqa: BLE001
        return None
