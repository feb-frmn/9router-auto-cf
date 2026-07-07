# Cloudflare Workers AI Farmer

Automated Cloudflare Workers AI token harvester + inference proxy with anti-ban protection and neuron budget tracking.

## Quick Start

```bash
# 1. Install
pip install DrissionPage requests

# 2. Add accounts (email|password or email|password|proxy)
echo "your@gmail.com|yourpassword" > akun.txt

# 3. Harvest tokens
python3 cf_farmer.py

# 4. Start inference proxy
python3 cf_proxy.py

# 5. Run audit tests
python3 test_demo.py --verbose
```

## Two Tools, One Pipeline

### 1. cf_farmer.py — Token Harvester

Harvests Cloudflare Workers AI API tokens via Google OAuth.

```bash
python3 cf_farmer.py                           # harvest all
python3 cf_farmer.py --proxy http://ip:port    # global proxy
python3 cf_farmer.py --only user@email.com    # single account
python3 cf_farmer.py --clean                   # reset cf_keys.txt
python3 cf_farmer.py --delay 30               # custom delay
```

**akun.txt format:**
```
email|password
email|password|http://proxy:port     # per-account proxy
```

**Anti-ban features:**
- Randomized browser fingerprint (user agent, window size, timezone)
- Human-like typing & clicking delays
- Profile trace removal after each account (cache, cookies, history)
- Session logout before each login
- Random delays between accounts
- Per-account browser profile (no session bleed)
- Only sends `id` field to CF API (no `name` leak = no 400)

**GSuite support:**
- Works with `@gmail.com` AND custom domain GSuite
- Handles Workspace TOS screen
- Handles Account Chooser
- Handles Google consent screens

### 2. cf_proxy.py — Inference Proxy

OpenAI-compatible proxy that rotates across all harvested accounts.

```bash
python3 cf_proxy.py                    # start on port 8750
python3 cf_proxy.py --port 9000       # custom port
python3 cf_proxy.py --key mysecret    # API key auth
python3 cf_proxy.py --import-9router  # import from 9Router DB
```

**Features:**
- Round-robin account rotation (thread-safe, race-safe)
- Neuron budget estimation per model (proactive skip before 429)
- 429 auto-cooldown (90s rate limit, 00:00 UTC daily limit)
- OpenAI-compatible: `/v1/chat/completions` + `/v1/models`
- Streaming support (SSE)
- CF passthrough: `/ai/run/:model` (image gen, TTS, etc)
- Web dashboard at `http://localhost:8750/`
- Import from cf_keys.txt OR 9Router DB

**Neuron tracking:**
```
neurons ≈ prompt_tokens × rate_in + completion_tokens × rate_out
```
- Rates from CF pricing page per model
- Unknown models → 70b-class fallback (skip early, don't overrun)
- 10,000 free neurons/day per account
- Reset at 00:00 UTC

**9Router integration:**
```
Base URL: http://127.0.0.1:8750/v1
API Key: (whatever you set with --key)
Models: @cf/meta/llama-3.3-70b-instruct-fp8-fast, etc.
```

## Output Format

`cf_keys.txt`:
```
cloudflare_abc123|https://api.cloudflare.com/client/v4/accounts/abc123.../ai/v1|cfut_TOKEN|["@cf/zai-org/glm-5.2",...]
```

## Test Suite

```bash
python3 test_demo.py --verbose    # full (49 tests)
python3 test_demo.py --quick      # skip browser tests
```

Categories: syntax, import, logic, token format, permission fix, bare except, anti-ban, proxy, GSuite, neuron tracking, E2E mock.

## ☕ Support

https://saweria.co/febfrmn
