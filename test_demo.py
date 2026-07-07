#!/usr/bin/env python3
"""
test_demo.py — Audit & test suite for CF Workers AI Farmer (Pure API).

Tests:
  1. Syntax check      — all Python files valid
  2. Import check      — requests installed (no browser deps)
  3. Logic check       — read_accounts, get_harvested, make_session
  4. Token format      — cf_keys.txt format valid
  5. Permission fix    — clean_perms (only id field sent)
  6. Bare except       — no bare except: clauses
  7. Anti-detection    — random UA, session cookies, proxy support
  8. Proxy support     — per-account + global proxy
  9. No browser deps   — no DrissionPage, Playwright, Selenium imports
  10. Neuron tracking  — cf_proxy.py estimation logic
  11. End-to-end mock  — mock harvest + proxy flow

Usage:
  python3 test_demo.py              # all tests
  python3 test_demo.py --verbose    # show all output
"""

import os, sys, json, re, time, argparse
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PASS = 0
FAIL = 0
SKIP = 0
VERBOSE = False

def result(name, ok, detail=""):
    global PASS, FAIL
    icon = "✅" if ok else "❌"
    status = "PASS" if ok else "FAIL"
    if not ok and detail:
        status += f" — {detail}"
    print(f"  {icon} {name}: {status}")
    if ok: PASS += 1
    else: FAIL += 1
    if VERBOSE and ok and detail:
        print(f"     {detail}")

def skipped(name, reason=""):
    global SKIP
    print(f"  ⏭️  {name}: SKIP{' — ' + reason if reason else ''}")
    SKIP += 1

# ─── 1. SYNTAX CHECK ──────────────────────────────────────────────────────────
def test_syntax():
    print("\n📋 1. Syntax Check")
    print("   " + "─" * 50)
    for f in sorted(SCRIPT_DIR.glob("*.py")):
        if f.name == "test_demo.py": continue
        try:
            compile(f.read_text(), str(f), "exec")
            result(f"Syntax: {f.name}", True)
        except SyntaxError as e:
            result(f"Syntax: {f.name}", False, str(e))

# ─── 2. IMPORT CHECK ──────────────────────────────────────────────────────────
def test_imports():
    print("\n📋 2. Import Check")
    print("   " + "─" * 50)
    for label, module in [("requests", "requests"), ("urllib3", "urllib3")]:
        try:
            __import__(module)
            result(f"Import: {label}", True)
        except ImportError as e:
            result(f"Import: {label}", False, str(e))

    # Verify NO browser deps are imported by cf_farmer
    fpath = SCRIPT_DIR / "cf_farmer.py"
    if fpath.exists():
        content = fpath.read_text()
        for dep in ["DrissionPage", "playwright", "selenium"]:
            has_import = bool(re.search(rf'(?:from|import)\s+{dep}', content))
            result(f"No browser dep: {dep} not imported by cf_farmer", not has_import,
                   f"Imported!" if has_import else "Not imported — pure API")
    else:
        skipped("cf_farmer.py not found")

