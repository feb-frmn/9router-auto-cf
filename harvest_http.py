#!/usr/bin/env python3
"""
harvest_http.py — FASE 2B: Pure-HTTP OAuth (EKSPERIMENTAL)
Replay Google session cookies via curl_cffi → ikutin redirect chain CF OAuth
→ grab auth code → exchange → CF session → create token via API

CATATAN PENTING:
- Kalau CF pakai PKCE → flow akan GAGAL (code_verifier dibuat di JS browser CF)
- CSRF header name rotate antar versi dash → harus di-detect runtime
- Jika PKCE terdeteksi, gunakan harvest_hybrid.py sebagai fallback

Prereq: jalankan login_capture.py dulu untuk capture Google session cookies.
Output: cf_keys.txt (sama dengan bot_cf.py dan harvest_hybrid.py)
"""
import os, sys, re, json, time, random, argparse
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urljoin

try:
    from curl_cffi.requests import AsyncSession, Session
except ImportError:
    print("Install: pip install curl_cffi"); sys.exit(1)

SCRIPT_DIR  = Path(__file__).resolve().parent
SESSION_DIR = SCRIPT_DIR / "sessions"
AKUN_FILE   = SCRIPT_DIR / "akun.txt"
RESULT_FILE = SCRIPT_DIR / "cf_keys.txt"

MODELS = '["@cf/zai-org/glm-5.2","@cf/deepseek-ai/deepseek-r1-distill-qwen-32b","@cf/meta/llama-3.3-70b-instruct-fp8-fast","@cf/qwen/qwen2.5-coder-32b-instruct","@cf/qwen/qwq-32b"]'

CF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://dash.cloudflare.com",
    "Referer": "https://dash.cloudflare.com/",
}


def load_session(email):
    safe = email.replace("@","_at_").replace(".","_")
    f = SESSION_DIR / f"{safe}.json"
    if not f.exists():
        raise Exception(f"Session file gak ada: {f}\nJalankan: python3 login_capture.py --only {email}")
    data = json.loads(f.read_text())
    # Return as dict {name: value} untuk .google.com cookies
    return {c["name"]: c["value"] for c in data["cookies"]}


def get_harvested():
    if not RESULT_FILE.exists(): return set()
    ids = set()
    for line in RESULT_FILE.read_text().splitlines():
        parts = line.split("|")
        if len(parts) >= 2 and "/accounts/" in parts[1]:
            ids.add(parts[1].split("/accounts/")[1].split("/")[0])
    return ids


def cookies_to_jar(cookie_dict):
    """Convert dict ke format curl_cffi."""
    return cookie_dict


