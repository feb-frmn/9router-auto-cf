#!/usr/bin/env python3
"""
TEST: CF signup pakai temp mail (mail.tm API).
Cek apakah CF terima temp mail + apakah ada Turnstile di signup.
"""
import json, time, re
from curl_cffi.requests import Session

s = Session(impersonate="chrome124")

# ── STEP 1: Bikin temp email via mail.tm ──
print("[1] Bikin temp email via mail.tm...")
# Get available domain (XML response)
r = s.get("https://api.mail.tm/domains?page=1")
domain = re.search(r"<domain>([^<]+)</domain>", r.text)
if not domain:
    print("    Gak ada domain available!"); exit(1)
domain = domain.group(1)
email = f"cfarm{int(time.time())}{random_suffix}@{domain}" if (random_suffix := str(int(time.time()) % 1000)) else f"cfarm{int(time.time())}@{domain}"
email = f"cfarm{int(time.time())}@{domain}"
print(f"    Domain: {domain}")

r = s.post("https://api.mail.tm/accounts", json={"address": email, "password": "TempMail123!"})
print(f"    status: {r.status_code}")
if r.status_code not in (200, 201):
    print(f"    response: {r.text[:200]}")
    exit(1)
print(f"    ✅ Email: {email}")

# Login ke mail.tm buat ambil token
r = s.post("https://api.mail.tm/token", json={"address": email, "password": "TempMail123!"})
try:
    mail_token = r.json().get("token")
except Exception:
    # XML response fallback
    tk = re.search(r"<token>([^<]+)</token>", r.text)
    mail_token = tk.group(1) if tk else None
print(f"    mail.tm token: {'OK' if mail_token else 'FAIL'}")

# ── STEP 2: Cek CF signup page ──
print(f"\n[2] Cek CF signup page...")
r = s.get("https://dash.cloudflare.com/signup", allow_redirects=True)
print(f"    signup page status: {r.status_code}, url: {r.url[:70]}")

has_turnstile = "turnstile" in r.text.lower() or "cf-turnstile" in r.text.lower()
has_recaptcha = "recaptcha" in r.text.lower()
print(f"    Turnstile: {'⚠️ YES' if has_turnstile else '❌ no'}")
print(f"    reCAPTCHA: {'⚠️ YES' if has_recaptcha else '❌ no'}")

# ── STEP 3: POST CF signup ──
print(f"\n[3] POST CF signup...")
cf_password = "CfFarm2026!Secure"
signup_data = {
    "email": email,
    "password": cf_password,
    "confirm_password": cf_password,
    "first_name": "CF",
    "last_name": "Farmer",
    "terms": True,
}

for endpoint in [
    "https://dash.cloudflare.com/api/v4/signup",
    "https://api.cloudflare.com/client/v4/user",
    "https://dash.cloudflare.com/api/v4/register",
]:
    try:
        r = s.post(endpoint, json=signup_data, headers={
            "Content-Type": "application/json",
            "Origin": "https://dash.cloudflare.com",
            "Referer": "https://dash.cloudflare.com/signup",
        }, allow_redirects=False)
        path = endpoint.split("cloudflare.com")[1] if "cloudflare.com" in endpoint else endpoint
        print(f"    {path}: {r.status_code}")
        if r.status_code in (200, 201):
            print(f"    🎉 SUCCESS! Response: {r.text[:300]}")
            break
        elif r.status_code == 403:
            print(f"    403 — Turnstile/bot block")
            if "turnstile" in r.text.lower(): print(f"    Confirmed: Turnstile required")
            break
        elif r.status_code in (400, 422):
            print(f"    Response: {r.text[:200]}")
            break
    except Exception as e:
        print(f"    Error: {e}")

print(f"\n[DONE] Email: {email}")
