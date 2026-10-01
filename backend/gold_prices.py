"""Gold (XAUUSD) price bars for the trade charts.

Two sources, used in this order:

1. Bars stored in the journal database (table price_bars). These come from a
   price file you import on the Import page, e.g. FOREX.com XAUUSD candles
   exported from TradingView, so the chart shows exactly what you traded on.
2. Dukascopy's free historical data feed (no account or key). Minute, hour and
   day candles are fetched on demand, cached in price_bars, and reused.

Bars are stored with a UTC unix timestamp (seconds, bar open) and returned to the
chart in the same shape the Alpaca proxy used: {"t": ISO-8601 UTC, "o", "h", "l", "c", "v"}.
"""
import asyncio
import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

GOLD_TICKERS = {"XAUUSD", "GOLD", "XAU/USD", "XAU-USD"}
DUKA_ROOT = "https://jetta.dukascopy.com/v1"
DUKA_CODE = "XAU-USD"
TF_SECONDS = {"1Min": 60, "3Min": 180, "5Min": 300, "10Min": 600, "15Min": 900,
              "30Min": 1800, "1Hour": 3600, "1Day": 86400}
_TF_ALIASES = {"1m": "1Min", "3m": "3Min", "5m": "5Min", "10m": "10Min", "15m": "15Min",
               "30m": "30Min", "1h": "1Hour", "60m": "1Hour", "1d": "1Day", "d": "1Day",
               "1min": "1Min", "5min": "5Min", "15min": "15Min", "30min": "30Min", "1hour": "1Hour"}


def is_gold(ticker: str) -> bool:
    return (ticker or "").upper().replace(" ", "") in GOLD_TICKERS


