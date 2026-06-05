"""
Portfolio Intelligence — Analysis Engine
=========================================
Primary market prices: Polygon, then Tiingo, then yfinance.
Supplemental data: FRED, Finnhub, SEC API, FMP, NewsAPI.
Quant libraries are used when installed and degrade gracefully otherwise.
"""

import importlib
import json
import logging
import os
import re
import time
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from scipy import stats as sp_stats

import config

warnings.filterwarnings("ignore")
log = logging.getLogger("engine")


def optional_import(module_name: str):
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        log.warning("Optional library unavailable: %s (%s)", module_name, exc)
        return None


def safe_float(value, default=None):
    try:
        if value is None or value == "" or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def safe_round(value, digits=2, default=None):
    value = safe_float(value, default=None)
    if value is None or not np.isfinite(value):
        return default
    return round(value, digits)


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_json(v) for v in value]
    if isinstance(value, tuple):
        return [clean_json(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, float):
        return None if not np.isfinite(value) else value
    if pd.isna(value) if value is not None and not isinstance(value, (str, bool, list, dict)) else False:
        return None
    return value


def pct_change(current, base):
    current = safe_float(current)
    base = safe_float(base)
    if current is None or base in (None, 0):
        return None
    return round((current / base - 1) * 100, 2)


def has_key(key: str) -> bool:
    return bool(key and not key.startswith("YOUR_"))


def normalize_headline(headline: str) -> str:
    text = re.sub(r"\s+", " ", (headline or "").lower()).strip()
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    return text


def period_to_days(period: str) -> int:
    match = re.match(r"^(\d+)([dmy])$", period or "")
    if not match:
        return 252
    n = int(match.group(1))
    unit = match.group(2)
    return n if unit == "d" else n * 21 if unit == "m" else n * 252


def market_session(now: datetime | None = None) -> str:
    now = now or datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return "Closed"
    minutes = now.hour * 60 + now.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "Pre-market"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "Regular Market"
    if 16 * 60 <= minutes < 20 * 60:
        return "After Hours"
    return "Closed"


class YFinanceClient:
    """yfinance-first data adapter. No key required."""

    def __init__(self):
        self.yf = optional_import("yfinance")
        self._ticker_cache: dict[str, Any] = {}
        self._info_cache: dict[str, dict] = {}

    def available(self) -> bool:
        return self.yf is not None

    def ticker(self, symbol: str):
        if not self.available():
            return None
        if symbol not in self._ticker_cache:
            try:
                self._ticker_cache[symbol] = self.yf.Ticker(symbol)
            except Exception as exc:
                log.warning("yfinance ticker init failed for %s: %s", symbol, exc)
                return None
        return self._ticker_cache[symbol]

    def history(self, symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        try:
            t = self.ticker(symbol)
            if t is None:
                return pd.DataFrame()
            df = t.history(period=period, interval=interval, auto_adjust=False)
            if df is None or df.empty:
                return pd.DataFrame()
            df.index = pd.to_datetime(df.index).tz_localize(None)
            return df.sort_index()
        except Exception as exc:
            log.warning("yfinance history failed for %s: %s", symbol, exc)
            return pd.DataFrame()

    def prices(self, symbol: str, period: str = "1y") -> pd.Series:
        df = self.history(symbol, period=period)
        if df.empty:
            return pd.Series(dtype=float)
        col = "Adj Close" if "Adj Close" in df.columns else "Close"
        return df[col].dropna()

    def info(self, symbol: str) -> dict:
        if symbol in self._info_cache:
            return self._info_cache[symbol]
        try:
            t = self.ticker(symbol)
            info = dict(getattr(t, "info", {}) or {}) if t is not None else {}
            self._info_cache[symbol] = info
            return info
        except Exception as exc:
            log.warning("yfinance info failed for %s: %s", symbol, exc)
            return {}

    def quote(self, symbol: str) -> dict:
        info = self.info(symbol)
        prices = self.prices(symbol, period="5d")
        price = safe_float(info.get("regularMarketPrice") or info.get("currentPrice"))
        if price is None and not prices.empty:
            price = safe_float(prices.iloc[-1])
        prev = safe_float(info.get("previousClose"))
        if prev is None and len(prices) >= 2:
            prev = safe_float(prices.iloc[-2])
        change = price - prev if price is not None and prev is not None else None
        return {
            "price": safe_round(price),
            "prev_close": safe_round(prev),
            "change": safe_round(change),
            "change_pct": pct_change(price, prev),
            "source": "yfinance",
        }

    def fundamentals(self, symbol: str) -> dict:
        info = self.info(symbol)
        try:
            t = self.ticker(symbol)
            recs = getattr(t, "recommendations", None) if t is not None else None
            holders = getattr(t, "institutional_holders", None) if t is not None else None
            earnings_dates = getattr(t, "earnings_dates", None) if t is not None else None
        except Exception as exc:
            log.warning("yfinance secondary fundamentals failed for %s: %s", symbol, exc)
            recs, holders, earnings_dates = None, None, None
        return {
            "price": safe_round(info.get("currentPrice") or info.get("regularMarketPrice")),
            "pe": safe_round(info.get("trailingPE") or info.get("forwardPE")),
            "pb": safe_round(info.get("priceToBook")),
            "eps": safe_round(info.get("trailingEps") or info.get("forwardEps")),
            "dividend_yield": safe_round((info.get("dividendYield") or 0) * 100 if info.get("dividendYield") else None),
            "earnings_growth": safe_round((info.get("earningsGrowth") or 0) * 100 if info.get("earningsGrowth") else None),
            "revenue_growth": safe_round((info.get("revenueGrowth") or 0) * 100 if info.get("revenueGrowth") else None),
            "beta": safe_round(info.get("beta"), 3),
            "market_cap": safe_float(info.get("marketCap")),
            "week52_high": safe_round(info.get("fiftyTwoWeekHigh")),
            "week52_low": safe_round(info.get("fiftyTwoWeekLow")),
            "analyst_recommendation": info.get("recommendationKey"),
            "target_mean_price": safe_round(info.get("targetMeanPrice")),
            "recommendations": self._df_records(recs, 5),
            "institutional_holders": self._df_records(holders, 5),
            "earnings_dates": self._df_records(earnings_dates, 5),
            "source": "yfinance",
        }

    def options_summary(self, symbol: str) -> dict:
        try:
            t = self.ticker(symbol)
            expirations = list(getattr(t, "options", []) or []) if t is not None else []
            if not expirations:
                return {"expirations": [], "nearest": None}
            chain = t.option_chain(expirations[0])
            calls = chain.calls if hasattr(chain, "calls") else pd.DataFrame()
            puts = chain.puts if hasattr(chain, "puts") else pd.DataFrame()
            return {
                "expirations": expirations[:6],
                "nearest": expirations[0],
                "call_open_interest": int(calls.get("openInterest", pd.Series(dtype=float)).fillna(0).sum()) if not calls.empty else 0,
                "put_open_interest": int(puts.get("openInterest", pd.Series(dtype=float)).fillna(0).sum()) if not puts.empty else 0,
            }
        except Exception as exc:
            log.warning("yfinance options failed for %s: %s", symbol, exc)
            return {"error": str(exc)}

    @staticmethod
    def _df_records(df, limit=5) -> list:
        try:
            if df is None or len(df) == 0:
                return []
            out = df.reset_index().head(limit).copy()
            return clean_json(out.to_dict("records"))
        except Exception:
            return []


class PolygonClient:
    BASE = "https://api.polygon.io"

    def __init__(self):
        self.key = config.POLYGON_KEY
        self.disabled = False
        self._limit_logged = False
        self.cache_path = "eod_price_cache.json"
        self.cache = self._load_cache()
        self._invalidate_compressed_history_cache()
        self.scan_history_cache: dict[tuple[str, int], pd.DataFrame] = {}
        self.scan_ticker_history: dict[str, pd.DataFrame] = {}
        self.scan_quote_cache: dict[str, dict] = {}
        self.rate_limited_tickers: set[str] = set()
        self.failure_reasons: dict[str, str] = {}
        self.last_successful_at: dict[str, str] = {}
        self.request_times: list[float] = []
        self.max_requests_per_minute = 4

    def start_scan(self):
        self.scan_history_cache = {}
        self.scan_ticker_history = {}
        self.scan_quote_cache = {}
        self.rate_limited_tickers = set()
        self.failure_reasons = {}
        self._limit_logged = False

    def _load_cache(self) -> dict:
        if not os.path.exists(self.cache_path):
            return {}
        try:
            with open(self.cache_path, "r") as f:
                return json.load(f)
        except Exception as exc:
            log.warning("Polygon EOD cache read failed: %s", exc)
            return {}

    def _save_cache(self):
        try:
            with open(self.cache_path, "w") as f:
                json.dump(self.cache, f, default=str)
        except Exception as exc:
            log.warning("Polygon EOD cache write failed: %s", exc)

    def _invalidate_compressed_history_cache(self):
        watch = set(getattr(config, "TICKERS", []))
        watch.update(getattr(config, "MARKET_CONTEXT_TICKERS", []))
        watch.update(getattr(config, "FACTOR_PROXIES", {}).values())
        changed = False
        for ticker in list(watch):
            entry = self.cache.get(ticker)
            if not isinstance(entry, dict):
                continue
            history = entry.get("history") or []
            if 0 < len(history) < 60:
                log.info("Invalidating compressed Polygon cache for %s: %s rows", ticker, len(history))
                self.cache[ticker] = {
                    "quote": entry.get("quote") or {},
                    "history": [],
                    "latest_trading_date": entry.get("latest_trading_date"),
                    "last_successful_polygon_timestamp": entry.get("last_successful_polygon_timestamp"),
                    "compressed_history_invalidated": True,
                }
                changed = True
        if changed:
            self._save_cache()

    def _expected_latest_trading_day(self) -> str:
        now = datetime.now(ZoneInfo("America/New_York"))
        session = market_session(now)
        candidate = now.date()
        if now.weekday() >= 5:
            candidate = candidate - timedelta(days=now.weekday() - 4)
        elif session in {"Pre-market", "Regular Market"}:
            candidate = candidate - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate.strftime("%Y-%m-%d")

    def _cache_entry(self, ticker: str) -> dict:
        entry = self.cache.get(ticker) or {}
        if "history" not in entry and entry.get("price") is not None:
            entry = {"quote": entry, "history": [], "latest_trading_date": entry.get("latest_trading_date")}
        return entry if isinstance(entry, dict) else {}

    def _history_from_cache(self, ticker: str, days: int, require_latest: bool = False) -> pd.DataFrame:
        entry = self._cache_entry(ticker)
        if require_latest and entry.get("latest_trading_date") != self._expected_latest_trading_day():
            return pd.DataFrame()
        rows = entry.get("history") or []
        if not rows:
            return pd.DataFrame()
        try:
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")[["Open", "High", "Low", "Close", "Volume"]].sort_index().tail(days)
            df.attrs["polygon_cache_hit"] = True
            return df
        except Exception as exc:
            log.warning("Polygon cache parse failed for %s: %s", ticker, exc)
            return pd.DataFrame()

    def _cache_quote(self, ticker: str, quote: dict):
        entry = self._cache_entry(ticker)
        self.cache[ticker] = {
            "quote": quote,
            "history": entry.get("history") or [],
            "latest_trading_date": quote.get("latest_trading_date") or entry.get("latest_trading_date"),
            "last_successful_polygon_timestamp": self.last_successful_at.get(ticker) or entry.get("last_successful_polygon_timestamp"),
        }
        self._save_cache()

    def _cache_history(self, ticker: str, df: pd.DataFrame, quote: dict | None = None):
        if df.empty:
            return
        records = []
        for date, row in df.tail(520).iterrows():
            records.append({
                "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                "Open": safe_float(row.get("Open")),
                "High": safe_float(row.get("High")),
                "Low": safe_float(row.get("Low")),
                "Close": safe_float(row.get("Close")),
                "Volume": safe_float(row.get("Volume")),
            })
        latest_date = pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d")
        existing = self._cache_entry(ticker)
        existing_history = existing.get("history") or []
        if len(existing_history) > len(records):
            self.cache[ticker] = {
                "quote": quote or (existing.get("quote") or {}),
                "history": existing_history,
                "latest_trading_date": existing.get("latest_trading_date") or latest_date,
                "last_successful_polygon_timestamp": self.last_successful_at.get(ticker) or existing.get("last_successful_polygon_timestamp"),
            }
            self._save_cache()
            return
        self.cache[ticker] = {
            "quote": quote or (self._cache_entry(ticker).get("quote") or {}),
            "history": records,
            "latest_trading_date": latest_date,
            "last_successful_polygon_timestamp": self.last_successful_at.get(ticker),
        }
        self._save_cache()

    def _throttle(self):
        now = time.monotonic()
        self.request_times = [t for t in self.request_times if now - t < 60]
        if len(self.request_times) >= self.max_requests_per_minute:
            wait = 60 - (now - self.request_times[0])
            if wait > 0:
                log.info("Polygon throttle sleeping %.1fs to stay under free-tier pacing", wait)
                time.sleep(wait)
        self.request_times.append(time.monotonic())

    def _get(self, path: str, params: dict | None = None, ticker: str | None = None):
        if self.disabled or not has_key(self.key):
            return None
        if ticker and ticker in self.rate_limited_tickers:
            self.failure_reasons[ticker] = "rate_limited"
            return None
        params = dict(params or {})
        params["apiKey"] = self.key
        for attempt in range(2):
            try:
                self._throttle()
                r = requests.get(f"{self.BASE}{path}", params=params, timeout=15)
                if r.status_code == 429:
                    if attempt == 0:
                        log.warning("Polygon 429 for %s; retrying once after 12s", ticker or path)
                        time.sleep(12)
                        continue
                    if ticker:
                        self.rate_limited_tickers.add(ticker)
                        self.failure_reasons[ticker] = "rate_limited"
                    if not self._limit_logged:
                        log.warning("Polygon rate limit persisted; falling back ticker-by-ticker for this scan")
                        self._limit_logged = True
                    return None
                if r.status_code in {401, 403}:
                    self.disabled = True
                    if ticker:
                        self.failure_reasons[ticker] = f"http_{r.status_code}"
                    log.warning("Polygon disabled after HTTP %s; falling back to Tiingo/yfinance", r.status_code)
                    return None
                r.raise_for_status()
                if ticker:
                    self.failure_reasons.pop(ticker, None)
                    self.last_successful_at[ticker] = datetime.now(timezone.utc).isoformat()
                return r.json()
            except Exception as exc:
                if ticker:
                    self.failure_reasons[ticker] = "request_failed"
                log.warning("Polygon request failed for %s: %s", ticker or path, exc)
                return None
        return None

    def history(self, ticker: str, days: int = 252, prefer_cache: bool = False) -> pd.DataFrame:
        memo_key = (ticker, days)
        if memo_key in self.scan_history_cache:
            return self.scan_history_cache[memo_key].copy()
        if ticker in self.scan_ticker_history:
            df = self.scan_ticker_history[ticker].tail(days).copy()
            if len(df) >= min(days, 60) or days < 60:
                self.scan_history_cache[memo_key] = df
                return df.copy()
        cached = self._history_from_cache(ticker, days, require_latest=prefer_cache)
        if prefer_cache and not cached.empty and (len(cached) >= min(days, 60) or days < 60):
            self.scan_history_cache[memo_key] = cached
            self.scan_ticker_history[ticker] = cached
            return cached.copy()
        if ticker in self.rate_limited_tickers:
            fallback = self._history_from_cache(ticker, days)
            if not fallback.empty and (len(fallback) >= min(days, 60) or days < 60):
                self.scan_history_cache[memo_key] = fallback
                self.scan_ticker_history[ticker] = fallback
                return fallback.copy()
            return pd.DataFrame()
        to = datetime.utcnow().date()
        request_days = max(days, 520)
        frm = to - timedelta(days=max(request_days + 14, 20))
        data = self._get(f"/v2/aggs/ticker/{ticker}/range/1/day/{frm}/{to}", {
            "adjusted": "true",
            "sort": "asc",
            "limit": 5000,
        }, ticker=ticker)
        rows = (data or {}).get("results", [])
        if not rows:
            fallback = self._history_from_cache(ticker, days)
            if not fallback.empty and (len(fallback) >= min(days, 60) or days < 60):
                self.scan_history_cache[memo_key] = fallback
                self.scan_ticker_history[ticker] = fallback
                return fallback.copy()
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["t"], unit="ms")
        df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
        df = df.set_index("date")[["Open", "High", "Low", "Close", "Volume"]].sort_index()
        full_df = df.tail(request_days)
        out = full_df.tail(days).copy()
        self.scan_ticker_history[ticker] = full_df
        self.scan_history_cache[memo_key] = out
        self._cache_history(ticker, full_df)
        return out.copy()

    def quote(self, ticker: str, prefer_cache: bool = False) -> dict:
        if ticker in self.scan_quote_cache:
            return dict(self.scan_quote_cache[ticker])
        if prefer_cache:
            entry = self._cache_entry(ticker)
            cached_quote = dict(entry.get("quote") or {})
            if cached_quote and entry.get("latest_trading_date") == self._expected_latest_trading_day():
                cached_quote["cache_hit"] = True
                self.scan_quote_cache[ticker] = cached_quote
                return dict(cached_quote)
        df = self.history(ticker, days=10, prefer_cache=prefer_cache)
        if df.empty:
            entry = self._cache_entry(ticker)
            cached = dict(entry.get("quote") or {})
            if cached:
                cached["last_successful_polygon_timestamp"] = entry.get("last_successful_polygon_timestamp")
                return {**cached, "cache_hit": True}
            return {}
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else None
        session = market_session()
        quote = {
            "price": safe_round(latest["Close"]),
            "official_close": safe_round(latest["Close"]),
            "prev_close": safe_round(prev["Close"]) if prev is not None else None,
            "open": safe_round(latest["Open"]),
            "high": safe_round(latest["High"]),
            "low": safe_round(latest["Low"]),
            "volume": safe_float(latest["Volume"]),
            "latest_trading_date": df.index[-1].strftime("%Y-%m-%d"),
            "change": safe_round(latest["Close"] - prev["Close"]) if prev is not None else None,
            "change_pct": pct_change(latest["Close"], prev["Close"]) if prev is not None else None,
            "source": "Polygon Official",
            "market_session": session,
            "last_successful_polygon_timestamp": self.last_successful_at.get(ticker) or self._cache_entry(ticker).get("last_successful_polygon_timestamp"),
            "cache_hit": bool(df.attrs.get("polygon_cache_hit")),
        }
        self.scan_quote_cache[ticker] = quote
        if session in {"After Hours", "Closed"} or not df.attrs.get("polygon_cache_hit"):
            if len(df) >= 60:
                self._cache_history(ticker, df, quote=quote)
            else:
                self._cache_quote(ticker, quote)
        return dict(quote)


class FredClient:
    BASE = "https://api.stlouisfed.org/fred/series/observations"

    def get_series(self, series_id: str, limit: int = 12) -> list:
        if not has_key(config.FRED_KEY):
            return []
        try:
            r = requests.get(
                self.BASE,
                params={
                    "series_id": series_id,
                    "api_key": config.FRED_KEY,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": limit,
                },
                timeout=15,
            )
            r.raise_for_status()
            rows = []
            for obs in r.json().get("observations", []):
                val = safe_float(obs.get("value"))
                if val is not None:
                    rows.append({"date": obs.get("date"), "value": val})
            return rows
        except Exception as exc:
            log.warning("FRED failed for %s: %s", series_id, exc)
            return []

    def get_macro_dashboard(self) -> dict:
        result = {}
        for name, series_id in config.FRED_SERIES.items():
            data = self.get_series(series_id, limit=8)
            if not data:
                result[name] = {"series": series_id, "latest": None, "date": None, "trend": "unavailable"}
                continue
            latest = data[0]["value"]
            prev = data[1]["value"] if len(data) > 1 else None
            result[name] = {
                "series": series_id,
                "latest": latest,
                "date": data[0]["date"],
                "prev": prev,
                "change": safe_round(latest - prev) if prev is not None else None,
                "trend": "up" if prev is not None and latest > prev else "down" if prev is not None and latest < prev else "flat",
            }
        return result


class FinnhubClient:
    BASE = "https://finnhub.io/api/v1"

    def __init__(self):
        self.key = config.FINNHUB_KEY
        self.disabled_endpoints: set[str] = set()

    def _get(self, endpoint: str, params: dict | None = None) -> Any:
        if not has_key(self.key):
            return [] if endpoint in {"company-news", "stock/recommendation"} else {}
        if endpoint in self.disabled_endpoints:
            return [] if endpoint in {"company-news", "stock/recommendation", "stock/insider-transactions"} else {}
        params = dict(params or {})
        params["token"] = self.key
        try:
            r = requests.get(f"{self.BASE}/{endpoint}", params=params, timeout=15)
            if r.status_code in {401, 403, 429}:
                self.disabled_endpoints.add(endpoint)
                log.warning("Finnhub %s disabled after HTTP %s; skipping subsequent calls", endpoint, r.status_code)
                return [] if endpoint in {"company-news", "stock/recommendation", "stock/insider-transactions"} else {}
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            log.warning("Finnhub %s failed: %s", endpoint, exc)
            return [] if endpoint in {"company-news", "stock/recommendation"} else {}

    def quote(self, ticker: str) -> dict:
        data = self._get("quote", {"symbol": ticker}) or {}
        return {
            "price": safe_round(data.get("c")),
            "open": safe_round(data.get("o")),
            "high": safe_round(data.get("h")),
            "low": safe_round(data.get("l")),
            "prev_close": safe_round(data.get("pc")),
            "change": safe_round(data.get("d")),
            "change_pct": safe_round(data.get("dp")),
            "source": "Finnhub",
        }

    def recommendations(self, ticker: str) -> list:
        data = self._get("stock/recommendation", {"symbol": ticker})
        return data if isinstance(data, list) else []

    def company_news(self, ticker: str, days_back: int = 7) -> list:
        to = datetime.utcnow().strftime("%Y-%m-%d")
        frm = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        data = self._get("company-news", {"symbol": ticker, "from": frm, "to": to})
        return data if isinstance(data, list) else []

    def insider_transactions(self, ticker: str) -> list:
        data = self._get("stock/insider-transactions", {"symbol": ticker})
        return data.get("data", []) if isinstance(data, dict) else []

    def earnings_calendar(self, ticker: str) -> dict:
        frm = datetime.utcnow().strftime("%Y-%m-%d")
        to = (datetime.utcnow() + timedelta(days=90)).strftime("%Y-%m-%d")
        data = self._get("calendar/earnings", {"symbol": ticker, "from": frm, "to": to})
        return data if isinstance(data, dict) else {}


class TiingoClient:
    def __init__(self):
        self.headers = {"Authorization": f"Token {config.TIINGO_KEY}", "Content-Type": "application/json"}

    def history(self, ticker: str, days: int = 252) -> pd.DataFrame:
        if not has_key(config.TIINGO_KEY):
            return pd.DataFrame()
        start = (datetime.utcnow() - timedelta(days=days + 20)).strftime("%Y-%m-%d")
        try:
            r = requests.get(
                f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
                headers=self.headers,
                params={"startDate": start, "resampleFreq": "daily"},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert(None)
            df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
            return df.set_index("date")[["Open", "High", "Low", "Close", "Volume"]].sort_index().tail(days)
        except Exception as exc:
            log.warning("Tiingo prices failed for %s: %s", ticker, exc)
            return pd.DataFrame()

    def quote(self, ticker: str) -> dict:
        df = self.history(ticker, days=10)
        if df.empty:
            return {}
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else None
        return {
            "price": safe_round(latest["Close"]),
            "official_close": safe_round(latest["Close"]),
            "prev_close": safe_round(prev["Close"]) if prev is not None else None,
            "open": safe_round(latest["Open"]),
            "high": safe_round(latest["High"]),
            "low": safe_round(latest["Low"]),
            "volume": safe_float(latest["Volume"]),
            "latest_trading_date": df.index[-1].strftime("%Y-%m-%d"),
            "change": safe_round(latest["Close"] - prev["Close"]) if prev is not None else None,
            "change_pct": pct_change(latest["Close"], prev["Close"]) if prev is not None else None,
            "source": "Tiingo",
            "market_session": market_session(),
        }

    def news(self, tickers: list[str], limit: int = 30) -> list:
        if not has_key(config.TIINGO_KEY):
            return []
        try:
            r = requests.get(
                "https://api.tiingo.com/tiingo/news",
                headers=self.headers,
                params={"tickers": ",".join(tickers), "limit": limit, "sortBy": "publishedDate"},
                timeout=15,
            )
            r.raise_for_status()
            return r.json() if isinstance(r.json(), list) else []
        except Exception as exc:
            log.warning("Tiingo news failed: %s", exc)
            return []


class MarketDataClient:
    def __init__(self, polygon: PolygonClient, tiingo: TiingoClient, yf: YFinanceClient):
        self.polygon = polygon
        self.tiingo = tiingo
        self.yf = yf
        self.audit: dict[str, dict] = {}

    def start_scan(self, priority_tickers: list[str] | None = None):
        self.audit = {}
        self.polygon.start_scan()
        for ticker in priority_tickers or []:
            self.audit.setdefault(ticker, {"ticker": ticker, "priority": "portfolio"})

    def _record_audit(self, ticker: str, source: str, fallback_reason: str = "", polygon_quote: dict | None = None, cache_hit: bool = False):
        row = self.audit.setdefault(ticker, {"ticker": ticker})
        reason = self.polygon.failure_reasons.get(ticker)
        row.update({
            "source_used": source,
            "fallback_reason": fallback_reason,
            "polygon_failure_reason": reason,
            "last_successful_polygon_timestamp": self.polygon.last_successful_at.get(ticker) or (polygon_quote or {}).get("last_successful_polygon_timestamp") or self.polygon._cache_entry(ticker).get("last_successful_polygon_timestamp"),
            "cache_hit": bool(cache_hit or (polygon_quote or {}).get("cache_hit")),
            "latest_trading_date": (polygon_quote or {}).get("latest_trading_date") or self.polygon._cache_entry(ticker).get("latest_trading_date"),
        })

    def history(self, ticker: str, period: str = "1y", days: int | None = None, prefer_cache: bool = False) -> pd.DataFrame:
        lookback = days or period_to_days(period)
        df = self.polygon.history(ticker, lookback, prefer_cache=prefer_cache)
        if not df.empty:
            df.attrs["source"] = "Polygon Official"
            self._record_audit(ticker, "Polygon Official", cache_hit=bool(df.attrs.get("polygon_cache_hit")))
            return df
        polygon_reason = self.polygon.failure_reasons.get(ticker) or "polygon_unavailable"
        df = self.tiingo.history(ticker, lookback)
        if not df.empty:
            df.attrs["source"] = "Tiingo"
            self._record_audit(ticker, "Tiingo", fallback_reason=polygon_reason)
            return df
        df = self.yf.history(ticker, period=period)
        if not df.empty:
            colmap = {"Adj Close": "Close"} if "Close" not in df.columns and "Adj Close" in df.columns else {}
            df = df.rename(columns=colmap)
            keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
            df = df[keep].copy()
            df.attrs["source"] = "Yahoo Delayed"
            self._record_audit(ticker, "Yahoo Delayed", fallback_reason=polygon_reason)
            return df
        self._record_audit(ticker, "Unavailable", fallback_reason=polygon_reason)
        return pd.DataFrame()

    def prices(self, ticker: str, period: str = "1y", prefer_cache: bool = False) -> pd.Series:
        df = self.history(ticker, period=period, prefer_cache=prefer_cache)
        if df.empty or "Close" not in df.columns:
            return pd.Series(dtype=float)
        s = df["Close"].dropna()
        s.attrs["source"] = df.attrs.get("source")
        return s

    def quote(self, ticker: str, prefer_cache: bool = False) -> dict:
        quote = self.polygon.quote(ticker, prefer_cache=prefer_cache)
        if quote:
            self._record_audit(ticker, "Polygon Official", polygon_quote=quote, cache_hit=quote.get("cache_hit"))
            return quote
        polygon_reason = self.polygon.failure_reasons.get(ticker) or "polygon_unavailable"
        quote = self.tiingo.quote(ticker)
        if quote:
            self._record_audit(ticker, "Tiingo", fallback_reason=polygon_reason)
            return quote
        quote = self.yf.quote(ticker)
        if quote:
            quote["source"] = "Yahoo Delayed"
            quote["market_session"] = market_session()
            self._record_audit(ticker, "Yahoo Delayed", fallback_reason=polygon_reason)
            return quote
        self._record_audit(ticker, "Unavailable", fallback_reason=polygon_reason)
        return quote

    def price_audit_rows(self, tickers: list[str] | None = None) -> list[dict]:
        ordered = []
        seen = set()
        for ticker in tickers or []:
            seen.add(ticker)
            ordered.append(self.audit.get(ticker, {"ticker": ticker, "source_used": "Not requested"}))
        for ticker, row in self.audit.items():
            if ticker not in seen:
                ordered.append(row)
        return clean_json(ordered)


class SecClient:
    BASE = "https://api.sec-api.io"

    def search_form4(self, ticker: str, limit: int = 5) -> list:
        if not has_key(config.SEC_API_KEY):
            return []
        try:
            r = requests.post(
                self.BASE,
                headers={"Authorization": config.SEC_API_KEY, "Content-Type": "application/json"},
                json={
                    "query": {"query_string": {"query": f'ticker:"{ticker}" AND formType:"4"'}},
                    "from": "0",
                    "size": str(limit),
                    "sort": [{"filedAt": {"order": "desc"}}],
                },
                timeout=15,
            )
            r.raise_for_status()
            return r.json().get("filings", [])
        except Exception as exc:
            log.warning("SEC API Form 4 search failed for %s: %s", ticker, exc)
            return []

    def edgar_form4(self, ticker: str, cik: str | int | None = None, limit: int = 5) -> list:
        cik_value = safe_float(cik)
        if cik_value is None:
            return []
        cik_str = str(int(cik_value)).zfill(10)
        try:
            r = requests.get(
                f"https://data.sec.gov/submissions/CIK{cik_str}.json",
                headers={"User-Agent": "portfolio-intel/1.0 public-release"},
                timeout=15,
            )
            r.raise_for_status()
            recent = r.json().get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            accession = recent.get("accessionNumber", [])
            filed = recent.get("filingDate", [])
            primary = recent.get("primaryDocument", [])
            rows = []
            for i, form in enumerate(forms):
                if form != "4":
                    continue
                acc = accession[i].replace("-", "") if i < len(accession) else ""
                doc = primary[i] if i < len(primary) else ""
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_value)}/{acc}/{doc}" if acc and doc else ""
                rows.append({
                    "ticker": ticker,
                    "filed_at": filed[i] if i < len(filed) else None,
                    "form_type": "4",
                    "company": ticker,
                    "url": url,
                    "source": "SEC EDGAR public submissions",
                })
                if len(rows) >= limit:
                    break
            return rows
        except Exception as exc:
            log.warning("SEC EDGAR public submissions failed for %s: %s", ticker, exc)
            return []


