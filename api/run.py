"""
Vercel serverless function — /api/run
Runs the PM Signal pipeline and returns results as JSON.
GET  /api/run        → run pipeline, return flags + markets
"""

import json
import math
import re
import urllib.request
import urllib.parse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from http.server import BaseHTTPRequestHandler


# ── Paste pipeline logic inline (Vercel functions are single-file) ─────────────

@dataclass
class Market:
    id: str
    question: str
    yes_price: float
    volume: float
    liquidity: float
    end_date: str
    category: str
    tags: list
    active: bool
    slug: str = ""

@dataclass
class BaseRatePrior:
    reference_class: str
    historical_rate: float
    sample_size: int
    confidence: str
    notes: str = ""

@dataclass
class Flag:
    market_id: str
    question: str
    current_price: float
    strategy: str
    signal_direction: str
    edge: float
    model_price: float
    conviction: str
    reasoning: str
    related_market_ids: list = field(default_factory=list)
    resolution_notes: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PolymarketClient:
    GAMMA_BASE = "https://gamma-api.polymarket.com"

    def fetch_markets(self, limit=200, min_liquidity=500):
        params = urllib.parse.urlencode({
            "active": "true", "closed": "false",
            "limit": limit, "order": "volume", "ascending": "false",
        })
        try:
            req = urllib.request.Request(
                f"{self.GAMMA_BASE}/markets?{params}",
                headers={"User-Agent": "pm-signal/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.loads(resp.read())
            markets = []
            for m in raw:
                try:
                    outcomes = json.loads(m.get("outcomePrices", "[]"))
                    yes_price = float(outcomes[0]) if outcomes else 0.5
                    liquidity = float(m.get("liquidity", 0))
                    if liquidity < min_liquidity:
                        continue
                    markets.append(Market(
                        id=m.get("id", ""),
                        question=m.get("question", ""),
                        yes_price=yes_price,
                        volume=float(m.get("volume", 0)),
                        liquidity=liquidity,
                        end_date=m.get("endDate", ""),
                        category=m.get("category", ""),
                        tags=[t.get("label", "") for t in m.get("tags", [])],
                        active=True,
                        slug=m.get("slug", ""),
                    ))
                except Exception:
                    continue
            return markets if markets else self._demo()
        except Exception:
            return self._demo()

    def _demo(self):
        return [
            Market("pm_001", "Will the Federal Reserve cut rates in Q3 2025?", 0.38, 245000, 18000, "2025-09-30", "economics", ["fed","rates","macro"], True),
            Market("pm_002", "Will the Federal Reserve cut rates in Q4 2025?", 0.61, 189000, 14200, "2025-12-31", "economics", ["fed","rates","macro"], True),
            Market("pm_003", "Will US inflation fall below 3% by end of 2025?", 0.54, 132000, 9800, "2025-12-31", "economics", ["inflation","cpi","macro"], True),
            Market("pm_004", "Will Bitcoin exceed $120k in 2025?", 0.41, 890000, 65000, "2025-12-31", "crypto", ["bitcoin","btc"], True),
            Market("pm_005", "Will Bitcoin exceed $150k in 2025?", 0.28, 540000, 38000, "2025-12-31", "crypto", ["bitcoin","btc"], True),
            Market("pm_006", "Will the S&P 500 end 2025 above 6000?", 0.72, 320000, 24000, "2025-12-31", "finance", ["sp500","equities","macro"], True),
            Market("pm_007", "Will the S&P 500 end 2025 above 7000?", 0.18, 280000, 19000, "2025-12-31", "finance", ["sp500","equities","macro"], True),
            Market("pm_008", "Will there be a US recession before end of 2025?", 0.29, 415000, 31000, "2025-12-31", "economics", ["recession","gdp","macro"], True),
            Market("pm_009", "Will the UK general election happen before June 2025?", 0.12, 88000, 6200, "2025-06-30", "politics", ["uk","election"], True),
            Market("pm_010", "Will any G7 country enter recession in 2025?", 0.44, 167000, 12500, "2025-12-31", "economics", ["g7","recession","macro"], True),
            Market("pm_011", "Will GPT-5 be released before July 2025?", 0.67, 203000, 15800, "2025-07-01", "tech", ["ai","openai","gpt"], True),
            Market("pm_012", "Will Apple release AR glasses in 2025?", 0.21, 145000, 10200, "2025-12-31", "tech", ["apple","ar","hardware"], True),
            Market("pm_013", "Will Elon Musk remain Tesla CEO through 2025?", 0.74, 312000, 22000, "2025-12-31", "business", ["tesla","musk"], True),
            Market("pm_014", "Will there be a significant cyberattack on US infrastructure in 2025?", 0.55, 98000, 7100, "2025-12-31", "security", ["cyber","infrastructure"], True),
            Market("pm_015", "Will oil price exceed $100/barrel in 2025?", 0.33, 276000, 20000, "2025-12-31", "commodities", ["oil","energy","macro"], True),
        ]


class BaseRateEngine:
    PRIORS = {
        "fed_rate_cut_quarter": BaseRatePrior("Fed rate cut in a given quarter (easing cycle)", 0.35, 48, "medium", "Since 1990: ~35% of quarters see a cut when in easing cycle"),
        "fed_rate_cut_year":    BaseRatePrior("Fed cuts at least once in a calendar year", 0.55, 35, "medium", "~55% of years since 1988 include at least one cut"),
        "us_recession_annual":  BaseRatePrior("US recession starting in a given year", 0.14, 35, "high", "~2 recessions per 14 years since 1990"),
        "g7_recession_annual":  BaseRatePrior("Any G7 country enters recession in a year", 0.55, 35, "medium", "At least one G7 country in recession ~55% of years"),
        "sp500_year_end_above_prior": BaseRatePrior("S&P 500 ends year above prior year close", 0.72, 35, "high", "S&P 500 positive ~72% of calendar years since 1990"),
        "btc_2x_annual":        BaseRatePrior("Bitcoin doubles in a calendar year", 0.38, 10, "low", "BTC has 2x'd in ~4 of 10 full years"),
        "ceo_retention_annual": BaseRatePrior("Prominent CEO remains in role for full year", 0.82, 200, "high", "S&P 500 CEO turnover ~18% annually"),
        "tech_product_launch_h1": BaseRatePrior("Major tech product launch in announced window", 0.58, 80, "medium", "Slippage is common; announced windows ~58% hit rate"),
        "inflation_target_hit_annual": BaseRatePrior("Inflation hits central bank target within calendar year", 0.42, 30, "medium", "Post-2020 environment; historically higher hit rate"),
        "commodity_price_spike_annual": BaseRatePrior("Commodity exceeds +50% threshold in a year", 0.22, 40, "medium", "Oil, metals, ag commodities breach large thresholds ~22% of years"),
    }
    KEYWORD_MAP = [
        (["federal reserve","fed","rate cut","rate hike","fomc"], {"quarter":"fed_rate_cut_quarter","year":"fed_rate_cut_year"}),
        (["recession","gdp","contraction"], {"g7":"g7_recession_annual","us":"us_recession_annual","default":"us_recession_annual"}),
        (["s&p","sp500","stock market","equities"], {"default":"sp500_year_end_above_prior"}),
        (["bitcoin","btc"], {"default":"btc_2x_annual"}),
        (["ceo","remain","resign","fired","step down"], {"default":"ceo_retention_annual"}),
        (["release","launch","ship","announce"], {"default":"tech_product_launch_h1"}),
        (["inflation","cpi","pce"], {"default":"inflation_target_hit_annual"}),
        (["oil","energy","barrel","gas"], {"default":"commodity_price_spike_annual"}),
    ]

    def classify(self, market):
        q = market.question.lower()
        for keywords, class_map in self.KEYWORD_MAP:
            if any(k in q for k in keywords):
                if "g7" in q or "europe" in q:
                    key = class_map.get("g7", class_map.get("default"))
                elif any(x in q for x in ["quarter","q1","q2","q3","q4"]):
                    key = class_map.get("quarter", class_map.get("default"))
                else:
                    key = class_map.get("us", class_map.get("year", class_map.get("default")))
                if key:
                    return self.PRIORS.get(key)
        return None

    def flag(self, market, threshold=0.12):
        prior = self.classify(market)
        if not prior:
            return None
        edge = market.yes_price - prior.historical_rate
        if abs(edge) < threshold:
            return None
        direction = "overpriced" if edge > 0 else "underpriced"
        conviction = "high" if abs(edge) > 0.20 and prior.confidence == "high" else "medium" if abs(edge) > 0.15 else "low"
        return Flag(
            market_id=market.id, question=market.question,
            current_price=market.yes_price, strategy="base_rate",
            signal_direction=direction, edge=round(abs(edge), 3),
            model_price=prior.historical_rate, conviction=conviction,
            reasoning=(
                f"Reference class: '{prior.reference_class}'. "
                f"Historical rate: {prior.historical_rate:.0%} (n={prior.sample_size}, confidence={prior.confidence}). "
                f"Market prices at {market.yes_price:.0%} — {abs(edge):.0%} {direction.replace('priced',' vs base rate')}. "
                f"{prior.notes}"
            ),
        )


class CorrelationEngine:
    def _topic_key(self, market):
        q = market.question.lower()
        tag_set = set(t.lower() for t in market.tags)
        patterns = [
            (["federal reserve","fed rate","fomc","interest rate"], "fed_rates"),
            (["s&p 500","sp500","s&p500"], "sp500"),
            (["bitcoin","btc"], "bitcoin"),
            (["recession","gdp contraction"], "recession"),
            (["inflation","cpi","pce"], "inflation"),
            (["oil","crude","barrel"], "oil"),
        ]
        for keywords, key in patterns:
            if any(k in q for k in keywords) or any(k in tag_set for k in keywords):
                return key
        return None

    def _extract_threshold(self, question):
        for pat in [r"\$([0-9,]+)k?\b", r"([0-9]+(?:\.[0-9]+)?)\s*%", r"above\s+([0-9,]+)", r"exceed\s+([0-9,]+)"]:
            m = re.search(pat, question, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except ValueError:
                    continue
        return None

    def run(self, markets):
        groups = {}
        for m in markets:
            key = self._topic_key(m)
            if key:
                groups.setdefault(key, []).append(m)
        flags = []
        for group in groups.values():
            if len(group) < 2:
                continue
            wt = [(m, self._extract_threshold(m.question)) for m in group]
            wt = [(m, t) for m, t in wt if t is not None]
            wt.sort(key=lambda x: x[1])
            for i in range(len(wt) - 1):
                m_low, t_low = wt[i]
                m_high, t_high = wt[i + 1]
                if m_high.yes_price > m_low.yes_price + 0.05:
                    edge = m_high.yes_price - m_low.yes_price
                    flags.append(Flag(
                        market_id=m_high.id, question=m_high.question,
                        current_price=m_high.yes_price, strategy="correlation",
                        signal_direction="overpriced", edge=round(edge, 3),
                        model_price=round(m_low.yes_price - 0.03, 3),
                        conviction="high" if edge > 0.10 else "medium",
                        reasoning=(
                            f"Monotonicity violation: '{m_high.question}' "
                            f"(threshold {t_high:,.0f}, price {m_high.yes_price:.0%}) "
                            f"priced HIGHER than easier condition "
                            f"'{m_low.question}' (threshold {t_low:,.0f}, price {m_low.yes_price:.0%}). "
                            f"Gap: {edge:.0%}. The harder event cannot be more likely."
                        ),
                        related_market_ids=[m_low.id],
                    ))
        return flags


class ResolutionParser:
    PATTERNS = [
        (r"\bsignificant(ly)?\b", "Vague qualifier: 'significant' — resolver interpretation will vary"),
        (r"\bmajor\b", "Vague qualifier: 'major' — no objective threshold defined"),
        (r"\bsoon\b|\bshortly\b", "Temporal vagueness: no exact date specified"),
        (r"\bsubstantial(ly)?\b", "Vague qualifier: 'substantial' — resolver discretion likely"),
        (r"announce[sd]?\s+(a|an|the)?\s*(plan|intention)", "Announcement vs. action ambiguity"),
        (r"by (the )?end of\s+\d{4}", "Time boundary: 'by end of year' may include/exclude Dec 31"),
    ]

    def run(self, markets):
        flags = []
        for m in markets:
            findings = [desc for pat, desc in self.PATTERNS if re.search(pat, m.question, re.IGNORECASE)]
            clauses = len(re.findall(r"\b(and|or|but|if|unless|provided|except)\b", m.question, re.I))
            if clauses >= 3:
                findings.append(f"High logical complexity: {clauses} conjunctions/conditionals")
            if not findings:
                continue
            near_extreme = m.yes_price > 0.75 or m.yes_price < 0.25
            flags.append(Flag(
                market_id=m.id, question=m.question,
                current_price=m.yes_price, strategy="resolution",
                signal_direction="ambiguous", edge=0.0,
                model_price=m.yes_price,
                conviction="medium" if near_extreme else "low",
                reasoning=f"Resolution risk detected ({len(findings)} issue(s)).",
                resolution_notes=" | ".join(findings),
            ))
        return flags


def run_pipeline():
    client     = PolymarketClient()
    base_rate  = BaseRateEngine()
    correlation = CorrelationEngine()
    resolution = ResolutionParser()

    markets    = client.fetch_markets()
    br_flags   = [f for m in markets if (f := base_rate.flag(m))]
    corr_flags = correlation.run(markets)
    flagged    = {f.market_id for f in br_flags + corr_flags}
    priority   = [m for m in markets if m.id in flagged]
    other      = [m for m in markets if m.id not in flagged]
    res_flags  = resolution.run(priority + other[:30])

    all_flags = br_flags + corr_flags + res_flags
    conv_order = {"high": 0, "medium": 1, "low": 2}
    all_flags.sort(key=lambda f: (conv_order[f.conviction], -f.edge))

    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "markets_scanned": len(markets),
        "flags": [asdict(f) for f in all_flags],
        "summary": {
            "total": len(all_flags),
            "base_rate": len(br_flags),
            "correlation": len(corr_flags),
            "resolution": len(res_flags),
            "high_conviction": sum(1 for f in all_flags if f.conviction == "high"),
            "medium_conviction": sum(1 for f in all_flags if f.conviction == "medium"),
        },
        "markets": [asdict(m) for m in markets],
    }


# ── Vercel handler ─────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            result = run_pipeline()
            body = json.dumps(result).encode()
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
        pass  # suppress Vercel log noise