def local_offset_hours() -> float:
    try:
        return float(os.getenv("MT5_TIME_OFFSET_HOURS", "2"))
    except ValueError:
        return 2.0


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_bars (
            ticker TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts INTEGER NOT NULL,
            o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL,
            v REAL DEFAULT 0,
            source TEXT NOT NULL,
            PRIMARY KEY (ticker, timeframe, ts, source)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_price_bars_lookup ON price_bars(ticker, timeframe, source, ts)")


# ── Import (price file from TradingView or anywhere else) ────────────────────

def _norm_tf(value) -> str | None:
    s = str(value or "").strip()
    if s in TF_SECONDS:
        return s
    return _TF_ALIASES.get(s.lower())


def _to_epoch(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = int(value)
        return v // 1000 if v > 10_000_000_000 else v
    s = str(value).strip()
    if s.isdigit():
        return _to_epoch(int(s))
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def parse_price_file(raw: bytes) -> list[dict]:
    """A price file -> [{ticker, timeframe, ts, o, h, l, c, v, source}].

    Accepts:
    - CSV with columns symbol, timeframe, time_utc (or time/timestamp/t), open, high, low, close[, volume][, source]
    - JSON as returned by the TradingView connector: {"symbol", "interval", "bars": [{"t","o","h","l","c","v"}]}
      or a list of such objects.
    """
    text = raw.decode("utf-8-sig", errors="replace").strip()
    rows = []
    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        blocks = data if isinstance(data, list) else [data]
        for b in blocks:
            tf = _norm_tf(b.get("interval") or b.get("timeframe"))
            sym = b.get("symbol") or "XAUUSD"
            src = b.get("source") or "tradingview"
            for bar in b.get("bars", []):
                rows.append({"ticker": sym, "timeframe": tf, "ts": _to_epoch(bar.get("t")),
                             "o": bar.get("o"), "h": bar.get("h"), "l": bar.get("l"), "c": bar.get("c"),
                             "v": bar.get("v") or 0, "source": src})
    else:
        reader = csv.DictReader(io.StringIO(text))
        for i, r in enumerate(reader, start=2):
            k = {str(key).strip().lower(): val for key, val in r.items() if key}
            t = k.get("time_utc") or k.get("time") or k.get("timestamp") or k.get("t") or k.get("date")
            try:
                rows.append({"ticker": k.get("symbol") or "XAUUSD", "timeframe": _norm_tf(k.get("timeframe") or k.get("interval")),
                             "ts": _to_epoch(t), "o": float(k["open"]), "h": float(k["high"]), "l": float(k["low"]),
                             "c": float(k["close"]), "v": float(k.get("volume") or 0),
                             "source": k.get("source") or "tradingview"})
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"Line {i} of the price file could not be read (needs time, open, high, low, close).")
    out = []
    for r in rows:
        if not r["timeframe"]:
            raise ValueError("The price file needs a timeframe (e.g. 5m or 15m) for every row.")
        if r["ts"] is None or None in (r["o"], r["h"], r["l"], r["c"]):
            continue
        tick = r["ticker"].split(":")[-1].upper().replace("/", "")
        out.append({**r, "ticker": "XAUUSD" if is_gold(tick) or "XAU" in tick else tick,
                    "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]), "c": float(r["c"]), "v": float(r["v"] or 0)})
    if not out:
        raise ValueError("No price bars found in that file.")
    return out


def store_bars(conn, bars: list[dict]) -> int:
    ensure_table(conn)
    conn.executemany(
        "INSERT OR REPLACE INTO price_bars (ticker, timeframe, ts, o, h, l, c, v, source) VALUES (?,?,?,?,?,?,?,?,?)",
        [(b["ticker"], b["timeframe"], int(b["ts"]), b["o"], b["h"], b["l"], b["c"], b.get("v", 0), b["source"]) for b in bars])
    conn.commit()
    return len(bars)


# ── Dukascopy ────────────────────────────────────────────────────────────────

def decode_dukascopy(data: dict) -> list[tuple]:
    """Dukascopy candle JSON -> [(ts_sec, o, h, l, c, v)].

    The feed sends a base candle plus per-candle deltas in price units
    (price = units * multiplier) and time steps of `shift` milliseconds.
    Gaps (no trading) are skipped rather than filled with flat candles.
    """
    times = data.get("times") or []
    if not times:
        return []
    mult = float(data["multiplier"])
    scale = max(0, -Decimal(str(data["multiplier"])).normalize().as_tuple().exponent)
    ts = int(data["timestamp"])
    shift = int(data["shift"])
    ou, hu, lu, cu = (round(data[k] / mult) for k in ("open", "high", "low", "close"))
    vols = data.get("volumes") or [0] * len(times)
    out = []
    for i, dt in enumerate(times):
        ts += dt * shift
        ou += data["opens"][i]; hu += data["highs"][i]; lu += data["lows"][i]; cu += data["closes"][i]
        out.append((ts // 1000, round(ou * mult, scale), round(hu * mult, scale),
                    round(lu * mult, scale), round(cu * mult, scale), float(vols[i] or 0)))
    return out


_SOURCE_TF = {"minute": "1Min", "hour": "1Hour", "day": "1Day"}


def _duka_buckets(source: str, start: datetime, end: datetime, now: datetime):
    """(url, is_active) per data file covering [start, end)."""
    cur = start
    if source == "minute":
        cur = datetime(cur.year, cur.month, cur.day, tzinfo=timezone.utc)
        step = lambda d: d + timedelta(days=1)
        path = lambda d: f"{d.year}/{d.month}/{d.day}"
    elif source == "hour":
        cur = datetime(cur.year, cur.month, 1, tzinfo=timezone.utc)
        step = lambda d: datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=timezone.utc)
        path = lambda d: f"{d.year}/{d.month}"
    else:
        cur = datetime(cur.year, 1, 1, tzinfo=timezone.utc)
        step = lambda d: datetime(d.year + 1, 1, 1, tzinfo=timezone.utc)
        path = lambda d: f"{d.year}"
    base = f"{DUKA_ROOT}/candles/{source}/{DUKA_CODE}/BID"
    limit = min(end, now)
    while cur < limit:
        nxt = step(cur)
        active = cur <= now < nxt
        url = f"{base}?from={int(cur.timestamp() * 1000)}" if active else f"{base}/{path(cur)}"
        yield cur, nxt, url, active
        cur = nxt


async def fetch_dukascopy(conn, source: str, start: datetime, end: datetime) -> str | None:
    """Fill price_bars from Dukascopy for [start, end). Returns a warning or None."""
    ensure_table(conn)
    tf = _SOURCE_TF[source]
    now = datetime.now(timezone.utc)
    todo = []
    for b0, b1, url, active in _duka_buckets(source, start, end, now):
        if not active:
            have = conn.execute(
                "SELECT COUNT(*) FROM price_bars WHERE ticker='XAUUSD' AND timeframe=? AND source='dukascopy' AND ts>=? AND ts<?",
                (tf, int(b0.timestamp()), int(b1.timestamp()))).fetchone()[0]
            done = conn.execute("SELECT 1 FROM price_bars WHERE ticker='XAUUSD' AND timeframe=? AND source='dukascopy-empty' AND ts=?",
                                (tf, int(b0.timestamp()))).fetchone()
            if have or done:
                continue
        todo.append((b0, url, active))
    if not todo:
        return None
    sem = asyncio.Semaphore(6)
    errors = []

    async def one(client, b0, url, active):
        async with sem:
            try:
                r = await client.get(url)
                if r.status_code == 404:
                    return b0, active, []
                r.raise_for_status()
                return b0, active, decode_dukascopy(r.json())
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                return b0, active, None

    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "trading-journal-ai"}) as client:
        results = await asyncio.gather(*(one(client, *t) for t in todo))
    rows = []
    for b0, active, candles in results:
        if candles is None:
            continue
        if not candles and not active:
            # Weekend/holiday: remember the empty file so it is not fetched again.
            rows.append(("XAUUSD", tf, int(b0.timestamp()), 0, 0, 0, 0, 0, "dukascopy-empty"))
        for ts, o, h, l, c, v in candles:
            rows.append(("XAUUSD", tf, ts, o, h, l, c, v, "dukascopy"))
    if rows:
        conn.executemany("INSERT OR REPLACE INTO price_bars (ticker, timeframe, ts, o, h, l, c, v, source) VALUES (?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    if errors and len(errors) == len(todo):
        return f"Gold prices could not be downloaded from Dukascopy ({errors[0]})."
    return None


# ── Chart bars ───────────────────────────────────────────────────────────────

def _load(conn, timeframe: str, source: str, t0: int, t1: int):
    return conn.execute(
        "SELECT ts, o, h, l, c, v FROM price_bars WHERE ticker='XAUUSD' AND timeframe=? AND source=? AND ts>=? AND ts<? ORDER BY ts",
        (timeframe, source, t0, t1)).fetchall()


def aggregate(rows, seconds: int, week: bool = False):
    """Roll bars up into `seconds`-long buckets (or Monday-start weeks)."""
    out = []
    cur = None
    for ts, o, h, l, c, v in rows:
        if week:
            d = datetime.fromtimestamp(ts, timezone.utc)
            key = int((d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0).timestamp())
        else:
            key = ts - ts % seconds
        if cur and cur[0] == key:
            cur[2] = max(cur[2], h); cur[3] = min(cur[3], l); cur[4] = c; cur[5] += v
        else:
            if cur:
                out.append(tuple(cur))
            cur = [key, o, h, l, c, v]
    if cur:
        out.append(tuple(cur))
    return out


def _stored_source(conn, tf: str, t0: int, t1: int, day0: int, day1: int):
    """Best stored non-Dukascopy bars that can build `tf`: (rows, label) or (None, None)."""
    want = TF_SECONDS.get(tf, 300)
    for cand in ("1Min", "3Min", "5Min", "10Min", "15Min", "30Min", "1Hour", "1Day"):
        cs = TF_SECONDS[cand]
        if cs > want or want % cs:
            continue
        srcs = conn.execute(
            "SELECT DISTINCT source FROM price_bars WHERE ticker='XAUUSD' AND timeframe=? AND source NOT LIKE 'dukascopy%' AND ts>=? AND ts<?",
            (cand, day0, day1)).fetchall()
        for (src,) in srcs:
            rows = _load(conn, cand, src, t0, t1)
            if rows:
                return rows, cs, src
    return None, None, None


async def gold_chart(conn, date: str, timeframe: str, days_back: int) -> dict:
    ensure_table(conn)
    tf = timeframe if timeframe in TF_SECONDS or timeframe == "1Week" else "5Min"
    off = timedelta(hours=local_offset_hours())
    trade_day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if tf in ("1Day", "1Week"):
        start = trade_day - timedelta(days=days_back - 1)
        end = trade_day + timedelta(days=10)
    else:
        # Local calendar days (SAST): local midnight = UTC midnight - offset.
        start = trade_day - timedelta(days=days_back - 1) - off
        end = trade_day + timedelta(days=1) - off
    t0, t1 = int(start.timestamp()), int(end.timestamp())
    day0 = int((trade_day - off).timestamp())
    day1 = day0 + 86400

    rows, base_s, label = (None, None, None)
    if tf != "1Week":
        rows, base_s, label = _stored_source(conn, tf, t0, t1, day0, day1)
    warning = None
    if rows:
        source = "TradingView price file" if label == "tradingview" else label
    else:
        if tf in ("1Day", "1Week"):
            dsource, dtf = "day", "1Day"
        elif tf == "1Hour" and days_back > 5:
            dsource, dtf = "hour", "1Hour"
        else:
            dsource, dtf = "minute", "1Min"
        warning = await fetch_dukascopy(conn, dsource, start, end)
        rows = _load(conn, dtf, "dukascopy", t0, t1)
        base_s = TF_SECONDS[dtf]
        source = "Dukascopy"
        if not rows and tf not in ("1Day", "1Week"):
            # No download (offline or blocked): show coarser stored prices rather than nothing.
            for cand in ("5Min", "10Min", "15Min", "30Min", "1Hour"):
                if TF_SECONDS[cand] <= TF_SECONDS[tf]:
                    continue
                got = conn.execute("SELECT DISTINCT source FROM price_bars WHERE ticker='XAUUSD' AND timeframe=? "
                                   "AND source NOT LIKE 'dukascopy%' AND ts>=? AND ts<?", (cand, day0, day1)).fetchall()
                if got:
                    rows = _load(conn, cand, got[0][0], t0, t1)
                    return _response(date, rows, "TradingView price file" if got[0][0] == "tradingview" else got[0][0],
                                     f"Showing {cand.replace('Min', 'm').replace('1Hour', '1h')} prices: "
                                     f"{tf.replace('Min', 'm')} prices are not available for this day.")
    if tf == "1Week":
        rows = aggregate(rows, 0, week=True)
    elif TF_SECONDS[tf] != base_s:
        rows = aggregate(rows, TF_SECONDS[tf])
    return _response(date, rows, source, warning)


def _response(date, rows, source, warning=None):
    bars = [{"t": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "o": o, "h": h, "l": l, "c": c, "v": v} for ts, o, h, l, c, v in rows]
    res = {"ticker": "XAUUSD", "original_ticker": "XAUUSD", "date": date, "bars": bars, "source": source}
    if not bars:
        res["warning"] = warning or "No gold prices for this day yet."
    elif warning:
        res["warning"] = warning
    return res
