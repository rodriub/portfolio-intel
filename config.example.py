"""
Portfolio Intelligence - Public Sample Configuration
====================================================
Copy this file to config.py for a public-safe starter setup:

    cp config.example.py config.py

All holdings, costs, account values, goals, and notes below are fictional.
API keys should be supplied through environment variables or a local .env file.
Do not commit your real config.py if it contains personal portfolio data.
"""

import os

def _load_local_env(path=".env"):
    """Load local key=value pairs without overriding real environment vars."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        return True
    except Exception:
        return False

LOCAL_ENV_LOADED = _load_local_env()

def _first_nonempty_env(*names, default):
    """Return the first non-empty environment value and its variable name."""
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip(), name
    return default, None


# Accepted environment variable names, in priority order:
# Polygon: POLYGON_KEY, POLYGON_API_KEY
# Tiingo: TIINGO_KEY, TIINGO_API_KEY
# Finnhub: FINNHUB, FINNHUB_KEY
# NewsData.io: NEWSDATA_KEY
# SEC API: SEC_API, SEC_API_KEY
# FRED, FMP, and Alpha Vantage use FRED_KEY, FMP_KEY, and
# ALPHAVANTAGE_KEY respectively.
FRED_KEY, FRED_KEY_SOURCE = _first_nonempty_env("FRED_KEY", default="YOUR_FRED_KEY")
POLYGON_KEY, POLYGON_KEY_SOURCE = _first_nonempty_env("POLYGON_KEY", "POLYGON_API_KEY", default="YOUR_POLYGON_KEY")
TIINGO_KEY, TIINGO_KEY_SOURCE = _first_nonempty_env("TIINGO_KEY", "TIINGO_API_KEY", default="YOUR_TIINGO_KEY")
FINNHUB_KEY, FINNHUB_KEY_SOURCE = _first_nonempty_env("FINNHUB", "FINNHUB_KEY", default="YOUR_FINNHUB_KEY")
SEC_API_KEY, SEC_API_KEY_SOURCE = _first_nonempty_env("SEC_API", "SEC_API_KEY", default="YOUR_SEC_API_KEY")
FMP_KEY, FMP_KEY_SOURCE = _first_nonempty_env("FMP_KEY", default="YOUR_FMP_KEY")
ALPHAVANTAGE_KEY, ALPHAVANTAGE_KEY_SOURCE = _first_nonempty_env("ALPHAVANTAGE_KEY", default="YOUR_ALPHAVANTAGE_KEY")
NEWSAPI_KEY, NEWSAPI_KEY_SOURCE = _first_nonempty_env("NEWSAPI_KEY", default="YOUR_NEWSAPI_KEY")
NEWSDATA_KEY, NEWSDATA_KEY_SOURCE = _first_nonempty_env("NEWSDATA_KEY", default="YOUR_NEWSDATA_KEY")


def provider_configuration_report():
    """Return provider configuration state without exposing credential values."""
    providers = [
        ("Polygon", POLYGON_KEY, POLYGON_KEY_SOURCE),
        ("FRED", FRED_KEY, FRED_KEY_SOURCE),
        ("Tiingo", TIINGO_KEY, TIINGO_KEY_SOURCE),
        ("Finnhub", FINNHUB_KEY, FINNHUB_KEY_SOURCE),
        ("SEC API", SEC_API_KEY, SEC_API_KEY_SOURCE),
        ("FMP", FMP_KEY, FMP_KEY_SOURCE),
        ("Alpha Vantage", ALPHAVANTAGE_KEY, ALPHAVANTAGE_KEY_SOURCE),
        ("NewsData", NEWSDATA_KEY, NEWSDATA_KEY_SOURCE),
    ]
    return [
        {
            "provider": provider,
            "variable_detected": source,
            "configured": bool(value and not str(value).startswith("YOUR_")),
        }
        for provider, value, source in providers
    ]

# Sample portfolio. These are not recommendations.
PORTFOLIO = [
    {
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "sector": "Consumer Technology",
        "theme": "Quality Growth",
        "category": "Single Stock",
        "benchmark_group": "Growth/Tech",
        "risk_bucket": "Medium",
        "shares": 2.0,
        "avg_cost": 175.00,
        "notes": "Sample core technology holding.",
    },
    {
        "ticker": "MSFT",
        "name": "Microsoft Corp.",
        "sector": "Software/Cloud",
        "theme": "AI Infrastructure",
        "category": "Single Stock",
        "benchmark_group": "Growth/Tech",
        "risk_bucket": "Medium",
        "shares": 1.0,
        "avg_cost": 400.00,
        "notes": "Sample software and cloud exposure.",
    },
    {
        "ticker": "NVDA",
        "name": "NVIDIA Corp.",
        "sector": "Semiconductors",
        "theme": "AI Infrastructure",
        "category": "Single Stock",
        "benchmark_group": "Growth/Tech",
        "risk_bucket": "High growth",
        "shares": 1.5,
        "avg_cost": 120.00,
        "notes": "Sample semiconductor exposure.",
    },
    {
        "ticker": "JNJ",
        "name": "Johnson & Johnson",
        "sector": "Healthcare",
        "theme": "Defensive Quality",
        "category": "Single Stock",
        "benchmark_group": "Defensive",
        "risk_bucket": "Low",
        "shares": 1.0,
        "avg_cost": 155.00,
        "notes": "Sample defensive healthcare exposure.",
    },
    {
        "ticker": "VTI",
        "name": "Vanguard Total Stock Market ETF",
        "sector": "ETF",
        "theme": "Broad Market",
        "category": "ETF",
        "benchmark_group": "Broad Market",
        "risk_bucket": "Medium",
        "shares": 2.0,
        "avg_cost": 250.00,
        "notes": "Sample diversified equity ETF exposure.",
    },
]

TICKERS = [p["ticker"] for p in PORTFOLIO]

# Market context tickers are benchmarks/indicators only. They must never be
# included in portfolio value, P&L, allocation policy, goals, or family-office
# net worth calculations.
MARKET_CONTEXT = [
    {"ticker": "SPY", "label": "Broad Market / SPY", "group": "Broad Market / SPY"},
    {"ticker": "QQQ", "label": "Nasdaq / Growth / QQQ", "group": "Nasdaq / Growth / QQQ"},
    {"ticker": "GLD", "label": "Gold / Safe Haven / GLD", "group": "Gold / Safe Haven / GLD"},
]

MARKET_CONTEXT_TICKERS = [m["ticker"] for m in MARKET_CONTEXT]

WATCHLIST = [
    {"ticker": "GOOGL", "name": "Alphabet Inc.", "category": "Internet"},
    {"ticker": "AMZN", "name": "Amazon.com Inc.", "category": "Cloud/Consumer"},
    {"ticker": "AVGO", "name": "Broadcom Inc.", "category": "Semiconductors"},
    {"ticker": "ASML", "name": "ASML Holding", "category": "Semi Equipment"},
    {"ticker": "UNH", "name": "UnitedHealth Group", "category": "Healthcare"},
    {"ticker": "BRK-B", "name": "Berkshire Hathaway", "category": "Diversified"},
]

WATCHLIST_TICKERS = [w["ticker"] for w in WATCHLIST]

# Optional prediction-market or prospective themes. Leave empty for public use.
POLYMARKET = []
PROSPECTIVE = []

BUYING_POWER = 500.00
TOTAL_PORTFOLIO_VALUE = 2085.00
TAX_CONTEXT = "Sample tax context only; consult a qualified tax professional."
SPECIAL_FLAGS = {
    "NVDA": "Sample concentration watch: high-growth semiconductor exposure can dominate portfolio risk.",
}

PORTFOLIO_POLICY = {
    "max_single_stock_weight": 0.25,
    "max_sector_weight": 0.45,
    "max_theme_weight": 0.55,
    "minimum_cash_allocation": 0.05,
    "target_benchmark": "SPY",
    "time_horizon": "5-10 years",
    "risk_profile": "Growth-oriented with diversification and drawdown guardrails",
}

FRAMEWORK_RULES = {
    "max_risk_contribution": 0.35,
    "max_factor_concentration": 0.45,
    "cash_buffer_min": 0.05,
    "bernstein_relative_band": 0.25,
    "bernstein_absolute_band": 0.05,
    "daryanani_band": 0.20,
    "concentration_exception_weight": 0.35,
    "concentration_exception_gain_pct": 100,
    "staged_exit": {
        "stage_1_weight": 0.25,
        "stage_2_weight": 0.35,
        "stage_3_risk_contribution": 0.40,
        "stage_4_weight": 0.45,
        "stage_4_gain_pct": 300,
        "drawdown_from_high": 0.20,
        "valuation_stretch_pct": 50,
        "factor_crowding_pct": 45,
    },
    "position_sizing": {
        "conservative_pct": 0.02,
        "balanced_pct": 0.04,
        "aggressive_pct": 0.06,
        "high_volatility_cap_pct": 0.03,
    },
}

FACTOR_PROXIES = {
    "market": "SPY",
    "semiconductor": "SMH",
    "growth": "QQQ",
    "momentum": "MTUM",
    "volatility": "^VIX",
    "rates": "TLT",
}

LIQUIDITY_POLICY = {
    "normal_participation_rate": 0.10,
    "stressed_participation_rate": 0.05,
    "same_day_max_days": 1,
    "ladder_max_days": 3,
}

CORRELATION_POLICY = {
    "elevated_avg_pairwise_corr": 0.60,
    "collapse_avg_pairwise_corr": 0.75,
    "cluster_corr_threshold": 0.70,
}

THEME_MAP = {p["ticker"]: p["theme"] for p in PORTFOLIO}

INVESTMENT_GOALS = [
    {
        "name": "Preserve capital",
        "target_amount": None,
        "time_horizon_years": 1,
        "priority": "High",
        "risk_tolerance": "Low",
    },
    {
        "name": "Beat inflation + SPY annual return",
        "goal_type": "benchmark_relative",
        "benchmark": "SPY",
        "inflation_series": "CPIAUCSL",
        "objective": "Portfolio return should exceed CPI inflation and outperform SPY for the same year.",
        "target_amount": None,
        "time_horizon_years": 1,
        "priority": "High",
        "risk_tolerance": "Medium",
    },
    {
        "name": "Future education capital",
        "target_amount": 50000,
        "time_horizon_years": 8,
        "priority": "Medium",
        "risk_tolerance": "Medium",
    },
    {
        "name": "Future business capital",
        "target_amount": 75000,
        "time_horizon_years": 10,
        "priority": "Medium",
        "risk_tolerance": "Medium",
    },
]

RISK_FREE_RATE = 0.0525
MARKET_RETURN = 0.10
AAA_BOND_YIELD = 0.052
CONFIDENCE_LEVEL = 0.95
LOOKBACK_DAYS = 252
MEAN_REV_WINDOW = 20

FRED_SERIES = {
    "unemployment": "UNRATE",
    "initial_claims": "ICSA",
    "copper": "PCOPPUSDM",
    "yield_10y": "DGS10",
    "yield_2y": "DGS2",
    "yield_3m": "DGS3MO",
    "consumer_sent": "UMCSENT",
    "retail_sales": "RSXFS",
    "cpi": "CPIAUCSL",
    "recession_prob": "RECPROUSM156N",
    "vix": "VIXCLS",
    "fed_funds": "FEDFUNDS",
}

HOST = "0.0.0.0"
PORT = 8080
