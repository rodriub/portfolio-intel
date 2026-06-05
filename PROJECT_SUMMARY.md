# Project Summary: Portfolio Intelligence Platform v1.0

## Positioning

Portfolio Intelligence is a local-first institutional wealth and asset management decision-support platform. It reframes a retail portfolio dashboard into a CIO-style review system focused on risk, benchmark-relative outcomes, concentration, liquidity, factor exposure, macro context, and decision discipline.

## What It Does

The platform consolidates portfolio data, market context, macro indicators, news, factor models, stress tests, allocation policy, and behavioral review into a single dashboard and exportable weekly report.

The primary view is the Executive / CIO layer, which answers:

- What matters now?
- Which risks require attention?
- Is performance benchmark-relative or only nominal?
- Which scenarios could materially impair the portfolio?
- Which review actions should be queued without issuing hard buy/sell instructions?

## Architecture

- `app.py`: Flask app, routes, dashboard UI, PDF/Markdown export, local database routes
- `engine.py`: data clients, analytics modules, risk models, stress tests, signal generation
- `config.py`: local portfolio, policy, watchlist, API env var wiring, benchmark goals
- `requirements.txt`: Python dependencies
- `portfolio_intel.db`: local SQLite runtime database, not intended for public commit

## Analytics Layers

1. Executive / CIO Layer
   - CIO brief
   - Wealth View
   - priority engine
   - stress dashboard
   - allocation drift
   - institutional summary cards

2. Risk & Allocation Layer
   - risk analytics
   - risk contribution
   - factor exposure
   - correlation regime
   - liquidity ladder
   - benchmark attribution
   - capital efficiency
   - macro regime

3. Research & Monitoring Layer
   - market context
   - portfolio news
   - fundamentals
   - watchlist scan
   - price regime
   - conviction matrix
   - signal and action plan
   - macro pulse
   - insiders and filings

4. Operations & Audit Layer
   - API/provider status
   - price audit
   - fallback reasons
   - stale data warnings
   - cache age
   - module freshness

5. Knowledge & History Layer
   - investment memos
   - decision journal

## Release Notes

Version 1.0 is suitable as a portfolio analytics and decision-support showcase if local runtime files are kept private and any personal portfolio details in `config.py` are intentionally disclosed or sanitized before publication.

No API keys should be committed. No database, cache, log, or generated report files should be committed.

Not investment advice.
