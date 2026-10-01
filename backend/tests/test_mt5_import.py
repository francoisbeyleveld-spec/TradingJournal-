"""MT5 Trade History Report importer (.xlsx and .html).

    cd backend && python -m pytest tests -q

Rows are copied from a real Ava Trade MT5 report layout: prices with a space as
thousands separator and a comma decimal, times in UTC.
"""
import io
import json
import sys
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csv_parser import parse_mt5_report, parse_mt5_positions, read_mt5_report  # noqa: E402

HEAD = ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P",
        "Time", "Price", "Commission", "Swap", "Profit"]
ROWS = [
    ["2026.08.05 13:17:35", "155846452", "EURUSD", "buy", "1", "1,15483", "", "",
     "2026.08.05 13:18:08", "1,15485", "0,00", "0,00", "32,74"],
    # two gold buys opened together and closed together -> one trade
    ["2026.08.06 06:36:01", "155987802", "GOLD", "buy", "0.03", "4 255,32", "4 255,00", "4 268,00",
     "2026.08.06 07:44:14", "4 255,00", "0,00", "0,00", "- 15,69"],
    ["2026.08.06 06:36:11", "155987830", "GOLD", "buy", "0.03", "4 254,63", "4 255,00", "4 278,00",
     "2026.08.06 07:44:14", "4 255,00", "0,00", "0,00", "18,14"],
    # a gold sell
    ["2026.08.07 10:00:00", "156100001", "GOLD", "sell", "0.02", "4 300,00", "4 310,00", "4 280,00",
     "2026.08.07 10:30:00", "4 290,50", "0,00", "0,00", "350,00"],
]


def xlsx_bytes():
    wb = Workbook()
    ws = wb.active
    ws.append(["Trade History Report"])
    ws.append(["Account:", "89801601 (ZAR, Ava-Real 1-MT5, real, Hedge)"])
    ws.append([])
    ws.append(["Positions"])
    ws.append(HEAD)
    for r in ROWS:
        ws.append(r)
    ws.append([])
    ws.append(["Orders"])
    ws.append(["Open Time", "Order", "Symbol", "Type", "Volume", "Price"])
    ws.append(["2026.08.06 06:36:01", "1", "GOLD", "buy", "0.03", "4 255,32"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def html_bytes():
    cells = lambda r, tag="td": "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in r) + "</tr>"
    body = ("<html><body><table>"
            '<tr><th colspan="13">Positions</th></tr>' + cells(HEAD, "th")
            + "".join(cells(r) for r in ROWS)
            + '<tr><th colspan="13">Orders</th></tr></table></body></html>')
    return body.encode("utf-16")


def by_ticker(trades):
    out = {}
    for t in trades:
        out.setdefault(t["ticker"], []).append(t)
    return out


def test_xlsx_groups_and_pnl():
    trades, skipped = parse_mt5_report(xlsx_bytes(), account_id=1, offset_hours=2)
    assert skipped == 0
    t = by_ticker(trades)
    assert set(t) == {"XAUUSD"}  # gold-only: the EURUSD row is left out
    buy, sell = sorted(t["XAUUSD"], key=lambda x: x["trade_group"])
    # P&L is MT5's Profit column in the account currency: -15.69 + 18.14 = 2.45
    assert buy["side"] == "LONG" and abs(buy["gross_pnl"] - 2.45) < 0.001
    assert sell["side"] == "SHORT" and abs(sell["gross_pnl"] - 350.0) < 0.001
    # volume shown in ounces: 0.03 lot = 3 oz
    assert json.loads(buy["executions"])[0]["qty"] == 3


def test_times_shift_to_local():
    ex = parse_mt5_positions(read_mt5_report(xlsx_bytes()), offset_hours=2)
    first_gold = ex[0]
    assert (first_gold["iso_date"], first_gold["time"]) == ("2026-08-06", "08:36:01")


def test_html_report_matches_xlsx():
    a, _ = parse_mt5_report(xlsx_bytes(), account_id=1, offset_hours=2)
    b, _ = parse_mt5_report(html_bytes(), account_id=1, offset_hours=2)
    key = lambda ts: sorted((t["ticker"], round(t["gross_pnl"], 2)) for t in ts)
    assert key(a) == key(b)


def test_missing_profit_uses_fx_rate(monkeypatch):
    monkeypatch.setenv("MT5_FX_RATE", "16")
    rows = [HEAD, ["2026.08.07 10:00:00", "1", "XAUUSD", "buy", "0.01", "4000", "3990", "4020",
                   "2026.08.07 11:00:00", "4010", "0", "0", ""]]
    trades, _ = parse_mt5_report(html_from(rows), account_id=1, offset_hours=0)
    # (4010 - 4000) * 1 oz * 16 = 160
    assert abs(trades[0]["gross_pnl"] - 160.0) < 0.001


def html_from(rows):
    cells = lambda r: "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
    return ("<table>" + "".join(cells(r) for r in rows) + "</table>").encode("utf-8")
