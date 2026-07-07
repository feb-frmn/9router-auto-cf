# Cloudflare Workers AI Farmer

Automated Cloudflare Workers AI token harvester — login via Google OAuth (Gmail or GSuite), extract account ID, and create API tokens for 9Router injection.

## Features

- ✅ Google OAuth login (works with **@gmail.com** AND **GSuite custom domains**)
- ✅ Handles Google Workspace TOS screen + Account Chooser
- ✅ Human-like typing & clicking (anti-bot detection)
- ✅ Per-account browser profile (no session bleed)
- ✅ Auto-dedup (skip already-harvested accounts)
- ✅ Injects tokens into 9Router automatically

## Quick Start

```bash
# 1. Install dependencies
pip install DrissionPage curl_cffi requests

# 2. Add accounts
cp akun.txt.example akun.txt
echo "your_email@gmail.com|your_password" >> akun.txt

# 3. Harvest tokens
python3 bot_cf.py

# 4. Inject into 9Router
python3 inject_9router.py
```

## GSuite / Custom Domain Support

The bot works with **any** Google account:
- `@gmail.com` — standard Gmail
- `@yourcompany.com` — GSuite / Google Workspace

It handles:
- Workspace TOS ("I understand" button)
- Account Chooser ("Use another account")
- Google consent screens (Allow/Continue/Accept)

## Test Suite

Run the audit test suite to verify everything is working:

```bash
# Full test (includes browser + 9Router checks)
python3 test_demo.py --verbose

# Quick test (skip browser/network tests)
python3 test_demo.py --quick
```

**Test categories:**
1. Syntax check — all Python files compile
2. Import check — all dependencies installed
3. Logic check — core functions work correctly
4. Token format — cf_keys.txt format valid
5. Permission fix — clean_perms (no name field leak)
6. Bare except — no bare except: clauses
7. Browser check — Chromium + DrissionPage ready
8. 9Router check — API endpoint reachable
9. GSuite support — TOS/Chooser/OAuth handling verified
10. End-to-end demo — mock harvest flow verified

## Architecture

### Method 1: bot_cf.py (Recommended)
Browser-based, full Google OAuth flow. One script does everything.

### Method 2: 2-Phase (For bulk harvesting)
- `login_capture.py` — Phase 1: Login Google once per account, save cookies
- `harvest_hybrid.py` — Phase 2A: Silent OAuth (fast, no password typing)
- `harvest_http.py` — Phase 2B: Pure HTTP (experimental, may hit PKCE)
- `inject_9router.py` — Inject all harvested tokens into 9Router

## Output Format

`cf_keys.txt`:
```
cloudflare_abc123|https://api.cloudflare.com/client/v4/accounts/abc123.../ai/v1|cfut_TOKEN|["@cf/zai-org/glm-5.2",...]
```

## ☕ Support

https://saweria.co/febfrmn
