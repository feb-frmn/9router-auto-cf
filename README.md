# 9Router Auto CF

Auto-harvest Cloudflare Workers AI API tokens and inject into 9Router. Free inference at scale.

## What it does

```
your accounts (akun.txt)
    ↓
bot_cf.py (Chrome automation)
    ↓
cf_keys.txt (harvested API tokens)
    ↓
inject_9router.py (auto-inject to 9Router DB)
    ↓
9Router: cfai/@cf/zai-org/glm-5.2 → FREE
```

Each Cloudflare account gets its own API token + endpoint. 9Router load-balances across all of them automatically.

## Free models

| Model | ID |
|-------|-----|
| GLM 5.2 | `cfai/@cf/zai-org/glm-5.2` |
| DeepSeek R1 32B | `cfai/@cf/deepseek-ai/deepseek-r1-distill-qwen-32b` |
| Llama 3.3 70B | `cfai/@cf/meta/llama-3.3-70b-instruct-fp8-fast` |
| Llama 3.1 70B | `cfai/@cf/meta/llama-3.1-70b-instruct` |
| Qwen 2.5 Coder 32B | `cfai/@cf/qwen/qwen2.5-coder-32b-instruct` |

## Prerequisites

- Python 3.10+
- Chrome/Chromium installed
- 9Router running with [add-provider-db.sh](https://github.com/feb-frmn/9router-add-provider)
- sqlite3 (`sudo apt install sqlite3`)

## Setup

```bash
git clone https://github.com/feb-frmn/9router-auto-cf.git
cd 9router-auto-cf
pip install DrissionPage
```

### 1. Add your accounts

```bash
cp akun.txt.example akun.txt
nano akun.txt
```

Format: `email|password` per line

```
user1@gmail.com|yourpassword
user2@gmail.com|yourpassword
```

Google accounts that have access to Cloudflare Dashboard. Google Workspace accounts work great.

### 2. Harvest tokens

```bash
# Run all accounts (auto-skip already harvested)
python3 bot_cf.py

# Fresh run (clears previous tokens)
python3 bot_cf.py --clean
```

Per account: ~2-3 min (login + token creation + human-like delays). The bot handles:
- Google OAuth login (including Account Chooser, Workspace TOS, consent screens)
- Account ID extraction from CF Dashboard
- API token creation via CF internal API (all Workers AI permissions)
- Deduplication (skips accounts already in cf_keys.txt)

### 3. Inject into 9Router

```bash
python3 inject_9router.py
```

- Creates `cfai` provider node on first run
- Adds each token as a connection (9Router load-balances)
- Deduplicates (skips keys already in DB)
- After inject: `systemctl restart 9router`

### 4. Use it

```bash
curl http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"cfai/@cf/zai-org/glm-5.2","messages":[{"role":"user","content":"hello"}]}'
```

Works with any OpenAI-compatible client. Just use `cfai/<model-id>` as the model name.

## Options

```bash
python3 bot_cf.py              # harvest all accounts
python3 bot_cf.py --clean      # clear cf_keys.txt and start fresh
python3 inject_9router.py      # inject all tokens to 9Router
```

## How it works

1. **Harvest**: Opens Chrome per account → Google OAuth → CF Dashboard → creates API token via `/api/v4/user/tokens` with all 6 Workers AI permissions → saves to `cf_keys.txt`

2. **Inject**: Reads `cf_keys.txt` → calls `add-provider-db.sh` per token → inserts into 9Router's SQLite DB as OpenAI-compatible provider with prefix `cfai`

3. **Route**: User sends `cfai/@cf/zai-org/glm-5.2` → 9Router strips `cfai/` → forwards to CF endpoint with the token's account-scoped URL

Each account has a unique endpoint (`/accounts/{id}/ai/v1`). All share prefix `cfai` but each connection has its own base URL + API key. 9Router tries connections in order — if one hits rate limit, it falls over to the next.

## Files

| File | Purpose |
|------|---------|
| `bot_cf.py` | Chrome harvester — login + token creation |
| `inject_9router.py` | Reads cf_keys.txt → injects to 9Router DB |
| `akun.txt.example` | Account list template |
| `akun.txt` | Your accounts (gitignored) |
| `cf_keys.txt` | Harvested tokens (gitignored) |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Tab Google OAuth gak muncul" | Kill all Chrome processes first. Only one instance at a time. |
| "Field password gak muncul" | Google CAPTCHA. Try different IP or wait. |
| "Gagal ambil Account ID" | Dashboard didn't load. Re-run — skips already harvested. |
| "API gagal bikin token" | CF rate limit. Wait a few minutes, re-run. |
| inject: "sudah ada" | Normal — dedup. Skipped automatically. |
| Model not working in 9Router | `systemctl restart 9router` after inject. |

## Requirements

- **Cloudflare account** with Workers AI access (free plan works)
- **Chrome/Chromium** installed on the machine
- **9Router** running on the same machine (for inject)
- **add-provider-db.sh** from [9router-add-provider](https://github.com/feb-frmn/9router-add-provider)

## License

MIT
