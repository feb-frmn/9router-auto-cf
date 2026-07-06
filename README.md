# 9Router Auto CF

Auto-harvest Cloudflare Workers AI API tokens and inject into 9Router. Free inference at scale.

## Architecture (2-phase)

```
PHASE 1 — Login (browser, once per account — touches Google BotGuard once)
    login_capture.py
        ↓ saves: sessions/<email>.json  (Google cookie jar, lasts weeks-months)
        ↓ persists: .chrome_profiles/<email>/  (Chrome profile, stays logged in)

PHASE 2A — Harvest (hybrid: browser silent OAuth + CF API)  [RECOMMENDED]
    harvest_hybrid.py
        ↓ reuses Chrome profile → CF OAuth silent (no BotGuard, no password)
        ↓ creates token via CF internal fetch API
        ↓ saves: cf_keys.txt

PHASE 2B — Harvest (pure HTTP, experimental)  [MAY FAIL on PKCE]
    harvest_http.py
        ↓ loads Google cookies from sessions/<email>.json
        ↓ follows CF OAuth redirect chain via curl_cffi (TLS fingerprint spoof)
        ↓ auto-detects PKCE / CSRF / state / client_id at runtime
        ↓ falls back gracefully, tells you to use hybrid if PKCE blocks

PHASE 3 — Inject into 9Router
    inject_9router.py
        ↓ reads cf_keys.txt
        ↓ POST to 9Router built-in cloudflare-ai provider
```

**Key insight (from research):** Google BotGuard only gates the *password login step*,
NOT the OAuth authorize step. Once Google session cookies exist (from a one-time browser
login), all future CF OAuth grants are silent 302-redirects — no BotGuard, no password.
This is how operators scale to 6000+ connections.

## Why browser can't be fully eliminated

- Google BotGuard blocks pure-HTTP login (JS eval required for `bgRequest` token)
- CF may use PKCE (`code_verifier` generated in browser-side JS) → HTTP replay fails
- Cloudflare CSRF header name (`X-Atok`) rotates with dash frontend versions
- **One-time browser login = permanent cfut_ token** → browser rarely needed after initial harvest

## Free models (auto-registered by 9Router)

**Chat:**
- `cf/@cf/zai-org/glm-5.2` — GLM 5.2 (reasoning)
- `cf/@cf/deepseek-ai/deepseek-r1-distill-qwen-32b` — DeepSeek R1 32B
- `cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast` — Llama 3.3 70B
- `cf/@cf/moonshotai/kimi-k2.6` — Kimi K2.6
- `cf/@cf/qwen/qwen2.5-coder-32b-instruct` — Qwen 2.5 Coder 32B
- `cf/@cf/qwen/qwq-32b` — QwQ 32B
- `cf/@cf/zai-org/glm-4.7-flash` — GLM 4.7 Flash

**Limit:** 100K requests/day per account, 30 RPM. 9Router load-balances across all injected tokens.

## Setup

```bash
git clone https://github.com/feb-frmn/9router-auto-cf
cd 9router-auto-cf
pip install DrissionPage curl_cffi python-dotenv
```

## Usage

```bash
# 1. Buat akun.txt
cp akun.txt.example akun.txt
nano akun.txt  # isi: email|password (satu per baris)

# 2. Login & capture session (browser, 1x per akun — kena BotGuard sekali)
xvfb-run -a python3 login_capture.py
# atau test 1 akun:
xvfb-run -a python3 login_capture.py --only user@domain.com

# 3A. Harvest via hybrid (RECOMMENDED — pasti jalan)
xvfb-run -a python3 harvest_hybrid.py

# 3B. Harvest via pure HTTP (eksperimental — lebih cepat, mungkin kena PKCE)
python3 harvest_http.py
# atau test 1 akun:
python3 harvest_http.py --only user@domain.com

# 4. Inject ke 9Router
python3 inject_9router.py

# ATAU all-in-one (bot lama, tetap bisa dipakai):
xvfb-run -a python3 bot_cf.py
```

## akun.txt format

```
user1@example.com|password1
user2@example.com|password2
```

## Files

| File | Fungsi |
|------|--------|
| `login_capture.py` | Login Google 1x per akun, simpan cookie jar + persist Chrome profile |
| `harvest_hybrid.py` | Harvest token via browser silent OAuth (RECOMMENDED) |
| `harvest_http.py` | Harvest token via pure-HTTP curl_cffi (eksperimental) |
| `bot_cf.py` | All-in-one original bot (login + harvest dalam 1 script) |
| `inject_9router.py` | Inject cf_keys.txt ke 9Router built-in provider |
| `akun.txt` | Akun list (gitignored) |
| `cf_keys.txt` | Harvested API tokens (gitignored) |
| `sessions/` | Google cookie jars per akun (gitignored) |
| `.chrome_profiles/` | Chrome profiles per akun (gitignored) |

## Notes

- **Profile persist by email** (not index) — aman kalau urutan akun.txt berubah
- **IP consistency** penting — Google mendeteksi IP jump dan paksa re-login
- **Session lifetime** — Google cookies tahan weeks-months dengan profile persist
- **PKCE detection** — harvest_http.py auto-deteksi dan kasih pesan jelas kalau kena PKCE
- **cfut_ tokens are permanent** — sekali harvest, gak perlu login ulang


## ☕ Support

Kalau bot ini membantu, bisa traktir kopi:

[![Saweria](https://img.shields.io/badge/Saweria-ffb13b?style=for-the-badge&logo=ko-fi&logoColor=white)](https://saweria.co/febfrmn)
