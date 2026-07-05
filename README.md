# 9Router Auto CF

Auto-harvest Cloudflare Workers AI API tokens and inject into 9Router. Free inference at scale.

## What it does

```
your accounts (akun.txt)
    ↓
bot_cf.py (Chrome automation → CF internal API)
    ↓
cf_keys.txt (harvested API tokens)
    ↓
inject_9router.py (POST to 9Router built-in provider)
    ↓
cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast → FREE
```

Uses 9Router's **built-in `cloudflare-ai` provider**. No custom provider setup needed — just add API keys and go.

## Free models (auto-registered by 9Router)

**Chat (13 models):**
- `cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast` — Llama 3.3 70B
- `cf/@cf/deepseek-ai/deepseek-r1-distill-qwen-32b` — DeepSeek R1 32B
- `cf/@cf/moonshotai/kimi-k2.6` — Kimi K2.6
- `cf/@cf/qwen/qwen2.5-coder-32b-instruct` — Qwen 2.5 Coder 32B
- `cf/@cf/qwen/qwq-32b` — QwQ 32B
- `cf/@cf/zai-org/glm-4.7-flash` — GLM 4.7 Flash
- `cf/@cf/zai-org/glm-5.2` — GLM 5.2 (auto-registered by inject)
- `cf/@cf/mistralai/mistral-small-3.1-24b-instruct` — Mistral Small 3.1 24B
- ...and more

**Image (8 models):**
- `cf/@cf/black-forest-labs/flux-2-klein-9b` — FLUX.2 Klein 9B
- `cf/@cf/black-forest-labs/flux-2-dev` — FLUX.2 Dev
- `cf/@cf/stabilityai/stable-diffusion-xl-base-1.0` — SDXL
- ...and more

## Prerequisites

- Python 3.10+
- Chrome/Chromium installed
- 9Router running on `localhost:20128`

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

### 2. Harvest tokens

```bash
python3 bot_cf.py          # harvest all (auto-skip already done)
python3 bot_cf.py --clean  # fresh run
```

### 3. Inject into 9Router

```bash
python3 inject_9router.py
```

That's it. No restart needed. Models auto-discovered.

### 4. Use it

```bash
curl http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast","messages":[{"role":"user","content":"hello"}]}'
```

Works with any OpenAI-compatible client. Use `cf/<model-id>` as model name.

## How it works

1. **Harvest**: Chrome per account → Google OAuth → CF Dashboard → API token via CF internal API → save to `cf_keys.txt`

2. **Inject**: Reads `cf_keys.txt` → `POST /api/providers` per token → adds as connection to built-in `cloudflare-ai` provider with `accountId` in `providerSpecificData`

3. **Route**: `cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast` → 9Router strips `cf/` → forwards to `https://api.cloudflare.com/client/v4/accounts/{accountId}/ai/v1/chat/completions`

9Router load-balances across all connections automatically.

## Files

| File | Purpose |
|------|---------|
| `bot_cf.py` | Chrome harvester — login + token creation |
| `inject_9router.py` | Reads cf_keys.txt → POST to 9Router API |
| `akun.txt.example` | Account list template |
| `akun.txt` | Your accounts (gitignored) |
| `cf_keys.txt` | Harvested tokens (gitignored) |

## Options

```bash
python3 bot_cf.py              # harvest all accounts
python3 bot_cf.py --clean      # clear cf_keys.txt and start fresh
python3 inject_9router.py      # inject all tokens to 9Router
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Tab Google OAuth gak muncul" | Kill Chrome first. One instance at a time. |
| "Field password gak muncul" | Google CAPTCHA. Try different IP. |
| "API gagal bikin token" | CF rate limit. Wait a few minutes, re-run. |
| inject: "sudah ada" | Normal — dedup. Skipped automatically. |
| Model returns 502 | 9Router auto-retries on next connection. |

## License

MIT
