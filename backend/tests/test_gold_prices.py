"""Gold price bars for the trade charts: price-file import, Dukascopy decoding and the chart endpoint.

    cd backend && python -m pytest tests -q
"""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gold_prices as gp  # noqa: E402

DAY = 1786406400  # 2026-08-11 00:00:00 UTC (a Tuesday)


def db():
    conn = sqlite3.connect(":memory:")
    gp.ensure_table(conn)
    return conn


def duka_payload(day_start):
    # Base candle 4255.000 / 4256.000 / 4254.000 / 4255.500, then deltas in 0.001 units.
    return {
        "timestamp": day_start * 1000, "multiplier": 0.001, "shift": 60000,
        "open": 4255.0, "high": 4256.0, "low": 4254.0, "close": 4255.5,
        "times": [0, 1, 3],                 # minute 0, minute 1, minute 4 (2 quiet minutes skipped)
        "opens": [0, 500, -200], "highs": [0, 1000, 0], "lows": [0, 0, -1000], "closes": [0, 500, -1500],
        "volumes": [1.5, 2.0, 0.5],
    }


def test_decode_dukascopy():
    c = gp.decode_dukascopy(duka_payload(DAY))
    assert [x[0] - DAY for x in c] == [0, 60, 240]
    assert c[0][1:5] == (4255.0, 4256.0, 4254.0, 4255.5)
    assert c[1][1:5] == (4255.5, 4257.0, 4254.0, 4256.0)
    assert c[2][1:5] == (4255.3, 4257.0, 4253.0, 4254.5)
    assert c[2][5] == 0.5


def test_import_tradingview_json_and_csv():
    tv = {"symbol": "FOREXCOM:XAUUSD", "interval": "5m",
          "bars": [{"t": DAY, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
                   {"t": DAY + 300, "o": 1.5, "h": 3, "l": 1, "c": 2, "v": 5}]}
    bars = gp.parse_price_file(json.dumps(tv).encode())
    assert {b["ticker"] for b in bars} == {"XAUUSD"} and {b["timeframe"] for b in bars} == {"5Min"}
    csv_text = "symbol,timeframe,time_utc,open,high,low,close,volume\nXAUUSD,15m,2026-08-11T00:00:00Z,1,2,0.5,1.5,3\n"
    b2 = gp.parse_price_file(csv_text.encode())
    assert b2[0]["ts"] == DAY and b2[0]["timeframe"] == "15Min"


def test_chart_prefers_stored_tradingview_bars_and_aggregates():
    conn = db()
    # 5m bars from 00:00 to 00:55 UTC on the day (= 02:00-02:55 SAST)
    bars = [{"ticker": "XAUUSD", "timeframe": "5Min", "ts": DAY + i * 300, "o": 100 + i, "h": 101 + i,
             "l": 99 + i, "c": 100.5 + i, "v": 1, "source": "tradingview"} for i in range(12)]
    gp.store_bars(conn, bars)
    res = asyncio.run(gp.gold_chart(conn, "2026-08-11", "15Min", 1))
    assert res["source"] == "TradingView price file"
    assert len(res["bars"]) == 4
    first = res["bars"][0]
    assert first["t"] == "2026-08-11T00:00:00Z" and first["o"] == 100 and first["h"] == 103 and first["c"] == 102.5


def test_chart_falls_back_to_dukascopy(monkeypatch):
    conn = db()
    calls = []

    def handler(request):
        calls.append(str(request.url))
        path = request.url.path
        if path.endswith("/2026/8/11"):
            return httpx.Response(200, json=duka_payload(DAY))
        return httpx.Response(200, json={"times": []})

    real = httpx.AsyncClient
    monkeypatch.setattr(gp.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    res = asyncio.run(gp.gold_chart(conn, "2026-08-11", "1Min", 1))
    assert res["source"] == "Dukascopy"
    assert [b["t"] for b in res["bars"]] == ["2026-08-11T00:00:00Z", "2026-08-11T00:01:00Z", "2026-08-11T00:04:00Z"]
    assert any("/candles/minute/XAU-USD/BID/2026/8/11" in u for u in calls)
    # Second request is served from the cache without calling Dukascopy again.
    n = len(calls)
    asyncio.run(gp.gold_chart(conn, "2026-08-11", "1Min", 1))
    assert len(calls) == n
