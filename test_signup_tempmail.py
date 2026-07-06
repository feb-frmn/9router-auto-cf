#!/usr/bin/env python3
"""
TEST: CF signup via browser (DrissionPage) pakai temp mail.
CF Turnstile biasanya auto-pass di real browser (managed challenge).
Setelah signup → verify email via mail.tm API → login → grab account_id + token.
"""
import os, sys, json, time, re, random
from pathlib import Path
from DrissionPage import ChromiumPage, ChromiumOptions
from curl_cffi.requests import Session

SCRIPT_DIR = Path(__file__).resolve().parent

def create_temp_email():
    """Bikin temp email via mail.tm, return (email, password, mail_token)."""
    s = Session(impersonate="chrome124")
    r = s.get("https://api.mail.tm/domains?page=1")
    domain = re.search(r"<domain>([^<]+)</domain>", r.text)
    if not domain:
        raise Exception("mail.tm gak ada domain")
    domain = domain.group(1)
    email = f"cfarm{int(time.time())}{random.randint(100,999)}@{domain}"
    password = "TempMail123!"
    r = s.post("https://api.mail.tm/accounts", json={"address": email, "password": password})
    if r.status_code not in (200, 201):
        raise Exception(f"mail.tm create gagal: {r.status_code} {r.text[:100]}")
    r = s.post("https://api.mail.tm/token", json={"address": email, "password": password})
    try:
        token = r.json().get("token")
    except Exception:
        tk = re.search(r"<token>([^<]+)</token>", r.text)
        token = tk.group(1) if tk else None
    if not token:
        raise Exception("mail.tm token gagal")
    print(f"  ✅ Temp email: {email}")
    return email, password, token

def wait_verification_email(mail_token, timeout=120):
    """Poll mail.tm buat email verifikasi CF."""
    s = Session(impersonate="chrome124")
    headers = {"Authorization": f"Bearer {mail_token}"}
    print("  ⏳ Tunggu email verifikasi CF...", end="", flush=True)
    for _ in range(timeout // 5):
        time.sleep(5)
        print(".", end="", flush=True)
        r = s.get("https://api.mail.tm/messages", headers=headers)
        try:
            msgs = r.json().get("hydra:member", [])
        except Exception:
            # XML fallback
            msgs = []
            ids = re.findall(r"<id>([^<]+)</id>", r.text)
            for mid in ids:
                if "/messages/" in r.text:
                    msgs.append({"id": mid})
        if msgs:
            print(f" 📨 {len(msgs)} email!")
            # Ambil email pertama
            msg_id = msgs[0].get("id") if isinstance(msgs[0], dict) else msgs[0]
            r2 = s.get(f"https://api.mail.tm/messages/{msg_id}", headers=headers)
            body = r2.text
            # Cari verification link
            links = re.findall(r'https://[^\s"<>]+verify[^\s"<>]*', body, re.I)
            if not links:
                links = re.findall(r'https://dash\.cloudflare\.com/[^\s"<>]+', body, re.I)
            if not links:
                links = re.findall(r'https://[^\s"<>]+click[^\s"<>]*', body, re.I)
            if links:
                print(f"  🔗 Verify link: {links[0][:80]}...")
                return links[0]
            print(f"  ⚠️ Email diterima tapi gak ada link. Body: {body[:200]}")
            return None
    print(" TIMEOUT")
    return None


def signup_cf_browser(email, email_password):
    """Signup CF via browser pakai temp email."""
    cf_password = "CfFarm2026!Secure"

    profile_dir = SCRIPT_DIR / ".chrome_profiles" / f"signup_{int(time.time())}"
    profile_dir.mkdir(parents=True, exist_ok=True)

    co = ChromiumOptions()
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1280,720")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-gpu")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument(f"--user-data-dir={profile_dir}")
    co.set_local_port(9500)
    page = ChromiumPage(co)

    try:
        print(f"  [1] Buka CF signup...")
        page.get("https://dash.cloudflare.com/sign-up")
        time.sleep(random.uniform(4.0, 6.0))
        print(f"    URL: {page.url[:70]}")

        # Cari form signup
        print(f"  [2] Isi form signup...")

        # Email field
        email_input = page.ele('@type=email', timeout=10) or page.ele('#email', timeout=5)
        if not email_input:
            # Cari by placeholder
            email_input = page.ele('@placeholder=email', timeout=5) or page.ele('@placeholder=Email', timeout=5)
        if not email_input:
            raise Exception("Field email gak ketemu")
        email_input.clear()
        email_input.input(email)
        time.sleep(random.uniform(0.5, 1.0))

        # Password field
        pw_input = page.ele('@type=password', timeout=10)
        if not pw_input:
            raise Exception("Field password gak ketemu")
        pw_input.clear()
        pw_input.input(cf_password)
        time.sleep(random.uniform(0.5, 1.0))

        # Confirm password (kalau ada)
        pw_inputs = page.eles('@type=password', timeout=3)
        if len(pw_inputs) > 1:
            pw_inputs[1].clear()
            pw_inputs[1].input(cf_password)
            time.sleep(random.uniform(0.5, 1.0))

        print(f"  [3] Tunggu Turnstile auto-solve...")
        time.sleep(random.uniform(5.0, 8.0))

        # Submit
        print(f"  [4] Klik Create Account...")
        btn = page.ele('@text():Create', timeout=5) or page.ele('@text():Sign up', timeout=3) or page.ele('@type=submit', timeout=3)
        if not btn:
            raise Exception("Tombol signup gak ketemu")
        btn.click()
        time.sleep(random.uniform(5.0, 8.0))

        # Cek hasil
        print(f"    URL setelah submit: {page.url[:70]}")

        # Cek error
        if "error" in page.url.lower():
            raise Exception(f"Redirect ke error: {page.url}")

        # Cek apakah diminta verify email
        page_text = page.html.lower() if page.html else ""
        if "verify" in page_text or "check your email" in page_text or "verification" in page_text:
            print(f"  ✅ CF minta verify email — signup berhasil!")
            return cf_password, page, profile_dir
        elif "dash.cloudflare.com" in page.url and "/login" not in page.url:
            print(f"  ✅ Langsung login — signup berhasil!")
            return cf_password, page, profile_dir
        else:
            print(f"  ❓ Status gak jelas. URL: {page.url}")
            # Cek page text
            for kw in ["verify", "email", "check", "account", "welcome"]:
                if kw in page_text:
                    print(f"    Found '{kw}' in page")
            return cf_password, page, profile_dir

    except Exception as e:
        print(f"  [ERROR] {e}")
        try:
            page.quit()
        except:
            pass
        return None, None, None