class FmpClient:
    BASE = "https://financialmodelingprep.com/api/v3"

    def _get(self, path: str, params: dict | None = None):
        if not has_key(config.FMP_KEY):
            return []
        try:
            params = dict(params or {})
            params["apikey"] = config.FMP_KEY
            r = requests.get(f"{self.BASE}/{path}", params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            log.warning("FMP %s failed: %s", path, exc)
            return []

    def fundamentals_backup(self, ticker: str) -> dict:
        income = self._get(f"income-statement/{ticker}", {"limit": 1})
        ratios = self._get(f"ratios-ttm/{ticker}")
        dcf = self._get(f"discounted-cash-flow/{ticker}")
        return {
            "income_statement": income[0] if isinstance(income, list) and income else {},
            "ratios": ratios[0] if isinstance(ratios, list) and ratios else {},
            "dcf": dcf[0] if isinstance(dcf, list) and dcf else {},
        }


class NewsApiClient:
    BASE = "https://newsapi.org/v2/everything"

    def search(self, query: str, page_size: int = 25) -> list:
        if not has_key(config.NEWSAPI_KEY):
            return []
        try:
            r = requests.get(
                self.BASE,
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": page_size,
                    "apiKey": config.NEWSAPI_KEY,
                },
                timeout=15,
            )
            r.raise_for_status()
            return r.json().get("articles", [])
        except Exception as exc:
            log.warning("NewsAPI search failed for %s: %s", query, exc)
            return []


