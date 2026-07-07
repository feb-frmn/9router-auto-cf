#!/usr/bin/env python3
"""
Cloudflare Workers AI Farmer
Login via Google OAuth → Extract Account ID → Create API Token via CF API
(Human-like browser + fast API token creation)
"""

import os, sys, time, random, json
from DrissionPage import ChromiumPage, ChromiumOptions

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AKUN_FILE = os.path.join(SCRIPT_DIR, "akun.txt")
RESULT_FILE = os.path.join(SCRIPT_DIR, "cf_keys.txt")

MODELS = '["@cf/zai-org/glm-5.2", "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/meta/llama-3.1-70b-instruct", "@cf/qwen/qwen2.5-coder-32b-instruct"]'

def read_accounts():
    if not os.path.exists(AKUN_FILE): return []
    with open(AKUN_FILE, "r") as f:
        return [{"email": l.split('|')[0].strip(), "password": l.split('|')[1].strip()} 
                for l in f if '|' in l and l.strip()]

def get_harvested_accounts():
    """Read already-harvested account IDs from cf_keys.txt."""
    if not os.path.exists(RESULT_FILE):
        return set()
    harvested = set()
    with open(RESULT_FILE, "r") as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) >= 2:
                # Extract account_id from base_url
                url = parts[1]
                if "/accounts/" in url:
                    aid = url.split("/accounts/")[1].split("/")[0]
                    harvested.add(aid)
    return harvested

def human_type(ele, text):
    ele.clear()
    for char in text:
        ele.input(char)
        time.sleep(random.uniform(0.05, 0.2))

def human_click(page, ele):
    page.actions.move_to(ele)
    time.sleep(random.uniform(0.3, 0.7))
    ele.click()

def get_wa_permission_ids(page):
    """Fetch Workers AI related permission group IDs from CF API."""
    result = page.run_js("""
        return (async () => {
            const resp = await fetch('/api/v4/user/tokens/permission_groups');
            return await resp.json();
        })();
    """)
    if not result.get('success'):
        return []
    ids = []
    for g in result.get('result', []):
        name = g.get('name', '').lower()
        if 'workers ai' in name:
            ids.append({"id": g["id"], "name": g["name"]})
    return ids

def create_token_via_api(page, account_id, perm_ids):
    """Create API token via CF internal API (browser session).
    perm_ids: list of dicts with 'id' and 'name' — we only send 'id' to CF API.
    """
    # CF API expects permission_groups as [{"id": "..."}] — extra fields can cause 400
    clean_perms = [{"id": p["id"]} for p in perm_ids]
    payload = json.dumps({
        "name": f"cf-ai-{int(time.time())}",
        "policies": [{
            "effect": "allow",
            "permission_groups": clean_perms,
            "resources": {f"com.cloudflare.api.account.{account_id}": "*"}
        }]
    })
    # Use JSON.parse to safely escape the payload (avoids JS string injection)
    result = page.run_js("""
        return (async () => {
            const payload = JSON.parse(%s);
            const resp = await fetch('/api/v4/user/tokens', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            return await resp.json();
        })();
    """ % json.dumps(payload))
    if result.get('success') and result.get('result', {}).get('value'):
        return result['result']['value']
    return None

