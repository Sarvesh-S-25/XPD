"""Optional planning providers.

The estimator never asks a model what something will cost — models are not
calibrated for that, and the answer would sound authoritative while being a
guess. Instead a provider does the one thing regex cannot: **enumerate the
work**. PromptMeter then converts that list into turns and dollars with its own
numbers, which it can check against your history.

Providers, all optional:
  heuristic  — the default. No model, no key, no network, no cost.
  ollama     — a model running on your own machine. Free, needs RAM.
  anthropic / openai / gemini — an API key. Costs a fraction of a cent.

Only stdlib. urllib for HTTP, no SDKs to install.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from . import db, dpapi

TIMEOUT = 60

SYSTEM = (
    "You break a software request into the concrete pieces of work it implies. "
    "You do NOT estimate tokens, time or cost. Reply with JSON only."
)

PROMPT = """Break this request into the concrete steps someone would actually have to do.
Include work the request implies but does not state.

Request:
\"\"\"{prompt}\"\"\"

Reply with ONLY this JSON, no prose, no code fences:
{{"items":[{{"title":"short imperative title","detail":"one line","complexity":1-5}}],
 "notes":"one sentence on the biggest risk or unknown"}}

complexity: 1 = trivial edit, 3 = ordinary feature, 5 = hard or research-heavy.
Aim for 3-20 items. Be realistic, not exhaustive."""

REGISTRY = {
    "heuristic": {"label": "Built-in (no model)", "needs_key": False, "local": True,
                  "note": "Keyword scope analysis. Free, instant, offline."},
    "ollama":    {"label": "Ollama (on your PC)", "needs_key": False, "local": True,
                  "default_model": "llama3.2:3b",
                  "note": "Runs a small model locally. Free, no key, needs a few GB of RAM."},
    "anthropic": {"label": "Claude API key", "needs_key": True, "local": False,
                  "default_model": "claude-haiku-4-5",
                  "note": "Best plans. Roughly half a cent per estimate."},
    "openai":    {"label": "OpenAI API key", "needs_key": True, "local": False,
                  "default_model": "gpt-4o-mini",
                  "note": "Cheap and good."},
    "gemini":    {"label": "Gemini API key", "needs_key": True, "local": False,
                  "default_model": "gemini-2.0-flash",
                  "note": "Cheap, generous free tier."},
}


# ---------------------------------------------------------------- config
#
# Keys are stored under settings["provider_config"]["keys"][provider] as one
# of three shapes: {"enc": "<base64>"} (DPAPI-protected, Windows), {"plain":
# "..."} (no OS keychain available, stored honestly as plaintext), or a bare
# string (the pre-encryption format, read for backward compatibility with a
# database written before this existed). config() is the ONLY place that
# decrypts — save_config() must never round-trip through it, or every
# already-encrypted key still on disk would be re-saved back in plaintext the
# next time any single key changes.

def _decrypt_key(entry) -> str:
    if isinstance(entry, str):
        return entry                        # legacy pre-encryption shape
    if not isinstance(entry, dict):
        return ""
    if "enc" in entry:
        return dpapi.unprotect(entry["enc"]) or ""
    return str(entry.get("plain") or "")


def _encrypt_key(value: str) -> dict:
    blob = dpapi.protect(value)
    return {"enc": blob} if blob is not None else {"plain": value}


def _key_storage(entry) -> str:
    if isinstance(entry, dict) and "enc" in entry:
        return "encrypted"
    return "plaintext"                      # legacy string, or {"plain": ...}


def config() -> dict:
    c = db.get_setting("provider_config") or {}
    raw_keys = c.get("keys", {})
    return {
        "active": c.get("active", "heuristic"),
        "model": c.get("model", ""),
        "base_url": c.get("base_url", "http://localhost:11434"),
        "keys": {k: _decrypt_key(v) for k, v in raw_keys.items()},
        "auto": bool(c.get("auto", False)),  # plan every prompt, or only on request
    }


def public_config() -> dict:
    """Same, but keys masked — the UI never receives a full key."""
    c = config()
    raw_keys = (db.get_setting("provider_config") or {}).get("keys", {})
    return {
        "active": c["active"], "model": c["model"], "base_url": c["base_url"],
        "auto": c["auto"],
        "keys": {k: _mask(v) for k, v in c["keys"].items() if v},
        # per key: "encrypted" (DPAPI), "plaintext" (no OS keychain here, or a
        # key stored before this existed), or "unreadable" (an encrypted
        # entry exists but this machine/user can't decrypt it — DPAPI keys
        # aren't portable; re-entering the key is the only fix).
        "key_storage": {
            k: ("unreadable" if isinstance(v, dict) and "enc" in v and not c["keys"].get(k)
                else _key_storage(v))
            for k, v in raw_keys.items() if v
        },
        "dpapi_available": dpapi.AVAILABLE,
        "registry": [{"id": k, **v} for k, v in REGISTRY.items()],
    }


def _mask(k: str) -> str:
    k = str(k or "")
    return (k[:6] + "…" + k[-4:]) if len(k) > 12 else "…"


def save_config(patch: dict) -> dict:
    raw = db.get_setting("provider_config") or {}
    for f in ("active", "model", "base_url", "auto"):
        if f in patch and patch[f] is not None:
            raw[f] = patch[f]
    keys = raw.get("keys", {})
    key = patch.get("key")
    if key is not None:
        target = patch.get("key_for") or raw.get("active", "heuristic")
        if key == "":
            keys.pop(target, None)
        elif "…" not in key:            # ignore the masked value being echoed back
            keys[target] = _encrypt_key(key.strip())
    raw["keys"] = keys
    db.set_setting("provider_config", raw)
    return public_config()


def model_for(name: str) -> str:
    c = config()
    return c["model"] or REGISTRY.get(name, {}).get("default_model", "")


# ---------------------------------------------------------------- calling

def plan(prompt: str, provider: str | None = None) -> dict:
    """Ask the configured provider to enumerate the work. Never raises."""
    c = config()
    name = provider or c["active"]
    if name == "heuristic":
        return {"ok": False, "provider": "heuristic",
                "error": "The built-in provider does not draft plans."}

    body = PROMPT.format(prompt=prompt.strip()[:6000])
    started = time.time()
    try:
        if name == "ollama":
            raw = _ollama(body, c)
        elif name == "anthropic":
            raw = _anthropic(body, c)
        elif name == "openai":
            raw = _openai(body, c)
        elif name == "gemini":
            raw = _gemini(body, c)
        else:
            return {"ok": False, "provider": name, "error": f"Unknown provider '{name}'."}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:                                  # noqa: BLE001
            pass
        return {"ok": False, "provider": name,
                "error": _friendly(name, e.code, detail)}
    except urllib.error.URLError as e:
        return {"ok": False, "provider": name,
                "error": (f"Could not reach {name}. "
                          + ("Is Ollama running? Start it and try again."
                             if name == "ollama" else str(e.reason)))}
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False, "provider": name, "error": f"{type(e).__name__}: {e}"}

    parsed = _parse(raw)
    if not parsed.get("items"):
        return {"ok": False, "provider": name,
                "error": "The model replied but not with a usable plan.",
                "raw": raw[:400]}
    return {"ok": True, "provider": name, "model": model_for(name),
            "items": parsed["items"][:40], "notes": parsed.get("notes", ""),
            "seconds": round(time.time() - started, 1)}


def _friendly(name: str, code: int, detail: str) -> str:
    if code in (401, 403):
        return f"{name} rejected the key. Check it is correct and still active."
    if code == 404 and name == "ollama":
        return ("Ollama is running but does not have that model. Run "
                f"`ollama pull {model_for('ollama')}` first.")
    if code == 429:
        return f"{name} rate-limited the request. Wait a moment and retry."
    return f"{name} returned HTTP {code}. {detail}"


def _post(url: str, payload: dict, headers: dict) -> str:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _ollama(body: str, c: dict) -> str:
    base = (c["base_url"] or "http://localhost:11434").rstrip("/")
    out = _post(f"{base}/api/chat", {
        "model": model_for("ollama"),
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": body}],
        "stream": False, "format": "json",
        "options": {"temperature": 0.2},
    }, {})
    return (json.loads(out).get("message") or {}).get("content", "")


def _anthropic(body: str, c: dict) -> str:
    out = _post("https://api.anthropic.com/v1/messages", {
        "model": model_for("anthropic"), "max_tokens": 2000,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": body}],
    }, {"x-api-key": c["keys"].get("anthropic", ""),
        "anthropic-version": "2023-06-01"})
    blocks = json.loads(out).get("content") or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _openai(body: str, c: dict) -> str:
    out = _post("https://api.openai.com/v1/chat/completions", {
        "model": model_for("openai"),
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": body}],
        "response_format": {"type": "json_object"}, "temperature": 0.2,
    }, {"Authorization": "Bearer " + c["keys"].get("openai", "")})
    return json.loads(out)["choices"][0]["message"]["content"]


def _gemini(body: str, c: dict) -> str:
    model = model_for("gemini")
    key = c["keys"].get("gemini", "")
    out = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        {"systemInstruction": {"parts": [{"text": SYSTEM}]},
         "contents": [{"parts": [{"text": body}]}],
         "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}},
        {})
    cands = json.loads(out).get("candidates") or []
    parts = (cands[0].get("content") or {}).get("parts") if cands else []
    return "".join(p.get("text", "") for p in (parts or []))


def _parse(raw: str) -> dict:
    """Models wrap JSON in fences and prose. Dig it out."""
    if not raw:
        return {}
    txt = raw.strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
    try:
        d = json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return {}
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    if isinstance(d, list):
        d = {"items": d}
    items = []
    for it in (d.get("items") or d.get("steps") or d.get("tasks") or []):
        if isinstance(it, str):
            items.append({"title": it[:80], "detail": "", "complexity": 3})
            continue
        if not isinstance(it, dict):
            continue
        try:
            c = float(it.get("complexity") or 3)
        except (TypeError, ValueError):
            c = 3.0
        items.append({
            "title": str(it.get("title") or it.get("name") or it.get("step") or "")[:80],
            "detail": str(it.get("detail") or it.get("description") or "")[:240],
            "complexity": max(1.0, min(5.0, c)),
        })
    return {"items": [i for i in items if i["title"]], "notes": str(d.get("notes") or "")[:300]}


def test(provider: str | None = None) -> dict:
    """Round-trip a tiny request so failures surface at setup, not mid-estimate."""
    name = provider or config()["active"]
    if name == "heuristic":
        return {"ok": True, "provider": "heuristic",
                "message": "Built-in scope analysis is always available."}
    r = plan("Add a password reset flow to an existing web app with email delivery.", name)
    if r.get("ok"):
        return {"ok": True, "provider": name, "model": r.get("model"),
                "message": f"Working — drafted {len(r['items'])} steps in {r['seconds']}s.",
                "sample": [i["title"] for i in r["items"][:5]]}
    return {"ok": False, "provider": name, "message": r.get("error", "Unknown error.")}
