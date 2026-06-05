"""
Portfolio Intelligence — Flask Server
======================================
Run with:  python app.py
Access at: http://localhost:8080

Endpoints:
  GET  /                -> Dashboard UI
  POST /api/scan        -> Full scan
  POST /api/scan/<mod>  -> Single module scan
  GET  /api/status      -> Health check
  GET  /api/last-report -> Most recent cached report
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from html import escape
from io import BytesIO

from flask import Flask, Response, jsonify, request, send_file
from flask_cors import CORS
from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

import config
from engine import IntelEngine, market_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("server")

app = Flask(__name__, static_folder="static")
CORS(app)

DB_FILE = "portfolio_intel.db"
DB_ENGINE = create_engine(f"sqlite:///{DB_FILE}", connect_args={"check_same_thread": False})
Base = declarative_base()
SessionLocal = scoped_session(sessionmaker(bind=DB_ENGINE))


class InvestmentMemo(Base):
    __tablename__ = "investment_memos"

    id = Column(Integer, primary_key=True)
    ticker = Column(String(16), nullable=False, index=True)
    thesis = Column(Text, nullable=False, default="")
    risks = Column(Text, nullable=False, default="")
    catalysts = Column(Text, nullable=False, default="")
    valuation_logic = Column(Text, nullable=False, default="")
    horizon = Column(String(120), nullable=False, default="")
    invalidation_condition = Column(Text, nullable=False, default="")
    confidence_level = Column(String(32), nullable=False, default="Medium")
    created_at = Column(String(40), nullable=False)
    updated_at = Column(String(40), nullable=False)


class DecisionJournalEntry(Base):
    __tablename__ = "decision_journal_entries"

    id = Column(Integer, primary_key=True)
    ticker = Column(String(16), nullable=False, index=True)
    decision_type = Column(String(80), nullable=False, default="")
    reason = Column(Text, nullable=False, default="")
    confidence_level = Column(String(32), nullable=False, default="Medium")
    expected_outcome = Column(Text, nullable=False, default="")
    actual_outcome = Column(Text, nullable=False, default="")
    emotional_state = Column(String(120), nullable=False, default="")
    created_at = Column(String(40), nullable=False)


class Portfolio(Base):
    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, unique=True)
    account_type = Column(String(80), nullable=False, default="Taxable Brokerage")
    owner = Column(String(120), nullable=False, default="Portfolio Owner")
    base_currency = Column(String(8), nullable=False, default="USD")
    risk_profile = Column(String(160), nullable=False, default="")
    time_horizon = Column(String(80), nullable=False, default="")
    created_at = Column(String(40), nullable=False)
    updated_at = Column(String(40), nullable=False)


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    ticker = Column(String(16), nullable=False, index=True)
    name = Column(String(160), nullable=False, default="")
    sector = Column(String(120), nullable=False, default="")
    theme = Column(String(120), nullable=False, default="")
    shares = Column(Float, nullable=False, default=0)
    avg_cost = Column(Float, nullable=False, default=0)
    notes = Column(Text, nullable=False, default="")
    created_at = Column(String(40), nullable=False)
    updated_at = Column(String(40), nullable=False)


class PortfolioGoal(Base):
    __tablename__ = "portfolio_goals"

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    target_amount = Column(Float, nullable=False, default=0)
    time_horizon_years = Column(Float, nullable=False, default=0)
    priority = Column(String(40), nullable=False, default="")
    risk_tolerance = Column(String(40), nullable=False, default="")
    created_at = Column(String(40), nullable=False)


class CashBucket(Base):
    __tablename__ = "cash_buckets"

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    amount = Column(Float, nullable=False, default=0)
    purpose = Column(String(160), nullable=False, default="")
    currency = Column(String(8), nullable=False, default="USD")
    created_at = Column(String(40), nullable=False)
    updated_at = Column(String(40), nullable=False)


Base.metadata.create_all(DB_ENGINE)

engine = IntelEngine()
last_report = None
scan_started_at = None
scan_completed_at = None
loaded_from_cache = False
cache_timestamp = None
scan_running = False
REPORT_FILE = "last_report.json"

CANONICAL_MODULES = [
    "macro_pulse",
    "market_context",
    "portfolio_news",
    "fundamentals",
    "audit_reliability",
    "investment_committee",
    "risk_analytics",
    "macro_regime",
    "benchmark_attribution",
    "capital_efficiency",
    "factor_exposure",
    "correlation_regime",
    "liquidity_ladder",
    "risk_contribution",
    "price_regime",
    "congress_insiders",
    "signal_action_plan",
    "conviction_matrix",
    "policy_allocation",
    "drawdown_survival",
    "goals_based",
    "liquidity_concentration",
    "portfolio_narrative",
    "rebalancing_intelligence",
    "wealth_view",
    "family_office",
    "investment_memos",
    "decision_journal",
    "watchlist_scan",
    "price_audit",
    "behavioral_check",
]

LEGACY_MODULES = {
    "macro": "macro_pulse",
    "market": "market_context",
    "market_context": "market_context",
    "news": "portfolio_news",
    "audit": "audit_reliability",
    "reliability": "audit_reliability",
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
    "family": "family_office",
    "networth": "family_office",
    "memos": "investment_memos",
    "journal": "decision_journal",
    "price": "price_audit",
}


def save_report(report):
    global last_report, scan_completed_at, loaded_from_cache, cache_timestamp
    last_report = report
    scan_completed_at = datetime.now(timezone.utc).isoformat()
    loaded_from_cache = False
    cache_timestamp = scan_completed_at
    try:
        with open(REPORT_FILE, "w") as f:
            json.dump({
                "report": report,
                "timestamp": scan_completed_at,
                "scan_completed_at": scan_completed_at,
                "loaded_from_cache": False,
                "cache_timestamp": cache_timestamp,
            }, f, default=str)
    except Exception as exc:
        log.error("Failed to save report: %s", exc)


def load_cached_report():
    global last_report, scan_completed_at, loaded_from_cache, cache_timestamp
    if not os.path.exists(REPORT_FILE):
        return
    try:
        with open(REPORT_FILE, "r") as f:
            data = json.load(f)
        last_report = data.get("report")
        cache_timestamp = data.get("cache_timestamp") or data.get("scan_completed_at") or data.get("timestamp")
        scan_completed_at = data.get("scan_completed_at") or data.get("timestamp")
        loaded_from_cache = True
        log.info("Loaded cached report from %s", cache_timestamp)
    except Exception as exc:
        log.warning("Could not load cached report: %s", exc)


load_cached_report()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def memo_to_dict(memo):
    return {
        "id": memo.id,
        "ticker": memo.ticker,
        "thesis": memo.thesis,
        "risks": memo.risks,
        "catalysts": memo.catalysts,
        "valuation_logic": memo.valuation_logic,
        "horizon": memo.horizon,
        "invalidation_condition": memo.invalidation_condition,
        "confidence_level": memo.confidence_level,
        "created_at": memo.created_at,
        "updated_at": memo.updated_at,
    }


def journal_to_dict(entry):
    return {
        "id": entry.id,
        "ticker": entry.ticker,
        "decision_type": entry.decision_type,
        "reason": entry.reason,
        "confidence_level": entry.confidence_level,
        "expected_outcome": entry.expected_outcome,
        "actual_outcome": entry.actual_outcome,
        "emotional_state": entry.emotional_state,
        "created_at": entry.created_at,
    }


def clean_text(value, limit=5000):
    return str(value or "").strip()[:limit]


def seed_default_portfolio():
    db = SessionLocal()
    try:
        if db.query(Portfolio).count() > 0:
            return
        now = utc_now_iso()
        portfolio = Portfolio(
            name="Default Portfolio",
            account_type="Taxable Brokerage",
            owner="Portfolio Owner",
            base_currency="USD",
            risk_profile=config.PORTFOLIO_POLICY.get("risk_profile", ""),
            time_horizon=config.PORTFOLIO_POLICY.get("time_horizon", ""),
            created_at=now,
            updated_at=now,
        )
        db.add(portfolio)
        db.flush()
        for pos in config.PORTFOLIO:
            db.add(PortfolioPosition(
                portfolio_id=portfolio.id,
                ticker=pos.get("ticker", "").upper(),
                name=pos.get("name", ""),
                sector=pos.get("sector", ""),
                theme=config.THEME_MAP.get(pos.get("ticker"), "Other"),
                shares=float(pos.get("shares", 0) or 0),
                avg_cost=float(pos.get("avg_cost", 0) or 0),
                notes=pos.get("notes", ""),
                created_at=now,
                updated_at=now,
            ))
        db.add(CashBucket(
            portfolio_id=portfolio.id,
            name="Buying Power",
            amount=float(config.BUYING_POWER or 0),
            purpose="Cash allocation placeholder",
            currency="USD",
            created_at=now,
            updated_at=now,
        ))
        for goal in config.INVESTMENT_GOALS:
            db.add(PortfolioGoal(
                portfolio_id=portfolio.id,
                name=goal.get("name", ""),
                target_amount=float(goal.get("target_amount") or 0),
                time_horizon_years=float(goal.get("time_horizon_years", 0) or 0),
                priority=goal.get("priority", ""),
                risk_tolerance=goal.get("risk_tolerance", ""),
                created_at=now,
            ))
        db.commit()
        log.info("Seeded Default Portfolio from config.py")
    except Exception as exc:
        db.rollback()
        log.exception("Default portfolio seed failed: %s", exc)
    finally:
        db.close()


seed_default_portfolio()


def migrate_default_goals():
    db = SessionLocal()
    try:
        old_rows = db.query(PortfolioGoal).filter(
            (PortfolioGoal.name.like("Grow portfolio%")) | (PortfolioGoal.target_amount == 20_000)
        ).all()
        for row in old_rows:
            db.delete(row)
        default_portfolio = db.query(Portfolio).order_by(Portfolio.id.asc()).first()
        if default_portfolio:
            for goal in config.INVESTMENT_GOALS:
                existing = db.query(PortfolioGoal).filter(
                    PortfolioGoal.portfolio_id == default_portfolio.id,
                    PortfolioGoal.name == goal.get("name", ""),
                ).first()
                if not existing:
                    db.add(PortfolioGoal(
                        portfolio_id=default_portfolio.id,
                        name=goal.get("name", ""),
                        target_amount=float(goal.get("target_amount") or 0),
                        time_horizon_years=float(goal.get("time_horizon_years", 0) or 0),
                        priority=goal.get("priority", ""),
                        risk_tolerance=goal.get("risk_tolerance", ""),
                        created_at=utc_now_iso(),
                    ))
        db.commit()
    except Exception as exc:
        db.rollback()
        log.warning("Goal migration failed: %s", exc)
    finally:
        db.close()


migrate_default_goals()


def portfolio_to_dict(row):
    return {
        "id": row.id,
        "name": row.name,
        "account_type": row.account_type,
        "owner": row.owner,
        "base_currency": row.base_currency,
        "risk_profile": row.risk_profile,
        "time_horizon": row.time_horizon,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def position_to_dict(row, price=None, market_value=None, weight=None):
    return {
        "id": row.id,
        "portfolio_id": row.portfolio_id,
        "ticker": row.ticker,
        "name": row.name,
        "sector": row.sector,
        "theme": row.theme,
        "shares": row.shares,
        "avg_cost": row.avg_cost,
        "notes": row.notes,
        "price": price,
        "market_value": market_value,
        "weight_pct": weight,
    }


def goal_to_dict(row, current_value=0):
    progress = current_value / row.target_amount * 100 if row.target_amount else None
    return {
        "id": row.id,
        "portfolio_id": row.portfolio_id,
        "name": row.name,
        "target_amount": row.target_amount,
        "time_horizon_years": row.time_horizon_years,
        "priority": row.priority,
        "risk_tolerance": row.risk_tolerance,
        "progress_pct": round(progress, 2) if progress is not None else None,
    }


def cash_to_dict(row):
    return {
        "id": row.id,
        "portfolio_id": row.portfolio_id,
        "name": row.name,
        "amount": row.amount,
        "purpose": row.purpose,
        "currency": row.currency,
    }


def family_office_snapshot():
    db = SessionLocal()
    try:
        portfolios = db.query(Portfolio).order_by(Portfolio.id.asc()).all()
        positions = db.query(PortfolioPosition).all()
        cash_rows = db.query(CashBucket).all()
        goals = db.query(PortfolioGoal).all()
        price_map = {}
        stock_data = (last_report or {}).get("modules", {}).get("fundamentals", {}).get("stocks", {})
        for ticker, row in stock_data.items():
            price_map[ticker] = row.get("price")
        portfolio_values = {}
        position_rows = []
        total_value = 0
        for pos in positions:
            price = price_map.get(pos.ticker)
            if price is None:
                cfg = next((p for p in config.PORTFOLIO if p["ticker"] == pos.ticker), {})
                price = cfg.get("avg_cost", pos.avg_cost)
            value = float(pos.shares or 0) * float(price or 0)
            portfolio_values[pos.portfolio_id] = portfolio_values.get(pos.portfolio_id, 0) + value
            total_value += value
            position_rows.append({"row": pos, "price": price, "market_value": value})
        cash_by_portfolio = {}
        total_cash = 0
        for cash in cash_rows:
            cash_by_portfolio[cash.portfolio_id] = cash_by_portfolio.get(cash.portfolio_id, 0) + float(cash.amount or 0)
            total_cash += float(cash.amount or 0)
        net_worth = total_value + total_cash
        portfolio_items = []
        for portfolio in portfolios:
            stock_value = portfolio_values.get(portfolio.id, 0)
            cash_value = cash_by_portfolio.get(portfolio.id, 0)
            portfolio_items.append({
                **portfolio_to_dict(portfolio),
                "stock_value": round(stock_value, 2),
                "cash_value": round(cash_value, 2),
                "total_value": round(stock_value + cash_value, 2),
                "net_worth_weight_pct": round((stock_value + cash_value) / net_worth * 100, 2) if net_worth else None,
            })
        exposure = {}
        for item in position_rows:
            ticker = item["row"].ticker
            exposure[ticker] = exposure.get(ticker, 0) + item["market_value"]
        concentration = [
            {"ticker": ticker, "market_value": round(value, 2), "net_worth_weight_pct": round(value / net_worth * 100, 2) if net_worth else None}
            for ticker, value in sorted(exposure.items(), key=lambda kv: kv[1], reverse=True)
        ]
        goal_items = []
        for goal in goals:
            current = portfolio_values.get(goal.portfolio_id, 0) + cash_by_portfolio.get(goal.portfolio_id, 0)
            goal_items.append(goal_to_dict(goal, current_value=current))
        liquidity = []
        for portfolio in portfolio_items:
            liquidity.append({
                "portfolio": portfolio["name"],
                "cash_value": portfolio["cash_value"],
                "cash_weight_pct": round(portfolio["cash_value"] / portfolio["total_value"] * 100, 2) if portfolio["total_value"] else None,
                "liquidity_note": "Cash bucket available" if portfolio["cash_value"] else "No cash bucket funded",
            })
        return {
            "portfolios": portfolio_items,
            "positions": [position_to_dict(item["row"], item["price"], round(item["market_value"], 2), round(item["market_value"] / net_worth * 100, 2) if net_worth else None) for item in position_rows],
            "cash_buckets": [cash_to_dict(row) for row in cash_rows],
            "goals": goal_items,
            "net_worth": round(net_worth, 2),
            "total_portfolio_value": round(total_value, 2),
            "total_cash": round(total_cash, 2),
            "cash_allocation_pct": round(total_cash / net_worth * 100, 2) if net_worth else None,
            "cross_portfolio_concentration": concentration,
            "liquidity_overview": liquidity,
        }
    finally:
        db.close()


def parse_iso_age_hours(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 2)
    except Exception:
        return None


def file_age_hours(path):
    if not os.path.exists(path):
        return None
    return round((time.time() - os.path.getmtime(path)) / 3600, 2)


def audit_reliability_snapshot():
    report = last_report or {}
    modules = report.get("modules", {}) if isinstance(report, dict) else {}
    fundamentals = modules.get("fundamentals", {}).get("stocks", {})
    price_audit = modules.get("price_audit", {}).get("items", [])
    now = utc_now_iso()
    configured = {
        "Polygon": bool(config.POLYGON_KEY and not config.POLYGON_KEY.startswith("YOUR_")),
        "FRED": bool(config.FRED_KEY and not config.FRED_KEY.startswith("YOUR_")),
        "Tiingo": bool(config.TIINGO_KEY and not config.TIINGO_KEY.startswith("YOUR_")),
        "Finnhub": bool(config.FINNHUB_KEY and not config.FINNHUB_KEY.startswith("YOUR_")),
        "SEC API": bool(config.SEC_API_KEY and not config.SEC_API_KEY.startswith("YOUR_")),
        "FMP": bool(config.FMP_KEY and not config.FMP_KEY.startswith("YOUR_")),
        "NewsAPI": bool(config.NEWSAPI_KEY and not config.NEWSAPI_KEY.startswith("YOUR_")),
        "yfinance": True,
    }
    source_counts = {}
    stale_warnings = []
    for ticker, row in fundamentals.items():
        source = row.get("price_source") or "Unknown"
        source_counts[source] = source_counts.get(source, 0) + 1
        if not row.get("price"):
            stale_warnings.append({"scope": ticker, "warning": "Missing latest price in fundamentals."})
        if not row.get("latest_trading_date"):
            stale_warnings.append({"scope": ticker, "warning": "Missing latest trading date."})
    fallback_rows = []
    polygon_429 = []
    for row in price_audit:
        if row.get("fallback_reason") or row.get("polygon_failure_reason"):
            fallback_rows.append({
                "ticker": row.get("ticker"),
                "source_used": row.get("source_used"),
                "fallback_reason": row.get("fallback_reason"),
                "polygon_failure_reason": row.get("polygon_failure_reason"),
            })
        if row.get("polygon_failure_reason") == "rate_limited":
            polygon_429.append(row.get("ticker"))
    in_memory_429 = sorted(getattr(engine.polygon, "rate_limited_tickers", set()) or [])
    polygon_last_success = getattr(engine.polygon, "last_successful_at", {}) or {}
    last_polygon_timestamp = max(polygon_last_success.values()) if polygon_last_success else None
    provider_rows = [
        {"provider": "Polygon", "configured": configured["Polygon"], "status": "rate_limited" if polygon_429 or in_memory_429 else "available" if configured["Polygon"] else "not_configured", "last_successful_call": last_polygon_timestamp, "notes": f"{len(set(polygon_429 + in_memory_429))} ticker(s) rate limited"},
        {"provider": "Tiingo", "configured": configured["Tiingo"], "status": "available" if configured["Tiingo"] else "not_configured", "last_successful_call": scan_completed_at if modules.get("portfolio_news") or any(r.get("source_used") == "Tiingo" for r in price_audit) else None, "notes": "Fallback pricing/news provider"},
        {"provider": "Yahoo/yfinance", "configured": True, "status": "fallback_available", "last_successful_call": scan_completed_at if fundamentals else None, "notes": "Fundamentals and final delayed fallback"},
        {"provider": "FRED", "configured": configured["FRED"], "status": "available" if configured["FRED"] and modules.get("macro_pulse") else "not_configured" if not configured["FRED"] else "not_checked", "last_successful_call": scan_completed_at if modules.get("macro_pulse") else None, "notes": "Macro series"},
        {"provider": "Finnhub", "configured": configured["Finnhub"], "status": "available" if configured["Finnhub"] else "not_configured", "last_successful_call": scan_completed_at if modules.get("portfolio_news") else None, "notes": "Free company news/recommendations where available"},
        {"provider": "NewsAPI", "configured": configured["NewsAPI"], "status": "available" if configured["NewsAPI"] else "not_configured", "last_successful_call": scan_completed_at if modules.get("portfolio_news") else None, "notes": "General news context"},
        {"provider": "SEC API", "configured": configured["SEC API"], "status": "available" if configured["SEC API"] else "not_configured", "last_successful_call": scan_completed_at if modules.get("congress_insiders") else None, "notes": "Filings; EDGAR fallback exists"},
    ]
    module_rows = []
    report_age = parse_iso_age_hours(scan_completed_at or cache_timestamp)
    for module in CANONICAL_MODULES:
        if module in {"investment_memos", "decision_journal", "family_office", "audit_reliability"}:
            freshness = "local"
            age = None
        else:
            freshness = "fresh" if module in modules and (report_age is None or report_age <= 24) else "stale" if module in modules else "missing"
            age = report_age if module in modules else None
        module_rows.append({"module": module, "status": freshness, "age_hours": age})
    cache_rows = [
        {"cache": REPORT_FILE, "age_hours": file_age_hours(REPORT_FILE), "status": "present" if os.path.exists(REPORT_FILE) else "missing"},
        {"cache": "eod_price_cache.json", "age_hours": file_age_hours("eod_price_cache.json"), "status": "present" if os.path.exists("eod_price_cache.json") else "missing"},
        {"cache": DB_FILE, "age_hours": file_age_hours(DB_FILE), "status": "present" if os.path.exists(DB_FILE) else "missing"},
    ]
    total_expected_prices = len(config.TICKERS)
    priced = sum(1 for row in fundamentals.values() if row.get("price"))
    fallback_penalty = min(len(fallback_rows) * 4, 30)
    stale_penalty = min(len(stale_warnings) * 5, 25)
    missing_module_penalty = min(sum(1 for row in module_rows if row["status"] == "missing") * 2, 20)
    cache_penalty = 10 if report_age is not None and report_age > 48 else 0
    coverage_score = priced / total_expected_prices * 100 if total_expected_prices else 0
    data_quality_score = max(min(coverage_score - fallback_penalty - stale_penalty - missing_module_penalty - cache_penalty, 100), 0)
    return {
        "timestamp": now,
        "data_quality_score": round(data_quality_score, 1),
        "api_status": provider_rows,
        "price_source_coverage": [{"source": k, "count": v, "coverage_pct": round(v / max(total_expected_prices, 1) * 100, 2)} for k, v in source_counts.items()],
        "fallback_reasons": fallback_rows,
        "stale_data_warnings": stale_warnings,
        "last_successful_provider_call": provider_rows,
        "polygon_429_tracking": {"tickers": sorted(set(polygon_429 + in_memory_429)), "count": len(set(polygon_429 + in_memory_429))},
        "cache_age": cache_rows,
        "module_freshness": module_rows,
        "coverage": {"priced_holdings": priced, "expected_holdings": total_expected_prices, "coverage_pct": round(coverage_score, 2)},
        "notes": "Audit center uses local report/cache/provider state. It does not call external APIs.",
    }


def latest_report_or_error():
    if not last_report:
        return None, {"error": "No cached report available. Run a full scan before exporting a report."}
    ensure_current_goal_module(last_report)
    return last_report, None


def ensure_current_goal_module(report):
    modules = (report or {}).setdefault("modules", {})
    goals = modules.get("goals_based", {}).get("goals", [])
    has_old_goal = any(str(goal.get("name", "")).startswith("Grow portfolio") or goal.get("target_amount") == 20_000 for goal in goals)
    has_benchmark_goal = any(goal.get("name") == "Beat inflation + SPY annual return" for goal in goals)
    if has_benchmark_goal and not has_old_goal:
        return
    benchmark = modules.get("benchmark_attribution", {})
    macro = modules.get("macro_pulse", {})
    portfolio_return = benchmark.get("portfolio_return_pct")
    spy_return = benchmark.get("benchmark_return_pct")
    cpi_row = (macro.get("indicators") or {}).get("cpi", {})
    cpi_estimate = cpi_row.get("change")
    excess = round(float(portfolio_return) - float(spy_return), 2) if portfolio_return is not None and spy_return is not None else None
    real = round(float(portfolio_return) - float(cpi_estimate), 2) if portfolio_return is not None and cpi_estimate is not None else None
    if portfolio_return is None or spy_return is None or cpi_estimate is None:
        status = "Needs review"
    elif portfolio_return > spy_return and real > 0:
        status = "On track"
    elif portfolio_return <= spy_return:
        status = "Behind benchmark"
    else:
        status = "Needs review"
    replacement = {
        "name": "Beat inflation + SPY annual return",
        "goal_type": "benchmark_relative",
        "benchmark": "SPY",
        "objective": "Portfolio return should exceed CPI inflation and outperform SPY for the same year.",
        "target_amount": None,
        "time_horizon_years": 1,
        "priority": "High",
        "risk_tolerance": "High",
        "portfolio_ytd_return_pct": portfolio_return,
        "spy_ytd_return_pct": spy_return,
        "cpi_inflation_estimate_pct": cpi_estimate,
        "excess_return_vs_spy_pct": excess,
        "real_return_after_inflation_pct": real,
        "status": status,
        "benchmark_source": "cached benchmark attribution and macro pulse",
        "risk_mismatch_warning": "" if status == "On track" else "Review benchmark-relative progress and risk budget; objective is not a fixed-dollar target.",
    }
    fresh_goals = [goal for goal in goals if not (str(goal.get("name", "")).startswith("Grow portfolio") or goal.get("target_amount") == 20_000)]
    insert_at = 1 if fresh_goals else 0
    fresh_goals[insert_at:insert_at] = [replacement]
    modules["goals_based"] = {"goals": fresh_goals}
    if isinstance(modules.get("wealth_view"), dict):
        modules["wealth_view"]["goals_based"] = modules["goals_based"]


def short(value, limit=240):
    text = str(value if value is not None else "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def report_bundle():
    report, error = latest_report_or_error()
    if error:
        return None, error
    modules = report.get("modules", {})
    audit = audit_reliability_snapshot()
    family = family_office_snapshot()
    db = SessionLocal()
    try:
        memos = [memo_to_dict(row) for row in db.query(InvestmentMemo).order_by(InvestmentMemo.updated_at.desc()).limit(50).all()]
        journal = [journal_to_dict(row) for row in db.query(DecisionJournalEntry).order_by(DecisionJournalEntry.created_at.desc()).limit(50).all()]
    finally:
        db.close()
    return {
        "timestamp": scan_completed_at or cache_timestamp or report.get("timestamp"),
        "scan_started_at": scan_started_at,
        "scan_completed_at": scan_completed_at,
        "cache_timestamp": cache_timestamp,
        "loaded_from_cache": loaded_from_cache,
        "market_session": report.get("market_session"),
        "source_priority": report.get("source_priority", []),
        "modules": modules,
        "market_context": modules.get("market_context", {}),
        "fundamentals": modules.get("fundamentals", {}),
        "portfolio_news": modules.get("portfolio_news", {}),
        "watchlist": modules.get("watchlist_scan", {}),
        "price_regime": modules.get("price_regime", {}),
        "signal_action": modules.get("signal_action_plan", {}),
        "conviction": modules.get("conviction_matrix", {}),
        "macro_pulse": modules.get("macro_pulse", {}),
        "insiders": modules.get("congress_insiders", {}),
        "wealth": modules.get("wealth_view", {}),
        "family": family,
        "committee": modules.get("investment_committee", {}),
        "policy": modules.get("policy_allocation", {}),
        "risk": modules.get("risk_analytics", {}),
        "risk_contribution": modules.get("risk_contribution", {}),
        "factor_exposure": modules.get("factor_exposure", {}),
        "correlation": modules.get("correlation_regime", {}),
        "liquidity_ladder": modules.get("liquidity_ladder", {}),
        "benchmark": modules.get("benchmark_attribution", {}),
        "capital": modules.get("capital_efficiency", {}),
        "macro_regime": modules.get("macro_regime", {}),
        "drawdown": modules.get("drawdown_survival", {}),
        "goals": modules.get("goals_based", {}),
        "rebalancing": modules.get("rebalancing_intelligence", {}),
        "narrative": modules.get("portfolio_narrative", {}),
        "memos": memos,
        "journal": journal,
        "behavioral": modules.get("behavioral_check", {}),
        "price_audit": modules.get("price_audit", {}),
        "audit": audit,
    }, None


def rows_from_dict(mapping, keys):
    rows = []
    for key, value in (mapping or {}).items():
        row = {"ticker": key}
        row.update(value or {})
        rows.append({col: row.get(col) for col in keys})
    return rows


def render_print_table(rows, columns, limit=12):
    if not rows:
        return '<div class="empty-print">No data available.</div>'
    head = "".join(f"<th>{escape(label)}</th>" for _, label in columns)
    body = []
    for row in rows[:limit]:
        body.append("<tr>" + "".join(f"<td>{escape(short(row.get(key), 180))}</td>" for key, _ in columns) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def fmt_list(values, limit=6):
    if not values:
        return []
    if isinstance(values, dict):
        return [f"{k}: {v}" for k, v in list(values.items())[:limit]]
    if isinstance(values, list):
        out = []
        for item in values[:limit]:
            if isinstance(item, dict):
                out.append("; ".join(f"{k}: {v}" for k, v in item.items() if v not in (None, "")))
            else:
                out.append(str(item))
        return out
    return [str(values)]


def report_metadata(bundle):
    source_note = "Loaded from cache" if bundle["loaded_from_cache"] else "Live scan"
    return [
        f"Timestamp: {bundle.get('timestamp') or '--'}",
        f"Scan started: {bundle.get('scan_started_at') or '--'}",
        f"Scan completed: {bundle.get('scan_completed_at') or '--'}",
        f"Data source status: {source_note}",
        f"Cache timestamp: {bundle.get('cache_timestamp') or '--'}",
        f"Market session: {bundle.get('market_session') or '--'}",
        f"Source priority: {', '.join(bundle.get('source_priority') or []) or '--'}",
    ]


def report_layers(bundle):
    fundamentals = bundle["fundamentals"]
    stocks = fundamentals.get("stocks", {})
    holdings = rows_from_dict(stocks, ["ticker", "price", "portfolio_weight_pct", "total_return_pct", "price_source"])
    committee = bundle["committee"]
    brief = committee.get("weekly_brief", {})
    cards = committee.get("institutional_summary_cards", {})
    policy = bundle["policy"]
    family = bundle["family"]
    audit = bundle["audit"]
    wealth = bundle["wealth"]
    narrative = bundle["narrative"]
    market_context = bundle["market_context"]
    market_context_summary = "; ".join(
        f"{row.get('ticker')} daily {row.get('daily_change_pct', '--')}%, YTD {row.get('ytd_return_pct', '--')}%"
        for row in market_context.get("items", [])
    )
    decision_queue = committee.get("decision_queue") or []
    open_reviews = [r for r in decision_queue if str(r.get("severity", "")).lower() in {"critical", "high"}] or decision_queue[:8]
    return [
        {
            "title": "Executive / CIO Layer",
            "sections": [
                {"title": "CIO Brief", "bullets": [
                    f"Macro regime summary: {brief.get('macro_regime_summary', '--')}",
                    f"Largest portfolio risks: {'; '.join(brief.get('largest_portfolio_risks', [])) or '--'}",
                    f"Largest contributors to risk: {'; '.join(brief.get('largest_contributors_to_risk', [])) or '--'}",
                    f"Factor-linked stress: {brief.get('factor_linked_stress_summary', '--')}",
                    f"What changed this week: {'; '.join(brief.get('what_changed_this_week', [])) or '--'}",
                    f"What deserves attention next week: {', '.join(brief.get('what_deserves_attention_next_week', [])) or '--'}",
                ]},
                {"title": "Wealth View summary", "bullets": [
                    f"Net portfolio value: {policy.get('total_value', '--')}",
                    f"Portfolio risk grade: {cards.get('portfolio_risk_grade', '--')}",
                    f"Diversification grade: {cards.get('diversification_grade', '--')}",
                    f"Liquidity score: {cards.get('liquidity_score', '--')}",
                ]},
                {"title": "Family Office summary", "bullets": [
                    f"Net worth: {family.get('net_worth', '--')}",
                    f"Total portfolio value: {family.get('total_portfolio_value', '--')}",
                    f"Total cash: {family.get('total_cash', '--')}",
                    f"Portfolios: {len(family.get('portfolios', []))}",
                ], "table": (family.get("portfolios", []), [("name", "Portfolio"), ("account_type", "Account"), ("total_value", "Total"), ("net_worth_weight_pct", "Net Worth %")])},
                {"title": "Allocation Drift", "table": (committee.get("allocation_drift") or policy.get("breaches", []), [("ticker", "Ticker"), ("current_allocation_pct", "Current %"), ("target_policy_pct", "Target %"), ("drift_pct", "Drift %"), ("rebalance_pressure_score", "Pressure"), ("status", "Status")])},
                {"title": "Stress Dashboard", "bullets": [
                    f"Survival score: {(committee.get('stress_dashboard') or {}).get('survival_score', '--')}",
                    f"Factor-linked stress: {((committee.get('stress_dashboard') or {}).get('factor_stress_method_note') or bundle['drawdown'].get('factor_stress_method_note') or '--')}",
                ], "table": (bundle["drawdown"].get("scenarios", []), [("scenario", "Scenario"), ("shock_pct", "Shock %"), ("dollar_loss", "Dollar Loss"), ("portfolio_loss_pct", "Loss %"), ("recovery_needed_pct", "Recovery %")])},
                {"title": "Factor-Linked Stress Scenarios", "table": (((committee.get("stress_dashboard") or {}).get("factor_linked_scenarios") or bundle["drawdown"].get("factor_linked_scenarios", [])), [("scenario", "Scenario"), ("shock_assumption", "Shock Assumption"), ("affected_factor_proxy", "Factor/Proxy"), ("estimated_portfolio_loss_pct", "Loss %"), ("estimated_dollar_loss", "Dollar Loss"), ("main_affected_holdings", "Affected Holdings"), ("confidence", "Confidence"), ("method_used", "Method")])},
                {"title": "Goals Progress", "table": (bundle["goals"].get("goals", []), [("name", "Goal"), ("objective", "Objective"), ("portfolio_ytd_return_pct", "Portfolio YTD %"), ("spy_ytd_return_pct", "SPY YTD %"), ("cpi_inflation_estimate_pct", "CPI %"), ("excess_return_vs_spy_pct", "Excess vs SPY %"), ("real_return_after_inflation_pct", "Real Return %"), ("status", "Status"), ("risk_mismatch_warning", "Warning")])},
                {"title": "Decision Queue", "table": (decision_queue, [("review_type", "Review Type"), ("category", "Category"), ("severity", "Severity"), ("reason", "Reason")])},
                {"title": "Weekly Narrative", "bullets": [
                    f"Implicit bet: {narrative.get('implicit_bet', '--')}",
                    f"Main risks: {narrative.get('main_risks', '--')}",
                    f"Strongest holdings: {', '.join(narrative.get('strongest_holdings', [])) or '--'}",
                    f"Weakest holdings: {', '.join(narrative.get('weakest_holdings', [])) or '--'}",
                    f"Market context: {market_context_summary or '--'}",
                    f"What to watch next: {narrative.get('what_to_watch_next', '--')}",
                ]},
            ],
        },
        {
            "title": "Risk & Allocation Layer",
            "sections": [
                {"title": "Risk Analytics summary", "bullets": fmt_list(bundle["risk"].get("portfolio_metrics") or bundle["risk"], 8)},
                {"title": "Risk Contribution", "table": (bundle["risk_contribution"].get("items", []), [("ticker", "Ticker"), ("portfolio_weight_pct", "Weight %"), ("volatility_contribution_pct", "Risk %"), ("risk_minus_weight_pct", "Risk - Weight"), ("flag", "Flag")])},
                {"title": "Factor Exposure", "bullets": fmt_list(bundle["factor_exposure"].get("commentary", []), 6), "table": (bundle["factor_exposure"].get("factor_contributions") or bundle["factor_exposure"].get("proxy_factor_exposures", []), [("factor", "Factor"), ("proxy", "Proxy"), ("exposure_beta", "Beta"), ("risk_contribution_pct", "Contribution %"), ("exposure_weight_pct", "Proxy Weight %")])},
                {"title": "Factor-Linked Stress", "bullets": [bundle["drawdown"].get("factor_stress_method_note", "")], "table": (bundle["drawdown"].get("factor_linked_scenarios", []), [("scenario", "Scenario"), ("shock_assumption", "Shock Assumption"), ("affected_factor_proxy", "Factor/Proxy"), ("estimated_portfolio_loss_pct", "Loss %"), ("estimated_dollar_loss", "Dollar Loss"), ("confidence", "Confidence")])},
                {"title": "Correlation Regime", "bullets": [
                    f"Regime: {bundle['correlation'].get('regime', '--')}",
                    f"Average pairwise correlation 30d: {bundle['correlation'].get('average_pairwise_corr_30d', '--')}",
                    f"Average pairwise correlation 90d: {bundle['correlation'].get('average_pairwise_corr_90d', '--')}",
                    f"Diversification collapse risk: {bundle['correlation'].get('diversification_collapse_risk', '--')}",
                ], "table": (bundle["correlation"].get("clusters", []), [("cluster", "Cluster"), ("tickers", "Tickers"), ("average_correlation", "Avg Corr")])},
                {"title": "Liquidity Ladder", "bullets": [f"Portfolio liquidity grade: {bundle['liquidity_ladder'].get('portfolio_liquidity_grade', '--')}"], "table": (bundle["liquidity_ladder"].get("items", []), [("ticker", "Ticker"), ("bucket", "Bucket"), ("avg_dollar_volume", "Avg Dollar Volume"), ("normal_liquidation_days", "Normal Days"), ("stressed_liquidation_days", "Stressed Days")])},
                {"title": "Benchmark Attribution", "bullets": [
                    f"Benchmark: {bundle['benchmark'].get('benchmark', '--')}",
                    f"Active return: {bundle['benchmark'].get('active_return_pct', '--')}",
                    f"Tracking error: {bundle['benchmark'].get('tracking_error_pct', '--')}",
                    f"Information ratio: {bundle['benchmark'].get('information_ratio', '--')}",
                ], "table": (bundle["benchmark"].get("items", []), [("ticker", "Ticker"), ("weight_pct", "Weight %"), ("asset_return_pct", "Asset Return %"), ("active_contribution_pct", "Active Contribution %"), ("relative_result", "Result")])},
                {"title": "Capital Efficiency", "bullets": [f"Average efficiency score: {bundle['capital'].get('average_efficiency_score', '--')}"], "table": (bundle["capital"].get("items", []), [("ticker", "Ticker"), ("capital_efficiency_score", "Score"), ("label", "Label"), ("decision_option", "Decision Option")])},
                {"title": "Macro Regime", "bullets": [
                    f"Primary regime: {bundle['macro_regime'].get('primary_regime', '--')}",
                    f"Allocation posture: {bundle['macro_regime'].get('allocation_posture', '--')}",
                    f"Evidence: {'; '.join(bundle['macro_regime'].get('evidence', [])) or '--'}",
                ], "table": (bundle["macro_regime"].get("indicators", []), [("indicator", "Indicator"), ("latest", "Latest"), ("trend", "Trend"), ("regime_read", "Read")])},
            ],
        },
        {
            "title": "Research & Monitoring Layer",
            "sections": [
                {"title": "Fundamentals summary", "table": (holdings, [("ticker", "Ticker"), ("price", "Price"), ("portfolio_weight_pct", "Weight %"), ("total_return_pct", "P&L %"), ("price_source", "Source")])},
                {"title": "Market Context — Not Portfolio Holdings", "bullets": [market_context.get("disclaimer", "Market context only. Not portfolio holdings. Not investment advice.")], "table": (market_context.get("items", []), [("ticker", "Ticker"), ("official_close", "Official Close"), ("daily_change_pct", "Daily %"), ("ytd_return_pct", "YTD %"), ("source", "Source"), ("latest_trading_date", "Date")])},
                {"title": "Market Context News", "table": ([item for group in market_context.get("news_groups", []) for item in group.get("items", [])], [("group", "Group"), ("source", "Source"), ("date", "Date"), ("badge", "Badge"), ("headline", "Headline")])},
                {"title": "Portfolio News summary", "bullets": [f"Article count: {bundle['portfolio_news'].get('count', '--')}", f"Sources: {', '.join(bundle['portfolio_news'].get('sources', [])) or '--'}"], "table": (bundle["portfolio_news"].get("items", []), [("ticker", "Ticker"), ("source", "Source"), ("date", "Date"), ("headline", "Headline")])},
                {"title": "Watchlist Scan summary", "bullets": [f"Entry candidates: {len(bundle['watchlist'].get('entry_candidates', []))}"], "table": (bundle["watchlist"].get("items", []), [("ticker", "Ticker"), ("category", "Category"), ("price", "Price"), ("rsi14", "RSI"), ("signal", "Signal"), ("entry_candidate", "Candidate")])},
                {"title": "Price Regime", "bullets": [f"Overall regime: {(bundle['price_regime'].get('overall') or {}).get('regime', '--')}"], "table": (bundle["price_regime"].get("items", []), [("ticker", "Ticker"), ("regime", "Regime"), ("volatility_state", "Volatility"), ("commentary", "Commentary")])},
                {"title": "Signal & Action Plan", "table": (bundle["signal_action"].get("items", []), [("ticker", "Ticker"), ("signal_score", "Score"), ("action_label", "Action Label"), ("confidence", "Confidence"), ("reasons", "Reasons")])},
                {"title": "Portfolio Conviction Matrix summary", "table": (bundle["conviction"].get("items", []), [("ticker", "Ticker"), ("fundamental_quality_score", "Quality"), ("momentum_technical_score", "Momentum"), ("portfolio_weight_pct", "Weight %"), ("quadrant", "Quadrant")])},
                {"title": "Macro Pulse", "bullets": [
                    f"10Y-2Y spread: {(bundle['macro_pulse'].get('yield_curve') or {}).get('spread_10y_2y', '--')}",
                    f"10Y-3M spread: {(bundle['macro_pulse'].get('yield_curve') or {}).get('spread_10y_3m', '--')}",
                    f"Doctor Copper signal: {(bundle['macro_pulse'].get('doctor_copper') or {}).get('signal', '--')}",
                ]},
                {"title": "Insiders & Filings", "table": (bundle["insiders"].get("items", []) or bundle["insiders"].get("filings", []), [("ticker", "Ticker"), ("source", "Source"), ("date", "Date"), ("summary", "Summary")])},
            ],
        },
        {
            "title": "Operations & Audit Layer",
            "sections": [
                {"title": "Audit & Reliability", "bullets": [
                    f"Data quality score: {audit.get('data_quality_score', '--')}",
                    f"Price coverage: {(audit.get('coverage') or {}).get('coverage_pct', '--')}%",
                    f"Polygon 429 count: {(audit.get('polygon_429_tracking') or {}).get('count', 0)}",
                    f"Notes: {audit.get('notes', '--')}",
                ]},
                {"title": "Price Audit", "table": (bundle["price_audit"].get("items", []), [("ticker", "Ticker"), ("source_used", "Source Used"), ("fallback_reason", "Fallback Reason"), ("polygon_failure_reason", "Polygon Failure"), ("last_successful_polygon_timestamp", "Last Polygon Success")])},
                {"title": "data freshness", "table": (audit.get("module_freshness", []), [("module", "Module"), ("status", "Status"), ("age_hours", "Age Hours")])},
                {"title": "provider status", "table": (audit.get("api_status", []), [("provider", "Provider"), ("configured", "Configured"), ("status", "Status"), ("last_successful_call", "Last Success"), ("notes", "Notes")])},
                {"title": "fallback reasons", "table": (audit.get("fallback_reasons", []), [("ticker", "Ticker"), ("source_used", "Source Used"), ("fallback_reason", "Fallback Reason"), ("polygon_failure_reason", "Polygon Failure")])},
                {"title": "stale data warnings", "table": (audit.get("stale_data_warnings", []), [("scope", "Scope"), ("warning", "Warning")])},
            ],
        },
        {
            "title": "Knowledge & History Layer",
            "sections": [
                {"title": "Investment Memos summary", "table": (bundle["memos"], [("ticker", "Ticker"), ("confidence_level", "Confidence"), ("horizon", "Horizon"), ("thesis", "Thesis"), ("updated_at", "Updated")])},
                {"title": "Decision Journal summary", "table": (bundle["journal"], [("ticker", "Ticker"), ("decision_type", "Decision"), ("confidence_level", "Confidence"), ("emotional_state", "State"), ("created_at", "Created")])},
                {"title": "Behavioral Check", "bullets": [
                    f"Overall grade: {bundle['behavioral'].get('overall_grade', '--')}",
                    f"Average bias score: {bundle['behavioral'].get('avg_score', '--')}",
                    f"Concentration flag: {bundle['behavioral'].get('concentration_warning', '--')}",
                    f"Devil's advocate: {bundle['behavioral'].get('devils_advocate', '--')}",
                ]},
                {"title": "open review items", "table": (open_reviews, [("review_type", "Review Type"), ("category", "Category"), ("severity", "Severity"), ("reason", "Reason")])},
            ],
        },
    ]


def render_print_section(section):
    parts = [f"<h3>{escape(section['title'])}</h3>"]
    bullets = section.get("bullets") or []
    if bullets:
        parts.append("<ul>" + "".join(f"<li>{escape(short(item, 600))}</li>" for item in bullets if item) + "</ul>")
    if section.get("table"):
        rows, columns = section["table"]
        parts.append(render_print_table(rows, columns, limit=16))
    return "".join(parts)


def printable_report_html(bundle):
    layers = report_layers(bundle)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Weekly Portfolio Report</title>
<style>
  body {{ font-family: Inter, Arial, sans-serif; color:#111; background:#fff; margin: 32px; }}
  h1 {{ font-size: 24px; margin:0 0 4px; }}
  h2 {{ font-size: 15px; margin: 24px 0 8px; border-bottom:1px solid #111; padding-bottom:4px; text-transform:uppercase; }}
  h3 {{ font-size: 12px; margin: 14px 0 5px; }}
  .meta, .foot, .empty-print {{ color:#555; font-size:11px; }}
  ul {{ margin: 4px 0 8px 18px; padding:0; font-size:11px; }}
  li {{ margin: 2px 0; }}
  table {{ width:100%; border-collapse:collapse; font-size:10px; margin-top:8px; }}
  th {{ text-align:left; border-bottom:1px solid #111; padding:5px; background:#eee; }}
  td {{ border-bottom:1px solid #ddd; padding:5px; vertical-align:top; }}
  @media print {{ body {{ margin: 18mm; }} .no-print {{ display:none; }} h2 {{ break-after: avoid; }} table {{ break-inside:auto; }} }}
</style></head><body>
<button class="no-print" onclick="window.print()">Print / Save as PDF</button>
<h1>Weekly Portfolio Report</h1>
<div class="meta">{'<br>'.join(escape(x) for x in report_metadata(bundle))}</div>
{''.join(f"<h2>{escape(layer['title'])}</h2>{''.join(render_print_section(section) for section in layer['sections'])}" for layer in layers)}
<div class="foot">Not investment advice.</div>
</body></html>"""


