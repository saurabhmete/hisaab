"""Live EUR→INR reference rate from the ECB via frankfurter.app.

Best-effort only: the app must keep working fully offline, so failures are
cached briefly and reported as None — callers fall back to the last stored rate.
"""
import json
import time
import urllib.request

API_URL = "https://api.frankfurter.dev/v1/latest?base=EUR&symbols=INR"
_OK_TTL = 6 * 3600      # refresh a good rate every 6 hours (ECB updates daily)
_FAIL_TTL = 10 * 60     # don't retry a failing network more than every 10 min

_cache = {"at": 0.0, "rate": None}


def eur_inr() -> float | None:
    now = time.time()
    ttl = _OK_TTL if _cache["rate"] is not None else _FAIL_TTL
    if now - _cache["at"] < ttl:
        return _cache["rate"]
    rate = None
    try:
        # Cloudflare 403s the default Python-urllib agent; identify normally.
        req = urllib.request.Request(API_URL, headers={"User-Agent": "hisaab/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            rate = round(float(json.load(resp)["rates"]["INR"]), 3)
    except Exception:
        pass
    _cache.update(at=now, rate=rate)
    return rate
