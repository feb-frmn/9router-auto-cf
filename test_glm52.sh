#!/bin/bash
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " 🧪 Testing GLM-5.2 on Cloudflare Workers AI (via 9Router)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Use python to make the request to handle any JSON/Stream parsing issues smoothly
python3 -c "
import urllib.request
import urllib.error
import json

url = 'http://localhost:20128/v1/chat/completions'
data = json.dumps({
    'model': 'cf/@cf/zai-org/glm-5.2',
    'messages': [{'role': 'user', 'content': 'reply with exactly: PONG'}],
    'max_tokens': 10
}).encode('utf-8')

req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req, timeout=30) as response:
        res = response.read().decode('utf-8')
        # 9Router might append 'data: [DONE]' to non-streaming requests depending on provider
        res = res.replace('data: [DONE]', '').strip()
        parsed = json.loads(res)
        if 'choices' in parsed:
            print('✅ Test SUCCESS! Model cf/@cf/zai-org/glm-5.2 is working!')
        else:
            print('⚠️ Test FAILED. Check API key balance or 9Router setup.')
            print(f'   Response: {res[:200]}')
except Exception as e:
    print(f'⚠️ Test FAILED with error: {e}')
"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
