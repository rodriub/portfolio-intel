# Portfolio Intelligence Platform

Institutional-style asset and wealth management decision-support dashboard for a self-directed portfolio.

This project is not a trading bot. It is designed to help review portfolio risk, benchmark-relative performance, concentration, liquidity, macro context, factor exposure, stress scenarios, investment memos, and decision discipline.

## Core Principles

- Signal over noise
- Risk-governed decision support
- Benchmark-relative evaluation
- Auditable data sources and fallback logic
- Local-first storage for memos, journal entries, and portfolio records
- No binding buy/sell recommendations
- "Not investment advice" treatment for strategy outputs

## Features

- Executive / CIO Wealth View with "What Matters Now" summary cards
- Portfolio policy and allocation breach monitoring
- Benchmark attribution versus SPY
- Factor exposure and factor-linked stress scenarios
- Drawdown survival analysis
- Correlation regime monitor
- Liquidity ladder and concentration risk
- Portfolio conviction matrix
- Market context for SPY, QQQ, and GLD
- Portfolio news and market context news using available free APIs
- Audit and reliability center for provider status and stale data
- Investment memo and decision journal storage using local SQLite
- PDF and Markdown report exports for AI-readable weekly review

## Data Sources

The platform is designed to work with free or optional API tiers:

- Polygon.io for primary official pricing when configured
- yfinance for fallback prices, fundamentals, earnings, holders, and options metadata
- FRED for macroeconomic series
- NewsData for general delayed news context
- Finnhub free endpoints where available
- Tiingo where configured
- SEC API and public EDGAR fallback for filings context
- FMP as optional fundamentals backup

## Quick Start

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.py config.py
cp .env.example .env
python app.py
```

Open:

```text
http://127.0.0.1:8080
```

The app uses port `8080` to avoid common macOS AirPlay conflicts on port `5000`.

## Configuration

API keys are read from environment variables. See `.env.example` for names.

Public users should start from the sample configuration:

```bash
cp config.example.py config.py
```

Then edit `config.py` locally with their own portfolio assumptions. Do not commit a real `config.py` if it contains personal holdings, account values, cost basis, notes, goals, or tax context.

## Reports

The dashboard supports:

- `/api/export-report` for PDF export
- `/report/print` for browser print / Save as PDF
- `/api/export-report-md` for Markdown export to use with ChatGPT or Claude

## Safety

This platform is for decision support only. It does not provide financial, legal, or tax advice. Outputs are non-binding and should be reviewed with appropriate professional judgment.

Not investment advice.

# Portfolio Intelligence Platform

A multi-provider wealth management and portfolio intelligence platform integrating macroeconomic monitoring, factor exposure analysis, stress testing, portfolio governance, and risk management.

---

## Executive Wealth View

![Wealth View](screenshots/Screenshot%202026-06-05%20at%208.16.52%E2%80%AFPM.png)

## Macro Pulse

![Macro Pulse](screenshots/Screenshot%202026-06-05%20at%208.17.06%E2%80%AFPM.png)

## Audit & Reliability

![Audit & Reliability](screenshots/Screenshot%202026-06-05%20at%208.17.40%E2%80%AFPM.png)

## Position Sizing Engine

![Position Sizing](screenshots/Screenshot%202026-06-05%20at%208.18.43%E2%80%AFPM.png)
