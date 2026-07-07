# Cloudflare Workers AI Farmer

Automated Cloudflare Workers AI token harvester + inference proxy with anti-ban protection and neuron budget tracking.

## Quick Start

```bash
# 1. Install
pip install DrissionPage requests

# 2. Add your emails (email|password)
echo "your@email.com|yourpassword" > akun.txt

# 3. Sign up + harvest tokens
python3 cf_farmer.py

# 4. Start inference proxy
python3 cf_proxy.py

# 5. Run audit tests
python3 test_demo.py --verbose
```

## How It Works

### cf_farmer.py — Token Harvester

Reads `akun.txt`, then for each account:

1. **CF Signup** — fills signup form at `dash.cloudflare.com/sign-up`
2. **Email verification** — waits up to 180s for you to verify email
3. **Fallback** — if account already exists, tries CF login → Google OAuth
4. **Token creation** — creates Workers AI API token via CF internal API
5. **Save** — writes to `cf_keys.txt`

```bash
python3 cf_farmer.py                           # signup + harvest all
python3 cf_farmer.py --only user@email.com    # single account
python3 cf_farmer.py --proxy http://ip:port    # global proxy
python3 cf_farmer.py --delay 30               # custom delay
python3 cf_farmer.py --clean                   # reset cf_keys.txt
```

**akun.txt format:**
```
email|password
email|password|http://proxy:port     # per-account proxy (optional)
```

**Anti-ban features:**
- Randomized browser fingerprint (user agent, window size, timezone)
- Human-like typing & clicking delays
- Profile trace removal after each account
- Session logout before each signup
- Random delays between accounts
- Headless mode for servers

**Supported email types:**
- Gmail / GSuite (via Google OAuth fallback)
- Any email (via CF signup — user provides email)
- Existing CF accounts (via CF email+password login)

### cf_proxy.py — Inference Proxy

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

## Output Format

`cf_keys.txt`:
```
cloudflare_abc123|https://api.cloudflare.com/client/v4/accounts/abc123.../ai/v1|cfut_TOKEN|["@cf/zai-org/glm-5.2",...]
```

## Test Suite

```bash
python3 test_demo.py --verbose    # full (59 tests)
```

Categories: syntax, import, logic, token format, permission fix, bare except, anti-ban, CF signup, proxy, GSuite, neuron tracking, E2E mock.

## ☕ Support

https://saweria.co/febfrmn
