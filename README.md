# PM Signal — Vercel Deployment

## Project structure

```
pm-signal/
├── api/
│   ├── run.py              # Serverless function → GET /api/run
│   └── requirements.txt    # Python deps (stdlib only, no installs needed)
├── public/
│   └── index.html          # Dashboard SPA — fetches from /api/run
├── vercel.json             # Routing config
└── README.md
```

## Deploy in 3 steps

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "Initial PM Signal deploy"
gh repo create pm-signal --public --push   # or use GitHub UI
```

### 2. Import to Vercel
1. Go to [vercel.com/new](https://vercel.com/new)
2. Import your GitHub repo
3. Framework preset: **Other**
4. Root directory: `.` (leave default)
5. No build command needed
6. Click **Deploy**

That's it. Vercel auto-detects `api/run.py` as a serverless function
and `public/` as static assets.

### 3. Open your deployment
```
https://pm-signal-<your-name>.vercel.app
```

The dashboard loads, calls `/api/run`, runs the full pipeline against
the live Polymarket Gamma API, and renders all flagged markets.

---

## How it works on Vercel

```
Browser → GET /                → Vercel CDN → public/index.html
Browser → GET /api/run         → Vercel Function → api/run.py
                                   └─ hits gamma-api.polymarket.com
                                   └─ runs 3 strategy engines
                                   └─ returns JSON flags
```

Each "RUN SCAN" button click triggers a fresh `/api/run` call —
Polymarket data is fetched in real-time, no database needed.

---

## Vercel function limits (free tier)

| Limit          | Free tier   | Notes                          |
|----------------|-------------|--------------------------------|
| Execution time | 10s         | Pipeline runs in ~2–3s         |
| Memory         | 1024 MB     | Pipeline uses <50 MB           |
| Invocations    | 100k/month  | Well within budget             |
| Regions        | 1           | Add more on Pro                |

---

## Local development

```bash
# Install Vercel CLI
npm i -g vercel

# Run locally (mirrors production exactly)
vercel dev

# Visit http://localhost:3000
```

`vercel dev` spins up both the static server and the Python function
locally — no separate processes needed.

---

## Environment variables (optional)

If you later add a Claude API key for LLM-powered resolution parsing:

```bash
vercel env add ANTHROPIC_API_KEY
```

Then in `api/run.py`:
```python
import os
api_key = os.environ.get("ANTHROPIC_API_KEY")
```

---

## Adding auto-refresh (optional)

To refresh every 5 minutes on the live site, add to `public/index.html`
before `</script>`:

```js
setInterval(fetchAndInit, 5 * 60 * 1000);
```

---

## Upgrading to Vercel Pro

Only needed if you want:
- Execution time > 10s (not needed currently)
- Multiple regions / edge functions
- Password protection via Vercel Auth