# ─── 3. LOGIC CHECK ───────────────────────────────────────────────────────────
def test_logic():
    print("\n📋 3. Logic Check")
    print("   " + "─" * 50)
    try:
        import cf_farmer
        tmp = SCRIPT_DIR / ".test_akun.txt"
        tmp.write_text("test@gmail.com|pass123\nbad_entry\nuser@company.com|p455|http://proxy:8080\n")
        old = cf_farmer.AKUN_FILE
        cf_farmer.AKUN_FILE = tmp
        accounts = cf_farmer.read_accounts()
        cf_farmer.AKUN_FILE = old
        tmp.unlink()
        ok = len(accounts) == 2 and accounts[1].get("proxy") == "http://proxy:8080"
        result("read_accounts: format + proxy parsing", ok,
               f"Got {len(accounts)} accounts" if not ok else f"{len(accounts)} accounts, proxy detected")
    except Exception as e:
        result("read_accounts: format + proxy parsing", False, str(e))

    try:
        import cf_farmer
        tmp = SCRIPT_DIR / ".test_cf_keys.txt"
        tmp.write_text(
            "cloudflare_abc|https://api.cloudflare.com/client/v4/accounts/abc123def456/ai/v1|cfut_tok|[]\n"
            "cloudflare_xyz|https://api.cloudflare.com/client/v4/accounts/xyz789aaa/ai/v1|cfut_tok2|[]\n"
        )
        old = cf_farmer.RESULT_FILE
        cf_farmer.RESULT_FILE = tmp
        harvested = cf_farmer.get_harvested()
        cf_farmer.RESULT_FILE = old
        tmp.unlink()
        ok = "abc123def456" in harvested and "xyz789aaa" in harvested and len(harvested) == 2
        result("get_harvested: dedup set", ok, f"Got {harvested}" if not ok else f"{len(harvested)} unique")
    except Exception as e:
        result("get_harvested: dedup set", False, str(e))

    try:
        import cf_farmer
        s = cf_farmer.make_session()
        ua = s.headers.get("User-Agent", "")
        ok = "Chrome" in ua and "Mozilla" in ua
        result("make_session: UA + headers", ok, f"UA: {ua[:40]}" if not ok else "Session created with anti-detection headers")
        s.close()
    except Exception as e:
        result("make_session: UA + headers", False, str(e))

    try:
        import cf_farmer
        s = cf_farmer.make_session(proxy="http://127.0.0.1:8080")
        ok = s.proxies.get("https") == "http://127.0.0.1:8080"
        result("make_session: proxy support", ok)
        s.close()
    except Exception as e:
        result("make_session: proxy support", False, str(e))

    try:
        import cf_proxy
        aid = cf_proxy.extract_account_id("https://api.cloudflare.com/client/v4/accounts/abc123def/ai/v1")
        result("extract_account_id: URL parsing", aid == "abc123def",
               f"Got '{aid}'" if aid != "abc123def" else "Correct")
    except Exception as e:
        result("extract_account_id: URL parsing", False, str(e))

# ─── 4. TOKEN FORMAT ──────────────────────────────────────────────────────────
def test_token_format():
    print("\n📋 4. Token Format Check")
    print("   " + "─" * 50)
    keys_file = SCRIPT_DIR / "cf_keys.txt"
    if not keys_file.exists():
        skipped("cf_keys.txt: not found (run cf_farmer.py first)")
        return
    for i, line in enumerate(keys_file.read_text().strip().splitlines(), 1):
        parts = line.split("|")
        ok = (len(parts) == 4 and parts[0].startswith("cloudflare_")
              and "api.cloudflare.com" in parts[1] and "/accounts/" in parts[1]
              and parts[2].startswith("cfut_") and parts[3].startswith("["))
        result(f"cf_keys.txt line {i}", ok, "" if ok else f"format wrong: {len(parts)} parts")

# ─── 5. PERMISSION FIX ────────────────────────────────────────────────────────
def test_permission_fix():
    print("\n📋 5. Permission Groups Fix Check")
    print("   " + "─" * 50)
    fpath = SCRIPT_DIR / "cf_farmer.py"
    if not fpath.exists():
        skipped("cf_farmer.py: not found"); return
    content = fpath.read_text()
    # Check that only {"id": ...} is sent, not raw perm_ids with name
    has_clean = bool(re.search(r'permission_groups.*\[\{"id"', content))
    sends_raw = '"permission_groups": perm_ids' in content
    ok = has_clean and not sends_raw
    result("cf_farmer.py: clean_perms fix", ok, "Only sends id field" if ok else "Still raw")

# ─── 6. BARE EXCEPT ───────────────────────────────────────────────────────────
def test_no_bare_except():
    print("\n📋 6. Bare Except Check")
    print("   " + "─" * 50)
    for f in sorted(SCRIPT_DIR.glob("*.py")):
        if f.name == "test_demo.py": continue
        bare = re.findall(r'except\s*:', f.read_text())
        result(f"{f.name}: no bare except", len(bare) == 0,
               f"{len(bare)} found" if bare else "All Exception")

