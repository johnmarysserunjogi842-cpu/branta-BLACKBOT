"""
Institution-Grade ForexFactory Economic Calendar Service

Purpose:
- Provide RAW HIGH-impact macroeconomic risk events
- Protect execution engines from news volatility
- Serve bots, APIs, dashboards, and trading systems

Guarantees:
- Cache-first (memory + disk)
- Crash-proof
- Deterministic IDs
- UTC-safe timestamps
- RAW DATA ONLY (no adapters, no NewsEvent logic)
"""

from __future__ import annotations

import json
import time
import os
import hashlib
import logging
from typing import List, Dict
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ==================================================
# CONFIGURATION
# ==================================================

FF_URL = "https://www.forexfactory.com/calendar"

CACHE_FILE = "ff_calendar_cache.json"
CACHE_TTL_SECONDS = 1800  # 30 minutes

FETCH_LOCK = "ff_calendar.lock"

REQUEST_TIMEOUT = 10
MAX_RETRIES = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ==================================================
# LOGGING
# ==================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ==================================================
# IN-MEMORY CACHE (PROCESS LOCAL)
# ==================================================

_MEMORY_CACHE: List[Dict] = []
_MEMORY_CACHE_TS: float = 0.0

# ==================================================
# FALLBACK EVENTS (LAST RESORT)
# ==================================================

MANUAL_FALLBACK_EVENTS = [
    {"currency": "USD", "impact": "HIGH", "event": "Non-Farm Payrolls"},
    {"currency": "USD", "impact": "HIGH", "event": "CPI m/m"},
    {"currency": "USD", "impact": "HIGH", "event": "FOMC Interest Rate Decision"},
    {"currency": "EUR", "impact": "HIGH", "event": "ECB Interest Rate Decision"},
]

# ==================================================
# LOCKING (SINGLE FLIGHT)
# ==================================================

def _acquire_lock() -> bool:
    try:
        fd = os.open(FETCH_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _release_lock() -> None:
    try:
        os.remove(FETCH_LOCK)
    except FileNotFoundError:
        pass

# ==================================================
# DISK CACHE
# ==================================================

def _load_cache() -> List[Dict]:
    if not os.path.exists(CACHE_FILE):
        return []

    if time.time() - os.path.getmtime(CACHE_FILE) > CACHE_TTL_SECONDS:
        return []

    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _save_cache(data: List[Dict]) -> None:
    tmp = f"{CACHE_FILE}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CACHE_FILE)

# ==================================================
# NETWORK FETCH
# ==================================================

def _fetch_html() -> str:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(
                FF_URL,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning(f"Fetch attempt {attempt} failed: {e}")
            time.sleep(attempt)

    raise RuntimeError("ForexFactory unreachable")

# ==================================================
# PARSING
# ==================================================

def _event_id(date: str, time_: str, currency: str, event: str) -> str:
    key = f"{date}|{time_}|{currency}|{event}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def _safe_text(row, selector: str) -> str | None:
    cell = row.select_one(selector)
    return cell.text.strip() if cell else None


def _normalize_impact(impact_cell) -> str | None:
    raw = impact_cell.get("title", "").lower()
    if "high" in raw:
        return "HIGH"
    if "medium" in raw:
        return "MEDIUM"
    if "low" in raw:
        return "LOW"
    return None


def _parse_calendar(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("tr.calendar__row")

    events: List[Dict] = []
    current_date: str | None = None
    fetched_at = datetime.now(timezone.utc).isoformat()

    for row in rows:
        try:
            dt_attr = row.get("data-event-datetime")
            if dt_attr:
                current_date = dt_attr.split(" ")[0]

            if not current_date:
                continue

            currency = _safe_text(row, ".calendar__currency")
            title = _safe_text(row, ".calendar__event")
            time_ = _safe_text(row, ".calendar__time") or ""

            impact_cell = row.select_one(".calendar__impact span")
            impact = _normalize_impact(impact_cell)

            if not (currency and title and impact == "HIGH"):
                continue

            events.append({
                "event_id": _event_id(current_date, time_, currency, title),
                "date": current_date,
                "time": time_,
                "currency": currency,
                "impact": impact,
                "event": title,
                "timezone": "FOREXFACTORY_LOCAL",
                "source": "ForexFactory",
                "fetched_at": fetched_at,
            })

        except Exception:
            continue

    return events

# ==================================================
# PUBLIC API (RAW DATA ONLY)
# ==================================================

def get_economic_calendar() -> List[Dict]:
    """
    Returns RAW ForexFactory economic calendar events.

    Strategy:
    - In-memory cache
    - Disk cache
    - Single-flight fetch
    - Graceful fallback

    NOTE:
    This function returns RAW dicts.
    Adaptation is the responsibility of higher layers.
    """

    global _MEMORY_CACHE, _MEMORY_CACHE_TS

    now = time.time()

    # Memory cache
    if _MEMORY_CACHE and (now - _MEMORY_CACHE_TS) < CACHE_TTL_SECONDS:
        return _MEMORY_CACHE

    # Disk cache
    cached = _load_cache()
    if cached:
        _MEMORY_CACHE = cached
        _MEMORY_CACHE_TS = now
        logger.info("Serving economic calendar from cache")
        return cached

    # Single-flight fetch
    if not _acquire_lock():
        time.sleep(0.25)
        cached = _load_cache()
        return cached if cached else []

    try:
        logger.info("Fetching ForexFactory calendar")
        html = _fetch_html()
        data = _parse_calendar(html)

        if not data:
            raise RuntimeError("Parsed empty calendar")

        _save_cache(data)
        _MEMORY_CACHE = data
        _MEMORY_CACHE_TS = now
        return data

    except Exception as e:
        logger.error(f"Economic calendar failed: {e}")
        now_iso = datetime.now(timezone.utc).isoformat()

        return [
            {
                **event,
                "event_id": hashlib.md5(event["event"].encode()).hexdigest(),
                "date": None,
                "time": None,
                "source": "ManualFallback",
                "fetched_at": now_iso,
            }
            for event in MANUAL_FALLBACK_EVENTS
        ]

    finally:
        _release_lock()

# ==================================================
# CLI / TEST (RAW ONLY)
# ==================================================

if __name__ == "__main__":
    print(json.dumps(get_economic_calendar(), indent=2))