class QuantEngine:
    def __init__(self):
        self.rf = config.RISK_FREE_RATE
        self.rm = config.MARKET_RETURN
        self.confidence = config.CONFIDENCE_LEVEL

    def graham_value(self, eps, growth_rate):
        eps = safe_float(eps)
        growth = safe_float(growth_rate)
        y = config.AAA_BOND_YIELD * 100
        if eps is None or eps <= 0 or growth is None or y <= 0:
            return None
        growth = min(max(growth, 0), 30)
        return eps * (8.5 + 2 * growth) * 4.4 / y

    def capm_expected_return(self, beta):
        beta = safe_float(beta)
        return None if beta is None else self.rf + beta * (self.rm - self.rf)

    def discrete_kelly(self, returns: pd.Series):
        wins = returns[returns > 0]
        losses = returns[returns <= 0]
        if len(wins) == 0 or len(losses) == 0:
            return None
        p = len(wins) / len(returns)
        q = 1 - p
        b = wins.mean() / abs(losses.mean()) if losses.mean() != 0 else None
        if not b:
            return None
        return (b * p - q) / b

    def continuous_kelly(self, returns: pd.Series):
        var = returns.var()
        return returns.mean() / var if var and var > 0 else None

    def parametric_var(self, returns: pd.Series, horizon=5):
        if len(returns) < 30:
            return None
        z = sp_stats.norm.ppf(1 - self.confidence)
        return -(returns.mean() * horizon + z * returns.std() * np.sqrt(horizon))

    def historical_var(self, returns: pd.Series, horizon=5):
        if len(returns) < 30:
            return None
        rolling = returns.rolling(horizon).sum().dropna()
        return -np.percentile(rolling, (1 - self.confidence) * 100) if len(rolling) else None

    def historical_cvar(self, returns: pd.Series, horizon=5):
        if len(returns) < 30:
            return None
        rolling = returns.rolling(horizon).sum().dropna()
        threshold = np.percentile(rolling, (1 - self.confidence) * 100)
        tail = rolling[rolling <= threshold]
        return -tail.mean() if len(tail) else None

    def half_life(self, prices: pd.Series):
        if len(prices) < 30:
            return None
        try:
            lp = np.log(prices.dropna())
            delta = lp.diff()
            lagged = lp.shift(1)
            aligned = pd.concat([delta, lagged], axis=1).dropna()
            if len(aligned) < 20:
                return None
            slope, _, _, _, _ = sp_stats.linregress(aligned.iloc[:, 1], aligned.iloc[:, 0])
            return -np.log(2) / slope if slope < 0 else None
        except Exception as exc:
            log.warning("OU half-life failed: %s", exc)
            return None

    def technicals(self, prices: pd.Series) -> dict:
        if len(prices) < 60:
            return {"signal": "insufficient_data"}
        close = prices.dropna().rename("close")
        df = pd.DataFrame({"close": close})
        ta = optional_import("pandas_ta")
        try:
            if ta is not None:
                df["rsi"] = ta.rsi(df["close"], length=14)
                macd = ta.macd(df["close"])
                bb = ta.bbands(df["close"], length=20, std=2)
                df["sma20"] = ta.sma(df["close"], length=20)
                df["sma50"] = ta.sma(df["close"], length=50)
                df["ema12"] = ta.ema(df["close"], length=12)
                if macd is not None:
                    df["macd"] = macd.iloc[:, 0]
                    df["macd_signal"] = macd.iloc[:, 1]
                if bb is not None:
                    df["bb_lower"] = bb.iloc[:, 0]
                    df["bb_mid"] = bb.iloc[:, 1]
                    df["bb_upper"] = bb.iloc[:, 2]
            else:
                delta = df["close"].diff()
                gain = delta.clip(lower=0).rolling(14).mean()
                loss = -delta.clip(upper=0).rolling(14).mean()
                rs = gain / loss.replace(0, np.nan)
                df["rsi"] = 100 - (100 / (1 + rs))
                ema12 = df["close"].ewm(span=12, adjust=False).mean()
                ema26 = df["close"].ewm(span=26, adjust=False).mean()
                df["macd"] = ema12 - ema26
                df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
                df["sma20"] = df["close"].rolling(20).mean()
                df["sma50"] = df["close"].rolling(50).mean()
                df["ema12"] = ema12
                std = df["close"].rolling(20).std()
                df["bb_mid"] = df["sma20"]
                df["bb_upper"] = df["sma20"] + 2 * std
                df["bb_lower"] = df["sma20"] - 2 * std
            last = df.iloc[-1]
            z = (last["close"] - df["close"].rolling(20).mean().iloc[-1]) / df["close"].rolling(20).std().iloc[-1]
            signal = "neutral"
            if safe_float(last.get("rsi")) is not None and last["rsi"] < 30 and last["close"] <= last.get("bb_lower", np.nan):
                signal = "oversold_entry_candidate"
            elif safe_float(last.get("rsi")) is not None and last["rsi"] > 70 and last["close"] >= last.get("bb_upper", np.nan):
                signal = "overbought_trim_candidate"
            elif z <= -1.5:
                signal = "mean_reversion_watch"
            elif z >= 1.5:
                signal = "extended"
            return clean_json({
                "rsi14": safe_round(last.get("rsi"), 2),
                "macd": safe_round(last.get("macd"), 3),
                "macd_signal": safe_round(last.get("macd_signal"), 3),
                "bb_lower": safe_round(last.get("bb_lower"), 2),
                "bb_mid": safe_round(last.get("bb_mid"), 2),
                "bb_upper": safe_round(last.get("bb_upper"), 2),
                "sma20": safe_round(last.get("sma20"), 2),
                "sma50": safe_round(last.get("sma50"), 2),
                "ema12": safe_round(last.get("ema12"), 2),
                "zscore": safe_round(z, 3),
                "half_life_days": safe_round(self.half_life(close), 1),
                "signal": signal,
            })
        except Exception as exc:
            log.warning("Technical indicator calculation failed: %s", exc)
            return {"signal": "indicator_error", "error": str(exc)}

    def auto_arima_forecast(self, prices: pd.Series, steps=5) -> dict:
        if len(prices) < 80:
            return {"status": "insufficient_data"}
        pm = optional_import("pmdarima")
        if pm is None:
            return {"status": "pmdarima_unavailable"}
        try:
            model = pm.auto_arima(prices.dropna(), seasonal=False, stepwise=True, suppress_warnings=True, error_action="ignore")
            forecast = model.predict(n_periods=steps)
            current = prices.iloc[-1]
            final = forecast[-1]
            return {
                "order": list(model.order),
                "forecast": [safe_round(v, 2) for v in forecast],
                "current": safe_round(current, 2),
                "direction": "up" if final > current else "down",
                "change_pct": pct_change(final, current),
                "aic": safe_round(model.aic(), 2),
            }
        except Exception as exc:
            log.warning("auto_arima failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def garch_forecast(self, returns: pd.Series) -> dict:
        if len(returns) < 80:
            return {"status": "insufficient_data"}
        arch = optional_import("arch")
        if arch is None:
            return {"status": "arch_unavailable"}
        try:
            model = arch.arch_model(returns.dropna() * 100, vol="Garch", p=1, q=1, rescale=False)
            fitted = model.fit(disp="off")
            forecast = fitted.forecast(horizon=5)
            daily_vol = np.sqrt(forecast.variance.iloc[-1].mean()) / 100
            hist_vol = returns.std()
            return {
                "garch_annual_volatility": safe_round(daily_vol * np.sqrt(252) * 100, 2),
                "historical_annual_volatility": safe_round(hist_vol * np.sqrt(252) * 100, 2),
                "volatility_gap_pct": safe_round((daily_vol - hist_vol) * np.sqrt(252) * 100, 2),
                "aic": safe_round(fitted.aic, 2),
            }
        except Exception as exc:
            log.warning("GARCH failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def empyrical_metrics(self, returns: pd.Series, market_returns: pd.Series) -> dict:
        emp = optional_import("empyrical")
        aligned = pd.concat([returns, market_returns], axis=1).dropna()
        if len(aligned) < 30:
            return {}
        r = aligned.iloc[:, 0]
        m = aligned.iloc[:, 1]
        if emp is not None:
            try:
                return clean_json({
                    "alpha": safe_round(emp.alpha(r, m, risk_free=self.rf / 252), 4),
                    "beta_empyrical": safe_round(emp.beta(r, m), 3),
                    "annual_return": safe_round(emp.annual_return(r) * 100, 2),
                    "annual_volatility": safe_round(emp.annual_volatility(r) * 100, 2),
                    "sharpe": safe_round(emp.sharpe_ratio(r, risk_free=self.rf / 252), 3),
                    "sortino": safe_round(emp.sortino_ratio(r, required_return=self.rf / 252), 3),
                    "calmar": safe_round(emp.calmar_ratio(r), 3),
                    "max_drawdown": safe_round(emp.max_drawdown(r) * 100, 2),
                    "omega": safe_round(emp.omega_ratio(r, risk_free=self.rf / 252), 3),
                })
            except Exception as exc:
                log.warning("empyrical metrics failed: %s", exc)
        beta = np.cov(r, m)[0, 1] / np.var(m) if np.var(m) else None
        ann_ret = r.mean() * 252
        ann_vol = r.std() * np.sqrt(252)
        cum = (1 + r).cumprod()
        max_dd = ((cum / cum.cummax()) - 1).min()
        return {
            "alpha": safe_round(ann_ret - (self.rf + (beta or 1) * (m.mean() * 252 - self.rf)), 4),
            "beta_empyrical": safe_round(beta, 3),
            "annual_return": safe_round(ann_ret * 100, 2),
            "annual_volatility": safe_round(ann_vol * 100, 2),
            "sharpe": safe_round((ann_ret - self.rf) / ann_vol, 3) if ann_vol else None,
            "sortino": None,
            "calmar": safe_round(ann_ret / abs(max_dd), 3) if max_dd else None,
            "max_drawdown": safe_round(max_dd * 100, 2),
            "omega": None,
        }

    def quantstats_metrics(self, returns: pd.Series) -> dict:
        qs = optional_import("quantstats")
        if qs is None or len(returns) < 30:
            return {}
        try:
            return {
                "sharpe_qs": safe_round(qs.stats.sharpe(returns), 3),
                "sortino_qs": safe_round(qs.stats.sortino(returns), 3),
                "max_drawdown_qs": safe_round(qs.stats.max_drawdown(returns) * 100, 2),
                "calmar_qs": safe_round(qs.stats.calmar(returns), 3),
                "metrics_table_available": True,
            }
        except Exception as exc:
            log.warning("quantstats metrics failed: %s", exc)
            return {"metrics_table_available": False, "error": str(exc)}

    def ffn_stats(self, prices: pd.Series) -> dict:
        ffn = optional_import("ffn")
        if ffn is None or len(prices) < 30:
            return {}
        try:
            stats = prices.to_frame("asset").calc_stats()["asset"]
            return {
                "ffn_total_return": safe_round(stats.total_return * 100, 2),
                "ffn_cagr": safe_round(stats.cagr * 100, 2),
                "ffn_max_drawdown": safe_round(stats.max_drawdown * 100, 2),
                "ffn_daily_sharpe": safe_round(stats.daily_sharpe, 3),
            }
        except Exception as exc:
            log.warning("ffn stats failed: %s", exc)
            return {}

    def riskfolio_analysis(self, returns_df: pd.DataFrame) -> dict:
        if returns_df.empty or returns_df.shape[1] < 2:
            return {}
        rp = optional_import("riskfolio")
        if rp is None:
            return {"status": "riskfolio_unavailable"}
        try:
            port = rp.Portfolio(returns=returns_df)
            port.assets_stats(method_mu="hist", method_cov="hist")
            cvar_weights = port.optimization(model="Classic", rm="CVaR", obj="MinRisk", rf=self.rf / 252, l=0, hist=True)
            mv_weights = port.optimization(model="Classic", rm="MV", obj="Sharpe", rf=self.rf / 252, l=0, hist=True)
            frontier = port.efficient_frontier(model="Classic", rm="MV", points=25, rf=self.rf / 252, hist=True)
            risk_parity = port.rp_optimization(model="Classic", rm="MV", rf=self.rf / 252, hist=True)
            frontier_rows = []
            if frontier is not None and not frontier.empty:
                mu = returns_df.mean() * 252
                cov = returns_df.cov() * 252
                for col in frontier.columns[:25]:
                    w = frontier[col].values
                    ret = float(w @ mu.values)
                    vol = float(np.sqrt(w @ cov.values @ w))
                    frontier_rows.append({"return": round(ret * 100, 2), "volatility": round(vol * 100, 2)})
            return clean_json({
                "cvar_min_risk_weights": cvar_weights.iloc[:, 0].round(4).to_dict() if cvar_weights is not None else {},
                "max_sharpe_weights": mv_weights.iloc[:, 0].round(4).to_dict() if mv_weights is not None else {},
                "risk_parity_weights": risk_parity.iloc[:, 0].round(4).to_dict() if risk_parity is not None else {},
                "efficient_frontier": frontier_rows,
                "black_litterman": "available via riskfolio; no explicit investor views supplied",
            })
        except Exception as exc:
            log.warning("riskfolio analysis failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def portfolio_summary(self, returns_df: pd.DataFrame, weights: np.ndarray) -> dict:
        if returns_df.empty:
            return {}
        port_ret = (returns_df * weights).sum(axis=1)
        cum = (1 + port_ret).cumprod()
        max_dd = ((cum / cum.cummax()) - 1).min()
        ann_ret = port_ret.mean() * 252
        ann_vol = port_ret.std() * np.sqrt(252)
        return {
            "annual_return": safe_round(ann_ret * 100, 2),
            "annual_volatility": safe_round(ann_vol * 100, 2),
            "sharpe": safe_round((ann_ret - self.rf) / ann_vol, 3) if ann_vol else None,
            "sortino": safe_round((ann_ret - self.rf) / (port_ret[port_ret < 0].std() * np.sqrt(252)), 3) if len(port_ret[port_ret < 0]) else None,
            "calmar": safe_round(ann_ret / abs(max_dd), 3) if max_dd else None,
            "max_drawdown": safe_round(max_dd * 100, 2),
            "var_parametric_1w": safe_round(self.parametric_var(port_ret, 5) * 100 if self.parametric_var(port_ret, 5) is not None else None, 2),
            "var_historical_1w": safe_round(self.historical_var(port_ret, 5) * 100 if self.historical_var(port_ret, 5) is not None else None, 2),
            "cvar_historical_1w": safe_round(self.historical_cvar(port_ret, 5) * 100 if self.historical_cvar(port_ret, 5) is not None else None, 2),
            "quantstats": self.quantstats_metrics(port_ret),
        }


class BehavioralAudit:
    def score(self, full_report: dict | None = None):
        modules = (full_report or {}).get("modules", {})
        fundamentals = modules.get("fundamentals", {}).get("stocks", {})
        risk = modules.get("risk_analytics", {})
        weights = risk.get("portfolio", {}).get("real_weights", {})
        concentration_ticker, concentration_weight = max(
            weights.items(),
            key=lambda item: safe_float(item[1], 0),
            default=(None, 41.0),
        )
        concentration_weight = safe_float(concentration_weight, 41.0)
        concentration_warning = config.SPECIAL_FLAGS.get(concentration_ticker, "") if concentration_ticker else ""
        ai_names = ["ai", "semi", "optical", "chip", "infra"]
        ai_count = sum(1 for p in config.PORTFOLIO if any(token in p.get("sector", "").lower() for token in ai_names))
        ai_ratio = ai_count / len(config.PORTFOLIO)
        hhi = sum((safe_float(w, 0) / 100) ** 2 for w in weights.values()) if weights else (0.41 ** 2)
        gains = [safe_float(v.get("total_return_pct"), 0) for v in fundamentals.values() if isinstance(v, dict)]
        avg_gain = np.mean(gains) if gains else 0
        scores = {
            "herding": min(10, round(ai_ratio * 10)),
            "confirmation": min(10, round(ai_ratio * 8 + 1)),
            "disposition": 8 if concentration_weight and concentration_weight >= 35 else 5,
            "overconfidence": 7 if avg_gain > 100 else 4,
            "recency": 7 if ai_ratio >= 0.75 else 4,
            "anchoring": 7 if concentration_weight and concentration_weight >= 35 else 5,
            "home_bias": 3,
            "loss_aversion": 5,
        }
        avg = float(np.mean(list(scores.values())))
        return {
            "bias_scores": scores,
            "avg_score": round(avg, 1),
            "overall_grade": "A" if avg < 3 else "B" if avg < 4.5 else "C" if avg < 6 else "D" if avg < 7.5 else "F",
            "portfolio_concentration_hhi": round(hhi, 4),
            "concentration_warning": concentration_warning,
            "tax_context": "Tax treatment depends on investor residency and jurisdiction.",
            "devils_advocate": (
                "The portfolio may be heavily exposed to one theme or position. Large unrealized gains and concentrated weights "
                "can make holding feel rational even if the forward risk/reward has changed. A disciplined review should ask what "
                "would happen if the main growth driver slows, margins normalize, or one earnings miss forces "
                "multiple compression before the thesis is invalidated."
            ),
            "recommendations": [
                "Review staged-exit rules for any concentrated position separately from the original purchase thesis.",
                "Use position weights, not unrealized gains, as the first risk-control input.",
                "Tax treatment depends on investor residency and jurisdiction.",
            ],
        }


class IntelEngine:
    def __init__(self):
        self.yf = YFinanceClient()
        self.fred = FredClient()
        self.finnhub = FinnhubClient()
        self.polygon = PolygonClient()
        self.tiingo = TiingoClient()
        self.market = MarketDataClient(self.polygon, self.tiingo, self.yf)
        self.sec = SecClient()
        self.fmp = FmpClient()
        self.newsapi = NewsApiClient()
        self.quant = QuantEngine()
        self.behavioral = BehavioralAudit()
        self.module_dispatch = {
            "macro_pulse": self._scan_macro,
            "market_context": self._scan_market_context,
            "portfolio_news": self._scan_news,
            "fundamentals": self._scan_fundamentals,
            "investment_committee": self._scan_investment_committee,
            "risk_analytics": self._scan_risk,
            "macro_regime": self._scan_macro_regime,
            "benchmark_attribution": self._scan_benchmark_attribution,
            "capital_efficiency": self._scan_capital_efficiency,
            "factor_exposure": self._scan_factor_exposure,
            "correlation_regime": self._scan_correlation_regime,
            "liquidity_ladder": self._scan_liquidity_ladder,
            "risk_contribution": self._scan_risk_contribution,
            "price_regime": self._scan_price_regime,
            "congress_insiders": self._scan_congress,
            "signal_action_plan": self._scan_signal_action_plan,
            "conviction_matrix": self._scan_conviction_matrix,
            "policy_allocation": self._scan_policy_allocation,
            "drawdown_survival": self._scan_drawdown_survival,
            "goals_based": self._scan_goals_based,
            "liquidity_concentration": self._scan_liquidity_concentration,
            "portfolio_narrative": self._scan_portfolio_narrative,
            "rebalancing_intelligence": self._scan_rebalancing_intelligence,
            "wealth_view": self._scan_wealth_view,
            "watchlist_scan": self._scan_watchlist,
            "price_audit": self._scan_price_audit,
            "behavioral_check": self._scan_behavioral,
        }

    def run_full_scan(self):
        log.info("Starting full portfolio intelligence scan")
        self.market.start_scan(config.TICKERS)
        report = {"timestamp": datetime.now(timezone.utc).isoformat(), "modules": {}, "source_priority": ["Polygon Official", "Tiingo", "Yahoo Delayed", "FRED", "Finnhub", "SEC API", "FMP", "NewsAPI"], "market_session": market_session()}
        for key, fn in self.module_dispatch.items():
            try:
                report["modules"][key] = fn(report) if key == "behavioral_check" else fn()
            except Exception as exc:
                log.exception("Module %s failed", key)
                report["modules"][key] = {"error": str(exc)}
        return clean_json(report)

    def run_module(self, module_id: str):
        legacy = {
            "macro": "macro_pulse",
            "market": "market_context",
            "market_context": "market_context",
            "news": "portfolio_news",
            "committee": "investment_committee",
            "cio": "investment_committee",
            "risk": "risk_analytics",
            "macro_regime": "macro_regime",
            "benchmark": "benchmark_attribution",
            "attribution": "benchmark_attribution",
            "capital_efficiency": "capital_efficiency",
            "efficiency": "capital_efficiency",
            "factors": "factor_exposure",
            "correlation": "correlation_regime",
            "ladder": "liquidity_ladder",
            "risk_contribution": "risk_contribution",
            "regime": "price_regime",
            "congress": "congress_insiders",
            "watchlist": "watchlist_scan",
            "behavioral": "behavioral_check",
            "sentiment": "signal_action_plan",
            "conviction": "conviction_matrix",
            "policy": "policy_allocation",
            "drawdown": "drawdown_survival",
            "goals": "goals_based",
            "liquidity": "liquidity_concentration",
            "narrative": "portfolio_narrative",
            "rebalancing": "rebalancing_intelligence",
            "wealth": "wealth_view",
            "price": "price_audit",
        }
        key = legacy.get(module_id, module_id)
        fn = self.module_dispatch.get(key)
        if not fn:
            return {"error": f"Unknown module: {module_id}"}
        try:
            self.market.start_scan(config.TICKERS)
            return clean_json(fn({"modules": {"risk_analytics": self._scan_risk(), "fundamentals": self._scan_fundamentals()}}) if key == "behavioral_check" else fn())
        except Exception as exc:
            log.exception("Single module %s failed", key)
            return {"error": str(exc)}

    def _position_for(self, ticker: str) -> dict:
        return next((p for p in config.PORTFOLIO if p["ticker"] == ticker), {})

    def _cached_portfolio_news(self) -> dict | None:
        path = "last_report.json"
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r") as f:
                cached = json.load(f)
            news = cached.get("report", {}).get("modules", {}).get("portfolio_news")
            if isinstance(news, dict) and news.get("items"):
                news = dict(news)
                news["cache_fallback"] = True
                news["note"] = "Live news APIs returned no articles; showing cached portfolio news from last_report.json."
                return news
        except Exception as exc:
            log.warning("Cached news fallback failed: %s", exc)
        return None

    def _market_context_items(self) -> list[dict]:
        items = []
        for item in getattr(config, "MARKET_CONTEXT", []):
            ticker = item["ticker"]
            quote = self.market.quote(ticker, prefer_cache=True)
            prices = self.market.prices(ticker, period="ytd", prefer_cache=True)
            if prices.empty:
                prices = self.market.prices(ticker, period="1y", prefer_cache=True)
            ytd_return = None
            if len(prices) >= 2:
                year = datetime.now(timezone.utc).year
                ytd_prices = prices[prices.index >= pd.Timestamp(f"{year}-01-01")]
                base = ytd_prices.iloc[0] if len(ytd_prices) >= 2 else prices.iloc[0]
                ytd_return = pct_change(prices.iloc[-1], base)
            status = "loaded" if quote.get("price") is not None else "failed"
            items.append({
                "ticker": ticker,
                "label": item.get("label", ticker),
                "group": item.get("group", ticker),
                "official_close": quote.get("official_close") or quote.get("price"),
                "latest_close": quote.get("price"),
                "previous_close": quote.get("prev_close"),
                "daily_change": quote.get("change"),
                "daily_change_pct": quote.get("change_pct"),
                "ytd_return_pct": ytd_return,
                "source": quote.get("source") or "Unavailable",
                "latest_trading_date": quote.get("latest_trading_date"),
                "market_session": quote.get("market_session") or market_session(),
                "status": status,
                "is_portfolio_holding": False,
            })
        return clean_json(items)

    def _market_context_news(self) -> list[dict]:
        groups = []
        seen: set[str] = set()
        context = getattr(config, "MARKET_CONTEXT", [])

        def add(group: dict, headline, source, date, url="", badge="delayed"):
            title = (headline or "").strip()
            if not title:
                return
            key = normalize_headline(title)
            if key in seen:
                return
            seen.add(key)
            group["items"].append({
                "ticker": group["ticker"],
                "group": group["group"],
                "headline": title,
                "source": source or "Unknown",
                "date": date,
                "url": url,
                "badge": badge,
            })

        query_map = {
            "SPY": "(S&P 500 OR SPY OR broad market)",
            "QQQ": "(Nasdaq OR QQQ OR growth stocks)",
            "GLD": "(gold OR GLD OR safe haven)",
        }
        for item in context:
            ticker = item["ticker"]
            group = {"ticker": ticker, "group": item.get("group", ticker), "label": item.get("label", ticker), "items": [], "sources": []}
            for article in self.finnhub.company_news(ticker, days_back=10):
                ts = article.get("datetime")
                date = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if isinstance(ts, (int, float)) else ts
                add(group, article.get("headline"), article.get("source", "Finnhub"), date, article.get("url", ""), "delayed")
            for article in self.newsapi.search(query_map.get(ticker, ticker), page_size=10):
                add(group, article.get("title"), (article.get("source") or {}).get("name", "NewsAPI"), article.get("publishedAt"), article.get("url", ""), "delayed")
            for article in self.tiingo.news([ticker], limit=8):
                add(group, article.get("title"), article.get("source", "Tiingo"), article.get("publishedDate"), article.get("url", ""), "delayed")
            group["items"] = group["items"][:5]
            group["count"] = len(group["items"])
            group["sources"] = sorted({row["source"] for row in group["items"]})
            groups.append(group)
        return clean_json(groups)

    def _scan_market_context(self, *_, include_news: bool = True):
        items = self._market_context_items()
        news_groups = self._market_context_news() if include_news else []
        loaded_count = sum(1 for row in items if row.get("status") == "loaded")
        return clean_json({
            "items": items,
            "news_groups": news_groups,
            "count": len(items),
            "loaded_count": loaded_count,
            "status": "loaded" if loaded_count else "failed",
            "disclaimer": "Market Context — Not Portfolio Holdings. Not investment advice.",
        })

    def _market_context_line(self) -> str:
        context = self._scan_market_context(include_news=False)
        parts = []
        for row in context.get("items", []):
            change = row.get("daily_change_pct")
            ytd = row.get("ytd_return_pct")
            parts.append(f"{row.get('ticker')} daily {change if change is not None else '--'}%, YTD {ytd if ytd is not None else '--'}%")
        return "; ".join(parts) or "Market context unavailable."

    def _scan_macro(self, *_):
        macro = self.fred.get_macro_dashboard()
        y10 = safe_float(macro.get("yield_10y", {}).get("latest"))
        y2 = safe_float(macro.get("yield_2y", {}).get("latest"))
        y3m = safe_float(macro.get("yield_3m", {}).get("latest"))
        copper = macro.get("copper", {})
        spread_10_2 = safe_round(y10 - y2, 3) if y10 is not None and y2 is not None else None
        spread_10_3m = safe_round(y10 - y3m, 3) if y10 is not None and y3m is not None else None
        copper_trend = copper.get("trend")
        return {
            "indicators": macro,
            "yield_curve": {
                "spread_10y_2y": spread_10_2,
                "spread_10y_3m": spread_10_3m,
                "inverted": bool((spread_10_2 is not None and spread_10_2 < 0) or (spread_10_3m is not None and spread_10_3m < 0)),
            },
            "doctor_copper": {
                "price": copper.get("latest"),
                "trend": copper_trend,
                "signal": "bullish_growth" if copper_trend == "up" else "cyclical_warning" if copper_trend == "down" else "neutral",
            },
        }

    def _macro_series_state(self, macro: dict, key: str):
        row = macro.get(key, {})
        return safe_float(row.get("latest")), safe_float(row.get("prev")), row.get("trend"), row.get("date")

    def _scan_macro_regime(self, *_):
        pulse = self._scan_macro()
        macro = pulse.get("indicators", {})
        unemployment, unemployment_prev, unemployment_trend, _ = self._macro_series_state(macro, "unemployment")
        claims, claims_prev, claims_trend, _ = self._macro_series_state(macro, "initial_claims")
        cpi, cpi_prev, cpi_trend, _ = self._macro_series_state(macro, "cpi")
        retail, retail_prev, retail_trend, _ = self._macro_series_state(macro, "retail_sales")
        sentiment, sentiment_prev, sentiment_trend, _ = self._macro_series_state(macro, "consumer_sent")
        recession, _, recession_trend, _ = self._macro_series_state(macro, "recession_prob")
        vix, _, vix_trend, _ = self._macro_series_state(macro, "vix")
        fed_funds, fed_prev, fed_trend, _ = self._macro_series_state(macro, "fed_funds")
        copper, _, copper_trend, _ = self._macro_series_state(macro, "copper")
        curve = pulse.get("yield_curve", {})
        spread_10_2 = safe_float(curve.get("spread_10y_2y"))
        spread_10_3m = safe_float(curve.get("spread_10y_3m"))

        scores = {"expansion": 0, "slowdown": 0, "inflation_pressure": 0, "risk_off": 0}
        evidence = []
        if retail_trend == "up" or sentiment_trend == "up":
            scores["expansion"] += 1
            evidence.append("Retail sales or consumer sentiment is improving.")
        if copper_trend == "up":
            scores["expansion"] += 1
            evidence.append("Copper trend points to cyclically firmer demand.")
        if unemployment_trend == "up" or claims_trend == "up":
            scores["slowdown"] += 2
            evidence.append("Labor-market indicators are weakening.")
        if spread_10_2 is not None and spread_10_2 < 0:
            scores["slowdown"] += 1
            evidence.append("10Y-2Y curve is inverted.")
        if spread_10_3m is not None and spread_10_3m < 0:
            scores["slowdown"] += 1
            evidence.append("10Y-3M curve is inverted.")
        if cpi_trend == "up" or fed_trend == "up":
            scores["inflation_pressure"] += 2
            evidence.append("CPI or fed funds trend is rising.")
        if vix is not None and vix > 25:
            scores["risk_off"] += 2
            evidence.append("VIX is elevated above 25.")
        if recession is not None and recession > 20:
            scores["risk_off"] += 2
            evidence.append("FRED recession probability is elevated.")
        if vix_trend == "up" and recession_trend == "up":
            scores["risk_off"] += 1
            evidence.append("Volatility and recession probability are both moving higher.")
        market_context_line = self._market_context_line()
        if market_context_line != "Market context unavailable.":
            evidence.append(f"Market context: {market_context_line}")

        primary = max(scores, key=scores.get)
        if scores[primary] == 0:
            primary = "neutral"
        allocation_posture = {
            "expansion": "Risk budget can remain growth-oriented, subject to existing concentration limits.",
            "slowdown": "Review drawdown survival, cash buffer, and single-theme exposure before adding risk.",
            "inflation_pressure": "Rates sensitivity and long-duration growth exposure deserve extra scrutiny.",
            "risk_off": "Prioritize liquidity, correlation, and recovery-needed analysis over return chasing.",
            "neutral": "Macro signals are mixed; maintain policy discipline and monitor changes week to week.",
        }.get(primary)
        rows = [
            {"indicator": "Unemployment", "latest": unemployment, "previous": unemployment_prev, "trend": unemployment_trend, "regime_read": "slowdown risk if rising"},
            {"indicator": "Initial claims", "latest": claims, "previous": claims_prev, "trend": claims_trend, "regime_read": "slowdown risk if rising"},
            {"indicator": "CPI", "latest": cpi, "previous": cpi_prev, "trend": cpi_trend, "regime_read": "inflation pressure if rising"},
            {"indicator": "Retail sales", "latest": retail, "previous": retail_prev, "trend": retail_trend, "regime_read": "growth support if rising"},
            {"indicator": "Consumer sentiment", "latest": sentiment, "previous": sentiment_prev, "trend": sentiment_trend, "regime_read": "growth support if rising"},
            {"indicator": "VIX", "latest": vix, "previous": None, "trend": vix_trend, "regime_read": "risk-off if elevated"},
            {"indicator": "Recession probability", "latest": recession, "previous": None, "trend": recession_trend, "regime_read": "risk-off if elevated"},
            {"indicator": "Fed funds", "latest": fed_funds, "previous": fed_prev, "trend": fed_trend, "regime_read": "rates pressure if rising"},
            {"indicator": "Copper", "latest": copper, "previous": None, "trend": copper_trend, "regime_read": "cyclical demand proxy"},
        ]
        return clean_json({
            "primary_regime": primary,
            "scores": scores,
            "yield_curve": curve,
            "indicators": rows,
            "evidence": evidence or ["Macro data is mixed without a dominant regime."],
            "allocation_posture": allocation_posture,
            "market_context": market_context_line,
            "disclaimer": "Macro regime detection uses free delayed macro data for risk-budget context only.",
        })

    def _scan_news(self, *_):
        by_ticker = {ticker: [] for ticker in config.TICKERS}
        seen = set()

        def add(headline, source, date, ticker, url=""):
            title = (headline or "").strip()
            if not title or ticker not in by_ticker:
                return
            key = normalize_headline(title)
            if key in seen:
                return
            seen.add(key)
            by_ticker[ticker].append({"ticker": ticker, "headline": title, "source": source or "Unknown", "date": date, "url": url})

        for ticker in config.TICKERS:
            for item in self.finnhub.company_news(ticker, days_back=10):
                ts = item.get("datetime")
                date = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if isinstance(ts, (int, float)) else ts
                add(item.get("headline"), item.get("source", "Finnhub"), date, ticker, item.get("url", ""))

        for item in self.tiingo.news(config.TICKERS, limit=40):
            tickers = item.get("tickers") or []
            matched = [t for t in tickers if t in config.TICKERS]
            for ticker in matched[:2]:
                add(item.get("title"), item.get("source", "Tiingo"), item.get("publishedDate"), ticker, item.get("url", ""))

        for article in self.newsapi.search(" OR ".join(config.TICKERS), page_size=30):
            blob = f"{article.get('title', '')} {article.get('description', '')}"
            matched = [t for t in config.TICKERS if t in blob]
            for ticker in matched:
                add(article.get("title"), (article.get("source") or {}).get("name", "NewsAPI"), article.get("publishedAt"), ticker, article.get("url", ""))

        top_feed = []
        priority = list(config.TICKERS)
        for ticker in priority:
            top_feed.extend(by_ticker.get(ticker, [])[:3])
        counts = {ticker: len(items) for ticker, items in by_ticker.items()}
        if not top_feed:
            cached = self._cached_portfolio_news()
            if cached:
                return cached
        return {
            "items": top_feed,
            "by_ticker": by_ticker,
            "counts_by_ticker": counts,
            "count": sum(counts.values()),
            "top_feed_cap_per_ticker": 3,
            "sources": ["Finnhub", "NewsAPI", "Tiingo"],
            "dedupe": "normalized_headline",
            "note": "Grouped by ticker first; each ticker is capped at three top-feed articles.",
        }

    def _scan_fundamentals(self, *_):
        stocks = {}
        quote_values = {}
        for ticker in config.TICKERS:
            pos = self._position_for(ticker)
            market_quote = self.market.quote(ticker)
            fh_quote = self.finnhub.quote(ticker)
            price = market_quote.get("price") or fh_quote.get("price") or pos.get("avg_cost", 0)
            yf_fund = self.yf.fundamentals(ticker)
            fmp_backup = self.fmp.fundamentals_backup(ticker)
            eps = yf_fund.get("eps") or safe_float(fmp_backup.get("ratios", {}).get("netIncomePerShareTTM"))
            growth = yf_fund.get("earnings_growth") or yf_fund.get("revenue_growth") or safe_float(fmp_backup.get("ratios", {}).get("revenueGrowthTTM"))
            graham = self.quant.graham_value(eps, growth)
            shares = safe_float(pos.get("shares"), 0)
            avg_cost = safe_float(pos.get("avg_cost"), 0)
            market_value = shares * price if price else 0
            quote_values[ticker] = market_value
            stocks[ticker] = {
                "name": pos.get("name"),
                "sector": pos.get("sector"),
                "theme": pos.get("theme") or config.THEME_MAP.get(ticker),
                "category": pos.get("category"),
                "benchmark_group": pos.get("benchmark_group"),
                "risk_bucket": pos.get("risk_bucket"),
                "price": safe_round(price),
                "day_change_pct": market_quote.get("change_pct") or fh_quote.get("change_pct"),
                "price_source": market_quote.get("source") or fh_quote.get("source"),
                "market_session": market_quote.get("market_session") or market_session(),
                "latest_trading_date": market_quote.get("latest_trading_date"),
                "open": market_quote.get("open"),
                "high": market_quote.get("high"),
                "low": market_quote.get("low"),
                "volume": market_quote.get("volume"),
                "shares": shares,
                "avg_cost": avg_cost,
                "market_value": safe_round(market_value),
                "unrealized_pnl": safe_round((price - avg_cost) * shares if price and avg_cost else None),
                "total_return_pct": pct_change(price, avg_cost),
                "pe": yf_fund.get("pe"),
                "pb": yf_fund.get("pb"),
                "eps": eps,
                "dividend_yield": yf_fund.get("dividend_yield"),
                "earnings_growth": yf_fund.get("earnings_growth"),
                "revenue_growth": yf_fund.get("revenue_growth"),
                "graham_value": safe_round(graham),
                "vs_graham_pct": pct_change(price, graham),
                "capm_expected_return": None,
                "analyst_recommendation": yf_fund.get("analyst_recommendation"),
                "target_mean_price": yf_fund.get("target_mean_price"),
                "institutional_holders": yf_fund.get("institutional_holders", []),
                "earnings_dates": yf_fund.get("earnings_dates", []),
                "options": self.yf.options_summary(ticker),
                "fmp_backup": fmp_backup,
                "special_flag": config.SPECIAL_FLAGS.get(ticker),
                "notes": pos.get("notes"),
            }
        total = sum(quote_values.values()) + config.BUYING_POWER
        for ticker, value in quote_values.items():
            stocks[ticker]["portfolio_weight_pct"] = safe_round(value / total * 100 if total else None, 2)
            beta = safe_float(stocks[ticker].get("beta"))
            stocks[ticker]["capm_expected_return"] = safe_round(self.quant.capm_expected_return(beta) * 100 if beta is not None and self.quant.capm_expected_return(beta) is not None else None, 2)
        return {"stocks": clean_json(stocks), "buying_power": config.BUYING_POWER, "tax_context": "Tax treatment depends on investor residency and jurisdiction."}

    def _scan_price_audit(self, *_):
        context_tickers = getattr(config, "MARKET_CONTEXT_TICKERS", ["SPY"])
        tickers = config.TICKERS + [item["ticker"] for item in config.WATCHLIST] + context_tickers
        if not any(row.get("source_used") and row.get("source_used") != "Not requested" for row in self.market.audit.values()):
            for ticker in config.TICKERS:
                self.market.quote(ticker)
            for item in config.WATCHLIST:
                self.market.quote(item["ticker"], prefer_cache=True)
            for ticker in context_tickers:
                self.market.quote(ticker, prefer_cache=True)
        return {
            "items": self.market.price_audit_rows(tickers),
            "polygon_rate_limit_policy": "Max 4 Polygon requests per minute; each 429 waits 12 seconds and retries once before ticker-level fallback.",
            "fallback_order": ["Polygon Official", "Tiingo", "Yahoo Delayed"],
        }

    def _portfolio_return_frame(self, period: str = "2y") -> dict:
        target_days = period_to_days(period)
        prices = {}
        excluded = []
        position_values = {}
        for ticker in config.TICKERS:
            pos = self._position_for(ticker)
            shares = safe_float(pos.get("shares"), 0)
            series = self.market.prices(ticker, period=period, prefer_cache=False)
            if not series.empty:
                s = series.dropna().copy()
                s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                s = s[~s.index.duplicated(keep="last")].sort_index()
                if len(s) >= min(target_days, 60) and shares > 0:
                    prices[ticker] = s
                    position_values[ticker] = s * shares
                else:
                    excluded.append({"ticker": ticker, "reason": f"insufficient_history rows={len(s)}"})
            else:
                excluded.append({"ticker": ticker, "reason": "missing_price_history"})
        close_df = pd.DataFrame(prices).sort_index()
        position_value_df = pd.DataFrame(position_values).sort_index()
        aligned_values = position_value_df.dropna(how="any")
        if aligned_values.empty:
            returns_df = close_df.pct_change().dropna(how="all") if not close_df.empty else pd.DataFrame()
            return {
                "prices": prices,
                "close_prices": close_df,
                "position_values": {},
                "position_value_frame": position_value_df,
                "returns": returns_df,
                "weights": pd.Series(dtype=float),
                "portfolio_returns": pd.Series(dtype=float),
                "portfolio_value": pd.Series(dtype=float),
                "excluded_tickers": excluded,
                "diagnostics": {
                    "portfolio_history_len": 0,
                    "excluded_tickers": excluded,
                    "data_method": "synthetic_shares_x_close",
                },
            }
        portfolio_value = aligned_values.sum(axis=1)
        portfolio_returns = portfolio_value.pct_change().dropna()
        aligned_closes = close_df.reindex(aligned_values.index).dropna(how="any")
        returns_df = aligned_closes.pct_change().dropna()
        latest_values = aligned_values.iloc[-1].to_dict()
        total = sum(safe_float(v, 0) for v in latest_values.values()) or 1
        weights = pd.Series({ticker: safe_float(value, 0) / total for ticker, value in latest_values.items()})
        diagnostics = {
            "portfolio_history_len": len(portfolio_returns),
            "excluded_tickers": excluded,
            "included_tickers": list(aligned_values.columns),
            "data_method": "synthetic_shares_x_close",
            "portfolio_value_first_date": portfolio_value.index.min().isoformat() if len(portfolio_value) else None,
            "portfolio_value_last_date": portfolio_value.index.max().isoformat() if len(portfolio_value) else None,
        }
        return {
            "prices": prices,
            "close_prices": aligned_closes,
            "position_values": latest_values,
            "position_value_frame": aligned_values,
            "returns": returns_df,
            "weights": weights,
            "portfolio_returns": portfolio_returns,
            "portfolio_value": portfolio_value,
            "excluded_tickers": excluded,
            "diagnostics": diagnostics,
        }

    def _pairwise_average(self, corr: pd.DataFrame):
        if corr.empty or len(corr) < 2:
            return None
        vals = corr.where(~np.eye(len(corr), dtype=bool)).stack()
        return safe_round(vals.mean(), 3) if len(vals) else None

    def _proxy_factor_exposure_rows(self) -> list[dict]:
        snap = self._portfolio_snapshot()
        holdings = snap.get("holdings", [])

        def match(row, predicate):
            try:
                return bool(predicate(row))
            except Exception:
                return False

        definitions = [
            ("AI Infrastructure exposure", lambda r: r.get("theme") == "AI Infrastructure"),
            ("Semiconductor exposure", lambda r: r.get("theme") == "Semiconductors" or "semi" in str(r.get("sector", "")).lower()),
            ("Optical/AI Infra exposure", lambda r: "optical" in str(r.get("sector", "")).lower() or r.get("theme") == "AI Infrastructure"),
            ("Gaming exposure", lambda r: "gaming" in str(r.get("sector", "")).lower() or r.get("theme") == "Interactive Entertainment"),
            ("ETF exposure", lambda r: r.get("category") == "ETF" or "etf" in str(r.get("sector", "")).lower()),
            ("Single-stock concentration", lambda r: r.get("category") == "Single Stock"),
            ("Growth/Tech proxy exposure", lambda r: "growth" in str(r.get("benchmark_group", "")).lower() or "tech" in str(r.get("benchmark_group", "")).lower()),
        ]
        rows = []
        for factor, predicate in definitions:
            matched = [row for row in holdings if match(row, predicate)]
            weight = sum(safe_float(row.get("weight"), 0) for row in matched) * 100
            rows.append({
                "factor": factor,
                "exposure_weight_pct": safe_round(weight, 2),
                "tickers": ", ".join(row.get("ticker", "") for row in matched) or "--",
                "classification_basis": "holdings metadata",
                "method": "Proxy factor exposure based on holdings classification, not return regression.",
            })
        return clean_json(rows)

    def _position_attribution_rows(self, frame: dict, weights: pd.Series, benchmark_return: float | None = None) -> list[dict]:
        rows = []
        prices_by_ticker = frame.get("prices", {})
        tickers = (frame.get("diagnostics") or {}).get("included_tickers") or list(prices_by_ticker.keys()) or config.TICKERS
        for ticker in tickers:
            pos = self._position_for(ticker)
            series = prices_by_ticker.get(ticker)
            asset_total = None
            if series is not None and len(series.dropna()) >= 2:
                clean = series.dropna()
                asset_total = clean.iloc[-1] / clean.iloc[0] - 1 if clean.iloc[0] else None
            if asset_total is None:
                quote = self.market.quote(ticker, prefer_cache=True)
                price = safe_float(quote.get("price"))
                avg_cost = safe_float(pos.get("avg_cost"))
                asset_total = price / avg_cost - 1 if price is not None and avg_cost not in (None, 0) else None
            weight = safe_float(weights.get(ticker), None)
            if weight is None:
                quote = self.market.quote(ticker, prefer_cache=True)
                price = safe_float(quote.get("price")) or safe_float(pos.get("avg_cost"), 0)
                shares = safe_float(pos.get("shares"), 0)
                values = {}
                for p in config.PORTFOLIO:
                    q = self.market.quote(p["ticker"], prefer_cache=True)
                    px = safe_float(q.get("price")) or safe_float(p.get("avg_cost"), 0)
                    values[p["ticker"]] = safe_float(p.get("shares"), 0) * px
                total = sum(values.values()) or 1
                weight = shares * price / total if total else 0
            active = asset_total - benchmark_return if asset_total is not None and benchmark_return is not None else None
            rows.append({
                "ticker": ticker,
                "weight_pct": safe_round(weight * 100, 2),
                "asset_return_pct": safe_round(asset_total * 100, 2) if asset_total is not None else None,
                "benchmark_return_pct": safe_round(benchmark_return * 100, 2) if benchmark_return is not None else None,
                "contribution_pct": safe_round(weight * asset_total * 100, 2) if asset_total is not None else None,
                "active_contribution_pct": safe_round(weight * active * 100, 2) if active is not None else None,
                "relative_result": "outperforming" if active is not None and active > 0 else "underperforming" if active is not None and active < 0 else "benchmark_unavailable",
                "method": "simplified" if benchmark_return is not None else "position_return_only",
            })
        return clean_json(rows)

    def _scan_factor_exposure(self, *_):
        frame = self._portfolio_return_frame("2y")
        portfolio_returns = frame["portfolio_returns"]
        warnings_list = []
        proxy_rows = self._proxy_factor_exposure_rows()
        diagnostics = dict(frame.get("diagnostics") or {})
        if frame.get("excluded_tickers"):
            warnings_list.append(f"Excluded tickers from synthetic history: {', '.join(row.get('ticker', '') for row in frame.get('excluded_tickers', []))}.")
        factors = {}
        for factor, proxy in config.FACTOR_PROXIES.items():
            series = self.market.prices(proxy, period="2y")
            if not series.empty:
                factors[factor] = series.pct_change().dropna()
            else:
                warnings_list.append(f"Missing proxy history for {factor} ({proxy}).")
                log.warning("Factor exposure empty return series: proxy=%s factor=%s", proxy, factor)
        factor_df = pd.DataFrame(factors).dropna()
        diagnostics["benchmark_history_len"] = len(factor_df)
        if portfolio_returns.empty or len(portfolio_returns) < 90:
            log.warning("Factor exposure empty return series: portfolio_returns length=%s columns=%s", len(portfolio_returns), list(frame.get("returns", pd.DataFrame()).columns))
            diagnostics.update({
                "aligned_len": 0,
                "data_method": "synthetic_shares_x_close",
                "regression_window": 0,
                "pca_observations": 0,
            })
            return clean_json({
                "status": "partial",
                "reason": "Insufficient history for full factor decomposition",
                "warnings": list(dict.fromkeys(warnings_list + ["Insufficient history for full factor decomposition"])),
                "diagnostics": diagnostics,
                "portfolio_history_len": diagnostics.get("portfolio_history_len"),
                "benchmark_history_len": diagnostics.get("benchmark_history_len"),
                "aligned_len": diagnostics.get("aligned_len"),
                "excluded_tickers": diagnostics.get("excluded_tickers", []),
                "data_method": diagnostics.get("data_method"),
                "regression_window": diagnostics.get("regression_window"),
                "pca_observations": diagnostics.get("pca_observations"),
                "items": proxy_rows,
                "proxy_factor_exposures": proxy_rows,
                "factor_contributions": [],
                "pca": [],
                "pca_status": "insufficient_history",
                "model_r2": None,
                "method_note": "Proxy factor exposure based on holdings classification, not return regression.",
                "commentary": [
                    "Insufficient history for full factor decomposition. Available price history is not long enough for a stable institutional factor model.",
                    "Proxy factor exposure based on holdings classification, not return regression.",
                ],
                "disclaimer": "Institutional risk model using free proxy data. For allocation context only; not a buy/sell signal.",
            })
        betas = {}
        r2 = None
        x = pd.DataFrame()
        aligned = pd.DataFrame()
        try:
            aligned = pd.concat([portfolio_returns.rename("portfolio"), factor_df], axis=1).dropna()
            diagnostics["aligned_len"] = len(aligned)
        except Exception as exc:
            log.exception("Factor exposure regression alignment failed: %s", exc)
            warnings_list.append("Regression alignment failed; showing partial factor data only.")
            diagnostics["aligned_len"] = 0
        if factor_df.empty or len(aligned) < 60:
            warnings_list.append("Insufficient history for full factor decomposition")
            log.warning("Factor exposure regression alignment insufficient: aligned_len=%s factor_cols=%s", len(aligned), list(factor_df.columns))
            x = factor_df
            diagnostics["regression_window"] = 0
        else:
            try:
                y = aligned["portfolio"]
                x = aligned.drop(columns=["portfolio"])
                x_std = (x - x.mean()) / x.std().replace(0, np.nan)
                y_std = (y - y.mean()) / y.std() if y.std() else y
                model_df = pd.concat([y_std.rename("portfolio"), x_std], axis=1).dropna()
                diagnostics["regression_window"] = len(model_df)
                if len(model_df) >= 60:
                    X = np.column_stack([np.ones(len(model_df)), model_df.drop(columns=["portfolio"]).values])
                    coeffs, _, _, _ = np.linalg.lstsq(X, model_df["portfolio"].values, rcond=None)
                    pred = X @ coeffs
                    ss_res = np.sum((model_df["portfolio"].values - pred) ** 2)
                    ss_tot = np.sum((model_df["portfolio"].values - model_df["portfolio"].mean()) ** 2)
                    r2 = 1 - ss_res / ss_tot if ss_tot else None
                    betas = {factor: coeff for factor, coeff in zip(model_df.drop(columns=["portfolio"]).columns, coeffs[1:])}
                else:
                    warnings_list.append("Insufficient aligned observations after standardization.")
                    log.warning("Factor exposure regression insufficient after standardization: model_df_len=%s", len(model_df))
            except Exception as exc:
                log.exception("Factor exposure regression failed: %s", exc)
                warnings_list.append("Regression failed; showing partial factor data only.")
        rows = []
        contribution_den = sum(abs(beta) * safe_float(x[factor].std(), 0) for factor, beta in betas.items() if factor in x.columns) or 1
        factor_names = list(betas.keys()) if betas else list(factor_df.columns)
        for factor in factor_names:
            beta = betas.get(factor)
            if factor not in x.columns and factor in factor_df.columns:
                x = factor_df
            vol = safe_float((x[factor].std() if factor in x.columns else factor_df[factor].std()), 0) * np.sqrt(252)
            contribution = abs(beta) * safe_float(x[factor].std(), 0) / contribution_den * 100 if beta is not None and factor in x.columns else None
            rolling_beta = None
            try:
                if not aligned.empty and factor in aligned.columns:
                    recent = aligned[["portfolio", factor]].tail(126).dropna()
                    if len(recent) >= 45 and recent[factor].var() > 0:
                        rolling_beta = np.cov(recent["portfolio"], recent[factor])[0, 1] / recent[factor].var()
            except Exception as exc:
                log.exception("Factor exposure rolling regression failed for %s: %s", factor, exc)
                warnings_list.append(f"Rolling regression failed for {factor}.")
            rows.append({
                "factor": factor,
                "proxy": config.FACTOR_PROXIES.get(factor),
                "exposure_beta": safe_round(beta, 3),
                "rolling_6m_beta": safe_round(rolling_beta, 3),
                "factor_annual_volatility_pct": safe_round(vol * 100, 2),
                "risk_contribution_pct": safe_round(contribution, 2),
            })
        pca_rows = []
        pca_status = "unavailable"
        returns_df = frame["returns"].dropna()
        if returns_df.shape[1] >= 2 and len(returns_df) >= 60:
            standardized = (returns_df - returns_df.mean()) / returns_df.std().replace(0, np.nan)
            standardized = standardized.dropna()
            diagnostics["pca_observations"] = len(standardized)
            try:
                decomposition = optional_import("sklearn.decomposition")
                if decomposition is not None:
                    n_components = min(3, standardized.shape[1])
                    pca = decomposition.PCA(n_components=n_components)
                    pca.fit(standardized)
                    for i, ratio in enumerate(pca.explained_variance_ratio_, start=1):
                        pca_rows.append({"component": f"PC{i}", "explained_variance_pct": safe_round(ratio * 100, 2)})
                    pca_status = "sklearn_pca"
                else:
                    _, s, _ = np.linalg.svd(standardized.values, full_matrices=False)
                    explained = (s ** 2) / np.sum(s ** 2)
                    for i, ratio in enumerate(explained[:3], start=1):
                        pca_rows.append({"component": f"PC{i}", "explained_variance_pct": safe_round(ratio * 100, 2)})
                    pca_status = "svd_fallback"
            except Exception as exc:
                log.exception("PCA factor analysis failed: %s", exc)
                warnings_list.append("PCA failed; factor regression output remains available if shown.")
                pca_status = "error"
        else:
            warnings_list.append("Insufficient holding history for PCA common-driver analysis.")
            log.warning("PCA factor analysis insufficient history: rows=%s cols=%s", len(returns_df), returns_df.shape[1] if hasattr(returns_df, "shape") else 0)
            diagnostics["pca_observations"] = 0
        largest = max(rows, key=lambda r: safe_float(r.get("risk_contribution_pct"), 0), default={})
        pc1 = safe_float(pca_rows[0].get("explained_variance_pct")) if pca_rows else None
        commentary = []
        if largest:
            commentary.append(f"Dominant modeled factor is {largest.get('factor')} via {largest.get('proxy')}; this is an allocation-risk lens, not a trade signal.")
        if pc1 and pc1 > 60:
            commentary.append("PCA indicates one common driver explains most holding variance, so diversification may be weaker during stress.")
        semi = next((r for r in rows if r["factor"] == "semiconductor"), {})
        if safe_float(semi.get("exposure_beta"), 0) > 0.5:
            commentary.append("Semiconductor factor exposure is material; AI infrastructure and semi drawdowns should be evaluated at portfolio level.")
        commentary.append("Stress-test implication: shocks to broad growth, semiconductor capex, volatility spikes, or rates can affect multiple holdings at once.")
        status = "partial" if warnings_list or not betas else "success"
        return clean_json({
            "status": status,
            "reason": "; ".join(dict.fromkeys(warnings_list)) if warnings_list else "",
            "warnings": list(dict.fromkeys(warnings_list)),
            "diagnostics": diagnostics,
            "portfolio_history_len": diagnostics.get("portfolio_history_len"),
            "benchmark_history_len": diagnostics.get("benchmark_history_len"),
            "aligned_len": diagnostics.get("aligned_len"),
            "excluded_tickers": diagnostics.get("excluded_tickers", []),
            "data_method": diagnostics.get("data_method"),
            "regression_window": diagnostics.get("regression_window"),
            "pca_observations": diagnostics.get("pca_observations"),
            "items": rows,
            "proxy_factor_exposures": proxy_rows,
            "method_note": "Proxy factor exposure based on holdings classification, not return regression." if status == "partial" else "Return regression with holdings-classification fallback.",
            "factor_contributions": sorted(rows, key=lambda r: safe_float(r.get("risk_contribution_pct"), 0), reverse=True),
            "pca": pca_rows,
            "pca_status": pca_status,
            "model_r2": safe_round(r2, 3),
            "commentary": commentary,
            "disclaimer": "Institutional risk model using free proxy data. For allocation context only; not a buy/sell signal.",
        })

    def _scan_correlation_regime(self, *_):
        frame = self._portfolio_return_frame("2y")
        returns_df = frame["returns"]
        if returns_df.empty or returns_df.shape[1] < 2:
            return {"error": "Insufficient return history for correlation regime monitor."}
        corr30 = returns_df.tail(30).corr()
        corr90 = returns_df.tail(90).corr()
        avg30 = self._pairwise_average(corr30)
        avg90 = self._pairwise_average(corr90)
        threshold = config.CORRELATION_POLICY["cluster_corr_threshold"]
        clusters = []
        for i, a in enumerate(corr90.columns):
            for b in corr90.columns[i + 1:]:
                value = safe_float(corr90.loc[a, b])
                if value is not None and value >= threshold:
                    clusters.append({"holding_a": a, "holding_b": b, "corr_90d": safe_round(value, 3), "risk": "clustered"})
        if avg30 is not None and avg30 >= config.CORRELATION_POLICY["collapse_avg_pairwise_corr"]:
            regime = "diversification_collapse"
        elif avg90 is not None and avg90 >= config.CORRELATION_POLICY["elevated_avg_pairwise_corr"]:
            regime = "elevated_correlation"
        else:
            regime = "normal"
        heatmap = []
        for ticker in corr90.columns:
            row = {"ticker": ticker}
            for other in corr90.columns:
                row[other] = safe_round(corr90.loc[ticker, other], 2)
            heatmap.append(row)
        commentary = (
            "Diversification collapse risk is elevated when average pairwise correlation approaches crisis-like levels; position limits matter more than ticker count."
            if regime != "normal" else
            "Current pairwise correlations do not show a broad diversification collapse, but clustered AI/semi exposure should still be monitored."
        )
        return clean_json({
            "regime": regime,
            "average_pairwise_corr_30d": avg30,
            "average_pairwise_corr_90d": avg90,
            "clusters": clusters,
            "heatmap": heatmap,
            "tickers": list(corr90.columns),
            "commentary": commentary,
        })

    def _scan_liquidity_ladder(self, *_):
        snap = self._portfolio_snapshot()
        policy = config.LIQUIDITY_POLICY
        rows = []
        buckets = {"same-day": 0, "1-3 days": 0, "stressed liquidity": 0}
        for row in snap["holdings"]:
            ticker = row["ticker"]
            hist = self.market.history(ticker, period="3mo", days=70)
            avg_dollar_volume = None
            if not hist.empty and "Volume" in hist.columns and "Close" in hist.columns:
                avg_dollar_volume = safe_float((hist["Volume"].tail(60) * hist["Close"].tail(60)).mean())
            if avg_dollar_volume is None:
                avg_dollar_volume = safe_float(row.get("volume"), 0) * safe_float(row.get("price"), 0)
            position_value = safe_float(row.get("market_value"), 0)
            normal_capacity = avg_dollar_volume * policy["normal_participation_rate"] if avg_dollar_volume else 0
            stressed_capacity = avg_dollar_volume * policy["stressed_participation_rate"] if avg_dollar_volume else 0
            normal_days = position_value / normal_capacity if normal_capacity else None
            stressed_days = position_value / stressed_capacity if stressed_capacity else None
            if normal_days is None:
                bucket = "stressed liquidity"
            elif normal_days <= policy["same_day_max_days"]:
                bucket = "same-day"
            elif normal_days <= policy["ladder_max_days"]:
                bucket = "1-3 days"
            else:
                bucket = "stressed liquidity"
            buckets[bucket] += position_value
            rows.append({
                "ticker": ticker,
                "position_value": safe_round(position_value),
                "portfolio_weight_pct": safe_round(row.get("weight") * 100, 2),
                "avg_daily_dollar_volume": safe_round(avg_dollar_volume),
                "market_cap": safe_round(row.get("market_cap")),
                "normal_liquidation_days": safe_round(normal_days, 2),
                "stressed_liquidation_days": safe_round(stressed_days, 2),
                "bucket": bucket,
                "note": config.SPECIAL_FLAGS.get(ticker, ""),
            })
        stressed_weight = buckets["stressed liquidity"] / snap["total_value"] if snap["total_value"] else 0
        ladder_weight = buckets["1-3 days"] / snap["total_value"] if snap["total_value"] else 0
        grade = "A" if stressed_weight < 0.05 and ladder_weight < 0.25 else "B" if stressed_weight < 0.10 else "C" if stressed_weight < 0.20 else "D" if stressed_weight < 0.35 else "F"
        return clean_json({
            "items": rows,
            "bucket_values": {k: safe_round(v) for k, v in buckets.items()},
            "bucket_weights_pct": {k: safe_round(v / snap["total_value"] * 100 if snap["total_value"] else None, 2) for k, v in buckets.items()},
            "portfolio_liquidity_grade": grade,
            "commentary": "Liquidity ladder estimates exit capacity from average dollar volume and participation limits. It is designed for risk planning, not execution timing.",
        })

    def _scan_risk_contribution(self, *_):
        frame = self._portfolio_return_frame("2y")
        returns_df = frame["returns"]
        weights = frame["weights"]
        if returns_df.empty or returns_df.shape[1] < 2:
            return {"error": "Insufficient return history for risk contribution analysis."}
        cov = returns_df.cov()
        w = weights.reindex(returns_df.columns).fillna(0).values
        port_var = float(w @ cov.values @ w)
        if port_var <= 0:
            return {"error": "Portfolio variance unavailable."}
        port_vol_daily = np.sqrt(port_var)
        marginal = cov.values @ w / port_vol_daily
        component = w * marginal / port_vol_daily
        total_var_1w = self.quant.parametric_var(frame["portfolio_returns"], 5)
        rows = []
        for i, ticker in enumerate(returns_df.columns):
            weight_pct = weights.get(ticker, 0) * 100
            risk_pct = component[i] * 100
            flag = ""
            if risk_pct > weight_pct * 1.25 + 5:
                flag = "Risk contribution materially exceeds weight"
            special_flag = config.SPECIAL_FLAGS.get(ticker, "")
            if special_flag:
                flag = (flag + " | " if flag else "") + special_flag
            rows.append({
                "ticker": ticker,
                "portfolio_weight_pct": safe_round(weight_pct, 2),
                "volatility_contribution_pct": safe_round(risk_pct, 2),
                "marginal_volatility": safe_round(marginal[i] * np.sqrt(252) * 100, 2),
                "component_var_1w_pct": safe_round(total_var_1w * component[i] * 100 if total_var_1w is not None else None, 2),
                "risk_minus_weight_pct": safe_round(risk_pct - weight_pct, 2),
                "flag": flag,
            })
        rows = sorted(rows, key=lambda r: safe_float(r.get("volatility_contribution_pct"), 0), reverse=True)
        return clean_json({
            "items": rows,
            "portfolio_annual_volatility_pct": safe_round(port_vol_daily * np.sqrt(252) * 100, 2),
            "portfolio_var_1w_pct": safe_round(total_var_1w * 100 if total_var_1w is not None else None, 2),
            "commentary": "Risk contribution shows which holdings consume portfolio risk budget. A high contribution is a governance issue, not an automatic trade instruction.",
            "concentration_warning": next((config.SPECIAL_FLAGS.get(row.get("ticker"), "") for row in rows if config.SPECIAL_FLAGS.get(row.get("ticker"), "")), ""),
        })

    def _classify_price_regime(self, prices: pd.Series) -> dict:
        if prices.empty or len(prices) < 90:
            return {"regime": "insufficient_data"}
        returns = prices.pct_change().dropna()
        ret63 = self._period_return(prices, 63)
        ret21 = self._period_return(prices, 21)
        vol = safe_float(returns.tail(63).std()) * np.sqrt(252) * 100 if len(returns) >= 63 else None
        cum = (1 + returns.tail(126)).cumprod()
        drawdown = safe_float((cum / cum.cummax() - 1).min() * 100) if len(cum) else None
        autocorr = safe_float(returns.tail(63).autocorr(lag=1)) if len(returns) >= 63 else None
        technicals = self.quant.technicals(prices)
        if ret63 is not None and ret63 < -10 and (drawdown is not None and drawdown < -12):
            regime = "risk-off"
        elif vol is not None and vol > 45:
            regime = "volatile"
        elif ret63 is not None and ret63 > 8 and safe_float(technicals.get("sma50")) and safe_float(prices.iloc[-1]) > safe_float(technicals.get("sma50")):
            regime = "trending"
        elif autocorr is not None and autocorr < -0.05:
            regime = "mean-reverting"
        else:
            regime = "trending" if ret63 is not None and ret63 > 0 else "mean-reverting"
        return {
            "regime": regime,
            "return_1m": ret21,
            "return_3m": ret63,
            "annualized_volatility_pct": safe_round(vol, 2),
            "drawdown_6m_pct": safe_round(drawdown, 2),
            "return_autocorr_63d": safe_round(autocorr, 3),
            "rsi14": technicals.get("rsi14"),
            "half_life_days": technicals.get("half_life_days"),
        }

    def _scan_price_regime(self, *_):
        rows = []
        portfolio_frame = self._portfolio_return_frame("1y")
        for ticker in config.TICKERS:
            prices = portfolio_frame["prices"].get(ticker)
            if prices is None:
                prices = self.market.prices(ticker, period="1y")
            rows.append({"ticker": ticker, **self._classify_price_regime(prices)})
        port_returns = portfolio_frame["portfolio_returns"]
        port_prices = (1 + port_returns).cumprod() if not port_returns.empty else pd.Series(dtype=float)
        overall = self._classify_price_regime(port_prices)
        counts = {}
        for row in rows:
            counts[row["regime"]] = counts.get(row["regime"], 0) + 1
        commentary = {
            "trending": "Trend regime means recent gains are organized; use for risk budgeting and staged rebalancing context.",
            "mean-reverting": "Mean-reverting regime means position behavior is choppy; avoid over-interpreting short windows.",
            "volatile": "Volatile regime means sizing and drawdown survival matter more than near-term return estimates.",
            "risk-off": "Risk-off regime means correlations and liquidity assumptions should be tightened.",
        }.get(overall.get("regime"), "Regime classification is based on free price data and should be treated as context.")
        return clean_json({
            "overall": overall,
            "items": rows,
            "counts": counts,
            "method": "Rule-based robust fallback using return trend, realized volatility, drawdown, autocorrelation, and technical context.",
            "commentary": commentary,
            "disclaimer": "Risk-context regime detector only. It does not create buy/sell signals.",
        })

    def _severity(self, score: float):
        score = safe_float(score, 0)
        return "Critical" if score >= 85 else "High" if score >= 65 else "Medium" if score >= 35 else "Low"

    def _grade_from_score(self, score: float, invert=False):
        score = safe_float(score, 0)
        if invert:
            score = 100 - score
        return "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "F"

    def _scan_investment_committee(self, *_):
        macro = self._scan_macro_regime()
        policy = self._scan_policy_allocation()
        drawdown = self._scan_drawdown_survival()
        liquidity = self._scan_liquidity_concentration()
        ladder = self._scan_liquidity_ladder()
        risk_contrib = self._scan_risk_contribution()
        factors = self._scan_factor_exposure()
        corr = self._scan_correlation_regime()
        attribution = self._scan_benchmark_attribution()
        capital = self._scan_capital_efficiency()
        price_regime = self._scan_price_regime()
        behavioral = self._scan_behavioral({"modules": {"risk_analytics": self._scan_risk(), "fundamentals": self._scan_fundamentals()}})

        holdings = policy.get("holdings", [])
        breaches = policy.get("breaches", [])
        top_weight = safe_float(liquidity.get("top1_weight_pct"), 0)
        top3 = safe_float(liquidity.get("top3_weight_pct"), 0)
        avg_corr = safe_float(corr.get("average_pairwise_corr_90d"), 0)
        active_return = safe_float(attribution.get("active_return_pct"), 0)
        tracking_error = safe_float(attribution.get("tracking_error_pct"), 0)
        macro_regime = macro.get("primary_regime", "neutral")
        price_regime_label = (price_regime.get("overall") or {}).get("regime", "unknown")

        factor_top = (factors.get("factor_contributions") or [{}])[0]
        factor_score = min(abs(safe_float(factor_top.get("risk_contribution_pct"), 0)) * 1.2, 100)
        concentration_score = min(max(top_weight * 1.7, top3 * 0.9), 100)
        diversification_score = min(avg_corr * 100 + max(len(corr.get("clusters", [])) - 1, 0) * 8, 100)
        liquidity_score = {"A": 10, "B": 25, "C": 45, "D": 70, "F": 90}.get(ladder.get("portfolio_liquidity_grade"), 50)
        drawdown_rows = drawdown.get("scenarios", [])
        worst_drawdown = max([safe_float(r.get("portfolio_loss_pct"), 0) for r in drawdown_rows] or [0])
        drawdown_score = min(worst_drawdown * 1.7, 100)
        macro_score = 75 if macro_regime in {"risk_off", "slowdown", "inflation_pressure"} else 35 if macro_regime == "neutral" else 20
        regime_score = 75 if price_regime_label in {"risk-off", "volatile"} else 40 if price_regime_label == "mean-reverting" else 20
        allocation_score = min(len(breaches) * 25 + max(top_weight - config.PORTFOLIO_POLICY["max_single_stock_weight"] * 100, 0) * 1.4, 100)

        priority_items = [
            {"category": "concentration risk", "score": safe_round(concentration_score, 1), "severity": self._severity(concentration_score), "reason": f"Top holding weight {top_weight:.1f}% and top three weight {top3:.1f}%."},
            {"category": "factor crowding", "score": safe_round(factor_score, 1), "severity": self._severity(factor_score), "reason": f"Dominant factor: {factor_top.get('factor', 'unknown')} via {factor_top.get('proxy', '--')}."},
            {"category": "macro mismatch", "score": safe_round(macro_score, 1), "severity": self._severity(macro_score), "reason": f"Macro regime is {macro_regime}; posture: {macro.get('allocation_posture', '--')}"},
            {"category": "liquidity deterioration", "score": safe_round(liquidity_score, 1), "severity": self._severity(liquidity_score), "reason": f"Liquidity grade {ladder.get('portfolio_liquidity_grade', '--')}."},
            {"category": "drawdown vulnerability", "score": safe_round(drawdown_score, 1), "severity": self._severity(drawdown_score), "reason": f"Worst modeled portfolio loss {worst_drawdown:.1f}%."},
            {"category": "allocation breaches", "score": safe_round(allocation_score, 1), "severity": self._severity(allocation_score), "reason": f"{len(breaches)} policy breach(es) detected."},
            {"category": "regime instability", "score": safe_round(regime_score, 1), "severity": self._severity(regime_score), "reason": f"Portfolio price regime is {price_regime_label}."},
        ]
        priority_items = sorted(priority_items, key=lambda r: safe_float(r.get("score"), 0), reverse=True)

        target_single = config.PORTFOLIO_POLICY["max_single_stock_weight"] * 100
        drift_rows = []
        for row in holdings:
            current = safe_float(row.get("weight_pct"), 0)
            target = min(target_single, 100 / max(len(holdings), 1))
            drift = current - target
            pressure = min(abs(drift) * 3.0, 100)
            drift_rows.append({
                "ticker": row.get("ticker"),
                "current_allocation_pct": safe_round(current, 2),
                "target_policy_pct": safe_round(target, 2),
                "drift_pct": safe_round(drift, 2),
                "rebalance_pressure_score": safe_round(pressure, 1),
                "status": "above policy band" if drift > 5 else "below policy band" if drift < -5 else "inside policy band",
            })
        drift_rows = sorted(drift_rows, key=lambda r: abs(safe_float(r.get("drift_pct"), 0)), reverse=True)

        factor_stress = self._scan_factor_linked_stress(factors=factors, attribution=attribution)
        factor_shocks = factor_stress.get("items", [])
        worst_factor_stress = factor_stress.get("worst_scenario") or {}
        survival_raw = 100 - (drawdown_score * 0.35 + concentration_score * 0.25 + liquidity_score * 0.20 + diversification_score * 0.20)
        survival_score = max(min(survival_raw, 100), 0)

        review_queue = []
        for item in priority_items:
            if item["severity"] in {"Critical", "High"}:
                action = {
                    "concentration risk": "concentration review",
                    "factor crowding": "review thesis",
                    "macro mismatch": "macro review",
                    "liquidity deterioration": "monitor only",
                    "drawdown vulnerability": "review thesis",
                    "allocation breaches": "rebalance candidate",
                    "regime instability": "monitor only",
                }.get(item["category"], "monitor only")
                review_queue.append({"review_type": action, "category": item["category"], "severity": item["severity"], "reason": item["reason"]})
        for row in (capital.get("items") or [])[:3]:
            if row.get("label") in {"oversized risk budget", "capital review needed"}:
                review_queue.append({"review_type": "review thesis", "category": row.get("ticker"), "severity": "High" if row.get("label") == "oversized risk budget" else "Medium", "reason": row.get("label")})

        risk_rows = risk_contrib.get("items", [])
        strongest = sorted(holdings, key=lambda r: safe_float(r.get("pnl_pct"), -999), reverse=True)[:3]
        weakest = sorted(holdings, key=lambda r: safe_float(r.get("pnl_pct"), 999))[:3]
        contributors = risk_rows[:3]
        changed = [
            f"Macro regime currently reads {macro_regime}.",
            f"Benchmark active return is {active_return:+.2f}% with tracking error {tracking_error:.2f}%.",
            f"Market context: {self._market_context_line()}",
            f"Largest factor-linked stress is {worst_factor_stress.get('scenario', '--')} with estimated loss {worst_factor_stress.get('estimated_portfolio_loss_pct', '--')}%.",
            f"Top committee issue is {priority_items[0]['category']} at {priority_items[0]['severity']} severity." if priority_items else "No priority items generated.",
        ]
        next_week = [item["category"] for item in priority_items[:3]]
        brief = {
            "macro_regime_summary": macro.get("allocation_posture"),
            "largest_portfolio_risks": [f"{item['category']}: {item['reason']}" for item in priority_items[:4]],
            "largest_contributors_to_risk": [f"{r.get('ticker')} contributes {r.get('volatility_contribution_pct')}% of volatility risk" for r in contributors],
            "strongest_positions": [f"{r.get('ticker')} P&L {r.get('pnl_pct')}%" for r in strongest],
            "weakest_positions": [f"{r.get('ticker')} P&L {r.get('pnl_pct')}%" for r in weakest],
            "concentration_concerns": f"Top holding weight is {top_weight:.1f}%; concentrated positions remain standing committee review items.",
            "liquidity_concerns": f"Liquidity grade is {ladder.get('portfolio_liquidity_grade', '--')}; cash and exit capacity should be checked before risk expansion.",
            "allocation_drift": f"Largest drift is {drift_rows[0]['ticker']} at {drift_rows[0]['drift_pct']} percentage points." if drift_rows else "Allocation drift unavailable.",
            "benchmark_attribution_summary": f"Portfolio active return vs {attribution.get('benchmark', 'benchmark')} is {active_return:+.2f}%.",
            "factor_linked_stress_summary": f"Worst factor-linked planning case is {worst_factor_stress.get('scenario', '--')} with estimated portfolio loss {worst_factor_stress.get('estimated_portfolio_loss_pct', '--')}%.",
            "what_changed_this_week": changed,
            "what_deserves_attention_next_week": next_week,
        }
        cards = {
            "portfolio_risk_grade": self._grade_from_score(100 - survival_score),
            "diversification_grade": self._grade_from_score(diversification_score, invert=True),
            "macro_alignment_score": safe_round(100 - macro_score, 1),
            "concentration_score": safe_round(100 - concentration_score, 1),
            "liquidity_score": safe_round(100 - liquidity_score, 1),
            "behavioral_discipline_score": safe_round(max(0, 100 - safe_float(behavioral.get("avg_score"), 5) * 10), 1),
        }
        return clean_json({
            "weekly_brief": brief,
            "priority_engine": priority_items,
            "allocation_drift": drift_rows,
            "stress_dashboard": {
                "drawdown_simulations": drawdown_rows,
                "factor_shocks": factor_shocks,
                "factor_linked_scenarios": factor_shocks,
                "factor_stress_method_note": factor_stress.get("method_note"),
                "regime_risks": price_regime.get("items", []),
                "survival_score": safe_round(survival_score, 1),
            },
            "decision_queue": review_queue[:12],
            "institutional_summary_cards": cards,
            "disclaimer": "Investment committee outputs are governance prompts and review priorities, not buy/sell recommendations.",
        })

    def _scan_benchmark_attribution(self, *_):
        benchmark = config.PORTFOLIO_POLICY.get("target_benchmark", "SPY")
        frame = self._portfolio_return_frame("1y")
        returns_df = frame["returns"]
        weights = frame["weights"]
        portfolio_returns = frame["portfolio_returns"]
        warnings_list = []
        diagnostics = dict(frame.get("diagnostics") or {})
        if frame.get("excluded_tickers"):
            warnings_list.append(f"Excluded tickers from synthetic history: {', '.join(row.get('ticker', '') for row in frame.get('excluded_tickers', []))}.")
        benchmark_prices = self.market.prices(benchmark, period="1y")
        benchmark_source = getattr(benchmark_prices, "attrs", {}).get("source") if hasattr(benchmark_prices, "attrs") else None
        if benchmark_prices.empty:
            log.warning("Benchmark attribution empty benchmark series from market client: benchmark=%s", benchmark)
            try:
                benchmark_prices = self.yf.prices(benchmark, period="1y")
                benchmark_source = "Yahoo Delayed"
                if benchmark_prices.empty:
                    warnings_list.append(f"Benchmark series unavailable for {benchmark}.")
                    log.warning("Benchmark attribution empty benchmark series after Yahoo fallback: benchmark=%s", benchmark)
                else:
                    warnings_list.append(f"Benchmark used Yahoo delayed fallback for {benchmark}.")
            except Exception as exc:
                log.exception("Benchmark attribution Yahoo fallback failed for %s: %s", benchmark, exc)
                warnings_list.append(f"Benchmark fallback failed for {benchmark}.")
        benchmark_returns = benchmark_prices.pct_change().dropna() if not benchmark_prices.empty else pd.Series(dtype=float)
        diagnostics["benchmark_history_len"] = len(benchmark_returns)
        if portfolio_returns.empty:
            log.warning("Benchmark attribution empty return series: portfolio_returns length=0 returns_cols=%s", list(returns_df.columns))
            diagnostics.update({
                "aligned_len": 0,
                "data_method": "synthetic_shares_x_close",
                "regression_window": 0,
                "pca_observations": None,
            })
            return clean_json({
                "status": "partial",
                "benchmark": benchmark,
                "benchmark_source": benchmark_source or "Unavailable",
                "reason": "Insufficient portfolio return history for benchmark attribution.",
                "warnings": list(dict.fromkeys(warnings_list + ["Insufficient portfolio return history for benchmark attribution."])),
                "diagnostics": diagnostics,
                "portfolio_history_len": diagnostics.get("portfolio_history_len"),
                "benchmark_history_len": diagnostics.get("benchmark_history_len"),
                "aligned_len": diagnostics.get("aligned_len"),
                "excluded_tickers": diagnostics.get("excluded_tickers", []),
                "data_method": diagnostics.get("data_method"),
                "regression_window": diagnostics.get("regression_window"),
                "pca_observations": diagnostics.get("pca_observations"),
                "items": [],
                "summary_buckets": [],
                "commentary": ["Benchmark attribution is degraded because portfolio return history is unavailable."],
                "disclaimer": "Benchmark attribution is approximate and intended for wealth-management review, not trading performance claims.",
            })
        portfolio_total_simple = (1 + portfolio_returns.dropna()).prod() - 1 if len(portfolio_returns.dropna()) else None
        benchmark_total_simple = (1 + benchmark_returns.dropna()).prod() - 1 if len(benchmark_returns.dropna()) else None
        simple_active = portfolio_total_simple - benchmark_total_simple if portfolio_total_simple is not None and benchmark_total_simple is not None else None
        try:
            aligned = pd.concat([portfolio_returns.rename("portfolio"), benchmark_returns.rename("benchmark")], axis=1).dropna()
            diagnostics["aligned_len"] = len(aligned)
        except Exception as exc:
            log.exception("Benchmark attribution alignment failed for %s: %s", benchmark, exc)
            aligned = pd.DataFrame()
            warnings_list.append("Benchmark alignment failed.")
            diagnostics["aligned_len"] = 0
        if len(aligned) < 60:
            log.warning(
                "Benchmark attribution alignment insufficient: benchmark=%s aligned_len=%s portfolio_len=%s benchmark_len=%s",
                benchmark, len(aligned), len(portfolio_returns), len(benchmark_returns)
            )
            warnings_list.append("Insufficient aligned benchmark history for full attribution.")
            diagnostics["regression_window"] = len(aligned)
            diagnostics["pca_observations"] = None
            rows = self._position_attribution_rows(frame, weights, benchmark_total_simple)
            return clean_json({
                "status": "partial",
                "benchmark": benchmark,
                "benchmark_source": benchmark_source or "Unavailable",
                "reason": "; ".join(dict.fromkeys(warnings_list)) or "Insufficient portfolio or benchmark history for attribution.",
                "warnings": list(dict.fromkeys(warnings_list)),
                "diagnostics": diagnostics,
                "portfolio_history_len": diagnostics.get("portfolio_history_len"),
                "benchmark_history_len": diagnostics.get("benchmark_history_len"),
                "aligned_len": diagnostics.get("aligned_len"),
                "excluded_tickers": diagnostics.get("excluded_tickers", []),
                "data_method": diagnostics.get("data_method"),
                "regression_window": diagnostics.get("regression_window"),
                "pca_observations": diagnostics.get("pca_observations"),
                "portfolio_return_pct": safe_round(portfolio_total_simple * 100, 2) if portfolio_total_simple is not None else None,
                "benchmark_return_pct": safe_round(benchmark_total_simple * 100, 2) if benchmark_total_simple is not None else None,
                "active_return_pct": safe_round(simple_active * 100, 2) if simple_active is not None else None,
                "relative_status": "outperforming" if simple_active is not None and simple_active > 0 else "underperforming" if simple_active is not None and simple_active < 0 else "benchmark unavailable",
                "tracking_error_pct": None,
                "information_ratio": None,
                "beta_to_benchmark": None,
                "up_capture_pct": None,
                "down_capture_pct": None,
                "items": rows,
                "summary_buckets": [],
                "method_note": "Simplified attribution due to limited aligned history.",
                "commentary": [
                    "Simplified attribution due to limited aligned history.",
                    "Available position returns and simple active return are shown where possible.",
                ],
                "disclaimer": "Benchmark attribution is approximate and intended for wealth-management review, not trading performance claims.",
            })
        port_total = (1 + aligned["portfolio"]).prod() - 1
        bench_total = (1 + aligned["benchmark"]).prod() - 1
        active = port_total - bench_total
        diagnostics["regression_window"] = len(aligned)
        diagnostics["pca_observations"] = None
        active_daily = aligned["portfolio"] - aligned["benchmark"]
        tracking_error = active_daily.std() * np.sqrt(252)
        information_ratio = active_daily.mean() * 252 / tracking_error if tracking_error else None
        beta = np.cov(aligned["portfolio"], aligned["benchmark"])[0, 1] / np.var(aligned["benchmark"]) if np.var(aligned["benchmark"]) else None
        up = aligned[aligned["benchmark"] > 0]
        down = aligned[aligned["benchmark"] < 0]
        up_capture = (up["portfolio"].mean() / up["benchmark"].mean() * 100) if len(up) and up["benchmark"].mean() else None
        down_capture = (down["portfolio"].mean() / down["benchmark"].mean() * 100) if len(down) and down["benchmark"].mean() else None

        rows = []
        for ticker in returns_df.columns:
            pos_returns = pd.concat([returns_df[ticker].rename("asset"), aligned["benchmark"]], axis=1).dropna()
            if len(pos_returns) < 30:
                continue
            asset_total = (1 + pos_returns["asset"]).prod() - 1
            bench_same = (1 + pos_returns["benchmark"]).prod() - 1
            weight = safe_float(weights.get(ticker), 0)
            contribution = weight * asset_total
            active_contribution = weight * (asset_total - bench_same)
            rows.append({
                "ticker": ticker,
                "weight_pct": safe_round(weight * 100, 2),
                "asset_return_pct": safe_round(asset_total * 100, 2),
                "benchmark_return_pct": safe_round(bench_same * 100, 2),
                "contribution_pct": safe_round(contribution * 100, 2),
                "active_contribution_pct": safe_round(active_contribution * 100, 2),
                "relative_result": "additive" if active_contribution > 0 else "detractive" if active_contribution < 0 else "neutral",
            })
        sector_rows = []
        if rows:
            for bucket, group in pd.DataFrame(rows).groupby("relative_result"):
                sector_rows.append({"bucket": bucket, "active_contribution_pct": safe_round(group["active_contribution_pct"].sum(), 2)})
        commentary = []
        commentary.append(f"Benchmark is {benchmark}; attribution is simplified because exact transaction history is not available.")
        commentary.append("Active return should be interpreted as policy feedback: concentration and factor exposure can dominate benchmark-relative outcomes.")
        commentary.append(f"Market context: {self._market_context_line()}")
        if beta is not None and beta > 1.2:
            commentary.append("Portfolio beta is meaningfully above benchmark; drawdown expectations should be scaled accordingly.")
        return clean_json({
            "status": "partial" if warnings_list else "success",
            "reason": "; ".join(dict.fromkeys(warnings_list)) if warnings_list else "",
            "warnings": list(dict.fromkeys(warnings_list)),
            "diagnostics": diagnostics,
            "portfolio_history_len": diagnostics.get("portfolio_history_len"),
            "benchmark_history_len": diagnostics.get("benchmark_history_len"),
            "aligned_len": diagnostics.get("aligned_len"),
            "excluded_tickers": diagnostics.get("excluded_tickers", []),
            "data_method": diagnostics.get("data_method"),
            "regression_window": diagnostics.get("regression_window"),
            "pca_observations": diagnostics.get("pca_observations"),
            "benchmark": benchmark,
            "benchmark_source": benchmark_source or "Unknown",
            "portfolio_return_pct": safe_round(port_total * 100, 2),
            "benchmark_return_pct": safe_round(bench_total * 100, 2),
            "active_return_pct": safe_round(active * 100, 2),
            "relative_status": "outperforming" if active > 0 else "underperforming" if active < 0 else "in line",
            "tracking_error_pct": safe_round(tracking_error * 100, 2),
            "information_ratio": safe_round(information_ratio, 3),
            "beta_to_benchmark": safe_round(beta, 3),
            "up_capture_pct": safe_round(up_capture, 2),
            "down_capture_pct": safe_round(down_capture, 2),
            "items": sorted(rows, key=lambda r: abs(safe_float(r.get("active_contribution_pct"), 0)), reverse=True),
            "summary_buckets": sector_rows,
            "method_note": "Daily benchmark attribution using aligned portfolio and benchmark return history.",
            "commentary": commentary,
            "disclaimer": "Benchmark attribution is approximate and intended for wealth-management review, not trading performance claims.",
        })

    def _scan_capital_efficiency(self, *_):
        conviction = {row.get("ticker"): row for row in self._scan_conviction_matrix().get("items", [])}
        risk = {row.get("ticker"): row for row in self._scan_risk_contribution().get("items", [])}
        policy = self._scan_policy_allocation()
        rows = []
        for holding in policy.get("holdings", []):
            ticker = holding.get("ticker")
            conv = conviction.get(ticker, {})
            risk_row = risk.get(ticker, {})
            weight = safe_float(holding.get("weight_pct"), 0)
            quality = safe_float(conv.get("fundamental_quality_score"), 50)
            momentum = safe_float(conv.get("momentum_technical_score"), 50)
            risk_contribution = safe_float(risk_row.get("volatility_contribution_pct"), weight)
            pnl = safe_float(holding.get("pnl_pct"), 0)
            concentration_penalty = max(weight - config.PORTFOLIO_POLICY["max_single_stock_weight"] * 100, 0) * 0.8
            risk_penalty = max(risk_contribution - weight, 0) * 0.6
            quality_score = (quality * 0.55 + momentum * 0.25 + max(min(pnl / 5, 10), -10) * 2)
            efficiency = quality_score - concentration_penalty - risk_penalty
            efficiency = max(min(efficiency, 100), 0)
            if efficiency >= 70 and risk_contribution <= weight * 1.2 + 3:
                label = "efficient capital"
            elif weight > config.PORTFOLIO_POLICY["max_single_stock_weight"] * 100 or risk_contribution > weight * 1.25 + 5:
                label = "oversized risk budget"
            elif quality < 45 and momentum < 45:
                label = "capital review needed"
            else:
                label = "acceptable but monitor"
            option = (
                "rebalance toward target" if label == "oversized risk budget" else
                "review thesis and opportunity cost" if label == "capital review needed" else
                "hold within policy range"
            )
            flag = config.SPECIAL_FLAGS.get(ticker, "")
            rows.append({
                "ticker": ticker,
                "capital_efficiency_score": safe_round(efficiency, 1),
                "label": label,
                "decision_option": option,
                "portfolio_weight_pct": safe_round(weight, 2),
                "risk_contribution_pct": safe_round(risk_contribution, 2),
                "quality_score": safe_round(quality, 1),
                "momentum_score": safe_round(momentum, 1),
                "pnl_pct": safe_round(pnl, 2),
                "reason": "Compares quality, momentum durability, concentration, and risk-budget consumption.",
                "flag": flag,
            })
        avg_efficiency = np.mean([safe_float(r.get("capital_efficiency_score"), 0) for r in rows]) if rows else None
        inefficient = [r for r in rows if r.get("label") in {"oversized risk budget", "capital review needed"}]
        return clean_json({
            "items": sorted(rows, key=lambda r: safe_float(r.get("capital_efficiency_score"), 0)),
            "average_efficiency_score": safe_round(avg_efficiency, 1),
            "review_count": len(inefficient),
            "framework": "Quality and conviction per unit of concentration and risk-budget consumption.",
            "commentary": "Capital allocation efficiency is a governance layer: it highlights where portfolio dollars may be over-consuming risk budget or lacking quality support.",
            "disclaimer": "Decision options are policy review prompts, not buy/sell instructions.",
        })

    def _scan_risk(self, *_):
        prices = {}
        returns = {}
        for ticker in config.TICKERS + ["SPY"]:
            s = self.market.prices(ticker, period="2y")
            if s.empty:
                continue
            prices[ticker] = s
            returns[ticker] = s.pct_change().dropna()
        spy = returns.get("SPY", pd.Series(dtype=float))
        stock_metrics = {}
        for ticker in config.TICKERS:
            r = returns.get(ticker, pd.Series(dtype=float))
            p = prices.get(ticker, pd.Series(dtype=float))
            if r.empty:
                stock_metrics[ticker] = {"error": "No yfinance price history"}
                continue
            emp = self.quant.empyrical_metrics(r, spy)
            beta = emp.get("beta_empyrical")
            stock_metrics[ticker] = {
                **emp,
                **self.quant.quantstats_metrics(r),
                **self.quant.ffn_stats(p),
                "beta_manual": self._manual_beta(r, spy),
                "capm_expected_return": safe_round(self.quant.capm_expected_return(beta) * 100 if beta is not None and self.quant.capm_expected_return(beta) is not None else None, 2),
                "var_parametric_1w": safe_round(self.quant.parametric_var(r, 5) * 100 if self.quant.parametric_var(r, 5) is not None else None, 2),
                "var_historical_1w": safe_round(self.quant.historical_var(r, 5) * 100 if self.quant.historical_var(r, 5) is not None else None, 2),
                "cvar_historical_1w": safe_round(self.quant.historical_cvar(r, 5) * 100 if self.quant.historical_cvar(r, 5) is not None else None, 2),
                "kelly_continuous": safe_round(self.quant.continuous_kelly(r), 4),
                "kelly_discrete": safe_round(self.quant.discrete_kelly(r), 4),
                "technicals": self.quant.technicals(p),
                "garch": self.quant.garch_forecast(r),
                "auto_arima": self.quant.auto_arima_forecast(p, steps=5),
            }
        returns_df = pd.DataFrame({k: v for k, v in returns.items() if k in config.TICKERS}).dropna()
        portfolio = {}
        riskfolio = {}
        if not returns_df.empty:
            latest_values = []
            for ticker in returns_df.columns:
                pos = self._position_for(ticker)
                latest_price = safe_float(prices[ticker].iloc[-1])
                latest_values.append(safe_float(pos.get("shares"), 0) * latest_price)
            total_value = sum(latest_values) or 1
            weights = np.array([v / total_value for v in latest_values])
            portfolio = self.quant.portfolio_summary(returns_df, weights)
            portfolio["real_weights"] = {ticker: safe_round(weight * 100, 2) for ticker, weight in zip(returns_df.columns, weights)}
            portfolio["position_values"] = {ticker: safe_round(value) for ticker, value in zip(returns_df.columns, latest_values)}
            portfolio["correlation_matrix"] = clean_json(returns_df.corr().round(3).to_dict())
            riskfolio = self.quant.riskfolio_analysis(returns_df)
        return {"stock_metrics": clean_json(stock_metrics), "portfolio": clean_json(portfolio), "riskfolio": clean_json(riskfolio)}

    def _manual_beta(self, returns: pd.Series, market_returns: pd.Series):
        aligned = pd.concat([returns, market_returns], axis=1).dropna()
        if len(aligned) < 30:
            return None
        var = np.var(aligned.iloc[:, 1])
        return safe_round(np.cov(aligned.iloc[:, 0], aligned.iloc[:, 1])[0, 1] / var if var else None, 3)

    def _scan_congress(self, *_):
        insiders, sec_filings, holders, recs = [], [], [], []
        for ticker in config.TICKERS:
            for item in self.finnhub.insider_transactions(ticker)[:5]:
                row = dict(item)
                row["ticker"] = ticker
                row["source"] = "Finnhub insider transactions"
                insiders.append(row)
            api_filings = self.sec.search_form4(ticker, limit=3)
            if not api_filings:
                api_filings = self.sec.edgar_form4(ticker, self.yf.info(ticker).get("cik"), limit=3)
            for filing in api_filings:
                sec_filings.append({
                    "ticker": ticker,
                    "filed_at": filing.get("filedAt") or filing.get("filed_at"),
                    "form_type": filing.get("formType") or filing.get("form_type"),
                    "company": filing.get("companyName") or filing.get("company"),
                    "url": filing.get("linkToFilingDetails") or filing.get("url"),
                    "source": filing.get("source", "SEC API"),
                })
            fund = self.yf.fundamentals(ticker)
            for row in fund.get("institutional_holders", [])[:5]:
                holder = dict(row)
                holder["ticker"] = ticker
                holders.append(holder)
            fh_recs = self.finnhub.recommendations(ticker)
            if fh_recs:
                row = dict(fh_recs[0])
                row["ticker"] = ticker
                recs.append(row)
        unavailable = not insiders and not sec_filings and not holders
        return {
            "insider_trades": clean_json(insiders[:40]),
            "sec_form4_filings": clean_json(sec_filings[:30]),
            "institutional_holders": clean_json(holders[:40]),
            "analyst_recommendations": clean_json(recs),
            "message": "No free insider data available" if unavailable else "",
        }

    def _scan_signal_action_plan(self, *_):
        rows = []
        fundamentals = self._scan_fundamentals().get("stocks", {})
        weights = {ticker: safe_float(row.get("portfolio_weight_pct"), 0) for ticker, row in fundamentals.items()}
        newsapi_rows = self.newsapi.search(" OR ".join(config.TICKERS), page_size=50)
        tiingo_rows = self.tiingo.news(config.TICKERS, limit=40)
        newsapi_counts = {
            ticker: sum(1 for article in newsapi_rows if ticker in f"{article.get('title', '')} {article.get('description', '')}")
            for ticker in config.TICKERS
        }
        tiingo_counts = {ticker: 0 for ticker in config.TICKERS}
        for item in tiingo_rows:
            for ticker in item.get("tickers", []) or []:
                if ticker in tiingo_counts:
                    tiingo_counts[ticker] += 1
        for ticker in config.TICKERS:
            prices = self.market.prices(ticker, period="8mo")
            technicals = self.quant.technicals(prices)
            fund = fundamentals.get(ticker, {})
            news_count = len(self.finnhub.company_news(ticker, days_back=10)) + newsapi_counts.get(ticker, 0) + tiingo_counts.get(ticker, 0)
            fh_recs = self.finnhub.recommendations(ticker)
            rec = fund.get("analyst_recommendation")
            if fh_recs:
                latest = fh_recs[0]
                rec = f"buy {latest.get('buy', 0)} / hold {latest.get('hold', 0)} / sell {latest.get('sell', 0)}"
            row = self._build_signal_row(ticker, prices, technicals, fund, weights.get(ticker, 0), news_count, rec)
            rows.append(row)
        return {
            "items": clean_json(rows),
            "disclaimer": "Not investment advice. Signals are a rules-based checklist from free market data and may be stale or incomplete.",
            "concentration_warning": next((config.SPECIAL_FLAGS.get(row.get("ticker"), "") for row in rows if config.SPECIAL_FLAGS.get(row.get("ticker"), "")), ""),
        }

    def _period_return(self, prices: pd.Series, days: int):
        if prices.empty or len(prices) <= days:
            return None
        return pct_change(prices.iloc[-1], prices.iloc[-days])

    def _build_signal_row(self, ticker: str, prices: pd.Series, technicals: dict, fund: dict, weight: float, news_count: int, rec: str | None):
        one_m = self._period_return(prices, 21)
        three_m = self._period_return(prices, 63)
        six_m = self._period_return(prices, 126)
        price = safe_float(prices.iloc[-1]) if not prices.empty else safe_float(fund.get("price"))
        sma20 = safe_float(technicals.get("sma20"))
        sma50 = safe_float(technicals.get("sma50"))
        bb_lower = safe_float(technicals.get("bb_lower"))
        bb_upper = safe_float(technicals.get("bb_upper"))
        macd = safe_float(technicals.get("macd"))
        macd_signal = safe_float(technicals.get("macd_signal"))
        rsi = safe_float(technicals.get("rsi14"))
        graham_gap = safe_float(fund.get("vs_graham_pct"))
        pe = safe_float(fund.get("pe"))
        pb = safe_float(fund.get("pb"))
        score = 0
        reasons = []

        for label, value, weight_factor in [("1M", one_m, 0.45), ("3M", three_m, 0.35), ("6M", six_m, 0.25)]:
            if value is None:
                continue
            adj = max(min(value * weight_factor, 18), -18)
            score += adj
            reasons.append(f"{label} momentum {value:+.1f}%")

        if rsi is not None:
            if rsi < 30:
                score += 14
                reasons.append(f"RSI {rsi:.1f} is oversold")
            elif rsi > 70:
                score -= 14
                reasons.append(f"RSI {rsi:.1f} is overbought")
            else:
                reasons.append(f"RSI {rsi:.1f} is neutral")

        if macd is not None and macd_signal is not None:
            if macd > macd_signal:
                score += 8
                reasons.append("MACD is above signal line")
            else:
                score -= 8
                reasons.append("MACD is below signal line")

        if price and bb_lower and bb_upper and bb_upper > bb_lower:
            bb_position = (price - bb_lower) / (bb_upper - bb_lower)
            if bb_position < 0.2:
                score += 10
                reasons.append("Price is near lower Bollinger Band")
            elif bb_position > 0.8:
                score -= 10
                reasons.append("Price is near upper Bollinger Band")
            else:
                reasons.append("Price is inside Bollinger range")
        else:
            bb_position = None

        if price and sma20:
            d20 = pct_change(price, sma20)
            score += max(min((d20 or 0) * 0.4, 8), -8)
            reasons.append(f"Distance from 20D SMA {d20:+.1f}%" if d20 is not None else "20D SMA unavailable")
        else:
            d20 = None
        if price and sma50:
            d50 = pct_change(price, sma50)
            score += max(min((d50 or 0) * 0.25, 8), -8)
            reasons.append(f"Distance from 50D SMA {d50:+.1f}%" if d50 is not None else "50D SMA unavailable")
        else:
            d50 = None

        rec_text = (rec or "").lower()
        if "buy" in rec_text and "sell" not in rec_text:
            score += 6
            reasons.append(f"Analyst trend: {rec}")
        elif "sell" in rec_text or "underperform" in rec_text:
            score -= 8
            reasons.append(f"Analyst trend: {rec}")
        elif rec:
            reasons.append(f"Analyst trend: {rec}")

        if news_count >= 6:
            score += 4
            reasons.append(f"High recent news volume ({news_count})")
        elif news_count == 0:
            score -= 3
            reasons.append("No recent free-news hits")
        else:
            reasons.append(f"Recent news volume {news_count}")

        if graham_gap is not None:
            if graham_gap > 50:
                score -= 12
                reasons.append(f"Trades {graham_gap:+.1f}% vs Graham value")
            elif graham_gap < -20:
                score += 10
                reasons.append(f"Trades {graham_gap:+.1f}% vs Graham value")
            else:
                reasons.append(f"Graham gap {graham_gap:+.1f}%")
        elif pe and pe > 45:
            score -= 8
            reasons.append(f"Elevated PE {pe:.1f}")
        elif pe and pe > 0:
            reasons.append(f"PE {pe:.1f}")
        if pb and pb > 10:
            score -= 5
            reasons.append(f"Elevated PB {pb:.1f}")

        if weight >= 35:
            score -= 25
            reasons.append("Position concentration is extreme")
        elif weight >= 20:
            score -= 12
            reasons.append("Position concentration is high")

        special_flag = config.SPECIAL_FLAGS.get(ticker, "")
        if special_flag:
            score -= 15
            reasons.append(f"Special concentration warning: {special_flag}")

        score = int(max(min(round(score), 100), -100))
        confidence_points = sum(x is not None for x in [one_m, three_m, six_m, rsi, macd, macd_signal, price, sma20, sma50, pe, pb]) + (1 if rec else 0)
        confidence = "High" if confidence_points >= 9 else "Medium" if confidence_points >= 5 else "Low"
        if score >= 35 and weight < 20:
            action = "Accumulate"
        elif score >= 10:
            action = "Hold"
        elif score <= -35:
            action = "Avoid" if weight == 0 else "Trim"
        elif score <= -10:
            action = "Trim" if weight >= 20 else "Watch"
        else:
            action = "Watch"

        return {
            "ticker": ticker,
            "signal_score": score,
            "action": action,
            "confidence": confidence,
            "reasons": reasons[:8],
            "return_1m": one_m,
            "return_3m": three_m,
            "return_6m": six_m,
            "rsi14": technicals.get("rsi14"),
            "macd_trend": "bullish" if macd is not None and macd_signal is not None and macd > macd_signal else "bearish" if macd is not None and macd_signal is not None else None,
            "bollinger_position": safe_round(bb_position, 2),
            "distance_20d_sma": d20,
            "distance_50d_sma": d50,
            "analyst_trend": rec,
            "news_volume": news_count,
            "pe": pe,
            "pb": pb,
            "graham_gap": graham_gap,
            "portfolio_weight_pct": weight,
            "not_investment_advice": True,
        }

    def _scan_conviction_matrix(self, *_):
        fundamentals = self._scan_fundamentals().get("stocks", {})
        signals = {row.get("ticker"): row for row in self._scan_signal_action_plan().get("items", [])}
        spy_prices = self.market.prices("SPY", period="8mo")
        rows = []
        for ticker in config.TICKERS:
            fund = fundamentals.get(ticker, {})
            signal = signals.get(ticker, {})
            prices = self.market.prices(ticker, period="8mo")
            info = self.yf.info(ticker)
            quality = self._fundamental_quality_score(fund, info, signal)
            momentum = self._momentum_quality_score(prices, spy_prices, signal)
            weight = safe_float(fund.get("portfolio_weight_pct"), 0)
            pnl = safe_float(fund.get("total_return_pct"))
            state = "strong" if quality >= 60 and momentum >= 60 else "deteriorating" if quality < 40 or momentum < 40 else "neutral"
            quadrant = (
                "Strong Compounders" if quality >= 50 and momentum >= 50 else
                "Momentum but Expensive" if quality < 50 and momentum >= 50 else
                "Potential Turnarounds" if quality >= 50 and momentum < 50 else
                "Weak / Deteriorating"
            )
            warning = config.SPECIAL_FLAGS.get(ticker, "")
            rows.append({
                "ticker": ticker,
                "fundamental_quality_score": quality,
                "momentum_technical_score": momentum,
                "portfolio_weight_pct": weight,
                "pnl_pct": pnl,
                "signal_score": signal.get("signal_score"),
                "graham_gap": fund.get("vs_graham_pct"),
                "rsi14": signal.get("rsi14"),
                "analyst_consensus": signal.get("analyst_trend") or fund.get("analyst_recommendation"),
                "bubble_state": state,
                "quadrant": quadrant,
                "warning": warning,
                "quality_components": self._quality_components(fund, info, signal),
                "momentum_components": {
                    "return_1m": signal.get("return_1m"),
                    "return_3m": signal.get("return_3m"),
                    "relative_strength_1m_vs_spy": self._relative_strength(prices, spy_prices, 21),
                    "relative_strength_3m_vs_spy": self._relative_strength(prices, spy_prices, 63),
                    "volatility_adjusted_momentum": self._vol_adjusted_momentum(prices),
                    "macd_trend": signal.get("macd_trend"),
                    "bollinger_position": signal.get("bollinger_position"),
                    "distance_20d_sma": signal.get("distance_20d_sma"),
                    "distance_50d_sma": signal.get("distance_50d_sma"),
                },
            })
        return {
            "items": clean_json(rows),
            "quadrants": ["Strong Compounders", "Momentum but Expensive", "Potential Turnarounds", "Weak / Deteriorating"],
            "x_axis": "Fundamental Quality Score",
            "y_axis": "Momentum & Technical Strength Score",
            "bubble_size": "portfolio_weight_pct",
            "concentration_warning": next((row.get("warning", "") for row in rows if row.get("warning")), ""),
            "disclaimer": "Not investment advice. Conviction scores are heuristic and depend on available free data.",
        }

    def _quality_components(self, fund: dict, info: dict, signal: dict):
        revenue_growth = safe_float(fund.get("revenue_growth"))
        earnings_growth = safe_float(fund.get("earnings_growth"))
        pe = safe_float(fund.get("pe"))
        pb = safe_float(fund.get("pb"))
        roe = safe_float(info.get("returnOnEquity"))
        roic = safe_float(info.get("returnOnCapital") or info.get("returnOnAssets"))
        debt_equity = safe_float(info.get("debtToEquity"))
        free_cashflow = safe_float(info.get("freeCashflow") or info.get("operatingCashflow"))
        revenue = safe_float(info.get("totalRevenue"))
        fcf_margin = free_cashflow / revenue * 100 if free_cashflow is not None and revenue else None
        return {
            "graham_gap": fund.get("vs_graham_pct"),
            "pe": pe,
            "pb": pb,
            "pe_relative_to_growth": safe_round(pe / max(revenue_growth or earnings_growth or 0.1, 0.1), 2) if pe else None,
            "revenue_growth": revenue_growth,
            "earnings_growth": earnings_growth,
            "roe": safe_round(roe * 100 if roe is not None and abs(roe) <= 2 else roe, 2),
            "roic": safe_round(roic * 100 if roic is not None and abs(roic) <= 2 else roic, 2),
            "debt_to_equity": debt_equity,
            "free_cash_flow_margin": safe_round(fcf_margin, 2),
            "analyst_consensus": signal.get("analyst_trend") or fund.get("analyst_recommendation"),
        }

    def _fundamental_quality_score(self, fund: dict, info: dict, signal: dict):
        c = self._quality_components(fund, info, signal)
        score = 50
        graham_gap = safe_float(c.get("graham_gap"))
        if graham_gap is not None:
            score += max(min(-graham_gap * 0.18, 16), -16)
        pe = safe_float(c.get("pe"))
        growth = max(safe_float(c.get("revenue_growth"), 0), safe_float(c.get("earnings_growth"), 0))
        if pe and growth > 0:
            score += max(min((growth / pe) * 20, 14), -8)
        elif pe and pe > 50:
            score -= 10
        for key in ["revenue_growth", "earnings_growth"]:
            value = safe_float(c.get(key))
            if value is not None:
                score += max(min(value * 0.18, 12), -10)
        for key in ["roe", "roic"]:
            value = safe_float(c.get(key))
            if value is not None:
                score += max(min(value * 0.2, 10), -10)
        debt_equity = safe_float(c.get("debt_to_equity"))
        if debt_equity is not None:
            score -= max(min((debt_equity - 80) * 0.08, 14), 0) if debt_equity > 80 else 0
        fcf_margin = safe_float(c.get("free_cash_flow_margin"))
        if fcf_margin is not None:
            score += max(min(fcf_margin * 0.35, 12), -10)
        rec = str(c.get("analyst_consensus") or "").lower()
        if "buy" in rec and "sell" not in rec:
            score += 6
        elif "sell" in rec or "underperform" in rec:
            score -= 8
        return int(max(min(round(score), 100), 0))

    def _momentum_quality_score(self, prices: pd.Series, spy_prices: pd.Series, signal: dict):
        score = 50 + safe_float(signal.get("signal_score"), 0) * 0.35
        for key, weight in [("return_1m", 0.25), ("return_3m", 0.18), ("return_6m", 0.1)]:
            value = safe_float(signal.get(key))
            if value is not None:
                score += max(min(value * weight, 10), -10)
        for days, weight in [(21, 0.35), (63, 0.25)]:
            rs = self._relative_strength(prices, spy_prices, days)
            if rs is not None:
                score += max(min(rs * weight, 10), -10)
        vam = self._vol_adjusted_momentum(prices)
        if vam is not None:
            score += max(min(vam * 6, 10), -10)
        rsi = safe_float(signal.get("rsi14"))
        if rsi is not None:
            score += 6 if 45 <= rsi <= 65 else -8 if rsi > 75 else 4 if 30 <= rsi < 45 else -6
        if signal.get("macd_trend") == "bullish":
            score += 7
        elif signal.get("macd_trend") == "bearish":
            score -= 7
        bb_position = safe_float(signal.get("bollinger_position"))
        if bb_position is not None:
            score += 5 if 0.25 <= bb_position <= 0.75 else -6 if bb_position > 0.9 else 2
        for key in ["distance_20d_sma", "distance_50d_sma"]:
            value = safe_float(signal.get(key))
            if value is not None:
                score += max(min(value * 0.18, 6), -6)
        return int(max(min(round(score), 100), 0))

    def _relative_strength(self, prices: pd.Series, benchmark: pd.Series, days: int):
        own = self._period_return(prices, days)
        bench = self._period_return(benchmark, days)
        if own is None or bench is None:
            return None
        return safe_round(own - bench, 2)

    def _vol_adjusted_momentum(self, prices: pd.Series):
        if prices.empty or len(prices) < 63:
            return None
        returns = prices.pct_change().dropna()
        if returns.empty or returns.std() == 0:
            return None
        recent = prices.iloc[-1] / prices.iloc[-63] - 1
        vol = returns.tail(63).std() * np.sqrt(252)
        return safe_round(recent / vol, 3) if vol else None

    def _portfolio_snapshot(self):
        fundamentals = self._scan_fundamentals().get("stocks", {})
        rows = []
        total_value = config.BUYING_POWER
        for ticker, stock in fundamentals.items():
            value = safe_float(stock.get("market_value"), 0)
            total_value += value
            rows.append({
                "ticker": ticker,
                "name": stock.get("name"),
                "sector": stock.get("sector", "Other"),
                "theme": stock.get("theme") or config.THEME_MAP.get(ticker, "Other"),
                "category": stock.get("category") or self._position_for(ticker).get("category", "Single Stock"),
                "benchmark_group": stock.get("benchmark_group") or self._position_for(ticker).get("benchmark_group", "Other"),
                "risk_bucket": stock.get("risk_bucket") or self._position_for(ticker).get("risk_bucket", "Medium"),
                "market_value": value,
                "weight": safe_float(stock.get("portfolio_weight_pct"), 0) / 100,
                "pnl_pct": safe_float(stock.get("total_return_pct")),
                "price": stock.get("price"),
                "volume": safe_float(stock.get("volume")),
                "market_cap": safe_float(stock.get("fmp_backup", {}).get("ratios", {}).get("marketCapTTM")) or safe_float(self.yf.info(ticker).get("marketCap")),
                "source": stock.get("price_source"),
            })
        if total_value:
            for row in rows:
                row["weight"] = row["market_value"] / total_value
        return {"holdings": rows, "total_value": total_value, "cash": config.BUYING_POWER, "cash_weight": config.BUYING_POWER / total_value if total_value else 0}

    def _group_weights(self, rows, key):
        weights = {}
        for row in rows:
            group = row.get(key) or "Other"
            weights[group] = weights.get(group, 0) + safe_float(row.get("weight"), 0)
        return weights

    def _scan_policy_allocation(self, *_):
        snap = self._portfolio_snapshot()
        rows = snap["holdings"]
        policy = config.PORTFOLIO_POLICY
        sector_weights = self._group_weights(rows, "sector")
        theme_weights = self._group_weights(rows, "theme")
        breaches = []
        for row in rows:
            limit = policy["max_single_stock_weight"]
            if row["weight"] > limit:
                breaches.append({"type": "Single stock", "name": row["ticker"], "actual": safe_round(row["weight"] * 100, 2), "limit": safe_round(limit * 100, 2), "severity": "High"})
        for sector, weight in sector_weights.items():
            if weight > policy["max_sector_weight"]:
                breaches.append({"type": "Sector", "name": sector, "actual": safe_round(weight * 100, 2), "limit": safe_round(policy["max_sector_weight"] * 100, 2), "severity": "High"})
        for theme, weight in theme_weights.items():
            if weight > policy["max_theme_weight"]:
                breaches.append({"type": "Theme", "name": theme, "actual": safe_round(weight * 100, 2), "limit": safe_round(policy["max_theme_weight"] * 100, 2), "severity": "High"})
        if snap["cash_weight"] < policy["minimum_cash_allocation"]:
            breaches.append({"type": "Cash", "name": "Cash allocation", "actual": safe_round(snap["cash_weight"] * 100, 2), "limit": safe_round(policy["minimum_cash_allocation"] * 100, 2), "severity": "Medium"})
        return clean_json({
            "policy": policy,
            "total_value": safe_round(snap["total_value"]),
            "holdings": [{**r, "weight_pct": safe_round(r["weight"] * 100, 2)} for r in rows],
            "sector_weights": {k: safe_round(v * 100, 2) for k, v in sector_weights.items()},
            "theme_weights": {k: safe_round(v * 100, 2) for k, v in theme_weights.items()},
            "cash_weight_pct": safe_round(snap["cash_weight"] * 100, 2),
            "breaches": breaches,
            "concentration_warning": next((config.SPECIAL_FLAGS.get(b.get("name"), "") for b in breaches if config.SPECIAL_FLAGS.get(b.get("name"), "")), ""),
        })

    def _shock_result(self, name: str, affected: list[str], shock_pct: float, snap: dict):
        loss = sum(r["market_value"] * abs(shock_pct) for r in snap["holdings"] if r["ticker"] in affected)
        pct_loss = loss / snap["total_value"] if snap["total_value"] else 0
        recovery = pct_loss / (1 - pct_loss) if pct_loss < 1 else np.inf
        return {"scenario": name, "shock_pct": safe_round(shock_pct * 100, 1), "dollar_loss": safe_round(loss), "portfolio_loss_pct": safe_round(pct_loss * 100, 2), "recovery_needed_pct": safe_round(recovery * 100, 2)}

    def _scan_factor_linked_stress(self, snap: dict | None = None, factors: dict | None = None, attribution: dict | None = None, *_):
        snap = snap or self._portfolio_snapshot()
        factors = factors or self._scan_factor_exposure()
        attribution = attribution or self._scan_benchmark_attribution()
        holdings = snap.get("holdings", [])
        total_value = safe_float(snap.get("total_value"), 0)
        factor_rows = {row.get("factor"): row for row in factors.get("factor_contributions", [])}
        proxy_rows = {row.get("factor"): row for row in factors.get("proxy_factor_exposures", [])}

        def holding_match(row, scenario_key):
            sector = str(row.get("sector", "")).lower()
            theme = str(row.get("theme", "")).lower()
            group = str(row.get("benchmark_group", "")).lower()
            ticker = row.get("ticker")
            if scenario_key == "semiconductor":
                return ticker == "SMH" or "semi" in sector or "semiconductor" in theme
            if scenario_key == "ai_infrastructure":
                return "ai infrastructure" in theme or "optical" in sector
            if scenario_key == "growth":
                return "growth" in group or "tech" in group or ticker in {"QQQ", "TTWO"}
            if scenario_key == "rates":
                return "growth" in group or "tech" in group or "ai infrastructure" in theme or "semiconductor" in theme
            if scenario_key in {"market", "volatility"}:
                return True
            return False

        def affected_rows(scenario_key):
            return [row for row in holdings if holding_match(row, scenario_key)]

        def fallback_weight(rows):
            return sum(safe_float(row.get("weight"), 0) for row in rows)

        def factor_beta(factor):
            row = factor_rows.get(factor) or {}
            beta = safe_float(row.get("exposure_beta"))
            return beta if beta is not None else None

        def confidence_for(method, factor=None):
            aligned = safe_float(factors.get("aligned_len"), 0)
            bench_aligned = safe_float(attribution.get("aligned_len"), 0)
            if "benchmark beta" in method and bench_aligned >= 180:
                return "high"
            if "factor regression" in method and aligned >= 180 and factor_beta(factor) is not None:
                return "medium"
            return "low"

        definitions = [
            {"name": "Semiconductors -20%", "shock": -0.20, "shock_label": "Semiconductor proxy shock -20%", "factor": "semiconductor", "proxy": "SMH", "key": "semiconductor"},
            {"name": "AI Infrastructure -30%", "shock": -0.30, "shock_label": "AI infrastructure theme shock -30%", "factor": "ai_infrastructure", "proxy": "Holdings classification", "key": "ai_infrastructure"},
            {"name": "Nasdaq / QQQ -15%", "shock": -0.15, "shock_label": "QQQ proxy shock -15%", "factor": "growth", "proxy": "QQQ", "key": "growth"},
            {"name": "Rates +100bp", "shock": -0.08, "shock_label": "Rates +100bp; TLT duration proxy -8%", "factor": "rates", "proxy": "TLT", "key": "rates"},
            {"name": "Market / SPY -20%", "shock": -0.20, "shock_label": "SPY benchmark shock -20%", "factor": "market", "proxy": "SPY", "key": "market"},
            {"name": "Volatility shock / VIX +40%", "shock": 0.40, "shock_label": "VIX proxy shock +40%", "factor": "volatility", "proxy": "^VIX", "key": "volatility"},
        ]

        rows = []
        for item in definitions:
            affected = affected_rows(item["key"])
            affected_weight = fallback_weight(affected)
            method = "proxy factor exposure from portfolio weights and holdings classification"
            loss_pct = 0.0
            beta = None

            if item["factor"] == "market":
                beta = safe_float(attribution.get("beta_to_benchmark"))
                if beta is not None:
                    loss_pct = max(0, abs(beta * item["shock"]))
                    method = "benchmark beta from aligned synthetic portfolio returns"
                else:
                    loss_pct = affected_weight * abs(item["shock"])
            elif item["factor"] == "volatility":
                beta = factor_beta("volatility")
                if beta is not None:
                    loss_pct = max(0, abs(beta * item["shock"]) * 0.5)
                    method = "factor regression volatility sensitivity, scaled for VIX shock"
                else:
                    loss_pct = affected_weight * 0.08
                    method = "risk-off proxy using portfolio equity weight; VIX proxy unavailable or unstable"
            elif item["factor"] == "ai_infrastructure":
                proxy = next((r for r in proxy_rows.values() if r.get("factor") == "AI Infrastructure exposure"), None)
                proxy_weight = safe_float((proxy or {}).get("exposure_weight_pct"), affected_weight * 100) / 100
                loss_pct = proxy_weight * abs(item["shock"])
            else:
                beta = factor_beta(item["factor"])
                if beta is not None:
                    loss_pct = max(0, abs(beta * item["shock"]))
                    method = "factor regression exposure using synthetic portfolio returns"
                else:
                    loss_pct = affected_weight * abs(item["shock"])

            rows.append({
                "scenario": item["name"],
                "shock_assumption": item["shock_label"],
                "affected_factor_proxy": item["proxy"],
                "estimated_portfolio_loss_pct": safe_round(loss_pct * 100, 2),
                "estimated_dollar_loss": safe_round(total_value * loss_pct),
                "main_affected_holdings": ", ".join(row.get("ticker", "") for row in affected[:8]) or "Portfolio-level proxy",
                "confidence": confidence_for(method, item["factor"]),
                "method_used": method,
            })

        worst = max(rows, key=lambda r: safe_float(r.get("estimated_portfolio_loss_pct"), 0), default={})
        return clean_json({
            "items": rows,
            "worst_scenario": worst,
            "method_note": "Risk-planning estimates using existing portfolio weights, factor exposure, benchmark beta, and holdings classifications. Not investment advice.",
        })

    def _scan_drawdown_survival(self, *_):
        snap = self._portfolio_snapshot()
        rows = snap["holdings"]
        largest = max(rows, key=lambda r: r["weight"], default={}).get("ticker")
        semi = [r["ticker"] for r in rows if "semi" in r.get("sector", "").lower() or r.get("theme") == "Semiconductors"]
        ai = [r["ticker"] for r in rows if r.get("theme") == "AI Infrastructure"]
        scenarios = []
        for shock in [0.10, 0.20, 0.30, 0.40]:
            scenarios.append(self._shock_result(f"{largest} drops {int(shock*100)}%", [largest], shock, snap))
        for shock in [0.20, 0.35, 0.50]:
            scenarios.append(self._shock_result(f"Semiconductor sector drops {int(shock*100)}%", semi, shock, snap))
        scenarios.extend([
            self._shock_result("AI infrastructure theme unwinds", ai, 0.35, snap),
            self._shock_result("2008-style equity shock", [r["ticker"] for r in rows], 0.45, snap),
            self._shock_result("COVID-style shock", [r["ticker"] for r in rows], 0.30, snap),
            self._shock_result("Rates shock", [r["ticker"] for r in rows if r.get("theme") in {"AI Infrastructure", "Semiconductors"}], 0.25, snap),
        ])
        factor_stress = self._scan_factor_linked_stress(snap=snap)
        return {
            "scenarios": clean_json(scenarios),
            "factor_linked_scenarios": factor_stress.get("items", []),
            "factor_stress_method_note": factor_stress.get("method_note"),
            "largest_holding": largest,
            "total_value": safe_round(snap["total_value"]),
        }

    def _series_ytd_return(self, prices: pd.Series):
        if prices.empty:
            return None
        prices = prices.dropna().sort_index()
        if prices.empty:
            return None
        year = datetime.now(timezone.utc).year
        ytd = prices[prices.index >= pd.Timestamp(f"{year}-01-01")]
        if len(ytd) < 2:
            ytd = prices
        if len(ytd) < 2:
            return None
        return pct_change(ytd.iloc[-1], ytd.iloc[0])

    def _portfolio_ytd_return(self):
        frame = self._portfolio_return_frame("1y")
        portfolio_returns = frame.get("portfolio_returns", pd.Series(dtype=float))
        if portfolio_returns.empty:
            return None
        year = datetime.now(timezone.utc).year
        ytd = portfolio_returns[portfolio_returns.index >= pd.Timestamp(f"{year}-01-01")]
        if ytd.empty:
            ytd = portfolio_returns
        return safe_round(((1 + ytd).prod() - 1) * 100)

    def _inflation_ytd_estimate(self):
        rows = self.fred.get_series("CPIAUCSL", limit=24)
        if len(rows) < 2:
            return None
        current_year = datetime.now(timezone.utc).year
        parsed = []
        for row in rows:
            try:
                parsed.append((pd.Timestamp(row["date"]), safe_float(row["value"])))
            except Exception:
                continue
        parsed = [(date, value) for date, value in parsed if value is not None]
        if len(parsed) < 2:
            return None
        parsed.sort(key=lambda item: item[0])
        latest_date, latest_value = parsed[-1]
        start_candidates = [item for item in parsed if item[0].year < current_year]
        start_value = start_candidates[-1][1] if start_candidates else parsed[0][1]
        return pct_change(latest_value, start_value)

    def _benchmark_goal_row(self, goal: dict):
        benchmark = goal.get("benchmark") or config.PORTFOLIO_POLICY.get("target_benchmark", "SPY")
        portfolio_ytd = self._portfolio_ytd_return()
        spy_prices = self.yf.prices(benchmark, period="1y")
        benchmark_source = "yfinance adjusted close"
        if spy_prices.empty:
            spy_prices = self.market.prices(benchmark, period="1y")
            benchmark_source = getattr(spy_prices, "attrs", {}).get("source") or "market price fallback"
        spy_ytd = self._series_ytd_return(spy_prices)
        inflation = self._inflation_ytd_estimate()
        excess = safe_round(portfolio_ytd - spy_ytd) if portfolio_ytd is not None and spy_ytd is not None else None
        real = safe_round(portfolio_ytd - inflation) if portfolio_ytd is not None and inflation is not None else None
        if portfolio_ytd is None or spy_ytd is None or inflation is None:
            status = "Needs review"
        elif portfolio_ytd > spy_ytd and real > 0:
            status = "On track"
        elif portfolio_ytd <= spy_ytd:
            status = "Behind benchmark"
        else:
            status = "Needs review"
        warning = ""
        if status != "On track":
            warning = "Review benchmark-relative progress and risk budget; objective is not a fixed-dollar target."
        return {
            **goal,
            "target_amount": None,
            "current_value": None,
            "progress_pct": None,
            "required_annual_return_pct": None,
            "benchmark": benchmark,
            "portfolio_ytd_return_pct": portfolio_ytd,
            "spy_ytd_return_pct": spy_ytd,
            "cpi_inflation_estimate_pct": inflation,
            "excess_return_vs_spy_pct": excess,
            "real_return_after_inflation_pct": real,
            "status": status,
            "benchmark_source": benchmark_source,
            "risk_mismatch_warning": warning,
        }

    def _scan_goals_based(self, *_):
        snap = self._portfolio_snapshot()
        goals = []
        for goal in config.INVESTMENT_GOALS:
            if goal.get("goal_type") == "benchmark_relative":
                goals.append(self._benchmark_goal_row(goal))
                continue
            target = safe_float(goal.get("target_amount"), 0)
            years = safe_float(goal.get("time_horizon_years"), 1)
            progress = snap["total_value"] / target if target else 0
            required = (target / snap["total_value"]) ** (1 / years) - 1 if snap["total_value"] > 0 and target > snap["total_value"] and years > 0 else 0
            mismatch = ""
            if goal.get("risk_tolerance") == "Low" and self._scan_liquidity_concentration().get("risk_grade") in {"D", "F"}:
                mismatch = "Current concentration is high for a capital-preservation goal."
            goals.append({**goal, "current_value": safe_round(snap["total_value"]), "progress_pct": safe_round(progress * 100, 2), "required_annual_return_pct": safe_round(required * 100, 2), "status": "On track" if not mismatch else "Needs review", "risk_mismatch_warning": mismatch})
        return {"goals": clean_json(goals)}

    def _scan_liquidity_concentration(self, *_):
        snap = self._portfolio_snapshot()
        rows = snap["holdings"]
        weights = sorted([r["weight"] for r in rows], reverse=True)
        hhi = sum(w ** 2 for w in weights)
        sector_weights = self._group_weights(rows, "sector")
        theme_weights = self._group_weights(rows, "theme")
        returns = {}
        for row in rows:
            prices = self.market.prices(row["ticker"], period="1y")
            if not prices.empty:
                returns[row["ticker"]] = prices.pct_change().dropna()
        cluster = None
        if len(returns) >= 2:
            corr = pd.DataFrame(returns).dropna().corr()
            vals = corr.where(~np.eye(len(corr), dtype=bool)).stack()
            cluster = safe_round(vals.mean(), 3) if len(vals) else None
        liquidity = []
        for row in rows:
            dollar_volume = safe_float(row.get("volume"), 0) * safe_float(row.get("price"), 0)
            liquidity.append({"ticker": row["ticker"], "dollar_volume": safe_round(dollar_volume), "market_cap": safe_round(row.get("market_cap")), "liquidity_flag": "Thin/unknown" if dollar_volume < 5_000_000 else "Normal"})
        max_sector = max(list(sector_weights.values()) or [0])
        penalties = (hhi > 0.25) + (weights[0] > 0.35 if weights else False) + (sum(weights[:3]) > 0.75 if weights else False) + (max_sector > 0.55) + (cluster is not None and cluster > 0.65)
        grade = "A" if penalties == 0 else "B" if penalties == 1 else "C" if penalties == 2 else "D" if penalties == 3 else "F"
        return clean_json({
            "risk_grade": grade,
            "hhi": safe_round(hhi, 4),
            "top1_weight_pct": safe_round((weights[0] if weights else 0) * 100, 2),
            "top3_weight_pct": safe_round(sum(weights[:3]) * 100, 2),
            "sector_weights": {k: safe_round(v * 100, 2) for k, v in sector_weights.items()},
            "theme_weights": {k: safe_round(v * 100, 2) for k, v in theme_weights.items()},
            "avg_correlation": cluster,
            "liquidity": liquidity,
        })

    def _scan_portfolio_narrative(self, *_):
        policy = self._scan_policy_allocation()
        liquidity = self._scan_liquidity_concentration()
        macro = self._scan_macro()
        factor_stress = self._scan_factor_linked_stress()
        worst_factor_stress = factor_stress.get("worst_scenario") or {}
        market_context = self._market_context_line()
        holdings = policy.get("holdings", [])
        strongest = sorted(holdings, key=lambda r: safe_float(r.get("pnl_pct"), -999), reverse=True)[:2]
        weakest = sorted(holdings, key=lambda r: safe_float(r.get("pnl_pct"), 999))[:2]
        top_themes = sorted(policy.get("theme_weights", {}).items(), key=lambda kv: kv[1], reverse=True)[:2]
        return {
            "implicit_bet": f"The portfolio is primarily allocated to {', '.join(k for k, _ in top_themes) or 'growth equities'}, with outcomes tied to AI infrastructure capex, semiconductor equipment demand, and multiple discipline.",
            "main_risks": f"The main risks are single-name concentration, theme crowding, high correlation during drawdowns, and sensitivity to rates or AI capex disappointment. Factor-linked stress highlight: {worst_factor_stress.get('scenario', '--')} estimated loss {worst_factor_stress.get('estimated_portfolio_loss_pct', '--')}%.",
            "strongest_holdings": [r.get("ticker") for r in strongest],
            "weakest_holdings": [r.get("ticker") for r in weakest],
            "macro_backdrop": f"Yield curve inverted: {macro.get('yield_curve', {}).get('inverted')}; Doctor Copper signal: {macro.get('doctor_copper', {}).get('signal')}.",
            "what_changed_this_week": f"Market context: {market_context}. Use NewsAPI and company-news headlines as delayed context; price, policy, and risk metrics drive the decision view.",
            "what_to_watch_next": "Watch concentrated position weight, semiconductor correlation, cash buffer, yield-curve normalization, SPY/QQQ/GLD context, factor-linked stress losses, and whether earnings growth validates current valuation.",
            "liquidity_concentration_grade": liquidity.get("risk_grade"),
            "factor_linked_stress": factor_stress.get("items", []),
        }

    def _scan_rebalancing_intelligence(self, *_):
        policy = self._scan_policy_allocation()
        options = []
        for row in policy.get("holdings", []):
            weight = safe_float(row.get("weight_pct"), 0)
            if weight > config.PORTFOLIO_POLICY["max_single_stock_weight"] * 100:
                action = "trim gradually"
                reason = "Position exceeds policy single-stock limit."
                risk = "Concentration can dominate portfolio outcomes."
                tradeoff = "Trimming reduces upside participation if momentum continues."
            elif weight < 5:
                action = "add only if underweight"
                reason = "Small allocation relative to portfolio."
                risk = "Adding increases theme exposure."
                tradeoff = "Wait for valuation and risk budget alignment."
            else:
                action = "hold"
                reason = "Position is inside current policy limits."
                risk = "Holdings can still move together in a selloff."
                tradeoff = "Holding avoids unnecessary turnover."
            options.append({"ticker": row.get("ticker"), "option": action, "reason": reason, "risk": risk, "tradeoff": tradeoff})
        options.append({"ticker": "PORTFOLIO", "option": "rebalance toward target", "reason": "Policy breaches should be evaluated at the portfolio level.", "risk": "Rebalancing too quickly can create timing risk.", "tradeoff": "Gradual moves preserve flexibility."})
        return {"options": options, "disclaimer": "Not investment advice. These are decision options, not buy/sell orders."}

    def _scan_wealth_view(self, *_):
        return clean_json({
            "policy_allocation": self._scan_policy_allocation(),
            "investment_committee": self._scan_investment_committee(),
            "drawdown_survival": self._scan_drawdown_survival(),
            "goals_based": self._scan_goals_based(),
            "market_context": self._scan_market_context(include_news=False),
            "macro_regime": self._scan_macro_regime(),
            "benchmark_attribution": self._scan_benchmark_attribution(),
            "capital_efficiency": self._scan_capital_efficiency(),
            "liquidity_concentration": self._scan_liquidity_concentration(),
            "factor_exposure": self._scan_factor_exposure(),
            "factor_linked_stress": self._scan_factor_linked_stress(),
            "correlation_regime": self._scan_correlation_regime(),
            "liquidity_ladder": self._scan_liquidity_ladder(),
            "risk_contribution": self._scan_risk_contribution(),
            "price_regime": self._scan_price_regime(),
            "portfolio_narrative": self._scan_portfolio_narrative(),
            "rebalancing_intelligence": self._scan_rebalancing_intelligence(),
            "disclaimer": "Not investment advice. This dashboard is a decision-support platform for asset allocation, risk, and goals.",
        })

    def _scan_watchlist(self, *_):
        rows = []
        for item in config.WATCHLIST:
            ticker = item["ticker"]
            market_quote = self.market.quote(ticker, prefer_cache=True)
            fh_quote = self.finnhub.quote(ticker)
            fund = self.yf.fundamentals(ticker)
            prices = self.market.prices(ticker, period="6mo", prefer_cache=True)
            technicals = self.quant.technicals(prices)
            signal = technicals.get("signal")
            rows.append({
                "ticker": ticker,
                "name": item["name"],
                "category": item["category"],
                "price": market_quote.get("price") or fh_quote.get("price"),
                "day_change_pct": market_quote.get("change_pct") or fh_quote.get("change_pct"),
                "price_source": market_quote.get("source") or fh_quote.get("source"),
                "market_session": market_quote.get("market_session") or market_session(),
                "pe": fund.get("pe"),
                "pb": fund.get("pb"),
                "revenue_growth": fund.get("revenue_growth"),
                "rsi14": technicals.get("rsi14"),
                "zscore": technicals.get("zscore"),
                "signal": signal,
                "entry_candidate": signal in {"oversold_entry_candidate", "mean_reversion_watch"},
            })
        return {"items": clean_json(rows), "entry_candidates": [r for r in rows if r.get("entry_candidate")]}

    def _scan_behavioral(self, full_report=None):
        return clean_json(self.behavioral.score(full_report))