# ─── 7. ANTI-DETECTION ────────────────────────────────────────────────────────
def test_anti_detection():
    print("\n📋 7. Anti-Detection Check (Pure API)")
    print("   " + "─" * 50)
    fpath = SCRIPT_DIR / "cf_farmer.py"
    if not fpath.exists():
        skipped("cf_farmer.py not found"); return
    content = fpath.read_text()

    checks = [
        ("Random user agents (USER_AGENTS)", "USER_AGENTS" in content),
        ("random_ua() function", "def random_ua" in content),
        ("Session cookie persistence", "requests.Session()" in content),
        ("sec-ch-ua header spoofing", "sec-ch-ua" in content),
        ("sec-fetch headers", "sec-fetch" in content),
        ("Accept-Language header", "Accept-Language" in content),
        ("Retry on 429/5xx", "status_forcelist" in content),
        ("Session close (cleanup)", "session.close()" in content),
        ("No browser imports", "DrissionPage" not in content and "playwright" not in content and "selenium" not in content),
        ("No headless args", "--headless" not in content),
        ("No Chrome profile dirs", "chrome_profiles" not in content and "user-data-dir" not in content),
    ]
    for name, ok in checks:
        result(f"Anti-detection: {name}", ok)

    # 7b. API Flow Check
    print("\n📋 7b. API Flow Check")
    print("   " + "─" * 50)
    checks = [
        ("cf_login function (API)", "def cf_login" in content),
        ("POST /api/v4/login", "/api/v4/login" in content or 'f"{API_BASE}/login"' in content),
        ("GET /api/v4/accounts", "/accounts" in content),
        ("GET permission_groups", "permission_groups" in content),
        ("POST /api/v4/user/tokens", "/user/tokens" in content),
        ("create_token function", "def create_token" in content),
        ("get_account_id function", "def get_account_id" in content),
        ("get_permission_groups function", "def get_permission_groups" in content),
        ("save_token function", "def save_token" in content),
        ("harvest_one function", "def harvest_one" in content),
        ("No cf_signup (removed — pure login)", "def cf_signup" not in content),
        ("No cf_login_google (removed)", "def cf_login_google" not in content),
        ("No cf_login_email (removed)", "def cf_login_email" not in content),
        ("No extract_account_id from browser", "def extract_account_id" not in content or "page.tab_ids" not in content),
        ("No human_type (browser typing)", "def human_type" not in content),
        ("No human_click (browser click)", "def human_click" not in content),
        ("No setup_browser (browser launch)", "def setup_browser" not in content),
        ("No wipe_profile_traces", "def wipe_profile_traces" not in content),
    ]
    for name, ok in checks:
        result(f"API Flow: {name}", ok)

# ─── 8. PROXY SUPPORT ─────────────────────────────────────────────────────────
def test_proxy():
    print("\n📋 8. Proxy Support Check")
    print("   " + "─" * 50)
    fpath = SCRIPT_DIR / "cf_farmer.py"
    if not fpath.exists():
        skipped("cf_farmer.py not found"); return
    content = fpath.read_text()
    checks = [
        ("Global --proxy flag", "--proxy" in content and "global_proxy" in content),
        ("Per-account proxy (akun.txt)", '"proxy"' in content and 'parts[2]' in content),
        ("requests session proxy", "s.proxies" in content),
        ("Proxy displayed in output", "Proxy:" in content or "proxy" in content.lower()),
        ("No Chrome --proxy-server arg", "--proxy-server" not in content),
    ]
    for name, ok in checks:
        result(f"Proxy: {name}", ok)

# ─── 9. NO BROWSER DEPS ───────────────────────────────────────────────────────
def test_no_browser():
    print("\n📋 9. No Browser Dependencies Check")
    print("   " + "─" * 50)
    for f in sorted(SCRIPT_DIR.glob("*.py")):
        if f.name == "test_demo.py": continue
        content = f.read_text()
        browser_imports = []
        for dep in ["DrissionPage", "playwright", "selenium", "pyppeteer"]:
            if re.search(rf'(?:from|import)\s+{dep}', content):
                browser_imports.append(dep)
        result(f"{f.name}: no browser imports", len(browser_imports) == 0,
               f"Found: {browser_imports}" if browser_imports else "Pure API")