@app.route("/api/status")
def status():
    return jsonify({
        "status": "ok",
        "scan_running": scan_running,
        "scan_started_at": scan_started_at,
        "scan_completed_at": scan_completed_at,
        "loaded_from_cache": loaded_from_cache,
        "cache_timestamp": cache_timestamp,
        "last_scan": scan_completed_at,
        "market_session": market_session(),
        "portfolio_size": len(config.TICKERS),
        "watchlist_size": len(config.WATCHLIST_TICKERS),
        "modules": CANONICAL_MODULES,
        "port": config.PORT,
    })


@app.route("/api/memos", methods=["GET", "POST"])
def memos():
    db = SessionLocal()
    try:
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            now = utc_now_iso()
            ticker = clean_text(payload.get("ticker"), 16).upper()
            if not ticker:
                return jsonify({"error": "ticker is required"}), 400
            memo = InvestmentMemo(
                ticker=ticker,
                thesis=clean_text(payload.get("thesis")),
                risks=clean_text(payload.get("risks")),
                catalysts=clean_text(payload.get("catalysts")),
                valuation_logic=clean_text(payload.get("valuation_logic")),
                horizon=clean_text(payload.get("horizon"), 120),
                invalidation_condition=clean_text(payload.get("invalidation_condition")),
                confidence_level=clean_text(payload.get("confidence_level"), 32) or "Medium",
                created_at=now,
                updated_at=now,
            )
            db.add(memo)
            db.commit()
            return jsonify({"item": memo_to_dict(memo)}), 201
        rows = db.query(InvestmentMemo).order_by(InvestmentMemo.updated_at.desc()).limit(100).all()
        return jsonify({"items": [memo_to_dict(row) for row in rows]})
    except Exception as exc:
        db.rollback()
        log.exception("Memo API failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


@app.route("/api/journal", methods=["GET", "POST"])
def decision_journal():
    db = SessionLocal()
    try:
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            ticker = clean_text(payload.get("ticker"), 16).upper()
            if not ticker:
                return jsonify({"error": "ticker is required"}), 400
            entry = DecisionJournalEntry(
                ticker=ticker,
                decision_type=clean_text(payload.get("decision_type"), 80),
                reason=clean_text(payload.get("reason")),
                confidence_level=clean_text(payload.get("confidence_level"), 32) or "Medium",
                expected_outcome=clean_text(payload.get("expected_outcome")),
                actual_outcome=clean_text(payload.get("actual_outcome")),
                emotional_state=clean_text(payload.get("emotional_state"), 120),
                created_at=utc_now_iso(),
            )
            db.add(entry)
            db.commit()
            return jsonify({"item": journal_to_dict(entry)}), 201
        rows = db.query(DecisionJournalEntry).order_by(DecisionJournalEntry.created_at.desc()).limit(100).all()
        return jsonify({"items": [journal_to_dict(row) for row in rows]})
    except Exception as exc:
        db.rollback()
        log.exception("Decision journal API failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


@app.route("/api/scan", methods=["POST"])
def full_scan():
    global scan_running, scan_started_at
    if scan_running:
        return jsonify({"error": "Scan already running"}), 409
    scan_started_at = datetime.now(timezone.utc).isoformat()
    scan_running = True

    def run():
        global scan_running
        try:
            report = engine.run_full_scan()
            save_report(report)
        except Exception as exc:
            log.exception("Scan failed: %s", exc)
        finally:
            scan_running = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "scan_started", "scan_started_at": scan_started_at, "message": "Full scan initiated. Poll /api/status or /api/last-report."})


