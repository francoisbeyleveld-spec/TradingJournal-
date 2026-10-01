"""Importing an MT5 report again later the same day.

    cd backend && python -m pytest tests -q

The trades already stored for that day are regrouped with the new fills. They used to be
rebuilt from price x qty, which turned their rand profit into a US-dollar price move
(R-701.44 became -42.63). Fills now keep their booked amount, and trades stored before
that are repaired from the report.
"""
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HEAD = ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P",
        "Time", "Price", "Commission", "Swap", "Profit"]
# Times are UTC; +2h gives 1 October in South Africa.
MORNING = [
    # three buys closed together in the same second at the same price, each with its own profit
    ["2026.10.01 05:34:36", "164316113", "GOLD", "buy", "0.03", 4190.00, 4185.0, 4200.0, "2026.10.01 06:39:40", 4185.0, 0.0, 0.0, -246.81],
    ["2026.10.01 05:34:39", "164316139", "GOLD", "buy", "0.03", 4189.74, 4185.0, 4200.0, "2026.10.01 06:39:40", 4185.0, 0.0, 0.0, -233.98],
    ["2026.10.01 05:43:15", "164317653", "GOLD", "buy", "0.03", 4189.47, 4185.0, 4200.0, "2026.10.01 06:39:40", 4185.0, 0.0, 0.0, -220.65],
    ["2026.10.01 10:24:58", "164377295", "GOLD", "buy", "0.03", 4165.35, 4160.0, 4170.0, "2026.10.01 10:26:29", 4165.58, 0.0, 0.0, 11.44],
]
AFTERNOON = [
    ["2026.10.01 15:05:05", "164453680", "GOLD", "buy", "0.08", 4155.63, 4150.0, 4167.0, "2026.10.01 15:20:11", 4167.0, 0.0, 0.0, 1516.75],
]


def report(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["Trade History Report"])
    ws.append(["Positions"])
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    ws.append([])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("MT5_TIME_OFFSET_HOURS", "2")
    for name in ("database", "main"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        c.db = str(db)
        conn = sqlite3.connect(c.db)
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Ava MT5', 'day_trading')")
        conn.commit()
        yield c


def post(client, rows):
    r = client.post("/api/import-csv", data={"account_id": "1", "broker": "mt5"},
                    files={"file": ("ReportHistory.xlsx", report(rows))})
    assert r.status_code == 200, r.text
    return r.json()


def day_pnl(client):
    conn = sqlite3.connect(client.db)
    return sorted(round(p, 2) for (p,) in conn.execute(
        "SELECT net_pnl FROM trades WHERE date='2026-10-01'"))


def test_later_import_keeps_rand_profit_of_earlier_trades(client):
    post(client, MORNING)
    assert day_pnl(client) == [-701.44, 11.44]
    post(client, MORNING + AFTERNOON)
    assert day_pnl(client) == [-701.44, 11.44, 1516.75]
    assert post(client, MORNING + AFTERNOON)["imported"] == 0


def test_trades_stored_without_amounts_are_repaired(client):
    post(client, MORNING)
    # what an earlier version stored: price-derived dollars and no per-fill amount
    conn = sqlite3.connect(client.db)
    for tg, ex in conn.execute("SELECT trade_group, executions FROM trades").fetchall():
        execs = [{k: v for k, v in e.items() if k != "amount"} for e in json.loads(ex)]
        conn.execute("UPDATE trades SET executions=?, net_pnl=-42.63, gross_pnl=-42.63 WHERE trade_group=?",
                     (json.dumps(execs), tg))
    conn.commit()
    post(client, MORNING)
    assert day_pnl(client) == [-701.44, 11.44]