def harvest_cf(account, index, total):
    email = account["email"]
    password = account["password"]
    
    print(f"\n{'='*55}")
    print(f" [{index}/{total}] {email}")
    print(f"{'='*55}")
    
    co = ChromiumOptions()
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1280,720")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-gpu")
    co.set_argument("--disable-dev-shm-usage")
    # BUG FIX: user-data-dir unik + port unik per akun → tiap akun benar-benar
    # fresh profile, gak ada cookie bleed antar akun. Tanpa ini semua akun share
    # profile default (session akun pertama) → account_id kebaca sama semua.
    profile_dir = os.path.join(SCRIPT_DIR, ".chrome_profiles", f"acc_{index}")
    os.makedirs(profile_dir, exist_ok=True)
    co.set_argument(f"--user-data-dir={profile_dir}")
    co.set_local_port(9222 + index)  # port unik biar gak nyambung ke instance lain
    main_page = page = ChromiumPage(co)
    
    try:
        # ── [0] LOGOUT dulu — bersihin session akun sebelumnya ──
        # BUG FIX: tanpa ini, cookie akun sebelumnya nyangkut → semua akun
        # kebaca sebagai akun pertama (account_id sama). Wajib clear session.
        print(" [0] Logout & clear session akun sebelumnya...")
        try:
            page.get("https://dash.cloudflare.com/logout")
            time.sleep(random.uniform(2.0, 3.0))
        except Exception:
            pass
        # Clear semua cookies (CF + Google) biar OAuth mulai fresh
        try:
            page.set.cookies.clear()
        except Exception:
            pass
        try:
            page.get("https://accounts.google.com/Logout")
            time.sleep(random.uniform(2.0, 3.0))
            page.set.cookies.clear()
        except Exception:
            pass

        # ── [1] Login Cloudflare ──
        print(" [1] Buka Cloudflare Login...")
        page.get("https://dash.cloudflare.com/login")
        time.sleep(random.uniform(4.0, 6.0))
        
        # Cek apakah udah login (redirect ke dashboard)
        # Setelah logout+clear cookies, ini HARUS false. Kalau masih true,
        # berarti clear gagal → jangan trust, tetap paksa login ulang.
        if "dash.cloudflare.com/" in page.url and "/login" not in page.url:
            print("    ⚠️  Masih ada session — clear cookies lagi & reload login")
            try:
                page.set.cookies.clear()
            except Exception:
                pass
            page.get("https://dash.cloudflare.com/login")
            time.sleep(random.uniform(3.0, 5.0))

        if "dash.cloudflare.com/" in page.url and "/login" not in page.url:
            print("    Udah login (session tersimpan)")
        else:
            print(" [2] Klik Continue with Google...")
            google_btn = page.ele('@text():Google', timeout=10)
            if not google_btn:
                raise Exception("Tombol Google gak ketemu")
            
            tabs_before = set(page.tab_ids)
            human_click(page, google_btn)
            time.sleep(random.uniform(3.0, 5.0))
            
            # Cari tab Google
            new_tab_id = None
            for _ in range(5):
                time.sleep(1)
                new_tabs = set(page.tab_ids) - tabs_before
                if new_tabs:
                    new_tab_id = new_tabs.pop()
                    break
            if not new_tab_id:
                for tid in page.tab_ids:
                    try:
                        t = page.get_tab(tid)
                        if "accounts.google.com" in (t.url or ""):
                            new_tab_id = tid
                            break
                    except Exception:
                        continue
            if not new_tab_id:
                raise Exception("Tab Google OAuth gak muncul")
            
            tab = page.get_tab(new_tab_id)
            print(f" [3] Tab Google: {tab.url[:60]}...")
            time.sleep(random.uniform(2.0, 3.0))
            
            # Account Chooser
            if "accountchooser" in tab.url:
                print("        Account Chooser → Use another account...")
                btn = tab.ele("@text():Use another account", timeout=3)
                if btn:
                    human_click(tab, btn)
                    time.sleep(random.uniform(2.0, 3.0))
            
            # Input email
            print(f" [4] Login: {email}")
            tab.wait.ele_displayed("#identifierId", timeout=15)
            human_type(tab.ele('#identifierId'), email)
            time.sleep(random.uniform(0.5, 1.0))
            human_click(tab, tab.ele('#identifierNext'))
            time.sleep(random.uniform(4.0, 5.0))
            
            # Input password
            pw_ele = tab.ele('@type=password', timeout=10)
            if not pw_ele:
                raise Exception("Field password gak muncul")
            human_type(pw_ele, password)
            time.sleep(random.uniform(0.5, 1.0))
            human_click(tab, tab.ele('#passwordNext'))
            time.sleep(random.uniform(6.0, 8.0))
            
            # Workspace TOS
            cur = tab.url
            if "workspacetermsofservice" in cur or "speedbump" in cur:
                print("        Handle Workspace TOS...")
                btn = tab.ele('tag:button@@text():I understand', timeout=5)
                if btn:
                    human_click(tab, btn)
                    time.sleep(random.uniform(4.0, 6.0))
            
            # Google consent
            for _ in range(5):
                time.sleep(2)
                cur = tab.url
                if "accounts.google.com" not in cur:
                    break
                for sel in ['@text():Allow', '@text():Continue', '@text():Accept']:
                    btn = tab.ele(sel, timeout=1)
                    if btn:
                        human_click(tab, btn)
                        time.sleep(2)
                        break
            
            # Tunggu redirect
            print(" [5] Menunggu redirect ke Dashboard...")
            time.sleep(random.uniform(5.0, 8.0))
        
        # ── [2] Ambil Account ID ──
        account_id = None
        for tid in page.tab_ids:
            try:
                t = page.get_tab(tid)
                url = t.url or ""
                if "dash.cloudflare.com/" in url:
                    parts = url.split("dash.cloudflare.com/")
                    if len(parts) > 1:
                        aid = parts[1].split("/")[0].split("?")[0]
                        if len(aid) > 10:
                            account_id = aid
                            page = t
                            break
            except Exception:
                continue
        
        if not account_id:
            page.get("https://dash.cloudflare.com/")
            time.sleep(5)
            parts = page.url.split("dash.cloudflare.com/")
            if len(parts) > 1:
                aid = parts[1].split("/")[0].split("?")[0]
                if len(aid) > 10:
                    account_id = aid
        
        if not account_id:
            raise Exception(f"Gagal ambil Account ID. URL: {page.url}")
        
        print(f" [6] Account ID: {account_id}")
        
        # Dedup: check if this account already harvested
        if os.path.exists(RESULT_FILE):
            with open(RESULT_FILE, "r") as f:
                if account_id in f.read():
                    print(f" [SKIP] Akun {account_id[:12]}... sudah ada di cf_keys.txt")
                    return True
        
        # ── [3] Buat Token via API ──
        print(" [7] Ambil Workers AI permissions...")
        perm_ids = get_wa_permission_ids(page)
        if not perm_ids:
            raise Exception("Gagal ambil permission groups")
        print(f"    {len(perm_ids)} permissions: {[p['name'] for p in perm_ids]}")
        
        print(" [8] Buat API token...")
        token = create_token_via_api(page, account_id, perm_ids)
        
        if token:
            base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
            with open(RESULT_FILE, "a") as f:
                f.write(f"cloudflare_{account_id[:6]}|{base_url}|{token}|{MODELS}\n")
            print(f" [SUCCESS] Token: {token[:25]}...")
            print(f" [SAVED] → {RESULT_FILE}")
            return True
        else:
            print(" [FAILED] API gagal bikin token")
            return False
        
    except Exception as e:
        print(f" [ERROR] {email}: {e}")
        return False
    finally:
        try:
            main_page.quit()
        except Exception:
            pass

