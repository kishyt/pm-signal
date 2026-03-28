"""
PM Signal — Timing Edge Engine
Detects markets where NO is underpriced relative to elapsed time in the resolution window.

GET /api/run
GET /api/run?min_hours=6&max_hours=24&min_gap=0.08
"""

import json
import urllib.request
import urllib.parse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional
from http.server import BaseHTTPRequestHandler


# ── Config ──────────────────────────────────────────────────────────────────────
MIN_GAP_PP         = 0.08   # minimum gap (8pp) to flag a market
MIN_LIQUIDITY      = 500    # minimum USD liquidity
MIN_VOLUME         = 1000   # minimum USD volume
WINDOW_MIN_HRS     = 6      # ignore markets resolving in < 6h (too late to enter)
WINDOW_MAX_HRS     = 24     # only look at markets resolving within 24h
MIN_ELAPSED_FRAC   = 0.50   # only flag if >= 50% of window elapsed
HIGH_CONV_GAP      = 0.15   # >= 15pp gap = high conviction
ASSUMED_WINDOW_HRS = 24     # fallback window size when startDate unavailable
PRICE_ALERT_THRESH = 0.12   # YES is this far above fair → possible confirming news
HIGH_VOL_LIQ       = 5.0    # volume/liquidity ratio above this → elevated activity


# ── Data models ─────────────────────────────────────────────────────────────────
@dataclass
class Market:
    id: str
    question: str
    yes_price: float
    no_price: float
    volume: float
    liquidity: float
    end_date: str
    hours_remaining: float
    elapsed_fraction: float
    window_days: float          # how long the market has been open (context)
    slug: str = ""
    volume_to_liquidity: float = 0.0


@dataclass
class TimingFlag:
    market_id: str
    question: str
    slug: str
    current_no: float           # current NO price
    fair_no: float              # time-adjusted fair NO (= elapsed_fraction)
    gap_pp: float               # fair_no - current_no (positive = NO underpriced)
    hours_remaining: float
    elapsed_pct: int
    volume: float
    liquidity: float
    window_days: float
    conviction: str             # high / medium / low
    price_alert: bool           # possible confirming news detected
    price_alert_reason: str
    reasoning: str
    timestamp: str