@app.route("/api/scan/<module_id>", methods=["POST"])
def single_scan(module_id):
    canonical = LEGACY_MODULES.get(module_id, module_id)
    if canonical not in CANONICAL_MODULES:
        return jsonify({"error": f"Invalid module. Choose from: {CANONICAL_MODULES}"}), 400
    if canonical == "investment_memos":
        return memos()
    if canonical == "decision_journal":
        return decision_journal()
    if canonical == "family_office":
        return jsonify({"module": canonical, "data": family_office_snapshot(), "timestamp": datetime.now(timezone.utc).isoformat()})
    if canonical == "audit_reliability":
        return jsonify({"module": canonical, "data": audit_reliability_snapshot(), "timestamp": datetime.now(timezone.utc).isoformat()})
    result = engine.run_module(canonical)
    if isinstance(result, dict) and result.get("error"):
        return jsonify({"module": canonical, "data": result, "timestamp": datetime.now(timezone.utc).isoformat()}), 500
    return jsonify({"module": canonical, "data": result, "timestamp": datetime.now(timezone.utc).isoformat()})


@app.route("/api/last-report")
def get_last_report():
    if not last_report:
        return jsonify({"error": "No report available. Run a scan first."}), 404
    ensure_current_goal_module(last_report)
    return jsonify({
        "report": last_report,
        "timestamp": scan_completed_at or cache_timestamp,
        "scan_started_at": scan_started_at,
        "scan_completed_at": scan_completed_at,
        "loaded_from_cache": loaded_from_cache,
        "cache_timestamp": cache_timestamp,
    })


@app.route("/api/portfolio")
def get_portfolio():
    return jsonify({
        "portfolio": config.PORTFOLIO,
        "market_context": getattr(config, "MARKET_CONTEXT", []),
        "watchlist": config.WATCHLIST,
        "polymarket": config.POLYMARKET,
        "prospective": config.PROSPECTIVE,
        "buying_power": config.BUYING_POWER,
        "tax_context": "Tax treatment depends on investor residency and jurisdiction.",
        "special_flags": config.SPECIAL_FLAGS,
    })


@app.route("/api/portfolios")
def get_portfolios():
    return jsonify(family_office_snapshot())


@app.route("/api/family-office")
def get_family_office():
    return jsonify(family_office_snapshot())


@app.route("/api/audit-reliability")
def get_audit_reliability():
    return jsonify(audit_reliability_snapshot())


def add_pdf_table(story, rows, columns, styles, title=None, limit=14):
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors

    if title:
        story.append(Paragraph(title, styles["Heading2"]))
    if not rows:
        story.append(Paragraph("No data available.", styles["Small"]))
        story.append(Spacer(1, 8))
        return
    data = [[label for _, label in columns]]
    for row in rows[:limit]:
        data.append([short(row.get(key), 80) for key, _ in columns])
    table_obj = Table(data, repeatRows=1)
    table_obj.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6E6E6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#999999")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F7F7")]),
    ]))
    story.append(table_obj)
    story.append(Spacer(1, 10))


def add_pdf_section(story, section, styles):
    from reportlab.platypus import Paragraph, Spacer

    story.append(Paragraph(escape(section["title"]), styles["Heading3"]))
    for bullet in section.get("bullets") or []:
        if bullet:
            story.append(Paragraph(f"- {escape(short(bullet, 900))}", styles["Small"]))
    if section.get("table"):
        rows, columns = section["table"]
        add_pdf_table(story, rows, columns, styles, limit=12)
    else:
        story.append(Spacer(1, 4))


def markdown_table(rows, columns, limit=20):
    if not rows:
        return "_No data available._\n"
    headers = [label for _, label in columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows[:limit]:
        vals = []
        for key, _ in columns:
            value = short(row.get(key), 240).replace("\n", " ").replace("|", "\\|")
            vals.append(value)
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def report_markdown(bundle):
    lines = ["# Weekly Portfolio Report", ""]
    lines.extend(f"- {item}" for item in report_metadata(bundle))
    lines.extend(["", "Not investment advice.", ""])
    for layer in report_layers(bundle):
        lines.extend([f"## {layer['title']}", ""])
        for section in layer["sections"]:
            lines.extend([f"### {section['title']}", ""])
            for bullet in section.get("bullets") or []:
                if bullet:
                    lines.append(f"- {short(bullet, 900)}")
            if section.get("bullets"):
                lines.append("")
            if section.get("table"):
                rows, columns = section["table"]
                lines.append(markdown_table(rows, columns))
            lines.append("")
    return "\n".join(lines).strip() + "\n"


@app.route("/api/export-report")
def export_report():
    bundle, error = report_bundle()
    if error:
        return jsonify(error), 404
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except Exception as exc:
        return jsonify({"error": f"reportlab is required for PDF export: {exc}"}), 500

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=0.45 * inch, leftMargin=0.45 * inch, topMargin=0.45 * inch, bottomMargin=0.45 * inch)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="Footer", parent=styles["Small"], textColor=colors.HexColor("#555555")))
    story = []
    story.append(Paragraph("Weekly Portfolio Report", styles["Title"]))
    for item in report_metadata(bundle):
        story.append(Paragraph(escape(item), styles["Small"]))
    story.append(Spacer(1, 8))
    for layer in report_layers(bundle):
        story.append(Paragraph(escape(layer["title"]), styles["Heading2"]))
        for section in layer["sections"]:
            add_pdf_section(story, section, styles)
    story.append(Paragraph("Not investment advice.", styles["Footer"]))
    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name="weekly_portfolio_report.pdf")


@app.route("/api/export-report-md")
def export_report_md():
    bundle, error = report_bundle()
    if error:
        return jsonify(error), 404
    return Response(report_markdown(bundle), mimetype="text/markdown; charset=utf-8")


@app.route("/report/print")
def print_report():
    bundle, error = report_bundle()
    if error:
        return f"<h1>No report available</h1><p>{escape(error['error'])}</p>", 404
    return printable_report_html(bundle)


