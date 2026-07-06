#!/usr/bin/env python3
"""Debug: signup CF, tunggu Turnstile solve, screenshot setelah klik."""
import time, random
from pathlib import Path
from DrissionPage import ChromiumPage, ChromiumOptions

SCRIPT_DIR = Path(__file__).resolve().parent
profile_dir = SCRIPT_DIR / ".chrome_profiles" / "debug2"
profile_dir.mkdir(parents=True, exist_ok=True)

co = ChromiumOptions()
co.set_argument("--disable-blink-features=AutomationControlled")
co.set_argument("--window-size=1280,720")
co.set_argument("--no-sandbox"); co.set_argument("--disable-gpu")
co.set_argument("--disable-dev-shm-usage")
co.set_argument(f"--user-data-dir={profile_dir}")
co.set_local_port(9520)
page = ChromiumPage(co)

try:
    print("[1] Buka signup...")
    page.get("https://dash.cloudflare.com/sign-up")
    time.sleep(6)

    print("[2] Isi form...")
    ef = page.ele('@name=email', timeout=10)
    ef.clear(); ef.input("cfarm" + str(int(time.time())) + "@web-library.net")
    time.sleep(1)
    pw = page.ele('@name=password', timeout=5)
    pw.clear(); pw.input("TestPass123!Secure")
    time.sleep(1)

    # Screenshot SEBELUM submit (lihat Turnstile state)
    page.get_screenshot(path=str(SCRIPT_DIR / "signup_before.png"))
    print("[3] Screenshot before submit → signup_before.png")

    # Cek Turnstile state
    print("[4] Cek Turnstile widget...")
    turnstile_input = page.ele('@name=cf_challenge_response', timeout=3)
    if turnstile_input:
        val = turnstile_input.attrs.get("value", "")
        print(f"    cf_challenge_response value: {'EMPTY' if not val else val[:30] + '...'}")
    else:
        print("    Turnstile input gak ketemu")

    # Tunggu lebih lama buat Turnstile
    print("[5] Tunggu Turnstile 15s...")
    time.sleep(15)
    if turnstile_input:
        val = turnstile_input.attrs.get("value", "")
        print(f"    After 15s: {'EMPTY' if not val else val[:30] + '...'}")

    # Cari error messages di page
    print("[6] Cari error messages...")
    errors = page.eles('@class*=error', timeout=2) + page.eles('@class*=Error', timeout=2)
    for e in errors[:5]:
        try:
            txt = e.text.strip()
            if txt:
                print(f"    Error: {txt[:100]}")
        except: pass

    print("[7] Klik Sign up...")
    btn = page.ele('@text():Sign up', timeout=3)
    if btn:
        btn.click()
        time.sleep(8)

    # Screenshot SETELAH submit
    page.get_screenshot(path=str(SCRIPT_DIR / "signup_after.png"))
    print(f"[8] Screenshot after submit → signup_after.png")
    print(f"    URL: {page.url}")

    # Cari error setelah submit
    print("[9] Cari error setelah submit...")
    page_text = page.html or ""
    for kw in ["error", "invalid", "already", "exists", "verification", "check your email", "verify"]:
        if kw in page_text.lower():
            # Cari konteks di sekitar keyword
            idx = page_text.lower().find(kw)
            context = page_text[max(0,idx-30):idx+80]
            print(f"    Found '{kw}': ...{context}...")

    # Cek semua text yang visible
    print("[10] Page text (500 chars around body)...")
    body_match = page_text[page_text.lower().find("<body"):page_text.lower().find("<body")+1500] if "<body" in page_text.lower() else page_text[:1000]
    # Strip HTML tags buat readability
    import re
    clean = re.sub(r'<[^>]+>', ' ', body_match)
    clean = re.sub(r'\s+', ' ', clean).strip()
    print(f"    {clean[:500]}")

except Exception as e:
    print(f"ERROR: {e}")
finally:
    page.quit()
