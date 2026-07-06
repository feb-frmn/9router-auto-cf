#!/usr/bin/env python3
"""
TEST: Cek struktur form signup CF via browser.
Tunggu JS load, cari semua input fields yang ter-render.
"""
import time, random
from pathlib import Path
from DrissionPage import ChromiumPage, ChromiumOptions

SCRIPT_DIR = Path(__file__).resolve().parent
profile_dir = SCRIPT_DIR / ".chrome_profiles" / "debug_signup"
profile_dir.mkdir(parents=True, exist_ok=True)

co = ChromiumOptions()
co.set_argument("--disable-blink-features=AutomationControlled")
co.set_argument("--window-size=1280,720")
co.set_argument("--no-sandbox")
co.set_argument("--disable-gpu")
co.set_argument("--disable-dev-shm-usage")
co.set_argument(f"--user-data-dir={profile_dir}")
co.set_local_port(9510)
page = ChromiumPage(co)

try:
    print("[1] Buka CF signup...")
    page.get("https://dash.cloudflare.com/sign-up")
    print(f"    URL: {page.url}")
    print(f"    Tunggu JS load 8s...")
    time.sleep(8)

    print(f"\n[2] Cari semua input fields...")
    # Cari semua input
    inputs = page.eles("tag:input", timeout=5)
    print(f"    Total inputs: {len(inputs)}")
    for inp in inputs:
        try:
            attrs = inp.attrs
            print(f"    - type={attrs.get('type','?')} name={attrs.get('name','?')} id={attrs.get('id','?')} placeholder={attrs.get('placeholder','?')}")
        except:
            pass

    print(f"\n[3] Cari button...")
    buttons = page.eles("tag:button", timeout=3)
    for btn in buttons[:10]:
        try:
            print(f"    - text={btn.text[:40]!r} type={btn.attrs.get('type','?')}")
        except:
            pass

    print(f"\n[4] Cari Turnstile iframe...")
    iframes = page.eles("tag:iframe", timeout=3)
    for ifr in iframes[:5]:
        try:
            src = ifr.attrs.get("src", "")
            if "turnstile" in src or "challenge" in src:
                print(f"    🔄 Turnstile iframe: {src[:80]}")
            else:
                print(f"    iframe: {src[:60]}")
        except:
            pass

    print(f"\n[5] Page text (200 chars)...")
    try:
        print(f"    {page.html[:500]}")
    except:
        pass

    print(f"\n[6] Screenshot...")
    page.get_screenshot(path=str(SCRIPT_DIR / "signup_debug.png"))
    print(f"    Saved: signup_debug.png")

except Exception as e:
    print(f"ERROR: {e}")
finally:
    page.quit()