def main():
    print(f"\n{'='*55}")
    print(f" Cloudflare Workers AI Farmer")
    print(f"{'='*55}")
    print(f" ☕ Support: https://saweria.co/febfrmn\n")

    # Handle --clean flag
    if "--clean" in sys.argv:
        if os.path.exists(RESULT_FILE):
            os.remove(RESULT_FILE)
            print(f"✅ {RESULT_FILE} dihapus.")
        else:
            print(f"ℹ️ {RESULT_FILE} tidak ada.")
        sys.exit(0)
    
    accounts = read_accounts()
    if not accounts:
        print("Isi akun.txt (email|password)")
        sys.exit(1)
    
    # Warn if cf_keys.txt already has entries
    existing = get_harvested_accounts()
    if existing:
        print(f"⚠️  cf_keys.txt sudah punya {len(existing)} key.")
        print(f"   Akun yang sudah di-harvest akan di-skip otomatis.")
        print(f"   Mau fresh run? Jalankan: python3 bot_cf.py --clean")
        print()
    
    print(f"Total akun: {len(accounts)}")
    success = 0
    for i, acc in enumerate(accounts, 1):
        # Skip if already harvested (check by trying login — if dashboard shows same account_id, skip)
        ok = harvest_cf(acc, i, len(accounts))
        if ok:
            success += 1
        if i < len(accounts):
            delay = random.randint(8, 15)
            print(f"\nTunggu {delay}s...")
            time.sleep(delay)
    
    print(f"\n{'='*55}")
    print(f" DONE: {success}/{len(accounts)} berhasil")
    print(f"{'='*55}")

if __name__ == "__main__":
    main()