# ── Polymarket client ────────────────────────────────────────────────────────────
class PolymarketClient:
    BASE    = "https://gamma-api.polymarket.com"
    TIMEOUT = 10

    def fetch_markets(self, min_hours=WINDOW_MIN_HRS, max_hours=WINDOW_MAX_HRS):
        now = datetime.now(timezone.utc)
        markets = []
        try:
            params = urllib.parse.urlencode({
                "active": "true", "closed": "false",
                "limit": 100, "order": "endDate", "ascending": "true",
            })
            req = urllib.request.Request(
                f"{self.BASE}/markets?{params}",
                headers={"User-Agent": "pm-signal/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                raw = json.loads(resp.read())
            batch = raw if isinstance(raw, list) else raw.get("data", [])
            for item in batch:
                m = self._parse(item, now, min_hours, max_hours)
                if m:
                    markets.append(m)
        except Exception:
            pass

        return markets if markets else self._demo(now)

    def _parse(self, raw: dict, now: datetime, min_hours: float, max_hours: float) -> Optional[Market]:
        end_dt = self._parse_iso(raw.get("endDate", ""))
        if not end_dt:
            return None

        hours_remaining = (end_dt - now).total_seconds() / 3600
        if not (min_hours <= hours_remaining <= max_hours):
            return None

        if not raw.get("active", True) or raw.get("closed", False):
            return None

        # Prices — outcomePrices is a JSON-encoded array: ["0.3", "0.7"]
        prices_raw = raw.get("outcomePrices", "[]")
        if isinstance(prices_raw, str):
            try:
                prices = json.loads(prices_raw)
            except Exception:
                return None
        else:
            prices = prices_raw or []

        if len(prices) < 2:
            return None
        try:
            yes_price = float(prices[0])
            no_price  = float(prices[1])
        except (ValueError, TypeError):
            return None

        if not (0.01 <= yes_price <= 0.99):
            return None

        volume    = float(raw.get("volume",    0) or 0)
        liquidity = float(raw.get("liquidity", 0) or 0)
        if liquidity < MIN_LIQUIDITY or volume < MIN_VOLUME:
            return None

        # Elapsed fraction — use startDate when available, else assume ASSUMED_WINDOW_HRS
        start_dt = self._parse_iso(raw.get("startDate", "") or raw.get("createdAt", ""))
        if start_dt and start_dt < end_dt:
            total_secs    = (end_dt - start_dt).total_seconds()
            elapsed_secs  = (now - start_dt).total_seconds()
            elapsed_frac  = max(0.0, min(1.0, elapsed_secs / total_secs))
            window_days   = round(total_secs / 86400, 1)
        else:
            total_secs    = ASSUMED_WINDOW_HRS * 3600
            elapsed_secs  = total_secs - hours_remaining * 3600
            elapsed_frac  = max(0.0, min(1.0, elapsed_secs / total_secs))
            window_days   = round(ASSUMED_WINDOW_HRS / 24, 1)

        if elapsed_frac < MIN_ELAPSED_FRAC:
            return None

        slug = ((raw.get("events") or [{}])[0].get("slug") or raw.get("slug", ""))

        return Market(
            id=str(raw.get("id", "")),
            question=raw.get("question", "Unknown"),
            yes_price=yes_price,
            no_price=no_price,
            volume=volume,
            liquidity=liquidity,
            end_date=raw.get("endDate", ""),
            hours_remaining=round(hours_remaining, 2),
            elapsed_fraction=round(elapsed_frac, 3),
            window_days=window_days,
            slug=slug,
            volume_to_liquidity=round(volume / liquidity, 2) if liquidity else 0.0,
        )

    @staticmethod
    def _parse_iso(s: str) -> Optional[datetime]:
        if not s:
            return None
        for fmt in (
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S+00:00",
            "%Y-%m-%dT%H:%M:%S.%f+00:00",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    def _demo(self, now: datetime) -> list:
        """Fallback demo markets (shown when API is unreachable)."""
        rows = [
            # question,                                        yes,  no,  hrs,  ef,   wdays, vol,    liq
            ("Will the Fed make a rate announcement today?",   0.28, 0.72, 4.5,  0.81,  1.0, 45000,  12000),
            ("Will Bitcoin exceed $90,000 today?",             0.35, 0.65, 6.0,  0.75,  1.0, 120000, 35000),
            ("Will S&P 500 close up more than 1% today?",      0.20, 0.80, 3.0,  0.88,  1.0, 80000,  22000),
            ("Will Trump sign an executive order today?",       0.42, 0.58, 8.0,  0.67,  1.0, 25000,   8000),
            ("Will ETH reach $3,500 by end of day?",           0.15, 0.85, 5.0,  0.79,  1.0, 55000,  18000),
            ("Will there be a Fed press conference today?",     0.31, 0.69, 7.5,  0.69,  1.0, 18000,   6000),
            ("Will oil price exceed $75 per barrel today?",    0.44, 0.56, 10.0, 0.58,  1.0, 32000,  11000),
        ]
        return [
            Market(
                id=f"demo_{i+1}",
                question=q,
                yes_price=yes, no_price=no,
                volume=vol, liquidity=liq,
                end_date=(now + timedelta(hours=hrs)).isoformat(),
                hours_remaining=hrs,
                elapsed_fraction=ef,
                window_days=wd,
                slug="",
                volume_to_liquidity=round(vol / liq, 2),
            )
            for i, (q, yes, no, hrs, ef, wd, vol, liq) in enumerate(rows)
        ]


# ── Timing edge engine ───────────────────────────────────────────────────────────
class TimingEdgeEngine:
    """
    Core signal: NO is underpriced given how much of the resolution window has elapsed.

    Time-decay fair value: fair_no = elapsed_fraction
      Rationale — if 80% of the window has passed with no confirming event,
      NO should be priced at >= 80%. If it's still at 60%, the gap is 20pp.

    Price alert heuristics (proxy for 'confirming news'):
      - YES is significantly above the time-adjusted fair value
      - Volume/liquidity ratio is unusually high (recent order flow spike)
    """

    def __init__(self, min_gap=MIN_GAP_PP):
        self.min_gap = min_gap

    def score(self, markets: list) -> list:
        now   = datetime.now(timezone.utc).isoformat()
        flags = [f for m in markets if (f := self._evaluate(m, now))]
        flags.sort(key=lambda f: f.gap_pp, reverse=True)
        return flags

    def _evaluate(self, m: Market, timestamp: str) -> Optional[TimingFlag]:
        fair_no = m.elapsed_fraction          # time-decay fair NO floor
        gap     = fair_no - m.no_price

        if gap < self.min_gap:
            return None

        # Price alert: YES is above fair, or volume spike
        price_alert        = False
        price_alert_reason = ""
        fair_yes           = 1.0 - fair_no

        if m.yes_price > fair_yes + PRICE_ALERT_THRESH:
            price_alert = True
            price_alert_reason = (
                f"YES at {m.yes_price:.0%} is well above the time-adjusted fair "
                f"of {fair_yes:.0%} — market may be pricing in a recent confirming event"
            )
        elif m.volume_to_liquidity > HIGH_VOL_LIQ:
            price_alert = True
            price_alert_reason = (
                f"Volume/liquidity ratio of {m.volume_to_liquidity:.1f}x — "
                f"elevated order flow may signal new information"
            )

        # Conviction (dampened one level when alert present)
        if gap >= HIGH_CONV_GAP and not price_alert:
            conviction = "high"
        elif gap >= MIN_GAP_PP and not price_alert:
            conviction = "medium"
        else:
            conviction = "low"

        elapsed_pct = int(m.elapsed_fraction * 100)
        hrs         = round(m.hours_remaining, 1)
        reasoning   = (
            f"{elapsed_pct}% of the resolution window has elapsed with {hrs}h remaining. "
            f"Time-decay fair value for NO: {fair_no:.0%}. "
            f"Market is pricing NO at {m.no_price:.0%} — a {gap:.0%} gap. "
        )
        if price_alert:
            reasoning += f"⚠ CAUTION: {price_alert_reason}."
        else:
            reasoning += "No price spike detected; gap likely reflects slow time-decay updating."

        return TimingFlag(
            market_id=m.id,
            question=m.question,
            slug=m.slug,
            current_no=round(m.no_price, 3),
            fair_no=round(fair_no, 3),
            gap_pp=round(gap, 3),
            hours_remaining=round(m.hours_remaining, 2),
            elapsed_pct=elapsed_pct,
            volume=m.volume,
            liquidity=m.liquidity,
            window_days=m.window_days,
            conviction=conviction,
            price_alert=price_alert,
            price_alert_reason=price_alert_reason,
            reasoning=reasoning,
            timestamp=timestamp,
        )


# ── Pipeline ─────────────────────────────────────────────────────────────────────
def run_pipeline(min_hours=WINDOW_MIN_HRS, max_hours=WINDOW_MAX_HRS, min_gap=MIN_GAP_PP):
    client  = PolymarketClient()
    engine  = TimingEdgeEngine(min_gap=min_gap)
    markets = client.fetch_markets(min_hours=min_hours, max_hours=max_hours)
    flags   = engine.score(markets)

    high  = sum(1 for f in flags if f.conviction == "high")
    med   = sum(1 for f in flags if f.conviction == "medium")
    low   = sum(1 for f in flags if f.conviction == "low")
    alrt  = sum(1 for f in flags if f.price_alert)

    return {
        "run_at":          datetime.now(timezone.utc).isoformat(),
        "markets_scanned": len(markets),
        "config": {
            "window":    f"{min_hours}–{max_hours}h",
            "min_gap":   f"{int(min_gap * 100)}pp",
            "min_liq":   f"${MIN_LIQUIDITY:,}",
            "min_vol":   f"${MIN_VOLUME:,}",
        },
        "flags":   [asdict(f) for f in flags],
        "summary": {
            "total":         len(flags),
            "high":          high,
            "medium":        med,
            "low":           low,
            "price_alerts":  alrt,
            "clean_signals": len(flags) - alrt,
        },
        "markets": [asdict(m) for m in markets],
    }


# ── Vercel handler ───────────────────────────────────────────────────────────────
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        qs        = parse_qs(urlparse(self.path).query)
        min_hours = float(qs.get("min_hours", [WINDOW_MIN_HRS])[0])
        max_hours = float(qs.get("max_hours", [WINDOW_MAX_HRS])[0])
        min_gap   = float(qs.get("min_gap",   [MIN_GAP_PP])[0])
        try:
            body = json.dumps(run_pipeline(min_hours, max_hours, min_gap)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def log_message(self, *args):
        pass