@app.route("/")
def dashboard():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Weekly Portfolio Intelligence</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {
    --bg: #000000;
    --panel: #050505;
    --row-a: #0A0A0A;
    --row-b: #111111;
    --border: #1D1D1D;
    --text: #E0E0E0;
    --muted: #888888;
    --blue: #4A9EFF;
    --green: #00FF88;
    --red: #FF4444;
    --amber: #FFB020;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; min-height: 100%; background: var(--bg); color: var(--text); }
  body { font-family: Inter, system-ui, sans-serif; }
  button { font: inherit; }
  .shell { width: min(1500px, 100%); margin: 0 auto; padding: 14px; }
  .topbar {
    display: grid; grid-template-columns: 1fr auto; gap: 14px; align-items: center;
    border-bottom: 1px solid var(--border); padding-bottom: 12px;
  }
  h1 {
    margin: 0; font: 800 18px/1.1 Inter, sans-serif; letter-spacing: 0;
    color: #FFFFFF; text-transform: uppercase;
  }
  .subline { margin-top: 6px; font: 500 11px/1.5 "JetBrains Mono", monospace; color: var(--muted); }
  .time-grid { display:flex; flex-wrap:wrap; gap: 6px 12px; margin-top: 8px; font: 600 10px/1.4 "JetBrains Mono", monospace; color: var(--muted); }
  .time-grid span { color: var(--text); }
  .badge { display:inline-flex; align-items:center; gap:6px; border:1px solid var(--border); background:#050505; border-radius:4px; padding:3px 6px; }
  .badge.live { border-color: rgba(0,255,136,.35); color: var(--green); }
  .badge.cache { border-color: rgba(255,176,32,.35); color: var(--amber); }
  .badge.loading { border-color: rgba(255,176,32,.45); color: var(--amber); }
  .mini-spinner { width: 8px; height: 8px; border: 1px solid #463400; border-top-color: var(--amber); border-radius: 999px; animation: spin .8s linear infinite; display:inline-block; }
  .actions { display: flex; gap: 8px; align-items: center; justify-content: flex-end; flex-wrap: wrap; }
  .portfolio-select { min-width: 190px; border-color:#26384E; background:#030A12; color:var(--blue); font-size:11px; text-transform:uppercase; }
  .btn {
    border: 1px solid #1B4F36; background: #001B0F; color: var(--green);
    padding: 10px 13px; border-radius: 4px; cursor: pointer;
    font: 700 11px/1 "JetBrains Mono", monospace; text-transform: uppercase;
  }
  .btn.secondary { border-color: #26384E; background: #06111F; color: var(--blue); }
  .btn:disabled { opacity: .45; cursor: wait; }
  .holdings {
    display: grid; grid-template-columns: repeat(6, minmax(135px, 1fr)); gap: 8px;
    margin: 12px 0;
  }
  .holding {
    border: 1px solid rgba(0,255,136,.26); background: var(--row-a); padding: 9px 10px; border-radius: 4px;
    min-height: 72px;
  }
  .holding.selected { border-color: rgba(0,255,136,.7); box-shadow: 0 0 14px rgba(0,255,136,.16), inset 2px 0 0 var(--green); }
  .warn-badge { display:inline-flex; margin-top:6px; border:1px solid rgba(255,176,32,.45); color:var(--amber); background:#120B00; padding:2px 5px; border-radius:3px; font:700 9px "JetBrains Mono", monospace; text-transform:uppercase; }
  .market-context {
    border: 1px solid #172B3F; background:#03070C; padding: 10px; border-radius: 4px; margin: 0 0 12px;
  }
  .market-context-head {
    display:flex; justify-content:space-between; align-items:center; gap:10px; color:var(--blue);
    font:800 10px "JetBrains Mono", monospace; text-transform:uppercase; margin-bottom:8px;
  }
  .market-context-grid { display:grid; grid-template-columns: repeat(3, minmax(135px, 1fr)); gap:8px; }
  .market-tile { border:1px solid var(--border); background:#050505; border-radius:4px; padding:8px; }
  .market-tile .ticker { font-size:12px; }
  .ticker { display:flex; justify-content:space-between; gap: 8px; align-items:center; font: 700 13px "JetBrains Mono", monospace; }
  .ticker span:last-child { color: var(--muted); font-weight: 500; font-size: 10px; }
  .pnl { margin-top: 8px; font: 700 17px "JetBrains Mono", monospace; }
  .small { margin-top: 4px; color: var(--muted); font: 500 10px/1.4 "JetBrains Mono", monospace; }
  .tabs {
    display: grid; gap: 8px; border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border); padding: 10px 0; margin-bottom: 12px;
  }
  .layer-group {
    border: 1px solid var(--border); background: #030303; border-radius: 4px; overflow: hidden;
  }
  .layer-group.executive {
    background: #05090F;
  }
  .layer-group.active-layer {
    border-color: rgba(74,158,255,.72); box-shadow: inset 3px 0 0 var(--blue), 0 0 14px rgba(74,158,255,.12);
  }
  .layer-head {
    width: 100%; border: 0; background: transparent; color: var(--text); cursor: pointer;
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 10px 12px; text-align: left;
  }
  .layer-title {
    display: flex; align-items: center; gap: 8px;
    color: var(--blue); font: 800 11px "JetBrains Mono", monospace; text-transform: uppercase;
  }
  .layer-summary {
    margin-top: 4px; color: var(--muted); font: 500 10px/1.4 "JetBrains Mono", monospace;
  }
  .layer-meta {
    color: var(--muted); font: 700 10px "JetBrains Mono", monospace; white-space: nowrap;
  }
  .layer-body {
    display: flex; flex-wrap: wrap; gap: 6px; padding: 0 10px 10px;
  }
  .layer-group.collapsed .layer-body { display: none; }
  .tab {
    display: flex; align-items: center; gap: 8px; min-width: fit-content;
    padding: 8px 10px; border: 1px solid var(--border); border-radius: 4px;
    background: #030303; color: var(--muted); cursor: pointer;
    font: 700 10px "JetBrains Mono", monospace; text-transform: uppercase;
  }
  .layer-group.executive .tab { padding: 10px 11px; border-color: #1B324A; }
  .tab.active { color: var(--blue); background: #08101A; border-color: var(--blue); }
  .dot { width: 7px; height: 7px; border-radius: 999px; background: #333; display: inline-block; }
  .dot.loaded { background: var(--green); box-shadow: 0 0 10px rgba(0,255,136,.7); }
  .dot.loading { background: var(--amber); animation: pulse 1s infinite; }
  .dot.partial { background: var(--amber); box-shadow: 0 0 10px rgba(255,176,32,.65); }
  .dot.error { background: var(--red); box-shadow: 0 0 10px rgba(255,68,68,.65); }
  .panel {
    min-height: 430px; border: 1px solid var(--border); background: var(--panel);
    border-radius: 4px; padding: 14px; overflow-x: auto;
  }
  .panel-head { display:flex; justify-content:space-between; gap: 12px; align-items:flex-start; margin-bottom: 12px; }
  .panel-title { margin:0; color: var(--blue); font: 800 15px "JetBrains Mono", monospace; text-transform: uppercase; }
  .panel-note { color: var(--muted); font: 500 11px/1.5 "JetBrains Mono", monospace; }
  .metrics { display:grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 8px; margin: 10px 0 14px; }
  .metric { border:1px solid var(--border); background: var(--row-a); padding: 11px; border-radius: 4px; }
  .metric .label { color: var(--muted); font: 700 10px "JetBrains Mono", monospace; text-transform: uppercase; }
  .metric .value { margin-top: 8px; color: #FFFFFF; font: 800 22px "JetBrains Mono", monospace; }
  table { width:100%; border-collapse: collapse; font-family: "JetBrains Mono", monospace; font-size: 11px; min-width: 760px; }
  th { color: var(--blue); text-align:left; padding: 9px 10px; border-bottom: 1px solid #243548; background: #06101B; text-transform: uppercase; }
  td { padding: 9px 10px; border-bottom: 1px solid #171717; color: var(--text); vertical-align: top; }
  tr:nth-child(odd) td { background: var(--row-a); }
  tr:nth-child(even) td { background: var(--row-b); }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
  .neu { color: var(--blue); }
  .muted { color: var(--muted); }
  .news-list { display:grid; gap: 7px; }
  .news-item { border: 1px solid var(--border); background: var(--row-a); padding: 10px; border-radius: 4px; }
  .news-meta { color: var(--muted); font: 700 10px "JetBrains Mono", monospace; margin-bottom: 5px; }
  .news-title { color: var(--text); font-size: 13px; line-height: 1.35; }
  .detail-panel {
    border: 1px solid var(--border); background: #030303; border-radius: 4px; margin: 10px 0;
  }
  .detail-panel summary {
    cursor: pointer; padding: 10px 11px; color: var(--blue);
    font: 800 11px "JetBrains Mono", monospace; text-transform: uppercase;
  }
  .detail-panel-body { padding: 0 10px 10px; overflow-x: auto; }
  .mini-strip { display:grid; grid-template-columns: repeat(3, minmax(120px, 1fr)); gap: 8px; margin: 10px 0 14px; }
  .conclusion-grid { display:grid; grid-template-columns: repeat(5, minmax(150px, 1fr)); gap: 8px; margin: 10px 0 14px; }
  .conclusion-card { border:1px solid #22364C; background:#04080D; border-radius:4px; padding:11px; min-height:150px; }
  .conclusion-card .label { color:var(--blue); font:800 10px "JetBrains Mono", monospace; text-transform:uppercase; }
  .conclusion-card .value { margin-top:8px; color:#FFF; font:800 19px/1.15 "JetBrains Mono", monospace; white-space: pre-line; }
  .conclusion-card .support { margin-top:5px; color:var(--green); font:800 12px/1.25 "JetBrains Mono", monospace; }
  .conclusion-card .reason { margin-top:8px; color:var(--text); font:500 12px/1.35 Inter, sans-serif; }
  .conclusion-card .action { margin-top:10px; color:var(--amber); font:700 10px/1.35 "JetBrains Mono", monospace; text-transform:uppercase; }
  .filters { display:flex; flex-wrap:wrap; gap: 6px; margin: 10px 0 12px; }
  .filter-btn {
    border:1px solid var(--border); background:#050505; color:var(--muted); border-radius:4px;
    padding:7px 9px; font:700 10px "JetBrains Mono", monospace; cursor:pointer;
  }
  .filter-btn.active { border-color: var(--blue); color: var(--blue); background:#06101B; }
  .chart { display:grid; gap:8px; margin: 10px 0 16px; }
  .bar-row { display:grid; grid-template-columns: 54px 1fr 48px; gap:8px; align-items:center; font:700 11px "JetBrains Mono", monospace; }
  .bar-track { position:relative; height:18px; background:#080808; border:1px solid var(--border); border-radius:4px; overflow:hidden; }
  .bar-zero { position:absolute; left:50%; top:0; bottom:0; width:1px; background:#333; }
  .bar { position:absolute; top:2px; bottom:2px; border-radius:3px; min-width:2px; }
  .plot-wrap { width: 100%; min-height: 520px; border: 1px solid var(--border); background: #020202; border-radius: 4px; }
  .warning { border:1px solid rgba(255,176,32,.35); background:#120B00; color:var(--amber); padding:10px; border-radius:4px; margin:10px 0; font:700 11px/1.45 "JetBrains Mono", monospace; }
  .form-grid { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 10px 0 14px; }
  .form-field { display:grid; gap: 5px; }
  .form-field.full { grid-column: 1 / -1; }
  label { color: var(--muted); font: 700 10px "JetBrains Mono", monospace; text-transform: uppercase; }
  input, select, textarea {
    width:100%; border:1px solid var(--border); background:#030303; color:var(--text);
    border-radius:4px; padding:9px; font:500 12px/1.35 "JetBrains Mono", monospace;
  }
  textarea { min-height: 74px; resize: vertical; }
  .empty { padding: 58px 16px; text-align:center; color: var(--muted); font: 600 12px "JetBrains Mono", monospace; }
  .spinner {
    width: 26px; height: 26px; border-radius: 999px; display:inline-block;
    border: 2px solid #142316; border-top-color: var(--green);
    animation: spin .8s linear infinite, pulse 1.2s ease-in-out infinite;
  }
  .footer {
    color: var(--muted); border-top: 1px solid var(--border); padding: 12px 0 4px;
    margin-top: 12px; text-align:center; font: 600 10px "JetBrains Mono", monospace;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
  @media (max-width: 900px) {
    .topbar { grid-template-columns: 1fr; }
    .actions { justify-content: stretch; }
    .btn { flex: 1; }
    .holdings { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .market-context-grid { grid-template-columns: 1fr; }
    .mini-strip { grid-template-columns: 1fr; }
    .conclusion-grid { grid-template-columns: 1fr; }
    .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  @media (max-width: 520px) {
    .shell { padding: 10px; }
    h1 { font-size: 15px; }
    .holdings { grid-template-columns: 1fr; }
    .metrics { grid-template-columns: 1fr; }
    .form-grid { grid-template-columns: 1fr; }
    .layer-head { align-items: flex-start; }
    .layer-body { display: grid; grid-template-columns: 1fr; }
    .tab { justify-content: flex-start; width: 100%; padding: 10px; font-size: 10px; }
    .panel { padding: 10px; }
  }
</style>
</head>
<body>
<main class="shell">
  <section class="topbar">
    <div>
      <h1>Weekly Portfolio Intelligence</h1>
      <div class="subline" id="subline">Booting dashboard...</div>
      <div class="time-grid">
        <div class="badge">Current local time: <span id="localClock">--</span></div>
        <div class="badge">Last successful scan: <span id="lastSuccessfulScan">--</span></div>
        <div class="badge">Last refreshed/viewed: <span id="lastViewed">--</span></div>
        <div class="badge cache" id="dataSourceBadge">Data source: <span id="dataSource">--</span></div>
      </div>
    </div>
    <div class="actions">
      <select class="portfolio-select" id="portfolioSelector" onchange="selectPortfolio(this.value)"></select>
      <button class="btn secondary" id="exportBtn" onclick="exportWeeklyReport()">Export Weekly Report</button>
      <button class="btn" id="scanBtn" onclick="runFullScan()">Run Full Scan</button>
      <button class="btn secondary" id="refreshBtn" onclick="refreshAndScan()">Refresh Cache</button>
    </div>
  </section>
  <section class="holdings" id="holdings"></section>
  <section class="market-context" id="marketContextStrip"></section>
  <nav class="tabs" id="tabs"></nav>
  <section id="content" class="panel"></section>
  <footer class="footer">Not investment advice · Powered by Polygon + yfinance + FRED + Finnhub + Tiingo + SEC</footer>
</main>

<script>
const MODULE_GROUPS = [
  {
    id: 'executive',
    label: 'Executive / CIO Layer',
    summary: 'Committee brief, wealth view, policy drift, survival risk, goals and decisions.',
    primary: true,
    modules: [
      {id:'investment_committee', label:'CIO Brief'},
      {id:'wealth_view', label:'Wealth View'},
      {id:'family_office', label:'Family Office'},
      {id:'policy_allocation', label:'Allocation Drift'},
      {id:'drawdown_survival', label:'Stress Dashboard'},
      {id:'goals_based', label:'Goals Progress'},
      {id:'rebalancing_intelligence', label:'Decision Queue'},
      {id:'portfolio_narrative', label:'Weekly Narrative'}
    ]
  },
  {
    id: 'risk_allocation',
    label: 'Risk & Allocation Layer',
    summary: 'Portfolio risk, factors, liquidity, benchmark attribution and macro alignment.',
    modules: [
      {id:'risk_analytics', label:'Risk Analytics'},
      {id:'risk_contribution', label:'Risk Contribution'},
      {id:'correlation_regime', label:'Correlation Regime'},
      {id:'liquidity_ladder', label:'Liquidity Ladder'},
      {id:'liquidity_concentration', label:'Liquidity & Concentration'},
      {id:'factor_exposure', label:'Factor Exposure'},
      {id:'benchmark_attribution', label:'Benchmark Attribution'},
      {id:'capital_efficiency', label:'Capital Efficiency'},
      {id:'macro_regime', label:'Macro Regime'}
    ]
  },
  {
    id: 'research_monitoring',
    label: 'Research & Monitoring Layer',
    summary: 'Fundamentals, news, signals, watchlist, regimes and filings.',
    modules: [
      {id:'market_context', label:'Market Context'},
      {id:'portfolio_news', label:'Portfolio News'},
      {id:'fundamentals', label:'Fundamentals'},
      {id:'watchlist_scan', label:'Watchlist Scan'},
      {id:'price_regime', label:'Price Regime'},
      {id:'conviction_matrix', label:'Conviction Matrix'},
      {id:'signal_action_plan', label:'Signal & Action Plan'},
      {id:'macro_pulse', label:'Macro Pulse'},
      {id:'congress_insiders', label:'Insiders & Filings'}
    ]
  },
  {
    id: 'operations_audit',
    label: 'Operations & Audit Layer',
    summary: 'Provider health, price coverage, fallbacks, cache state and data freshness.',
    modules: [
      {id:'audit_reliability', label:'Audit & Reliability'},
      {id:'price_audit', label:'Price Audit'}
    ]
  },
  {
    id: 'knowledge_history',
    label: 'Knowledge & History Layer',
    summary: 'Investment memos, decision journal and behavioral review history.',
    modules: [
      {id:'investment_memos', label:'Investment Memos'},
      {id:'decision_journal', label:'Decision Journal'},
      {id:'behavioral_check', label:'Behavioral Check'}
    ]
  }
];

const MODULES = Array.from(new Map(MODULE_GROUPS.flatMap(g => g.modules).map(m => [m.id, m])).values());
let activeTab = 'investment_committee';
let collapsedLayers = {
  executive: false,
  risk_allocation: true,
  research_monitoring: true,
  operations_audit: true,
  knowledge_history: true
};
let moduleData = {};
let moduleLoading = {};
let portfolio = [];
let portfolioMeta = {};
let scanStartedAt = null;
let scanCompletedAt = null;
let cacheTimestamp = null;
let loadedFromCache = false;
let lastViewedAt = new Date();
let scanInFlight = false;
let newsFilter = 'ALL';
let memoData = [];
let journalData = [];
let familyOfficeData = {};
let auditReliabilityData = {};
let selectedPortfolioId = null;

const fmt = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});
const money = new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', maximumFractionDigits: 2});

function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function clsNum(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 'muted';
  return n > 0 ? 'pos' : n < 0 ? 'neg' : 'neu';
}

function val(v, suffix='') {
  if (v === null || v === undefined || v === '') return '<span class="muted">--</span>';
  if (typeof v === 'number') return `<span class="${clsNum(v)}">${fmt.format(v)}${suffix}</span>`;
  return esc(v);
}

function metric(label, value, suffix='') {
  return `<div class="metric"><div class="label">${esc(label)}</div><div class="value">${val(value, suffix)}</div></div>`;
}

function table(rows, cols) {
  if (!rows || !rows.length) return '<div class="empty">No rows returned.</div>';
  return `<table><thead><tr>${cols.map(c => `<th>${esc(c.label)}</th>`).join('')}</tr></thead><tbody>` +
    rows.map(r => `<tr>${cols.map(c => `<td>${c.render ? c.render(r) : val(r[c.key])}</td>`).join('')}</tr>`).join('') +
    '</tbody></table>';
}

async function init() {
  startLocalClock();
  updateViewedTime();
  await loadPortfolio();
  await loadFamilyOffice();
  await loadAuditReliability();
  await loadDecisionRecords();
  await refreshReport();
  renderTabs();
  renderHoldings();
  renderMarketContextStrip();
  renderContent();
  const s = await fetch('/api/status').then(r=>r.json()).catch(()=>({}));
  updateSubline(s);
}

async function loadPortfolio() {
  portfolioMeta = await fetch('/api/portfolio').then(r=>r.json()).catch(()=>({}));
  portfolio = portfolioMeta.portfolio || [];
}

async function loadDecisionRecords() {
  const memos = await fetch('/api/memos').then(r=>r.json()).catch(()=>({items:[]}));
  const journal = await fetch('/api/journal').then(r=>r.json()).catch(()=>({items:[]}));
  memoData = memos.items || [];
  journalData = journal.items || [];
  moduleData.investment_memos = {items: memoData};
  moduleData.decision_journal = {items: journalData};
}

async function loadFamilyOffice() {
  familyOfficeData = await fetch('/api/family-office').then(r=>r.json()).catch(()=>({portfolios:[]}));
  if (!selectedPortfolioId && (familyOfficeData.portfolios || []).length) {
    selectedPortfolioId = String(familyOfficeData.portfolios[0].id);
  }
  moduleData.family_office = familyOfficeData;
  renderPortfolioSelector();
}

function renderPortfolioSelector() {
  const el = document.getElementById('portfolioSelector');
  if (!el) return;
  const portfolios = familyOfficeData.portfolios || [];
  el.innerHTML = portfolios.map(p => `<option value="${p.id}" ${String(p.id) === String(selectedPortfolioId) ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
}

function selectPortfolio(id) {
  selectedPortfolioId = String(id);
  renderPortfolioSelector();
  renderContent();
}

async function loadAuditReliability() {
  auditReliabilityData = await fetch('/api/audit-reliability').then(r=>r.json()).catch(()=>({}));
  moduleData.audit_reliability = auditReliabilityData;
}

async function refreshReport() {
  lastViewedAt = new Date();
  updateViewedTime();
  const r = await fetch('/api/last-report').catch(()=>null);
  if (r && r.ok) {
    const d = await r.json();
    moduleData = d.report?.modules || {};
    moduleData.investment_memos = {items: memoData};
    moduleData.decision_journal = {items: journalData};
    moduleData.family_office = familyOfficeData;
    moduleData.audit_reliability = auditReliabilityData;
    scanStartedAt = d.scan_started_at || null;
    scanCompletedAt = d.scan_completed_at || d.timestamp || d.report?.timestamp || null;
    cacheTimestamp = d.cache_timestamp || d.timestamp || null;
    loadedFromCache = !!d.loaded_from_cache;
  }
  renderTabs();
  renderHoldings();
  renderMarketContextStrip();
  renderContent();
  updateSubline();
}

function updateSubline(status={}) {
  if (status.scan_completed_at) scanCompletedAt = status.scan_completed_at;
  if (status.scan_started_at) scanStartedAt = status.scan_started_at;
  if (status.cache_timestamp) cacheTimestamp = status.cache_timestamp;
  if (typeof status.loaded_from_cache === 'boolean') loadedFromCache = status.loaded_from_cache;
  const scanLabel = scanCompletedAt ? new Date(scanCompletedAt).toLocaleString() : 'No successful scan';
  const sourceLabel = scanInFlight || status.scan_running ? 'Refreshing...' : loadedFromCache ? `Loaded from cache${cacheTimestamp ? ': ' + new Date(cacheTimestamp).toLocaleString() : ''}` : 'Live scan';
  document.getElementById('subline').textContent =
    `${portfolio.length || status.portfolio_size || '?'} holdings · ${(portfolioMeta.watchlist || []).length || status.watchlist_size || '?'} watchlist`;
  document.getElementById('lastSuccessfulScan').textContent = scanLabel;
  document.getElementById('dataSource').textContent = sourceLabel;
  const badge = document.getElementById('dataSourceBadge');
  badge.className = `badge ${scanInFlight || status.scan_running ? 'loading' : loadedFromCache ? 'cache' : 'live'}`;
  if (scanInFlight || status.scan_running) {
    document.getElementById('dataSource').innerHTML = '<span class="mini-spinner"></span> Refreshing...';
  }
}

function startLocalClock() {
  const tick = () => { document.getElementById('localClock').textContent = new Date().toLocaleString(); };
  tick();
  setInterval(tick, 1000);
}

function updateViewedTime() {
  const el = document.getElementById('lastViewed');
  if (el) el.textContent = lastViewedAt.toLocaleString();
}

function renderHoldings() {
  const stocks = moduleData.fundamentals?.stocks || {};
  const total = Object.values(stocks).reduce((s, x) => s + (Number(x.market_value) || 0), 0) + (portfolioMeta.buying_power || 0);
  document.getElementById('holdings').innerHTML = portfolio.map(p => {
    const s = stocks[p.ticker] || {};
    const price = s.price;
    const pnl = s.total_return_pct ?? (price && p.avg_cost ? ((price / p.avg_cost - 1) * 100) : null);
    const weight = s.portfolio_weight_pct ?? (s.market_value && total ? s.market_value / total * 100 : null);
    const source = s.price_source || 'pending';
    const session = s.market_session || '';
    const concentrationWarn = portfolioMeta.special_flags?.[p.ticker] ? '<div class="warn-badge">Concentration Review</div>' : '';
    return `<div class="holding ${portfolioMeta.special_flags?.[p.ticker] ? 'selected' : ''}">
      <div class="ticker"><strong>${esc(p.ticker)}</strong><span>${weight != null ? fmt.format(weight) + '%' : 'pending'}</span></div>
      <div class="pnl ${clsNum(pnl)}">${pnl != null ? fmt.format(pnl) + '%' : '--'}</div>
      <div class="small">${price != null ? money.format(price) : 'price pending'} · ${esc(source)} ${session ? '· ' + esc(session) : ''}</div>
      ${concentrationWarn}
    </div>`;
  }).join('');
}

function renderMarketContextStrip() {
  const el = document.getElementById('marketContextStrip');
  if (!el) return;
  const data = moduleData.market_context || {};
  const rows = data.items || [];
  if (!rows.length) {
    el.innerHTML = `<div class="market-context-head"><span>Market Context — Not Portfolio Holdings</span><span class="muted">Not loaded</span></div>`;
    return;
  }
  el.innerHTML = `<div class="market-context-head"><span>Market Context — Not Portfolio Holdings</span><span>${esc(data.disclaimer || 'Not investment advice.')}</span></div>
  <div class="market-context-grid">${rows.map(r => `<div class="market-tile">
    <div class="ticker"><strong>${esc(r.ticker)}</strong><span>${esc(r.source || '--')}</span></div>
    <div class="pnl ${clsNum(r.daily_change_pct)}">${r.official_close != null ? money.format(r.official_close) : '--'}</div>
    <div class="small">Day ${r.daily_change_pct != null ? `<span class="${clsNum(r.daily_change_pct)}">${fmt.format(r.daily_change_pct)}%</span>` : '--'} · YTD ${r.ytd_return_pct != null ? `<span class="${clsNum(r.ytd_return_pct)}">${fmt.format(r.ytd_return_pct)}%</span>` : '--'} · ${esc(r.latest_trading_date || '')}</div>
  </div>`).join('')}</div>`;
}

function moduleState(id) {
  if (moduleLoading[id]) return 'loading';
  const data = moduleData[id];
  if (!data) return '';
  if (data.error || data.status === 'failed') return 'error';
  if (data.status === 'partial' || data.status === 'degraded') return 'partial';
  return 'loaded';
}

function renderTabs() {
  document.getElementById('tabs').innerHTML = MODULE_GROUPS.map(group => {
    const loaded = group.modules.filter(m => ['loaded','partial'].includes(moduleState(m.id))).length;
    const partials = group.modules.filter(m => moduleState(m.id) === 'partial').length;
    const errors = group.modules.filter(m => moduleState(m.id) === 'error').length;
    const loading = group.modules.some(m => moduleState(m.id) === 'loading');
    const active = group.modules.some(m => m.id === activeTab);
    const collapsed = collapsedLayers[group.id] && !active;
    const groupState = loading ? 'loading' : errors ? 'error' : partials ? 'partial' : loaded ? 'loaded' : '';
    const buttons = group.modules.map(m => {
      const state = moduleState(m.id);
      return `<button class="tab ${activeTab === m.id ? 'active' : ''}" onclick="switchTab('${m.id}')">
        <span class="dot ${state}"></span>${esc(m.label)}
      </button>`;
    }).join('');
    return `<section class="layer-group ${group.primary ? 'executive' : ''} ${active ? 'active-layer' : ''} ${collapsed ? 'collapsed' : ''}">
      <button class="layer-head" onclick="toggleLayer('${group.id}')" aria-expanded="${!collapsed}">
        <div>
          <div class="layer-title"><span class="dot ${groupState}"></span>${esc(group.label)}</div>
          <div class="layer-summary">${esc(group.summary)}</div>
        </div>
        <div class="layer-meta">${esc(active ? 'ACTIVE' : collapsed ? 'OPEN' : 'CLOSE')} · ${loaded}/${group.modules.length}${partials ? ' · PART ' + partials : ''}${errors ? ' · ERR ' + errors : ''}</div>
      </button>
      <div class="layer-body">${buttons}</div>
    </section>`;
  }).join('');
}

function toggleLayer(id) {
  collapsedLayers[id] = !collapsedLayers[id];
  renderTabs();
}

function groupForTab(id) {
  return MODULE_GROUPS.find(group => group.modules.some(m => m.id === id));
}

function switchTab(id) {
  activeTab = id;
  const group = groupForTab(id);
  if (group) collapsedLayers[group.id] = false;
  renderTabs();
  renderContent();
}

function panel(title, body, note='') {
  const local = activeTab === 'investment_memos' || activeTab === 'decision_journal';
  return `<div class="panel-head"><div><h2 class="panel-title">${esc(title)}</h2>${note ? `<div class="panel-note">${esc(note)}</div>` : ''}</div>
  ${local ? '' : `<button class="btn secondary" onclick="runModule('${activeTab}')">Run Module</button>`}</div>${body}`;
}

function detailPanel(title, body, open=false) {
  return `<details class="detail-panel" ${open ? 'open' : ''}><summary>${esc(title)}</summary><div class="detail-panel-body">${body}</div></details>`;
}

function miniMarketStrip(rows) {
  return `<div class="mini-strip">${(rows || []).slice(0, 3).map(r => `<div class="market-tile">
    <div class="ticker"><strong>${esc(r.ticker)}</strong><span>${esc(r.source || '--')}</span></div>
    <div class="pnl ${clsNum(r.daily_change_pct)}">${r.official_close != null ? money.format(r.official_close) : '--'}</div>
    <div class="small">Day ${r.daily_change_pct != null ? `<span class="${clsNum(r.daily_change_pct)}">${fmt.format(r.daily_change_pct)}%</span>` : '--'} · YTD ${r.ytd_return_pct != null ? `<span class="${clsNum(r.ytd_return_pct)}">${fmt.format(r.ytd_return_pct)}%</span>` : '--'}</div>
  </div>`).join('') || '<div class="empty">Market context not loaded.</div>'}</div>`;
}

function degradedBanner(data) {
  if (!data || !['partial','degraded'].includes(data.status)) return '';
  const warnings = data.warnings || (data.reason ? [data.reason] : []);
  const text = warnings.length ? warnings.join(' ') : 'Module returned partial data.';
  return `<div class="warning">Partial / degraded data: ${esc(text)}</div>`;
}

function renderContent() {
  const el = document.getElementById('content');
  const mod = MODULES.find(m => m.id === activeTab);
  if (moduleLoading[activeTab]) {
    el.innerHTML = `<div class="empty"><span class="spinner"></span><div style="margin-top:14px">Scanning ${esc(mod.label)}...</div></div>`;
    return;
  }
  const data = moduleData[activeTab];
  if (!data) {
    el.innerHTML = panel(mod.label, '<div class="empty">No data loaded for this module.</div>');
    return;
  }
  const renderers = {
    macro_pulse: renderMacro,
    market_context: renderMarketContext,
    portfolio_news: renderNews,
    fundamentals: renderFundamentals,
    audit_reliability: renderAuditReliability,
    investment_committee: renderInvestmentCommittee,
    policy_allocation: renderPolicyAllocation,
    drawdown_survival: renderDrawdownSurvival,
    goals_based: renderGoalsBased,
    rebalancing_intelligence: renderRebalancingIntelligence,
    portfolio_narrative: renderPortfolioNarrative,
    risk_analytics: renderRisk,
    macro_regime: renderMacroRegime,
    benchmark_attribution: renderBenchmarkAttribution,
    capital_efficiency: renderCapitalEfficiency,
    factor_exposure: renderFactorExposure,
    correlation_regime: renderCorrelationRegime,
    liquidity_ladder: renderLiquidityLadder,
    liquidity_concentration: renderLiquidityConcentration,
    risk_contribution: renderRiskContribution,
    price_regime: renderPriceRegime,
    congress_insiders: renderCongress,
    signal_action_plan: renderSignalActionPlan,
    conviction_matrix: renderConvictionMatrix,
    wealth_view: renderWealthView,
    family_office: renderFamilyOffice,
    investment_memos: renderInvestmentMemos,
    decision_journal: renderDecisionJournal,
    watchlist_scan: renderWatchlist,
    price_audit: renderPriceAudit,
    behavioral_check: renderBehavioral
  };
  const group = groupForTab(activeTab);
  const note = data.error ? data.error : group ? group.label : '';
  el.innerHTML = panel(mod.label, degradedBanner(data) + renderers[activeTab](data), note);
  if (activeTab === 'conviction_matrix') drawConvictionMatrix(data);
}

function renderMarketContext(data) {
  const rows = data.items || [];
  const newsRows = (data.news_groups || []).flatMap(group => group.items || []);
  return `<div class="metrics">
    ${metric('Indicators', rows.length)}
    ${metric('Loaded', data.loaded_count)}
    ${metric('Scope', 'Benchmarks')}
    ${metric('Portfolio Holdings', 'No')}
  </div>
  <div class="warning">${esc(data.disclaimer || 'Market Context — Not Portfolio Holdings. Not investment advice.')}</div>
  ${table(rows, [
    {key:'ticker', label:'Ticker'}, {key:'label', label:'Label'},
    {key:'official_close', label:'Official Close', render:r => r.official_close != null ? money.format(r.official_close) : '--'},
    {key:'daily_change_pct', label:'Daily %'}, {key:'ytd_return_pct', label:'YTD %'},
    {key:'source', label:'Source'}, {key:'latest_trading_date', label:'Latest Date'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Market Context News</h3>
  <div class="news-list">${(data.news_groups || []).map(group => `<article class="news-item">
    <div class="news-meta">${esc(group.group)} · ${group.count || 0} articles · ${(group.sources || []).map(esc).join(' + ') || 'No source'}</div>
    <div class="news-title">${(group.items || []).map(n => n.url ? `<a class="neu" href="${esc(n.url)}" target="_blank" rel="noreferrer">${esc(n.headline)}</a> <span class="muted">(${esc(n.source)} · ${esc(n.badge || 'delayed')})</span>` : `${esc(n.headline)} <span class="muted">(${esc(n.source)} · ${esc(n.badge || 'delayed')})</span>`).join('<br>') || '<span class="muted">No recent context news returned.</span>'}</div>
  </article>`).join('') || '<div class="empty">No market context news returned.</div>'}</div>
  ${newsRows.length ? `<div class="panel-note" style="margin-top:12px">Market news is delayed context and is intentionally separated from Portfolio News.</div>` : ''}`;
}

function renderMacro(data) {
  const yc = data.yield_curve || {};
  const dc = data.doctor_copper || {};
  const rows = Object.entries(data.indicators || {}).map(([k,v]) => ({name:k.replaceAll('_',' '), ...v}));
  return `<div class="metrics">
    ${metric('10Y-2Y', yc.spread_10y_2y, '%')}
    ${metric('10Y-3M', yc.spread_10y_3m, '%')}
    ${metric('Curve Inverted', yc.inverted ? 'YES' : 'NO')}
    ${metric('Copper Signal', dc.signal || '--')}
  </div>` + table(rows, [
    {key:'name', label:'Indicator'}, {key:'series', label:'FRED'}, {key:'latest', label:'Latest'},
    {key:'change', label:'Change'}, {key:'trend', label:'Trend'}, {key:'date', label:'Date'}
  ]);
}

function renderNews(data) {
  const counts = data.counts_by_ticker || {};
  const allItems = newsFilter === 'ALL'
    ? (data.items || [])
    : ((data.by_ticker || {})[newsFilter] || []);
  const filters = ['ALL', ...portfolio.map(p => p.ticker)];
  return `<div class="metrics">${metric('Total Articles', data.count ?? allItems.length)}${metric('Top Cap/Ticker', data.top_feed_cap_per_ticker ?? 3)}${metric('Sources', (data.sources || []).join(' + '))}</div>
  <div class="filters">${filters.map(t => `<button class="filter-btn ${newsFilter === t ? 'active' : ''}" onclick="setNewsFilter('${t}')">${esc(t)} ${t !== 'ALL' ? `(${counts[t] || 0})` : ''}</button>`).join('')}</div>
  <div class="news-list">${allItems.slice(0, 30).map(n => `<article class="news-item">
    <div class="news-meta">${esc(n.ticker)} · ${esc(n.source)} · ${esc(n.date || '')}</div>
    <div class="news-title">${n.url ? `<a class="neu" href="${esc(n.url)}" target="_blank" rel="noreferrer">${esc(n.headline)}</a>` : esc(n.headline)}</div>
  </article>`).join('') || '<div class="empty">No news returned.</div>'}</div>`;
}

function setNewsFilter(ticker) {
  newsFilter = ticker;
  renderContent();
}

function renderFundamentals(data) {
  const rows = Object.entries(data.stocks || {}).map(([ticker, s]) => ({ticker, ...s}));
  return `<div class="metrics">
    ${metric('Buying Power', data.buying_power)}
    ${metric('Largest Weight', rows.length ? Math.max(...rows.map(r=>Number(r.portfolio_weight_pct)||0)) : null, '%')}
    ${metric('Tax Context', 'Jurisdiction-specific')}
    ${metric('Holdings', rows.length)}
  </div>` + table(rows, [
    {key:'ticker', label:'Ticker', render:r => `<span class="${r.special_flag?'pos':'neu'}">${esc(r.ticker)}</span>`},
    {key:'price', label:'Price', render:r => money.format(r.price || 0)},
    {key:'price_source', label:'Source'},
    {key:'market_session', label:'Session'},
    {key:'latest_trading_date', label:'Date'},
    {key:'portfolio_weight_pct', label:'Weight %'},
    {key:'total_return_pct', label:'P&L %'},
    {key:'pe', label:'PE'}, {key:'pb', label:'PB'}, {key:'eps', label:'EPS'},
    {key:'graham_value', label:'Graham'}, {key:'analyst_recommendation', label:'Analyst'},
    {key:'special_flag', label:'Flag', render:r => r.special_flag ? `<span class="pos">${esc(r.special_flag)}</span>` : '<span class="muted">--</span>'}
  ]) + `<p class="panel-note">${esc(data.tax_context || '')}</p>`;
}

function renderRisk(data) {
  const p = data.portfolio || {};
  const rows = Object.entries(data.stock_metrics || {}).map(([ticker, s]) => ({ticker, ...s, ...(s.technicals || {})}));
  const frontier = data.riskfolio?.efficient_frontier || [];
  return `<div class="metrics">
    ${metric('Sharpe', p.sharpe)}
    ${metric('Sortino', p.sortino)}
    ${metric('Max DD', p.max_drawdown, '%')}
    ${metric('1W Hist VaR', p.var_historical_1w, '%')}
  </div>` + table(rows, [
    {key:'ticker', label:'Ticker'}, {key:'beta_empyrical', label:'Beta'}, {key:'alpha', label:'Alpha'},
    {key:'sharpe', label:'Sharpe'}, {key:'sortino', label:'Sortino'}, {key:'var_historical_1w', label:'VaR %'},
    {key:'rsi14', label:'RSI'}, {key:'zscore', label:'Z'}, {key:'signal', label:'Signal'}
  ]) + `<h3 class="panel-title" style="margin-top:16px">Riskfolio Frontier</h3>` + table(frontier, [
    {key:'return', label:'Return %'}, {key:'volatility', label:'Volatility %'}
  ]);
}

function renderAuditReliability(data) {
  const p429 = data.polygon_429_tracking || {};
  const coverage = data.coverage || {};
  return `<div class="metrics">
    ${metric('Data Quality', data.data_quality_score)}
    ${metric('Price Coverage', coverage.coverage_pct, '%')}
    ${metric('Polygon 429s', p429.count || 0)}
    ${metric('Warnings', (data.stale_data_warnings || []).length)}
  </div>
  <div class="news-item"><div class="news-meta">Reliability Note</div><div class="news-title">${esc(data.notes || '')}</div></div>
  <h3 class="panel-title" style="margin-top:16px">API Status by Provider</h3>
  ${table(data.api_status || [], [
    {key:'provider', label:'Provider'}, {key:'configured', label:'Configured'}, {key:'status', label:'Status'},
    {key:'last_successful_call', label:'Last Success', render:r => r.last_successful_call ? new Date(r.last_successful_call).toLocaleString() : '<span class="muted">--</span>'},
    {key:'notes', label:'Notes'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Price Source Coverage</h3>
  ${table(data.price_source_coverage || [], [
    {key:'source', label:'Source'}, {key:'count', label:'Count'}, {key:'coverage_pct', label:'Coverage %'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Fallback Reasons</h3>
  ${table(data.fallback_reasons || [], [
    {key:'ticker', label:'Ticker'}, {key:'source_used', label:'Source Used'}, {key:'fallback_reason', label:'Fallback Reason'}, {key:'polygon_failure_reason', label:'Polygon Failure'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Stale Data Warnings</h3>
  ${table(data.stale_data_warnings || [], [
    {key:'scope', label:'Scope'}, {key:'warning', label:'Warning'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Cache Age</h3>
  ${table(data.cache_age || [], [
    {key:'cache', label:'Cache'}, {key:'age_hours', label:'Age Hours'}, {key:'status', label:'Status'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Module Freshness</h3>
  ${table(data.module_freshness || [], [
    {key:'module', label:'Module'}, {key:'status', label:'Status'}, {key:'age_hours', label:'Age Hours'}
  ])}`;
}

function renderInvestmentCommittee(data) {
  const brief = data.weekly_brief || {};
  const cards = data.institutional_summary_cards || {};
  const stress = data.stress_dashboard || {};
  return `<div class="metrics">
    ${metric('Risk Grade', cards.portfolio_risk_grade || '--')}
    ${metric('Survival Score', stress.survival_score)}
    ${metric('Macro Align', cards.macro_alignment_score)}
    ${metric('Discipline', cards.behavioral_discipline_score)}
  </div>
  <div class="metrics">
    ${metric('Diversification', cards.diversification_grade || '--')}
    ${metric('Concentration', cards.concentration_score)}
    ${metric('Liquidity', cards.liquidity_score)}
    ${metric('Queue', (data.decision_queue || []).length)}
  </div>
  <h3 class="panel-title">Weekly Investment Committee Brief</h3>
  <div class="news-list">
    ${[
      ['Macro regime', brief.macro_regime_summary],
      ['Largest risks', (brief.largest_portfolio_risks || []).join('<br>')],
      ['Risk contributors', (brief.largest_contributors_to_risk || []).join('<br>')],
      ['Strongest positions', (brief.strongest_positions || []).join('<br>')],
      ['Weakest positions', (brief.weakest_positions || []).join('<br>')],
      ['Concentration', brief.concentration_concerns],
      ['Liquidity', brief.liquidity_concerns],
	      ['Allocation drift', brief.allocation_drift],
	      ['Benchmark attribution', brief.benchmark_attribution_summary],
	      ['Factor-linked stress', brief.factor_linked_stress_summary],
	      ['Changed this week', (brief.what_changed_this_week || []).join('<br>')],
      ['Next week attention', (brief.what_deserves_attention_next_week || []).join(', ')]
    ].map(([k,v]) => `<div class="news-item"><div class="news-meta">${esc(k)}</div><div class="news-title">${v || '--'}</div></div>`).join('')}
  </div>
  <h3 class="panel-title" style="margin-top:16px">Priority Engine</h3>
  ${table(data.priority_engine || [], [
    {key:'category', label:'Category'}, {key:'severity', label:'Severity'}, {key:'score', label:'Score'}, {key:'reason', label:'Reason'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Allocation Drift Monitor</h3>
  ${table(data.allocation_drift || [], [
    {key:'ticker', label:'Ticker'}, {key:'current_allocation_pct', label:'Current %'}, {key:'target_policy_pct', label:'Target %'},
    {key:'drift_pct', label:'Drift'}, {key:'rebalance_pressure_score', label:'Pressure'}, {key:'status', label:'Status'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Decision Queue</h3>
  ${table(data.decision_queue || [], [
    {key:'review_type', label:'Review Type'}, {key:'category', label:'Category'}, {key:'severity', label:'Severity'}, {key:'reason', label:'Reason'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Stress Dashboard</h3>
  ${table((stress.drawdown_simulations || []).slice(0, 8), [
    {key:'scenario', label:'Scenario'}, {key:'shock_pct', label:'Shock %'}, {key:'dollar_loss', label:'Dollar Loss'},
    {key:'portfolio_loss_pct', label:'Loss %'}, {key:'recovery_needed_pct', label:'Recovery %'}
  ])}
	  <h3 class="panel-title" style="margin-top:16px">Factor Shocks</h3>
	  ${table(stress.factor_shocks || [], [
	    {key:'scenario', label:'Scenario'}, {key:'shock_assumption', label:'Shock'}, {key:'affected_factor_proxy', label:'Factor/Proxy'},
	    {key:'estimated_portfolio_loss_pct', label:'Loss %'}, {key:'estimated_dollar_loss', label:'Dollar Loss'},
	    {key:'main_affected_holdings', label:'Affected Holdings'}, {key:'confidence', label:'Confidence'}, {key:'method_used', label:'Method'}
	  ])}
  <div class="panel-note" style="margin-top:12px">${esc(data.disclaimer || '')}</div>`;
}

function renderPolicyAllocation(data) {
  return `<div class="metrics">
    ${metric('Portfolio Value', data.total_value)}
    ${metric('Cash Weight', data.cash_weight_pct, '%')}
    ${metric('Policy Breaches', (data.breaches || []).length)}
    ${metric('Benchmark', data.policy?.target_benchmark || '--')}
  </div>
  ${data.concentration_warning ? `<div class="warning">${esc(data.concentration_warning)}</div>` : ''}
  <h3 class="panel-title">Policy Breaches</h3>
  ${table(data.breaches || [], [
    {key:'type', label:'Type'}, {key:'name', label:'Name'}, {key:'actual', label:'Actual %'},
    {key:'limit', label:'Limit %'}, {key:'severity', label:'Severity'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Holdings vs Allocation</h3>
  ${table(data.holdings || [], [
    {key:'ticker', label:'Ticker'}, {key:'weight_pct', label:'Weight %'}, {key:'market_value', label:'Market Value'},
    {key:'sector', label:'Sector'}, {key:'theme', label:'Theme'}, {key:'pnl_pct', label:'P&L %'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Sector Weights</h3>
  ${table(Object.entries(data.sector_weights || {}).map(([name, weight]) => ({name, weight})), [
    {key:'name', label:'Sector'}, {key:'weight', label:'Weight %'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Theme Weights</h3>
  ${table(Object.entries(data.theme_weights || {}).map(([name, weight]) => ({name, weight})), [
    {key:'name', label:'Theme'}, {key:'weight', label:'Weight %'}
  ])}`;
}

function renderDrawdownSurvival(data) {
  const factorRows = data.factor_linked_scenarios || [];
  return `<div class="metrics">
    ${metric('Portfolio Value', data.total_value)}
    ${metric('Largest Holding', data.largest_holding || '--')}
    ${metric('Scenarios', (data.scenarios || []).length)}
    ${metric('Max Loss', Math.max(...(data.scenarios || []).map(x => Number(x.portfolio_loss_pct) || 0)), '%')}
  </div>
  ${table(data.scenarios || [], [
    {key:'scenario', label:'Scenario'}, {key:'shock_pct', label:'Shock %'}, {key:'dollar_loss', label:'Dollar Loss'},
    {key:'portfolio_loss_pct', label:'Portfolio Loss %'}, {key:'recovery_needed_pct', label:'Recovery Needed %'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Factor-Linked Stress Scenarios</h3>
  ${data.factor_stress_method_note ? `<div class="panel-note">${esc(data.factor_stress_method_note)}</div>` : ''}
  ${table(factorRows, [
    {key:'scenario', label:'Scenario'}, {key:'shock_assumption', label:'Shock'}, {key:'affected_factor_proxy', label:'Factor/Proxy'},
    {key:'estimated_portfolio_loss_pct', label:'Loss %'}, {key:'estimated_dollar_loss', label:'Dollar Loss'},
    {key:'main_affected_holdings', label:'Affected Holdings'}, {key:'confidence', label:'Confidence'}, {key:'method_used', label:'Method'}
  ])}`;
}

function renderGoalsBased(data) {
  const benchmarkGoal = (data.goals || []).find(g => g.goal_type === 'benchmark_relative');
  return `<div class="metrics">
    ${metric('Goals', (data.goals || []).length)}
    ${metric('Benchmark Status', benchmarkGoal?.status || '--')}
    ${metric('Excess vs SPY', benchmarkGoal?.excess_return_vs_spy_pct, '%')}
    ${metric('Real Return', benchmarkGoal?.real_return_after_inflation_pct, '%')}
    ${metric('Warnings', (data.goals || []).filter(g => g.risk_mismatch_warning).length)}
  </div>
  ${table(data.goals || [], [
    {key:'name', label:'Goal'}, {key:'objective', label:'Objective'},
    {key:'portfolio_ytd_return_pct', label:'Portfolio YTD %'}, {key:'spy_ytd_return_pct', label:'SPY YTD %'},
    {key:'cpi_inflation_estimate_pct', label:'CPI %'}, {key:'excess_return_vs_spy_pct', label:'Excess vs SPY %'},
    {key:'real_return_after_inflation_pct', label:'Real Return %'}, {key:'status', label:'Status'},
    {key:'risk_mismatch_warning', label:'Risk Mismatch'}
  ])}`;
}

function renderRebalancingIntelligence(data) {
  return `<div class="metrics">
    ${metric('Decision Options', (data.options || []).length)}
    ${metric('Orders', 'None')}
    ${metric('Mandate', 'Policy')}
    ${metric('Advice', 'No')}
  </div>
  ${table(data.options || [], [
    {key:'ticker', label:'Ticker'}, {key:'option', label:'Decision Option'}, {key:'reason', label:'Reason'},
    {key:'risk', label:'Risk'}, {key:'tradeoff', label:'Tradeoff'}
  ])}
  <div class="panel-note" style="margin-top:12px">${esc(data.disclaimer || 'Not investment advice.')}</div>`;
}

function renderPortfolioNarrative(data) {
  const rows = [
    ['Implicit Bet', data.implicit_bet],
    ['Main Risks', data.main_risks],
    ['Strongest Holdings', (data.strongest_holdings || []).join(', ')],
    ['Weakest Holdings', (data.weakest_holdings || []).join(', ')],
    ['Macro Backdrop', data.macro_backdrop],
    ['Changed This Week', data.what_changed_this_week],
    ['Watch Next', data.what_to_watch_next]
  ];
  return `<div class="metrics">
    ${metric('Liquidity Grade', data.liquidity_concentration_grade || '--')}
    ${metric('Strongest', (data.strongest_holdings || []).length)}
    ${metric('Weakest', (data.weakest_holdings || []).length)}
    ${metric('Tone', 'Analytical')}
  </div>
  <div class="news-list">${rows.map(([k,v]) => `<div class="news-item"><div class="news-meta">${esc(k)}</div><div class="news-title">${esc(v || '--')}</div></div>`).join('')}</div>`;
}

function renderLiquidityConcentration(data) {
  return `<div class="metrics">
    ${metric('Risk Grade', data.risk_grade || '--')}
    ${metric('HHI', data.hhi)}
    ${metric('Top 1 Weight', data.top1_weight_pct, '%')}
    ${metric('Top 3 Weight', data.top3_weight_pct, '%')}
  </div>
  <div class="metrics">
    ${metric('Avg Correlation', data.avg_correlation)}
    ${metric('Liquidity Rows', (data.liquidity || []).length)}
    ${metric('Sectors', Object.keys(data.sector_weights || {}).length)}
    ${metric('Themes', Object.keys(data.theme_weights || {}).length)}
  </div>
  <h3 class="panel-title">Liquidity Proxy</h3>
  ${table(data.liquidity || [], [
    {key:'ticker', label:'Ticker'}, {key:'dollar_volume', label:'Dollar Volume'},
    {key:'market_cap', label:'Market Cap'}, {key:'liquidity_flag', label:'Flag'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Sector Concentration</h3>
  ${table(Object.entries(data.sector_weights || {}).map(([name, weight]) => ({name, weight})), [
    {key:'name', label:'Sector'}, {key:'weight', label:'Weight %'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Theme Concentration</h3>
  ${table(Object.entries(data.theme_weights || {}).map(([name, weight]) => ({name, weight})), [
    {key:'name', label:'Theme'}, {key:'weight', label:'Weight %'}
  ])}`;
}

function renderMacroRegime(data) {
  const scores = Object.entries(data.scores || {}).map(([regime, score]) => ({regime, score}));
  const curve = data.yield_curve || {};
  return `<div class="metrics">
    ${metric('Primary Regime', data.primary_regime || '--')}
    ${metric('10Y-2Y', curve.spread_10y_2y, '%')}
    ${metric('10Y-3M', curve.spread_10y_3m, '%')}
    ${metric('Curve Inverted', curve.inverted ? 'YES' : 'NO')}
  </div>
  <div class="news-item"><div class="news-meta">Allocation Posture</div><div class="news-title">${esc(data.allocation_posture || '')}</div></div>
  <h3 class="panel-title" style="margin-top:16px">Regime Scorecard</h3>
  ${table(scores, [{key:'regime', label:'Regime'}, {key:'score', label:'Score'}])}
  <h3 class="panel-title" style="margin-top:16px">Macro Evidence</h3>
  ${table(data.indicators || [], [
    {key:'indicator', label:'Indicator'}, {key:'latest', label:'Latest'}, {key:'previous', label:'Previous'},
    {key:'trend', label:'Trend'}, {key:'regime_read', label:'Read'}
  ])}
  <div class="news-list" style="margin-top:12px">${(data.evidence || []).map(x => `<div class="news-item"><div class="news-title">${esc(x)}</div></div>`).join('')}</div>
  <div class="panel-note" style="margin-top:12px">${esc(data.disclaimer || '')}</div>`;
}

function excludedTickerLabel(rows) {
  if (!rows || !rows.length) return 'None';
  return rows.map(row => typeof row === 'string' ? row : `${row.ticker || '--'}${row.reason ? ': ' + row.reason : ''}`).join(', ');
}

function analyticsDiagnostics(data, includePca=true) {
  return `<h3 class="panel-title" style="margin-top:16px">Diagnostics</h3>
  <div class="metrics">
    ${metric('Portfolio History', data.portfolio_history_len)}
    ${metric('Benchmark History', data.benchmark_history_len)}
    ${metric('Aligned Returns', data.aligned_len)}
    ${metric('Data Method', data.data_method || '--')}
  </div>
  <div class="metrics">
    ${metric('Excluded Tickers', excludedTickerLabel(data.excluded_tickers || []))}
    ${metric('Regression Window', data.regression_window)}
    ${metric('PCA Observations', includePca ? data.pca_observations : 'N/A')}
  </div>`;
}

function renderBenchmarkAttribution(data) {
  return `<div class="metrics">
    ${metric('Benchmark', data.benchmark || '--')}
    ${metric('Active Return', data.active_return_pct, '%')}
    ${metric('Relative Status', data.relative_status || '--')}
    ${metric('Benchmark Source', data.benchmark_source || '--')}
  </div>
  <div class="metrics">
    ${metric('Portfolio Return', data.portfolio_return_pct, '%')}
    ${metric('Benchmark Return', data.benchmark_return_pct, '%')}
    ${metric('Tracking Error', data.tracking_error_pct, '%')}
    ${metric('Info Ratio', data.information_ratio)}
  </div>
  <div class="metrics">
    ${metric('Beta', data.beta_to_benchmark)}
    ${metric('Down Capture', data.down_capture_pct, '%')}
  </div>
  ${analyticsDiagnostics(data, false)}
  ${data.method_note ? `<div class="news-item"><div class="news-meta">Attribution Method</div><div class="news-title">${esc(data.method_note)}</div></div>` : ''}
  <h3 class="panel-title" style="margin-top:16px">Position Attribution</h3>
  ${table(data.items || [], [
    {key:'ticker', label:'Ticker'}, {key:'weight_pct', label:'Weight %'}, {key:'asset_return_pct', label:'Asset Return %'},
    {key:'benchmark_return_pct', label:'Benchmark %'}, {key:'contribution_pct', label:'Contribution %'},
    {key:'active_contribution_pct', label:'Active Contribution %'}, {key:'relative_result', label:'Result'}, {key:'method', label:'Method'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Summary Buckets</h3>
  ${table(data.summary_buckets || [], [{key:'bucket', label:'Bucket'}, {key:'active_contribution_pct', label:'Active Contribution %'}])}
  <div class="news-list" style="margin-top:12px">${(data.commentary || []).map(x => `<div class="news-item"><div class="news-title">${esc(x)}</div></div>`).join('')}</div>
  <div class="panel-note" style="margin-top:12px">${esc(data.disclaimer || '')}</div>`;
}

function renderCapitalEfficiency(data) {
  const rows = data.items || [];
  return `<div class="metrics">
    ${metric('Avg Efficiency', data.average_efficiency_score)}
    ${metric('Review Count', data.review_count)}
    ${metric('Framework', 'Risk Budget')}
    ${metric('Positions', rows.length)}
  </div>
  <div class="news-item"><div class="news-meta">Capital Allocation Lens</div><div class="news-title">${esc(data.commentary || '')}</div></div>
  ${contributionBars(rows.slice().reverse(), 'capital_efficiency_score')}
  ${table(rows, [
    {key:'ticker', label:'Ticker'}, {key:'capital_efficiency_score', label:'Efficiency'},
    {key:'label', label:'Label'}, {key:'decision_option', label:'Decision Option'},
    {key:'portfolio_weight_pct', label:'Weight %'}, {key:'risk_contribution_pct', label:'Risk %'},
    {key:'quality_score', label:'Quality'}, {key:'momentum_score', label:'Momentum'},
    {key:'pnl_pct', label:'P&L %'}, {key:'flag', label:'Flag'}
  ])}
  <div class="panel-note" style="margin-top:12px">${esc(data.framework || '')} ${esc(data.disclaimer || '')}</div>`;
}

function contributionBars(rows, valueKey, labelKey='ticker') {
  if (!rows || !rows.length) return '<div class="empty">No contribution rows returned.</div>';
  return `<div class="chart">${rows.map(r => {
    const v = Math.max(0, Math.min(100, Number(r[valueKey]) || 0));
    const color = v > 35 ? 'var(--red)' : v > 20 ? 'var(--amber)' : 'var(--blue)';
    return `<div class="bar-row">
      <div>${esc(r[labelKey] || r.factor || '--')}</div>
      <div class="bar-track"><span class="bar" style="left:0;width:${Math.max(2, v)}%;background:${color}"></span></div>
      <div class="${v > 35 ? 'neg' : v > 20 ? 'neu' : 'muted'}">${fmt.format(v)}%</div>
    </div>`;
  }).join('')}</div>`;
}

function renderFactorExposure(data) {
  const rows = data.items || [];
  const proxyRows = data.proxy_factor_exposures || (data.status === 'partial' ? rows : []);
  const regressionRows = data.proxy_factor_exposures ? rows : [];
  const topProxy = proxyRows.slice().sort((a,b) => (Number(b.exposure_weight_pct)||0) - (Number(a.exposure_weight_pct)||0))[0];
  return `<div class="metrics">
    ${metric('Factors', rows.length || proxyRows.length)}
    ${metric('Model R²', data.model_r2)}
    ${metric('PCA Method', data.pca_status || '--')}
    ${metric('Top Factor', rows[0]?.factor || topProxy?.factor || '--')}
  </div>
  ${analyticsDiagnostics(data, true)}
  ${data.method_note ? `<div class="news-item"><div class="news-meta">Factor Method</div><div class="news-title">${esc(data.method_note)}</div></div>` : ''}
  <h3 class="panel-title" style="margin-top:16px">Proxy Factor Exposure</h3>
  ${contributionBars(proxyRows, 'exposure_weight_pct', 'factor')}
  ${table(proxyRows, [
    {key:'factor', label:'Factor'}, {key:'exposure_weight_pct', label:'Weight %'}, {key:'tickers', label:'Tickers'},
    {key:'classification_basis', label:'Basis'}, {key:'method', label:'Method'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Return Regression Exposure</h3>
  ${table(regressionRows, [
    {key:'factor', label:'Factor'}, {key:'proxy', label:'Proxy'}, {key:'exposure_beta', label:'Exposure Beta'},
    {key:'rolling_6m_beta', label:'6M Beta'}, {key:'factor_annual_volatility_pct', label:'Factor Vol %'},
    {key:'risk_contribution_pct', label:'Contribution %'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">PCA Common Driver</h3>
  ${(data.pca || []).length ? table(data.pca || [], [{key:'component', label:'Component'}, {key:'explained_variance_pct', label:'Explained Variance %'}]) : `<div class="news-item"><div class="news-meta">PCA Status</div><div class="news-title">${esc(data.pca_status === 'insufficient_history' ? 'Insufficient history for PCA common-driver analysis.' : data.pca_status || 'Unavailable')}</div></div>`}
  <div class="news-list" style="margin-top:12px">${(data.commentary || []).map(x => `<div class="news-item"><div class="news-title">${esc(x)}</div></div>`).join('')}</div>
  <div class="panel-note" style="margin-top:12px">${esc(data.disclaimer || '')}</div>`;
}

function renderCorrelationRegime(data) {
  const rows = data.heatmap || [];
  const tickers = data.tickers || [];
  const heatCols = [{key:'ticker', label:'Ticker'}].concat(tickers.map(t => ({key:t, label:t})));
  return `<div class="metrics">
    ${metric('Regime', data.regime || '--')}
    ${metric('Avg Corr 30D', data.average_pairwise_corr_30d)}
    ${metric('Avg Corr 90D', data.average_pairwise_corr_90d)}
    ${metric('Clusters', (data.clusters || []).length)}
  </div>
  <div class="news-item"><div class="news-meta">Diversification Context</div><div class="news-title">${esc(data.commentary || '')}</div></div>
  <h3 class="panel-title" style="margin-top:16px">Clustered Holdings</h3>
  ${table(data.clusters || [], [
    {key:'holding_a', label:'Holding A'}, {key:'holding_b', label:'Holding B'}, {key:'corr_90d', label:'90D Corr'}, {key:'risk', label:'Risk'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">90D Correlation Matrix</h3>
  ${table(rows, heatCols)}`;
}

function renderLiquidityLadder(data) {
  const weights = data.bucket_weights_pct || {};
  return `<div class="metrics">
    ${metric('Liquidity Grade', data.portfolio_liquidity_grade || '--')}
    ${metric('Same-Day', weights['same-day'], '%')}
    ${metric('1-3 Days', weights['1-3 days'], '%')}
    ${metric('Stressed', weights['stressed liquidity'], '%')}
  </div>
  <div class="news-item"><div class="news-meta">Liquidity Planning</div><div class="news-title">${esc(data.commentary || '')}</div></div>
  ${table(data.items || [], [
    {key:'ticker', label:'Ticker'}, {key:'position_value', label:'Position $'}, {key:'portfolio_weight_pct', label:'Weight %'},
    {key:'avg_daily_dollar_volume', label:'Avg $ Vol'}, {key:'market_cap', label:'Market Cap'},
    {key:'normal_liquidation_days', label:'Normal Days'}, {key:'stressed_liquidation_days', label:'Stressed Days'},
    {key:'bucket', label:'Bucket'}, {key:'note', label:'Flag'}
  ])}`;
}

function renderRiskContribution(data) {
  const rows = data.items || [];
  return `<div class="metrics">
    ${metric('Portfolio Vol', data.portfolio_annual_volatility_pct, '%')}
    ${metric('1W VaR', data.portfolio_var_1w_pct, '%')}
    ${metric('Positions', rows.length)}
    ${metric('Largest Risk', rows.length ? Math.max(...rows.map(r=>Number(r.volatility_contribution_pct)||0)) : null, '%')}
  </div>
  ${data.concentration_warning ? `<div class="warning">${esc(data.concentration_warning)}</div>` : ''}
  ${contributionBars(rows, 'volatility_contribution_pct')}
  ${table(rows, [
    {key:'ticker', label:'Ticker'}, {key:'portfolio_weight_pct', label:'Weight %'}, {key:'volatility_contribution_pct', label:'Risk Contrib %'},
    {key:'risk_minus_weight_pct', label:'Risk - Weight'}, {key:'marginal_volatility', label:'Marginal Vol'},
    {key:'component_var_1w_pct', label:'Component VaR %'}, {key:'flag', label:'Flag'}
  ])}
  <div class="panel-note" style="margin-top:12px">${esc(data.commentary || '')}</div>`;
}

function renderPriceRegime(data) {
  const overall = data.overall || {};
  return `<div class="metrics">
    ${metric('Portfolio Regime', overall.regime || '--')}
    ${metric('3M Return', overall.return_3m, '%')}
    ${metric('Volatility', overall.annualized_volatility_pct, '%')}
    ${metric('Drawdown', overall.drawdown_6m_pct, '%')}
  </div>
  <div class="news-item"><div class="news-meta">Risk Context</div><div class="news-title">${esc(data.commentary || '')}</div></div>
  ${table(data.items || [], [
    {key:'ticker', label:'Ticker'}, {key:'regime', label:'Regime'}, {key:'return_1m', label:'1M %'}, {key:'return_3m', label:'3M %'},
    {key:'annualized_volatility_pct', label:'Vol %'}, {key:'drawdown_6m_pct', label:'DD %'},
    {key:'return_autocorr_63d', label:'Autocorr'}, {key:'rsi14', label:'RSI'}, {key:'half_life_days', label:'Half-Life'}
  ])}
  <div class="panel-note" style="margin-top:12px">${esc(data.method || '')} ${esc(data.disclaimer || '')}</div>`;
}

function renderCongress(data) {
  return `<div class="metrics">
    ${metric('Insider Trades', (data.insider_trades || []).length)}
    ${metric('SEC Form 4', (data.sec_form4_filings || []).length)}
    ${metric('Inst. Holders', (data.institutional_holders || []).length)}
    ${metric('Analyst Recs', (data.analyst_recommendations || []).length)}
  </div>${data.message ? `<div class="empty">${esc(data.message)}</div>` : ''}` + table(data.sec_form4_filings || [], [
    {key:'ticker', label:'Ticker'}, {key:'filed_at', label:'Filed'}, {key:'form_type', label:'Form'}, {key:'source', label:'Source'},
    {key:'company', label:'Company'}, {key:'url', label:'Filing', render:r => r.url ? `<a class="neu" href="${esc(r.url)}" target="_blank">Open</a>` : '--'}
  ]) + `<h3 class="panel-title" style="margin-top:16px">Institutional Holders</h3>` + table(data.institutional_holders || [], [
    {key:'ticker', label:'Ticker'}, {key:'Holder', label:'Holder'}, {key:'Shares', label:'Shares'}, {key:'Value', label:'Value'}
  ]) + `<h3 class="panel-title" style="margin-top:16px">Finnhub Recommendations</h3>` + table(data.analyst_recommendations || [], [
    {key:'ticker', label:'Ticker'}, {key:'period', label:'Period'}, {key:'strongBuy', label:'Strong Buy'},
    {key:'buy', label:'Buy'}, {key:'hold', label:'Hold'}, {key:'sell', label:'Sell'}
  ]);
}

function renderSignalActionPlan(data) {
  const rows = data.items || [];
  return `<div class="metrics">
    ${metric('Signals', rows.length)}
    ${metric('Avg Score', rows.length ? rows.reduce((s,r)=>s + (Number(r.signal_score)||0), 0) / rows.length : null)}
    ${metric('Disclaimer', 'Not investment advice')}
  </div>
  ${renderSignalChart(rows)}
  <div class="news-item"><div class="news-meta">Disclaimer</div><div class="news-title">${esc(data.disclaimer || 'Not investment advice.')}</div></div>
  ${data.concentration_warning ? `<div class="news-item"><div class="news-meta">Concentration Warning</div><div class="news-title pos">${esc(data.concentration_warning)}</div></div>` : ''}
  ${table(rows, [
    {key:'ticker', label:'Ticker'}, {key:'signal_score', label:'Score'}, {key:'action', label:'Action'},
    {key:'confidence', label:'Confidence'}, {key:'return_1m', label:'1M %'}, {key:'return_3m', label:'3M %'},
    {key:'return_6m', label:'6M %'}, {key:'rsi14', label:'RSI'}, {key:'macd_trend', label:'MACD'},
    {key:'portfolio_weight_pct', label:'Weight %'}, {key:'reasons', label:'Reasons', render:r => (r.reasons || []).map(esc).join('<br>')}
  ])}`;
}

function renderSignalChart(rows) {
  if (!rows.length) return '<div class="empty">No signal rows returned.</div>';
  return `<div class="chart">${rows.map(r => {
    const score = Math.max(-100, Math.min(100, Number(r.signal_score) || 0));
    const width = Math.max(2, Math.abs(score) / 2);
    const left = score >= 0 ? 50 : 50 - width;
    const color = score > 10 ? 'var(--green)' : score < -10 ? 'var(--red)' : 'var(--blue)';
    return `<div class="bar-row">
      <div>${esc(r.ticker)}</div>
      <div class="bar-track"><span class="bar-zero"></span><span class="bar" style="left:${left}%;width:${width}%;background:${color}"></span></div>
      <div class="${clsNum(score)}">${score}</div>
    </div>`;
  }).join('')}</div>`;
}

function renderConvictionMatrix(data) {
  const rows = data.items || [];
  const concentration = rows.find(r => r.warning);
  return `<div class="metrics">
    ${metric('Holdings', rows.length)}
    ${metric('Avg Quality', rows.length ? rows.reduce((s,r)=>s + (Number(r.fundamental_quality_score)||0), 0) / rows.length : null)}
    ${metric('Avg Momentum', rows.length ? rows.reduce((s,r)=>s + (Number(r.momentum_technical_score)||0), 0) / rows.length : null)}
    ${metric('Bubble Size', 'Weight %')}
  </div>
  ${concentration ? `<div class="warning">${esc(concentration.warning)}</div>` : ''}
  <div id="convictionPlot" class="plot-wrap"></div>
  <h3 class="panel-title" style="margin-top:16px">Matrix Detail</h3>
  ${table(rows, [
    {key:'ticker', label:'Ticker'},
    {key:'fundamental_quality_score', label:'Quality'},
    {key:'momentum_technical_score', label:'Momentum'},
    {key:'portfolio_weight_pct', label:'Weight %'},
    {key:'pnl_pct', label:'P&L %'},
    {key:'signal_score', label:'Signal'},
    {key:'graham_gap', label:'Graham Gap'},
    {key:'rsi14', label:'RSI'},
    {key:'analyst_consensus', label:'Analyst'},
    {key:'quadrant', label:'Quadrant'}
  ])}`;
}

function drawConvictionMatrix(data) {
  const rows = data.items || [];
  const el = document.getElementById('convictionPlot');
  if (!el || !rows.length || !window.Plotly) {
    if (el && !window.Plotly) el.innerHTML = '<div class="empty">Plotly failed to load.</div>';
    return;
  }
  const colors = rows.map(r => r.bubble_state === 'strong' ? '#00FF88' : r.bubble_state === 'deteriorating' ? '#FF4444' : '#FFB020');
  const sizes = rows.map(r => Math.max(18, Math.min(72, 14 + (Number(r.portfolio_weight_pct) || 0) * 1.4)));
  const hover = rows.map(r => [
    `<b>${esc(r.ticker)}</b>`,
    `Weight: ${fmt.format(Number(r.portfolio_weight_pct) || 0)}%`,
    `P&L: ${r.pnl_pct == null ? '--' : fmt.format(r.pnl_pct) + '%'}`,
    `Signal score: ${r.signal_score ?? '--'}`,
    `Graham: ${r.graham_gap == null ? '--' : fmt.format(r.graham_gap) + '%'}`,
    `RSI: ${r.rsi14 ?? '--'}`,
    `Analyst: ${esc(r.analyst_consensus || '--')}`,
    r.warning ? `<br>${esc(r.warning)}` : ''
  ].join('<br>'));
  const trace = {
    type: 'scatter',
    mode: 'markers+text',
    x: rows.map(r => r.fundamental_quality_score),
    y: rows.map(r => r.momentum_technical_score),
    text: rows.map(r => r.ticker),
    textposition: 'top center',
    textfont: {family:'JetBrains Mono, monospace', size: 12, color:'#E0E0E0'},
    hovertext: hover,
    hoverinfo: 'text',
    marker: {
      size: sizes,
      color: colors,
      opacity: 0.82,
      line: {color:'#E0E0E0', width: 1}
    }
  };
  const layout = {
    paper_bgcolor: '#020202',
    plot_bgcolor: '#020202',
    margin: {l: 54, r: 18, t: 24, b: 54},
    autosize: true,
    height: Math.max(420, Math.min(560, window.innerHeight * 0.62)),
    font: {family:'JetBrains Mono, monospace', color:'#E0E0E0'},
    xaxis: {
      title: {text:'Fundamental Quality Score', font:{color:'#4A9EFF'}},
      range: [0, 100],
      gridcolor: '#1D1D1D',
      zeroline: false
    },
    yaxis: {
      title: {text:'Momentum & Technical Strength Score', font:{color:'#4A9EFF'}},
      range: [0, 100],
      gridcolor: '#1D1D1D',
      zeroline: false
    },
    shapes: [
      {type:'line', x0:50, x1:50, y0:0, y1:100, line:{color:'#333', width:1, dash:'dot'}},
      {type:'line', x0:0, x1:100, y0:50, y1:50, line:{color:'#333', width:1, dash:'dot'}}
    ],
    annotations: [
      {x:76, y:94, text:'Strong Compounders', showarrow:false, font:{color:'#00FF88', size:11}},
      {x:24, y:94, text:'Momentum but Expensive', showarrow:false, font:{color:'#FFB020', size:11}},
      {x:76, y:8, text:'Potential Turnarounds', showarrow:false, font:{color:'#4A9EFF', size:11}},
      {x:24, y:8, text:'Weak / Deteriorating', showarrow:false, font:{color:'#FF4444', size:11}}
    ],
    hoverlabel: {bgcolor:'#0A0A0A', bordercolor:'#4A9EFF', font:{color:'#E0E0E0'}},
    showlegend: false
  };
  const config = {responsive: true, displayModeBar: false};
  Plotly.react(el, [trace], layout, config);
}

function renderWealthView(data) {
  const policy = data.policy_allocation || {};
  const committee = data.investment_committee || {};
  const brief = committee.weekly_brief || {};
  const committeeCards = committee.institutional_summary_cards || {};
  const stressDashboard = committee.stress_dashboard || {};
  const drawdown = data.drawdown_survival || {};
  const goals = data.goals_based || {};
  const liquidity = data.liquidity_concentration || {};
  const attribution = data.benchmark_attribution || {};
  const capital = data.capital_efficiency || {};
  const factors = data.factor_exposure || {};
  const factorStress = data.factor_linked_stress || stressDashboard || {};
  const factorStressRows = factorStress.items || factorStress.factor_linked_scenarios || factorStress.factor_shocks || [];
  const marketContext = data.market_context || moduleData.market_context || {};
  const narrative = data.portfolio_narrative || {};
  const rebalance = data.rebalancing_intelligence || {};
  const riskContrib = data.risk_contribution || {};
  const ladder = data.liquidity_ladder || {};
  const topRisks = (committee.priority_engine || []).slice(0, 3);
  const topActions = (committee.decision_queue || []).slice(0, 3);
  const dominantFactor = (factors.factor_contributions || [])[0] || (factors.proxy_factor_exposures || [])[0] || {};
  const worstDrawdown = [...(drawdown.scenarios || [])].sort((a,b) => (Number(b.portfolio_loss_pct) || 0) - (Number(a.portfolio_loss_pct) || 0))[0] || {};
  const worstFactorStress = [...factorStressRows].sort((a,b) => (Number(b.estimated_portfolio_loss_pct) || 0) - (Number(a.estimated_portfolio_loss_pct) || 0))[0] || {};
  const benchmarkGoal = (goals.goals || []).find(g => g.goal_type === 'benchmark_relative') || (goals.goals || [])[0] || {};
  const contributors = (attribution.items || []).filter(r => Number(r.active_contribution_pct) > 0).slice(0, 3);
  const detractors = (attribution.items || []).filter(r => Number(r.active_contribution_pct) < 0).slice(0, 3);
  const efficiencyRows = capital.items || [];
  const lowEfficiency = efficiencyRows.filter(r => ['oversized risk budget','capital review needed'].includes(r.label)).slice(0, 3);
  const metricText = v => (v === null || v === undefined || v === '' ? 'Metric unavailable' : String(v));
  const tableOrMessage = (rows, cols, message='No active items requiring review.') => (rows && rows.length) ? table(rows, cols) : `<div class="empty">${esc(message)}</div>`;
  const conclusionCard = (label, conclusion, support, reason, action) => `<article class="conclusion-card">
    <div class="label">${esc(label)}</div>
    <div class="value">${esc(conclusion || 'Metric unavailable')}</div>
    ${support ? `<div class="support">${esc(support)}</div>` : ''}
    <div class="reason">${esc(reason || 'Metric unavailable')}</div>
    <div class="action">${esc(action || 'Review context')}</div>
  </article>`;
  const topRisk = topRisks[0] || {};
  const benchmarkStatus = benchmarkGoal.status || attribution.relative_status || 'Metric unavailable';
  const allocationBreaches = policy.breaches || [];
  const largestRiskValue = String(topRisk.category || '').toLowerCase().includes('allocation')
    ? 'Critical allocation breach'
    : topRisk.category || 'Risk ranking unavailable';
  const largestRiskSupport = String(topRisk.category || '').toLowerCase().includes('allocation')
    ? `${allocationBreaches.length} policy breaches detected`
    : topRisk.severity || metricText(committeeCards.portfolio_risk_grade || liquidity.risk_grade);
  const largestRiskAction = String(topRisk.category || '').toLowerCase().includes('allocation')
    ? 'Review allocation breaches'
    : topRisk.category ? `Review ${topRisk.category}` : 'Run committee scan';
  const dominantFactorName = dominantFactor.factor
    ? `${String(dominantFactor.factor).replace(/_/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase())} Exposure`
    : 'Factor exposure unavailable';
  const dominantFactorSupport = dominantFactor.risk_contribution_pct != null
    ? `${dominantFactor.risk_contribution_pct}% factor contribution`
    : dominantFactor.exposure_weight_pct != null ? `${dominantFactor.exposure_weight_pct}% proxy exposure` : 'Metric unavailable';
  const benchmarkMain = String(benchmarkStatus).toLowerCase().includes('outperform')
    ? 'Outperforming SPY'
    : String(benchmarkStatus).toLowerCase().includes('behind') || String(benchmarkStatus).toLowerCase().includes('underperform')
      ? 'Behind benchmark'
      : benchmarkStatus;
  const benchmarkSupport = attribution.active_return_pct != null
    ? `${Number(attribution.active_return_pct) >= 0 ? '+' : ''}${attribution.active_return_pct}% active return`
    : benchmarkGoal.excess_return_vs_spy_pct != null ? `${Number(benchmarkGoal.excess_return_vs_spy_pct) >= 0 ? '+' : ''}${benchmarkGoal.excess_return_vs_spy_pct}% vs SPY` : 'Metric unavailable';
  const benchmarkReason = 'Driven by concentrated AI/Semiconductor exposure';
  const actionLabelMap = {
    'allocation breaches': 'Rebalance concentration',
    'concentration risk': 'Rebalance concentration',
    'factor crowding': 'Review semiconductor crowding',
    'regime instability': 'Monitor regime instability',
    'macro mismatch': 'Review macro alignment',
    'drawdown vulnerability': 'Review stress losses',
    'liquidity deterioration': 'Review liquidity capacity'
  };
  const actionLines = topActions.slice(0, 3).map((r, i) => `${i + 1}. ${actionLabelMap[String(r.category || '').toLowerCase()] || r.review_type || r.category || 'Review item'}`);
  const cioSummary = `Portfolio is ${String(benchmarkMain).toLowerCase()}, but ${topRisk.category || 'concentration'}, ${dominantFactor.factor || 'factor crowding'}, and modeled stress losses remain the primary risks.`;
  const detailStressTables = `
    ${tableOrMessage(drawdown.scenarios || [], [
      {key:'scenario', label:'Scenario'}, {key:'shock_pct', label:'Shock %'}, {key:'dollar_loss', label:'Dollar Loss'}, {key:'portfolio_loss_pct', label:'Portfolio Loss %'}, {key:'recovery_needed_pct', label:'Recovery Needed %'}
    ], 'Stress scenarios unavailable.')}
    <h3 class="panel-title" style="margin-top:16px">Factor-Linked Stress Detail</h3>
    ${tableOrMessage(factorStressRows, [
      {key:'scenario', label:'Scenario'}, {key:'shock_assumption', label:'Shock'}, {key:'affected_factor_proxy', label:'Factor/Proxy'},
      {key:'estimated_portfolio_loss_pct', label:'Loss %'}, {key:'estimated_dollar_loss', label:'Dollar Loss'},
      {key:'main_affected_holdings', label:'Affected Holdings'}, {key:'confidence', label:'Confidence'}, {key:'method_used', label:'Method'}
    ], 'Factor-linked stress scenarios unavailable.')}`;
  const benchmarkDetails = `
    ${tableOrMessage(attribution.items || [], [
      {key:'ticker', label:'Ticker'}, {key:'weight_pct', label:'Weight %'}, {key:'asset_return_pct', label:'Asset Return %'},
      {key:'benchmark_return_pct', label:'Benchmark Return %'}, {key:'active_contribution_pct', label:'Active Contribution %'}, {key:'relative_result', label:'Result'}
    ], 'Benchmark attribution unavailable.')}
    <h3 class="panel-title" style="margin-top:16px">Capital Efficiency Detail</h3>
    ${tableOrMessage(efficiencyRows, [
      {key:'ticker', label:'Ticker'}, {key:'capital_efficiency_score', label:'Efficiency'}, {key:'label', label:'Label'}, {key:'decision_option', label:'Decision Option'}
    ], 'Capital efficiency metrics unavailable.')}`;
  const goalsDetails = tableOrMessage(goals.goals || [], [
    {key:'name', label:'Goal'}, {key:'objective', label:'Objective'}, {key:'portfolio_ytd_return_pct', label:'Portfolio YTD %'},
    {key:'spy_ytd_return_pct', label:'SPY YTD %'}, {key:'cpi_inflation_estimate_pct', label:'CPI %'},
    {key:'excess_return_vs_spy_pct', label:'Excess vs SPY %'}, {key:'real_return_after_inflation_pct', label:'Real Return %'},
    {key:'status', label:'Status'}, {key:'risk_mismatch_warning', label:'Mismatch'}
  ], 'Goal not configured.');
  const appendixDetails = `
    <h3 class="panel-title">Risk Contribution</h3>
    ${tableOrMessage((riskContrib.items || []).slice(0, 8), [
      {key:'ticker', label:'Ticker'}, {key:'portfolio_weight_pct', label:'Weight %'}, {key:'volatility_contribution_pct', label:'Risk %'}, {key:'risk_minus_weight_pct', label:'Risk - Weight'}, {key:'flag', label:'Flag'}
    ], 'Risk contribution unavailable.')}
    <h3 class="panel-title" style="margin-top:16px">Liquidity Ladder</h3>
    ${tableOrMessage(ladder.items || [], [
      {key:'ticker', label:'Ticker'}, {key:'bucket', label:'Bucket'}, {key:'normal_liquidation_days', label:'Normal Days'}, {key:'stressed_liquidation_days', label:'Stressed Days'}
    ], 'Liquidity ladder unavailable.')}
    <h3 class="panel-title" style="margin-top:16px">Liquidity & Concentration</h3>
    ${tableOrMessage(liquidity.liquidity || [], [
      {key:'ticker', label:'Ticker'}, {key:'dollar_volume', label:'Dollar Volume'}, {key:'market_cap', label:'Market Cap'}, {key:'liquidity_flag', label:'Flag'}
    ], 'Liquidity detail unavailable.')}`;
	  return `<h3 class="panel-title">WHAT MATTERS NOW</h3>
  <div class="panel-note" style="margin-top:8px">${esc(cioSummary)}</div>
	  <div class="conclusion-grid">
	    ${conclusionCard(
	      'Largest risk',
	      largestRiskValue,
	      largestRiskSupport,
	      topRisk.reason || 'Priority engine has not returned a top risk.',
	      largestRiskAction
	    )}
	    ${conclusionCard(
	      'Worst stress scenario',
	      worstDrawdown.scenario || 'Stress scenario unavailable',
	      worstDrawdown.portfolio_loss_pct != null ? `${worstDrawdown.portfolio_loss_pct}% modeled loss` : 'Metric unavailable',
	      worstDrawdown.recovery_needed_pct != null ? `Recovery needed: ${worstDrawdown.recovery_needed_pct}%` : 'Drawdown survival data is not available.',
	      'Review drawdown survival'
	    )}
	    ${conclusionCard(
	      'Dominant factor',
	      dominantFactorName,
	      dominantFactorSupport,
	      dominantFactor.proxy ? `Proxy: ${dominantFactor.proxy}` : dominantFactor.tickers ? `Holdings: ${dominantFactor.tickers}` : 'Factor model or proxy classification is unavailable.',
	      'Review factor crowding'
	    )}
	    ${conclusionCard(
	      'Benchmark status',
	      benchmarkMain,
	      benchmarkSupport,
	      benchmarkReason,
	      'Review risk-adjusted performance'
	    )}
	    ${conclusionCard(
	      'Top 3 actions',
	      actionLines.length ? actionLines.join('\\n') : 'No active items',
	      topActions.length ? `${topActions.length} queued` : 'No active items',
	      topActions.map(r => `${r.category}: ${r.severity || 'review'}`).join('; ') || 'No active items requiring review.',
	      'Work decision queue'
	    )}
  </div>

  <div class="metrics">
    ${metric('Net Portfolio Value', policy.total_value)}
    ${metric('Risk Grade', committeeCards.portfolio_risk_grade || liquidity.risk_grade || '--')}
    ${metric('Survival Score', stressDashboard.survival_score)}
    ${metric('Capital Efficiency', capital.average_efficiency_score)}
  </div>
  ${miniMarketStrip(marketContext.items || [])}

  <h3 class="panel-title" style="margin-top:16px">Weekly Portfolio Narrative</h3>
  <div class="news-list">
    ${['implicit_bet','main_risks','what_changed_this_week','what_to_watch_next'].map(k => `<div class="news-item"><div class="news-meta">${esc(k.replaceAll('_',' '))}</div><div class="news-title">${Array.isArray(narrative[k]) ? narrative[k].map(esc).join(', ') : esc(narrative[k] || '--')}</div></div>`).join('')}
  </div>

  <h3 class="panel-title" style="margin-top:16px">Risk Requiring Attention</h3>
  ${policy.concentration_warning ? `<div class="warning">${esc(policy.concentration_warning)}</div>` : ''}
  <div class="metrics">
    ${metric('Top Holding', liquidity.top1_weight_pct, '%')}
    ${metric('Top 3 Holdings', liquidity.top3_weight_pct, '%')}
    ${metric('Dominant Factor', dominantFactor.factor || '--')}
    ${metric('Beta', attribution.beta_to_benchmark)}
    ${metric('Tracking Error', attribution.tracking_error_pct, '%')}
  </div>
  <div class="news-list">
    <div class="news-item"><div class="news-meta">Worst Drawdown Survival Scenario</div><div class="news-title">${esc(worstDrawdown.scenario || '--')} · Loss ${esc(worstDrawdown.portfolio_loss_pct ?? '--')}% · Recovery ${esc(worstDrawdown.recovery_needed_pct ?? '--')}%</div></div>
    <div class="news-item"><div class="news-meta">Largest Factor-Linked Stress Scenario</div><div class="news-title">${esc(worstFactorStress.scenario || '--')} · Loss ${esc(worstFactorStress.estimated_portfolio_loss_pct ?? '--')}% · ${esc(worstFactorStress.main_affected_holdings || '--')}</div></div>
  </div>

  <h3 class="panel-title" style="margin-top:16px">Allocation & Stress</h3>
  <div class="metrics">
    ${metric('Policy Breaches', (policy.breaches || []).length)}
    ${metric('Worst DD Loss', worstDrawdown.portfolio_loss_pct, '%')}
    ${metric('Worst Factor Loss', worstFactorStress.estimated_portfolio_loss_pct, '%')}
    ${metric('Liquidity Grade', ladder.portfolio_liquidity_grade || liquidity.risk_grade || '--')}
  </div>
  ${tableOrMessage(policy.breaches || [], [
    {key:'type', label:'Type'}, {key:'name', label:'Name'}, {key:'actual', label:'Actual %'}, {key:'limit', label:'Limit %'}, {key:'severity', label:'Severity'}
  ], 'No active policy breaches requiring review.')}
  ${detailPanel('Show detailed stress tables', detailStressTables)}

  <h3 class="panel-title" style="margin-top:16px">Benchmark & Efficiency</h3>
  <div class="metrics">
    ${metric('Active Return', attribution.active_return_pct, '%')}
    ${metric('Benchmark Return', attribution.benchmark_return_pct, '%')}
    ${metric('Tracking Error', attribution.tracking_error_pct, '%')}
    ${metric('Info Ratio', attribution.information_ratio)}
  </div>
  <div class="news-list">
    <div class="news-item"><div class="news-meta">Top Contributors</div><div class="news-title">${contributors.map(r => `${esc(r.ticker)} ${esc(r.active_contribution_pct)}%`).join('<br>') || 'No active items requiring review.'}</div></div>
    <div class="news-item"><div class="news-meta">Top Detractors</div><div class="news-title">${detractors.map(r => `${esc(r.ticker)} ${esc(r.active_contribution_pct)}%`).join('<br>') || 'No active items requiring review.'}</div></div>
    <div class="news-item"><div class="news-meta">Efficiency Review</div><div class="news-title">${lowEfficiency.map(r => `${esc(r.ticker)}: ${esc(r.label)} · ${esc(r.decision_option || '')}`).join('<br>') || `Average efficiency score: ${esc(capital.average_efficiency_score ?? '--')}`}</div></div>
  </div>
  ${detailPanel('Show benchmark and efficiency detail', benchmarkDetails)}

  <h3 class="panel-title" style="margin-top:16px">Goals & Decision</h3>
  <div class="metrics">
    ${metric('Goal Status', benchmarkGoal.status || '--')}
    ${metric('Excess vs SPY', benchmarkGoal.excess_return_vs_spy_pct, '%')}
    ${metric('Real Return', benchmarkGoal.real_return_after_inflation_pct, '%')}
    ${metric('Queue Items', (committee.decision_queue || []).length)}
  </div>
  ${tableOrMessage((committee.decision_queue || []).slice(0, 6), [
    {key:'review_type', label:'Review'}, {key:'category', label:'Category'}, {key:'severity', label:'Severity'}, {key:'reason', label:'Reason'}
  ], 'No active items requiring review.')}
  ${tableOrMessage((rebalance.options || []).slice(0, 5), [
    {key:'ticker', label:'Scope'}, {key:'option', label:'Option'}, {key:'reason', label:'Reason'}, {key:'risk', label:'Risk'}, {key:'tradeoff', label:'Tradeoff'}
  ], 'No active items requiring review.')}
  ${detailPanel('Show detailed goals table', goalsDetails)}
  ${detailPanel('Show risk and liquidity appendix', appendixDetails)}
  <div class="panel-note" style="margin-top:12px">${esc(data.disclaimer || rebalance.disclaimer || 'Not investment advice.')}</div>`;
}

function renderFamilyOffice(data) {
  const selected = (data.portfolios || []).find(p => String(p.id) === String(selectedPortfolioId)) || (data.portfolios || [])[0] || {};
  const selectedPositions = (data.positions || []).filter(p => p.portfolio_id === selected.id);
  const selectedGoals = (data.goals || []).filter(g => g.portfolio_id === selected.id);
  const goalAnalytics = moduleData.goals_based?.goals || [];
  const selectedGoalRows = selectedGoals.map(g => ({...g, ...(goalAnalytics.find(x => x.name === g.name) || {})}));
  goalAnalytics.forEach(g => {
    if (!selectedGoalRows.some(row => row.name === g.name)) selectedGoalRows.push(g);
  });
  const selectedCash = (data.cash_buckets || []).filter(c => c.portfolio_id === selected.id);
  return `<div class="metrics">
    ${metric('Net Worth', data.net_worth)}
    ${metric('Portfolio Value', data.total_portfolio_value)}
    ${metric('Cash', data.total_cash)}
    ${metric('Cash Allocation', data.cash_allocation_pct, '%')}
  </div>
  <div class="news-item"><div class="news-meta">Selected Portfolio</div><div class="news-title">${esc(selected.name || 'Default Portfolio')} · ${esc(selected.account_type || '--')} · ${esc(selected.risk_profile || '--')}</div></div>
  <h3 class="panel-title" style="margin-top:16px">Portfolio Allocation by Account/Bucket</h3>
  ${table(data.portfolios || [], [
    {key:'name', label:'Portfolio'}, {key:'account_type', label:'Account'}, {key:'stock_value', label:'Stock Value'},
    {key:'cash_value', label:'Cash'}, {key:'total_value', label:'Total'}, {key:'net_worth_weight_pct', label:'Net Worth %'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Selected Portfolio Positions</h3>
  ${table(selectedPositions, [
    {key:'ticker', label:'Ticker'}, {key:'name', label:'Name'}, {key:'theme', label:'Theme'}, {key:'shares', label:'Shares'},
    {key:'price', label:'Price'}, {key:'market_value', label:'Market Value'}, {key:'weight_pct', label:'Net Worth %'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Cross-Portfolio Concentration</h3>
  ${table(data.cross_portfolio_concentration || [], [
    {key:'ticker', label:'Ticker'}, {key:'market_value', label:'Market Value'}, {key:'net_worth_weight_pct', label:'Net Worth %'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Goals by Portfolio</h3>
  ${table(selectedGoalRows, [
    {key:'name', label:'Goal'}, {key:'objective', label:'Objective'},
    {key:'portfolio_ytd_return_pct', label:'Portfolio YTD %'}, {key:'spy_ytd_return_pct', label:'SPY YTD %'},
    {key:'cpi_inflation_estimate_pct', label:'CPI %'}, {key:'excess_return_vs_spy_pct', label:'Excess vs SPY %'},
    {key:'real_return_after_inflation_pct', label:'Real Return %'}, {key:'status', label:'Status'},
    {key:'priority', label:'Priority'}, {key:'risk_tolerance', label:'Risk'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Cash Buckets</h3>
  ${table(selectedCash, [
    {key:'name', label:'Bucket'}, {key:'amount', label:'Amount'}, {key:'purpose', label:'Purpose'}, {key:'currency', label:'Currency'}
  ])}
  <h3 class="panel-title" style="margin-top:16px">Liquidity Overview</h3>
  ${table(data.liquidity_overview || [], [
    {key:'portfolio', label:'Portfolio'}, {key:'cash_value', label:'Cash'}, {key:'cash_weight_pct', label:'Cash %'}, {key:'liquidity_note', label:'Note'}
  ])}
  <div class="panel-note" style="margin-top:12px">Local family-office architecture using SQLite. Existing analytics remain on the config-backed default portfolio until a later multi-portfolio analytics phase.</div>`;
}

function renderWatchlist(data) {
  const rows = data.items || [];
  return `<div class="metrics">
    ${metric('Names Scanned', rows.length)}
    ${metric('Entry Candidates', (data.entry_candidates || []).length)}
  </div>` + table(rows, [
    {key:'ticker', label:'Ticker'}, {key:'category', label:'Category'}, {key:'price', label:'Price'},
    {key:'day_change_pct', label:'Day %'}, {key:'price_source', label:'Source'}, {key:'pe', label:'PE'}, {key:'revenue_growth', label:'Rev Growth'},
    {key:'rsi14', label:'RSI'}, {key:'zscore', label:'Z'}, {key:'signal', label:'Signal'},
    {key:'entry_candidate', label:'Entry?', render:r => r.entry_candidate ? '<span class="pos">YES</span>' : '<span class="muted">NO</span>'}
  ]);
}

function tickerOptions() {
  return portfolio.map(p => `<option value="${esc(p.ticker)}">${esc(p.ticker)}</option>`).join('');
}

function renderInvestmentMemos(data) {
  const rows = data.items || [];
  return `<div class="metrics">
    ${metric('Memos', rows.length)}
    ${metric('Storage', 'SQLite')}
    ${metric('Scope', 'Local')}
    ${metric('Latest', rows[0]?.updated_at ? new Date(rows[0].updated_at).toLocaleDateString() : '--')}
  </div>
  <form class="form-grid" onsubmit="submitMemo(event)">
    <div class="form-field"><label>Ticker</label><select name="ticker">${tickerOptions()}</select></div>
    <div class="form-field"><label>Confidence</label><select name="confidence_level"><option>Medium</option><option>High</option><option>Low</option></select></div>
    <div class="form-field full"><label>Thesis</label><textarea name="thesis" required></textarea></div>
    <div class="form-field full"><label>Risks</label><textarea name="risks"></textarea></div>
    <div class="form-field full"><label>Catalysts</label><textarea name="catalysts"></textarea></div>
    <div class="form-field full"><label>Valuation Logic</label><textarea name="valuation_logic"></textarea></div>
    <div class="form-field"><label>Horizon</label><input name="horizon" placeholder="Example: 3-5 years"></div>
    <div class="form-field"><label>Invalidation Condition</label><input name="invalidation_condition" placeholder="What would break the thesis?"></div>
    <div class="form-field full"><button class="btn" type="submit">Save Memo</button></div>
  </form>
  ${table(rows, [
    {key:'ticker', label:'Ticker'}, {key:'confidence_level', label:'Confidence'}, {key:'horizon', label:'Horizon'},
    {key:'thesis', label:'Thesis'}, {key:'risks', label:'Risks'}, {key:'catalysts', label:'Catalysts'},
    {key:'valuation_logic', label:'Valuation Logic'}, {key:'invalidation_condition', label:'Invalidation'},
    {key:'updated_at', label:'Updated', render:r => new Date(r.updated_at).toLocaleString()}
  ])}`;
}

function renderDecisionJournal(data) {
  const rows = data.items || [];
  return `<div class="metrics">
    ${metric('Entries', rows.length)}
    ${metric('Storage', 'SQLite')}
    ${metric('Behavioral', 'Tracked')}
    ${metric('Latest', rows[0]?.created_at ? new Date(rows[0].created_at).toLocaleDateString() : '--')}
  </div>
  <form class="form-grid" onsubmit="submitJournal(event)">
    <div class="form-field"><label>Ticker</label><select name="ticker">${tickerOptions()}</select></div>
    <div class="form-field"><label>Decision Type</label><select name="decision_type"><option>hold</option><option>trim gradually</option><option>rebalance toward target</option><option>add only if underweight</option><option>watchlist candidate</option><option>thesis review</option></select></div>
    <div class="form-field"><label>Confidence</label><select name="confidence_level"><option>Medium</option><option>High</option><option>Low</option></select></div>
    <div class="form-field"><label>Emotional State</label><input name="emotional_state" placeholder="Calm, FOMO, anxious, anchored..."></div>
    <div class="form-field full"><label>Reason</label><textarea name="reason" required></textarea></div>
    <div class="form-field full"><label>Expected Outcome</label><textarea name="expected_outcome"></textarea></div>
    <div class="form-field full"><label>Actual Outcome Placeholder</label><textarea name="actual_outcome" placeholder="Leave blank until review"></textarea></div>
    <div class="form-field full"><button class="btn" type="submit">Save Journal Entry</button></div>
  </form>
  ${table(rows, [
    {key:'ticker', label:'Ticker'}, {key:'decision_type', label:'Decision'}, {key:'confidence_level', label:'Confidence'},
    {key:'emotional_state', label:'State'}, {key:'reason', label:'Reason'}, {key:'expected_outcome', label:'Expected'},
    {key:'actual_outcome', label:'Actual'}, {key:'created_at', label:'Created', render:r => new Date(r.created_at).toLocaleString()}
  ])}`;
}

async function submitMemo(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  const r = await fetch('/api/memos', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  if (r.ok) {
    event.target.reset();
    await loadDecisionRecords();
    renderContent();
  }
}

async function submitJournal(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  const r = await fetch('/api/journal', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  if (r.ok) {
    event.target.reset();
    await loadDecisionRecords();
    renderContent();
  }
}

function renderPriceAudit(data) {
  const rows = data.items || [];
  const rateLimited = rows.filter(r => r.polygon_failure_reason === 'rate_limited').length;
  const fallback = rows.filter(r => r.fallback_reason).length;
  return `<div class="metrics">
    ${metric('Tickers Audited', rows.length)}
    ${metric('Polygon Rate Limited', rateLimited)}
    ${metric('Fallbacks Used', fallback)}
    ${metric('Policy', '4/min')}
  </div>
  <div class="panel-note">${esc(data.polygon_rate_limit_policy || '')}</div>
  ${table(rows, [
    {key:'ticker', label:'Ticker'},
    {key:'source_used', label:'Source Used', render:r => `<span class="${r.source_used === 'Polygon Official' ? 'pos' : r.source_used === 'Unavailable' ? 'neg' : 'neu'}">${esc(r.source_used || 'Not requested')}</span>`},
    {key:'fallback_reason', label:'Fallback Reason', render:r => r.fallback_reason ? `<span class="neg">${esc(r.fallback_reason)}</span>` : '<span class="muted">--</span>'},
    {key:'polygon_failure_reason', label:'Polygon Failure', render:r => r.polygon_failure_reason ? `<span class="neg">${esc(r.polygon_failure_reason)}</span>` : '<span class="muted">--</span>'},
    {key:'last_successful_polygon_timestamp', label:'Last Polygon Success', render:r => r.last_successful_polygon_timestamp ? new Date(r.last_successful_polygon_timestamp).toLocaleString() : '<span class="muted">--</span>'},
    {key:'latest_trading_date', label:'Latest Trading Date'},
    {key:'cache_hit', label:'Cache', render:r => r.cache_hit ? '<span class="pos">HIT</span>' : '<span class="muted">--</span>'}
  ])}`;
}

function renderBehavioral(data) {
  const scores = Object.entries(data.bias_scores || {}).map(([bias, score]) => ({bias, score}));
  return `<div class="metrics">
    ${metric('Grade', data.overall_grade)}
    ${metric('Avg Bias', data.avg_score)}
    ${metric('HHI', data.portfolio_concentration_hhi)}
    ${metric('Tax Context', 'Jurisdiction-specific')}
  </div>
  <div class="news-item"><div class="news-meta">Devil's advocate</div><div class="news-title">${esc(data.devils_advocate || '')}</div></div>
  ${data.concentration_warning ? `<div class="news-item"><div class="news-meta">Concentration flag</div><div class="news-title pos">${esc(data.concentration_warning)}</div></div>` : ''}
  ${table(scores, [{key:'bias', label:'Bias'}, {key:'score', label:'Score'}])}
  <h3 class="panel-title" style="margin-top:16px">Recommendations</h3>
  <div class="news-list">${(data.recommendations || []).map(x => `<div class="news-item"><div class="news-title">${esc(x)}</div></div>`).join('')}</div>`;
}

function setScanButtons(disabled, label='Run Full Scan') {
  const btn = document.getElementById('scanBtn');
  const refreshBtn = document.getElementById('refreshBtn');
  btn.disabled = disabled;
  refreshBtn.disabled = disabled;
  btn.textContent = disabled ? 'Scanning...' : 'Run Full Scan';
  refreshBtn.textContent = disabled ? 'Refreshing...' : 'Refresh Cache';
}

async function exportWeeklyReport() {
  const btn = document.getElementById('exportBtn');
  btn.disabled = true;
  btn.textContent = 'Exporting...';
  try {
    const r = await fetch('/api/export-report');
    if (!r.ok) {
      const err = await r.json().catch(()=>({error:'Export failed'}));
      alert(err.error || 'Export failed');
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'weekly_portfolio_report.pdf';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Export Weekly Report';
  }
}

async function runFullScan() {
  if (scanInFlight) return;
  scanInFlight = true;
  lastViewedAt = new Date();
  updateViewedTime();
  setScanButtons(true);
  updateSubline({scan_running:true});
  MODULES.forEach(m => moduleLoading[m.id] = true);
  renderTabs();
  renderContent();
  try {
    const start = await fetch('/api/scan', {method:'POST'});
    if (!start.ok && start.status !== 409) throw new Error('scan start failed');
    const startData = await start.json().catch(()=>({}));
    if (startData.scan_started_at) scanStartedAt = startData.scan_started_at;
    updateSubline({scan_running:true, scan_started_at: scanStartedAt});
    const poll = setInterval(async () => {
      const s = await fetch('/api/status').then(r=>r.json()).catch(()=>({scan_running:false}));
      updateSubline(s);
      if (!s.scan_running) {
        clearInterval(poll);
        MODULES.forEach(m => moduleLoading[m.id] = false);
        scanInFlight = false;
        setScanButtons(false);
        await refreshReport();
      }
    }, 2500);
  } catch (e) {
    MODULES.forEach(m => moduleLoading[m.id] = false);
    scanInFlight = false;
    setScanButtons(false);
    moduleData[activeTab] = {error: e.message};
    updateSubline();
    renderTabs();
    renderContent();
  }
}

async function refreshAndScan() {
  await runFullScan();
}

async function runModule(id) {
  moduleLoading[id] = true;
  renderTabs();
  renderContent();
  try {
    const r = await fetch(`/api/scan/${id}`, {method:'POST'});
    const d = await r.json();
    moduleData[id] = d.data || d;
    if (!r.ok && !moduleData[id].error) moduleData[id].error = 'module scan failed';
  } catch (e) {
    moduleData[id] = {error: e.message};
  }
  moduleLoading[id] = false;
  renderTabs();
  renderHoldings();
  renderMarketContextStrip();
  renderContent();
}

init();
</script>
</body>
</html>"""


def schedule_weekly_scan():
    import schedule

    def job():
        log.info("Scheduled scan triggered.")
        try:
            report = engine.run_full_scan()
            save_report(report)
            log.info("Scheduled scan complete.")
        except Exception as exc:
            log.exception("Scheduled scan failed: %s", exc)

    schedule.every().friday.at("18:00").do(job)
    schedule.every().sunday.at("10:00").do(job)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    log.info("Starting server on %s:%s", config.HOST, config.PORT)
    app.run(host=config.HOST, port=config.PORT, debug=False)