# ─── 10. NEURON TRACKING ──────────────────────────────────────────────────────
def test_neuron_tracking():
    print("\n📋 10. Neuron Tracking Check")
    print("   " + "─" * 50)
    fpath = SCRIPT_DIR / "cf_proxy.py"
    if not fpath.exists():
        skipped("cf_proxy.py not found"); return
    try:
        import cf_proxy
        n = cf_proxy.estimate_neurons("@cf/meta/llama-3.2-1b-instruct", 1e6, 1e6)
        expected = 2457 + 18252
        result("Neuron estimation: known model", abs(n - expected) < 0.01,
               f"Expected {expected}, got {n:.2f}" if abs(n - expected) >= 0.01 else f"✓ {n:.2f}")

        n2 = cf_proxy.estimate_neurons("@cf/unknown/model", 1e6, 0)
        result("Neuron estimation: unknown fallback", abs(n2 - cf_proxy.DEFAULT_RATE["in"]) < 0.01,
               f"Expected {cf_proxy.DEFAULT_RATE['in']}, got {n2:.2f}")

        result("Neuron: NEURON_FREE_DAILY = 10000", cf_proxy.NEURON_FREE_DAILY == 10000)

        content = fpath.read_text()
        checks = [
            ("Rate table (RATES)", "RATES" in content and "llama-3.3-70b" in content),
            ("429 cooldown (mark_429)", "mark_429" in content),
            ("Daily limit 4006 handling", "4006" in content),
            ("Round-robin rotation", "_cursor" in content),
            ("In-flight reservation", "_reserved" in content),
            ("Thread-safe lock", "threading.Lock" in content or "_lock" in content),
            ("Account pool class", "AccountPool" in content),
            ("Streaming support", "stream" in content.lower()),
            ("OpenAI-compatible /v1/chat/completions", "/v1/chat/completions" in content),
            ("Dashboard HTML", "_send_dashboard" in content or "dashboard" in content.lower()),
            ("9Router DB import", "import_from_9router" in content),
            ("cf_keys.txt import", "import_from_file" in content),
        ]
        for name, ok in checks:
            result(f"Neuron: {name}", ok)
    except Exception as e:
        result("Neuron tracking import", False, str(e))

# ─── 11. E2E MOCK ─────────────────────────────────────────────────────────────
def test_e2e_mock():
    print("\n📋 11. End-to-End Demo (Mock)")
    print("   " + "─" * 50)
    try:
        import cf_farmer
        models = json.loads(cf_farmer.MODELS)
        result("Mock: MODELS valid JSON", isinstance(models, list) and len(models) > 0,
               f"{len(models)} models")

        fake_id = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        line = f"cloudflare_{fake_id[:6]}|https://api.cloudflare.com/client/v4/accounts/{fake_id}/ai/v1|cfut_FAKE|{cf_farmer.MODELS}"
        parts = line.split("|")
        result("Mock: output format", len(parts) == 4 and parts[2].startswith("cfut_"),
               f"{len(parts)} parts")

        # Verify make_session returns a proper session
        s = cf_farmer.make_session()
        result("Mock: make_session creates session", hasattr(s, "post") and hasattr(s, "get"))
        s.close()

        # Verify random_ua returns a string
        ua = cf_farmer.random_ua()
        result("Mock: random_ua returns string", isinstance(ua, str) and "Chrome" in ua,
               f"UA: {ua[:40]}")
    except Exception as e:
        result("Mock: import cf_farmer", False, str(e))

    try:
        import cf_proxy
        tmp_db = SCRIPT_DIR / ".test_proxy.db"
        db = cf_proxy.init_db(tmp_db)
        result("Mock: DB init", db is not None)
        p = cf_proxy.AccountPool(db)
        result("Mock: AccountPool creation", p is not None)
        s = p.stats()
        result("Mock: stats on empty pool", s["total"] == 0)
        tmp_db.unlink()
    except Exception as e:
        result("Mock: cf_proxy DB/pool", False, str(e))

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    global VERBOSE
    parser = argparse.ArgumentParser(description="CF Bot Test Suite (Pure API)")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose

    print("\n  ╔═══════════════════════════════════════════════╗")
    print("  ║  CF Workers AI Farmer — Test Suite (v2.1 API) ║")
    print("  ╚═══════════════════════════════════════════════╝")
    print(f"  Directory: {SCRIPT_DIR}")
    print()

    test_syntax()
    test_imports()
    test_logic()
    test_token_format()
    test_permission_fix()
    test_no_bare_except()
    test_anti_detection()
    test_proxy()
    test_no_browser()
    test_neuron_tracking()
    test_e2e_mock()

    print("\n  " + "═" * 50)
    print(f"  ✅ PASS: {PASS}  |  ❌ FAIL: {FAIL}  |  ⏭️  SKIP: {SKIP}")
    print("  " + "═" * 50)
    if FAIL > 0:
        print("\n  ⚠️  Some tests failed."); sys.exit(1)
    print("\n  🎉 All tests passed!"); sys.exit(0)

if __name__ == "__main__":
    main()
