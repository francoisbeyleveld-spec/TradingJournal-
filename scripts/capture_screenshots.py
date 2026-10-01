"""Retake the README screenshots from the running demo.

    launch.bat                     # start the demo on 3010 / 8010 first
    pip install playwright
    python scripts/capture_screenshots.py

Writes docs/screenshot-*.png. Everything captured is the synthetic seed from
scripts/seed_demo.py, never anyone's real trades.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = str(Path(__file__).resolve().parents[1] / "docs")
BASE = "http://localhost:3010"
W, H = 1560, 1000


def nav(page, label):
    page.get_by_role("button", name=label, exact=True).first.click()
    page.wait_for_timeout(2600)


def shot(page, name, height=None):
    page.wait_for_timeout(900)
    path = f"{OUT}/screenshot-{name}.png"
    if height:
        page.set_viewport_size({"width": W, "height": height})
        page.wait_for_timeout(700)
    page.screenshot(path=path)
    page.set_viewport_size({"width": W, "height": H})
    print("wrote", path)


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome")
    page = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(5000)

    # 1. Dashboard
    shot(page, "dashboard", 1080)

    # 2. Trade View
    nav(page, "Trade View")
    shot(page, "trade-view", 1000)

    # 3. Trade detail. Click the TICKER cell: the Setup column is an editable
    #    select that stops propagation, so a mid-row click never navigates.
    page.locator("tr.row-link").first.locator("td").nth(1).click()
    page.wait_for_timeout(4000)
    page.wait_for_selector("text=Back to trades", timeout=15000)
    assert page.locator("text=Back to trades").count(), "did not reach the trade detail page"
    page.wait_for_timeout(1500)
    shot(page, "trade-detail", 1240)

    # 4. Day Review, stepped back to a day with trades
    nav(page, "Day Review")
    page.wait_for_timeout(2500)
    for _ in range(20):
        if page.locator(".v3-daymark").count():
            break
        page.get_by_role("button", name="Previous").first.click()
        page.wait_for_timeout(1800)
    shot(page, "day-review", 1180)

    # 5. Reports
    nav(page, "Reports")
    shot(page, "reports", 1080)

    # 6. Settings
    nav(page, "Settings")
    shot(page, "settings", 900)

    b.close()
print("done")