def harvest_http_one(email):
    print(f"\n{'='*55}\n [HTTP] {email}\n{'='*55}")

    google_cookies = load_session(email)

    s = Session(impersonate="chrome124")
    cf_cookies = {}

    # ── STEP 0: Prime CF session (ambil __cf_bm, cf_clearance) ──
    print(" [1] Prime CF session...")
    # curl_cffi cookies adalah dict {name: value}, bukan list of objects
    r = s.get("https://dash.cloudflare.com/login",
               headers=CF_HEADERS, cookies=cf_cookies, allow_redirects=True)
    print(f"    status: {r.status_code}, url: {r.url[:70]}")
    # r.cookies di curl_cffi = dict-like, update langsung
    cf_cookies.update(dict(r.cookies))
    if r.status_code == 403:
        print("    ⚠️  403 dari CF login — coba tanpa Referer/Origin dulu")
        headers_bare = {k: v for k, v in CF_HEADERS.items() if k not in ("Origin", "Referer")}
        r = s.get("https://dash.cloudflare.com/login",
                   headers=headers_bare, allow_redirects=True)
        print(f"    retry status: {r.status_code}")
        cf_cookies.update(dict(r.cookies))

    # ── STEP 1: Klik "Continue with Google" → ambil OAuth initiation URL ──
    # CF button mengarah ke endpoint yang 302 ke Google authorize
    print(" [2] Cari OAuth initiation URL...")

    # Cari link Google OAuth dari halaman login
    oauth_init = None
    # Pattern 1: link langsung di HTML
    m = re.search(r'(https://dash\.cloudflare\.com[^\s"\']*(?:google|oauth|sso)[^\s"\']*)', r.text)
    if m:
        oauth_init = m.group(1)
        print(f"    Found dari HTML: {oauth_init[:80]}")

    if not oauth_init:
        # Pattern 2: cari di JS bundle CF (SPA, button dirender via React/JS)
        js_urls = re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', r.text)
        for jsu in js_urls[:5]:
            if not jsu.startswith("http"):
                jsu = "https://dash.cloudflare.com" + jsu
            try:
                rj = s.get(jsu, headers=CF_HEADERS, timeout=10)
                hits = re.findall(r'(https://[^\s"\'\\]+(?:oauth|google|sso)[^\s"\'\\]{0,80})', rj.text)
                if hits:
                    oauth_init = hits[0]
                    print(f"    Found dari JS bundle: {oauth_init[:80]}")
                    break
                paths = re.findall(r'["\'](/(?:api/v4/login|oauth|sso)[^"\'\\]{0,60})["\']', rj.text)
                google_paths = [p for p in paths if "google" in p.lower() or "sso" in p.lower()]
                if google_paths:
                    oauth_init = "https://dash.cloudflare.com" + google_paths[0]
                    print(f"    Found path dari JS: {oauth_init[:80]}")
                    break
            except Exception:
                continue

    if not oauth_init:
        # Pattern 3: probe known CF OAuth endpoints
        for candidate in [
            "https://dash.cloudflare.com/oauth2/auth?provider=google",
            "https://dash.cloudflare.com/api/v4/login/methods/google",
            "https://dash.cloudflare.com/api/v4/sso/google",
            "https://dash.cloudflare.com/api/v4/login/sso/google",
        ]:
            try:
                r2 = s.get(candidate, headers=CF_HEADERS, cookies=cf_cookies,
                            allow_redirects=False)
                loc = r2.headers.get("Location", "")
                print(f"    probe {candidate.split('cloudflare.com')[1]}: {r2.status_code} → {loc[:60] if loc else 'no redirect'}")
                if r2.status_code in (301, 302, 307, 308):
                    if "google.com" in loc:
                        # Location langsung ke Google → ini OAuth init URL kita
                        # Tapi kita perlu URL yang generate state, jadi ambil redirectnya
                        oauth_init = candidate
                        cf_cookies.update(dict(r2.cookies))
                        print(f"    ✅ OAuth init: {candidate} → Google redirect found!")
                        break
                    elif "cloudflare.com" not in loc and loc:
                        # Redirect ke tempat lain, coba ikuti
                        oauth_init = candidate
                        cf_cookies.update(dict(r2.cookies))
                        print(f"    ✅ OAuth init (non-CF redirect): {candidate}")
                        break
                    elif not loc or "cloudflare.com" in loc:
                        # Self-redirect ke CF lain, mungkin butuh state dulu
                        # Coba set oauth_init dan langsung follow
                        oauth_init = candidate
                        cf_cookies.update(dict(r2.cookies))
                        print(f"    OAuth init candidate (CF redirect): {candidate}")
                        break
            except Exception:
                continue

    if not oauth_init:
        raise Exception("PKCE/OAuth initiation URL gak ketemu — CF mungkin butuh JS untuk init OAuth. Fallback ke harvest_hybrid.py")

    # ── STEP 2: Ikutin redirect ke Google authorize ──
    print(" [3] Follow redirect ke Google authorize...")
    r3 = s.get(oauth_init, headers=CF_HEADERS, cookies=cf_cookies, allow_redirects=False)
    cf_cookies.update(dict(r3.cookies))

    google_auth_url = r3.headers.get("Location", "")
    # Kalau belum ke google, ikutin redirect CF dulu
    hops_cf = 0
    while google_auth_url and "dash.cloudflare.com" in google_auth_url and hops_cf < 5:
        hops_cf += 1
        print(f"    CF internal redirect: {google_auth_url[:70]}")
        r3 = s.get(google_auth_url, headers=CF_HEADERS, cookies=cf_cookies, allow_redirects=False)
        cf_cookies.update(dict(r3.cookies))
        google_auth_url = r3.headers.get("Location", "")

    if not google_auth_url or "accounts.google.com" not in google_auth_url:
        print(f"    Response status: {r3.status_code}")
        print(f"    Body (200 chars): {r3.text[:200]}")
        print(f"    Location: {google_auth_url[:100] if google_auth_url else 'NONE'}")
        raise Exception("Redirect ke Google gak ketemu — mungkin PKCE block atau Turnstile challenge")

    print(f"    Google authorize URL: {google_auth_url[:100]}...")

    # Parse params dari Google authorize URL
    parsed = urlparse(google_auth_url)
    params = parse_qs(parsed.query)
    state     = params.get("state", [None])[0]
    nonce     = params.get("nonce", [None])[0]
    client_id = params.get("client_id", [None])[0]
    redirect_uri = params.get("redirect_uri", [None])[0]
    code_challenge = params.get("code_challenge", [None])[0]

    print(f"    client_id: {client_id[:30] if client_id else 'NOT FOUND'}...")
    print(f"    state: {state[:20] if state else 'NOT FOUND'}...")
    if code_challenge:
        print(f"    ⚠️  PKCE DETECTED (code_challenge present) — flow mungkin gagal tanpa code_verifier")
    else:
        print(f"    ✅ No PKCE detected — murni OAuth code flow")

    # ── STEP 3: GET Google authorize dengan Google cookies ──
    print(" [4] Google authorize (replay cookies, harusnya auto-redirect)...")
    google_headers = {
        "User-Agent": CF_HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://dash.cloudflare.com/",
    }
    r4 = s.get(google_auth_url, headers=google_headers,
                cookies=google_cookies, allow_redirects=False)
    print(f"    status: {r4.status_code}")

    # Ikutin redirect chain sampai ke CF redirect_uri
    callback_url = None
    current = r4
    hops = 0
    all_cookies_google = dict(google_cookies)

    while hops < 10:
        hops += 1
        loc = current.headers.get("Location", "")
        all_cookies_google.update(dict(current.cookies))

        if not loc:
            # Mungkin ada consent screen di body
            if "consent" in (current.url or "").lower() or "consent" in current.text.lower()[:500]:
                print("    ⚠️  Consent screen muncul — belum pernah grant consent ke CF client")
                print("       Jalankan login_capture.py + login ke CF sekali dulu")
                raise Exception("Consent screen — jalankan login_capture.py --only " + email)
            if "dash.cloudflare.com" in (current.url or ""):
                callback_url = current.url
                break
            break

        print(f"    hop {hops}: {loc[:80]}")

        if "dash.cloudflare.com" in loc:
            callback_url = loc
            break
        elif "accounts.google.com" in loc:
            current = s.get(loc, headers=google_headers, cookies=all_cookies_google, allow_redirects=False)
        else:
            current = s.get(loc, headers=CF_HEADERS, cookies=cf_cookies, allow_redirects=False)
            cf_cookies.update(dict(current.cookies))

    if not callback_url:
        raise Exception("Redirect ke CF callback gak ketemu setelah 10 hop")

    # Cek apakah code ada di callback URL
    parsed_cb = urlparse(callback_url)
    cb_params  = parse_qs(parsed_cb.query)
    auth_code  = cb_params.get("code", [None])[0]
    if not auth_code:
        raise Exception(f"OAuth code gak ada di callback: {callback_url[:100]}")
    print(f"    ✅ OAuth code: {auth_code[:20]}...")

    # ── STEP 4: CF callback — exchange code → set session cookie ──
    print(" [5] CF callback (exchange code → session)...")
    r5 = s.get(callback_url, headers=CF_HEADERS, cookies=cf_cookies, allow_redirects=True)
    cf_cookies.update(dict(r5.cookies))
    print(f"    Final URL: {r5.url[:70]}")
    print(f"    CF cookies set: {list(cf_cookies.keys())}")

    # ── STEP 5: Confirm session + account_id ──
    print(" [6] Confirm session...")
    r6 = s.get("https://dash.cloudflare.com/api/v4/user",
                headers={**CF_HEADERS, "Accept": "application/json"},
                cookies=cf_cookies)
    if r6.status_code != 200:
        raise Exception(f"Session gak valid: {r6.status_code} — {r6.text[:100]}")

    user = r6.json()
    if not user.get("success"):
        raise Exception(f"CF /api/v4/user gagal: {user}")
    print(f"    User: {user['result'].get('email','?')}")

    # Ambil account_id
    r7 = s.get("https://dash.cloudflare.com/api/v4/accounts",
                headers={**CF_HEADERS, "Accept": "application/json"},
                cookies=cf_cookies)
    accounts_data = r7.json().get("result", [])
    if not accounts_data:
        raise Exception("Gak bisa ambil account list")
    account_id = accounts_data[0]["id"]
    print(f"    Account ID: {account_id}")

    # Check dedup
    harvested = get_harvested()
    if account_id in harvested:
        print(f" [SKIP] Udah di-harvest")
        return True

    # ── STEP 6: Detect CSRF token ──
    print(" [7] Detect CSRF token...")
    csrf_token = None
    csrf_header = None

    # Coba dari cookie
    for name, val in cf_cookies.items():
        if "atok" in name.lower() or "csrf" in name.lower() or "xsrf" in name.lower():
            csrf_token = val
            csrf_header = "X-Atok"
            print(f"    CSRF dari cookie [{name}]: {val[:20]}...")
            break

    # Coba dari GET /api/v4/user response header
    for hname in r6.headers:
        if "atok" in hname.lower() or "csrf" in hname.lower():
            csrf_token = r6.headers[hname]
            csrf_header = hname
            print(f"    CSRF dari header [{hname}]: {csrf_token[:20]}...")
            break

    # ── STEP 7: Ambil permission_groups ──
    print(" [8] Ambil Workers AI permissions...")
    r8 = s.get("https://dash.cloudflare.com/api/v4/user/tokens/permission_groups",
                headers={**CF_HEADERS, "Accept": "application/json"},
                cookies=cf_cookies)
    perm_data = r8.json().get("result", [])
    perm_ids = [{"id": g["id"], "name": g["name"]}
                for g in perm_data if "workers ai" in g.get("name","").lower()]
    if not perm_ids:
        raise Exception("Gagal ambil Workers AI permission groups")
    print(f"    {len(perm_ids)} perms: {[p['name'] for p in perm_ids]}")

    # ── STEP 8: POST create token ──
    print(" [9] Buat API token (POST /api/v4/user/tokens)...")
    token_payload = {
        "name": f"cf-ai-{int(time.time())}",
        "policies": [{"effect": "allow",
                      "permission_groups": perm_ids,
                      "resources": {f"com.cloudflare.api.account.{account_id}": "*"}}]
    }
    post_headers = {**CF_HEADERS, "Content-Type": "application/json", "Accept": "application/json"}
    if csrf_token and csrf_header:
        post_headers[csrf_header] = csrf_token
    # Selalu kirim X-Atok dari cookie "atok" jika ada
    if "atok" in cf_cookies:
        post_headers["X-Atok"] = cf_cookies["atok"]

    r9 = s.post("https://dash.cloudflare.com/api/v4/user/tokens",
                 headers=post_headers, cookies=cf_cookies,
                 json=token_payload)
    result = r9.json()
    print(f"    Response: {str(result)[:150]}")

    if result.get("success") and result.get("result", {}).get("value"):
        token = result["result"]["value"]
        base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
        with open(RESULT_FILE, "a") as f:
            f.write(f"cloudflare_{account_id[:6]}|{base_url}|{token}|{MODELS}\n")
        print(f" [SUCCESS] Token: {token[:20]}... → cf_keys.txt")
        return True
    else:
        print(f" [FAILED] Token creation gagal")
        print(f"    Errors: {result.get('errors', [])}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="test 1 akun (email)")
    args = ap.parse_args()

    if args.only:
        accounts = [{"email": args.only, "password": ""}]
    else:
        if not AKUN_FILE.exists():
            print("Isi akun.txt"); sys.exit(1)
        accounts = [{"email": l.split("|")[0].strip(), "password": l.split("|")[1].strip()}
                    for l in AKUN_FILE.read_text().splitlines() if "|" in l and l.strip()]

    print(f"Total akun: {len(accounts)}")
    ok = 0
    for i, acc in enumerate(accounts, 1):
        print(f"\n[{i}/{len(accounts)}]")
        try:
            if harvest_http_one(acc["email"]):
                ok += 1
        except Exception as e:
            print(f" [ERROR] {e}")
        if i < len(accounts):
            d = random.randint(5, 12)
            print(f"Tunggu {d}s..."); time.sleep(d)

    print(f"\n{'='*55}\n HTTP DONE: {ok}/{len(accounts)}\n{'='*55}")


if __name__ == "__main__":
    main()