def main():
    print("=" * 55)
    print("  CF Signup via Temp Mail (Browser)")
    print("=" * 55)

    # Step 1: Bikin temp email
    print("\n[STEP 1] Bikin temp email...")
    email, email_pw, mail_token = create_temp_email()

    # Step 2: Signup CF via browser
    print("\n[STEP 2] Signup CF via browser...")
    cf_password, page, profile_dir = signup_cf_browser(email, email_pw)
    if not page:
        print("\n❌ Signup gagal")
        return

    # Step 3: Tunggu email verifikasi
    print("\n[STEP 3] Tunggu email verifikasi CF...")
    verify_link = wait_verification_email(mail_token, timeout=120)

    if verify_link:
        print(f"\n[STEP 4] Buka verification link...")
        page.get(verify_link)
        time.sleep(random.uniform(5.0, 8.0))
        print(f"    URL: {page.url[:70]}")

    # Step 4: Login ke CF dashboard
    print(f"\n[STEP 5] Cek login CF...")
    time.sleep(3)
    if "dash.cloudflare.com/" in page.url and "/login" not in page.url and "/sign" not in page.url:
        print(f"  ✅ Logged in! URL: {page.url[:70]}")
        # Ambil account_id
        parts = page.url.split("dash.cloudflare.com/")
        if len(parts) > 1:
            aid = parts[1].split("/")[0].split("?")[0]
            if len(aid) > 10:
                print(f"  ✅ Account ID: {aid}")
    else:
        print(f"  ⚠️ Belum login. URL: {page.url[:70]}")
        print(f"     Mungkin perlu login manual: {email} / {cf_password}")

    print(f"\n{'='*55}")
    print(f"  Email: {email}")
    print(f"  CF Password: {cf_password}")
    print(f"  Mail.tm Password: {email_pw}")
    print(f"  Profile: {profile_dir}")
    print(f"{'='*55}")
    print(f"\nBot tetap running. Cek browser untuk debug.")

    # Keep alive
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        page.quit()


if __name__ == "__main__":
    main()
