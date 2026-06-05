<!--
Sync Impact Report
Version change: template → 1.0.0
Modified principles:
- Placeholder principles → I. Asset & Wealth Management Purpose
- Placeholder principles → II. Risk-Governed Decision Support
- Placeholder principles → III. Benchmark-Relative Evaluation
- Placeholder principles → IV. Data Reliability, Auditability & Source Discipline
- Placeholder principles → V. Modular, AI-Readable & Mobile-Friendly Delivery
Added sections:
- Platform Constraints
- Development Workflow & Quality Gates
Removed sections:
- Placeholder Section 2
- Placeholder Section 3
Templates requiring updates:
- ✅ updated .specify/templates/plan-template.md
- ✅ updated .specify/templates/spec-template.md
- ✅ updated .specify/templates/tasks-template.md
- ✅ checked .specify/templates/commands/*.md (directory absent)
- ✅ updated AGENTS.md
Follow-up TODOs:
- None
-->

# Portfolio Intelligence Constitution

## Core Principles

### I. Asset & Wealth Management Purpose
This platform MUST operate as an institutional-style asset and wealth management
decision-support system, not as a trading dashboard. Features MUST prioritize
portfolio policy, allocation, risk, liquidity, benchmark context, goals, memos,
auditability, and investment committee review over short-term trade excitement.
Trading-style features are allowed only when framed as risk context or governance
inputs, never as standalone calls to action.

Rationale: The platform exists to improve wealth-management discipline and weekly
or daily strategy review, not to maximize screen noise or encourage impulsive
transactions.

### II. Risk-Governed Decision Support
Predictions, signals, and recommendations MAY be produced, but they MUST be
probabilistic, risk-aware, benchmark-relative, and non-binding. The platform MUST
never present outputs as guaranteed outcomes or hard buy/sell instructions.
Recommendation language MUST include risk, tradeoff, confidence, and “Not
investment advice” wherever strategy or action language appears.

Rationale: Decision quality depends on explicit uncertainty, downside survival,
and behavioral discipline. Risk-governed recommendations are acceptable; blind
prediction and imperative trading instructions are not.

### III. Benchmark-Relative Evaluation
Portfolio goals and performance assessments MUST prefer benchmark-relative and
real-return evaluation over speculative fixed-dollar targets. SPY and CPI/inflation
context are the default comparison set unless a feature explicitly defines another
benchmark. Goals MUST show whether the portfolio is ahead or behind relevant
benchmarks and MUST avoid implying guaranteed paths to a target dollar amount.

Rationale: Wealth-management review needs opportunity-cost and purchasing-power
context. Fixed-dollar targets can mislead when detached from inflation, benchmark
returns, volatility, and liquidity needs.

### IV. Data Reliability, Auditability & Source Discipline
Every data-dependent feature MUST degrade gracefully when an API, cache, or provider
fails. The system MUST expose provider status, source coverage, fallback reasons,
stale-data warnings, cache age, and data quality where relevant. Free/local data
sources are the default; paid APIs MUST NOT be added unless explicitly justified
by user approval and documented tradeoffs. External API failures MUST be logged
without crashing scans or exports.

Rationale: Institutional decision support is only useful when users can tell what
data is fresh, delayed, missing, or inferred. Reliability and auditability are
mandatory product features, not implementation details.

### V. Modular, AI-Readable & Mobile-Friendly Delivery
The platform MUST preserve modular architecture and avoid large rewrites unless
they are necessary to maintain correctness or architecture. Dashboard and report
outputs MUST be mobile-friendly, institutional in tone, and organized for signal
over noise. PDF and Markdown exports MUST remain supported and AI-readable for
ChatGPT/Claude strategy reviews. Backward compatibility for existing routes,
modules, configuration, cache files, and local SQLite data MUST be preserved
unless a migration plan is documented.

Rationale: The platform has many interdependent modules. Sustainable iteration
requires stable module boundaries, clean exports, mobile usability, and formats
that can be reviewed by humans and AI systems.

## Platform Constraints

- The system MUST preserve existing dashboard modules unless a feature explicitly
  documents a migration or replacement.
- The default information hierarchy MUST prioritize signal over noise and keep
  Executive/CIO, Risk & Allocation, Research & Monitoring, Operations & Audit,
  and Knowledge & History concepts coherent across dashboard, PDF, and Markdown.
- Features that alter strategy, recommendation, goal, or benchmark language MUST
  include “Not investment advice” in user-facing outputs.
- Features MUST avoid paid APIs by default and use existing free/local sources:
  Polygon if configured, FRED, yfinance, NewsAPI, Tiingo/Finnhub if available,
  SEC/EDGAR, local cache, and SQLite.
- Data-fetching changes MUST include try/except logging and graceful fallback
  behavior. A known provider failure MUST NOT repeat noisy logs per ticker when
  a provider or endpoint is known unavailable.
- UI changes MUST remain mobile-friendly and preserve the established
  institutional visual style unless the specification explicitly authorizes a
  redesign.

## Development Workflow & Quality Gates

- Specs MUST identify whether a feature affects recommendations, goals,
  benchmarks, auditability, exports, or mobile UI.
- Plans MUST pass a Constitution Check covering asset/wealth-management purpose,
  risk governance, benchmark-relative framing, data reliability, modularity,
  backward compatibility, AI-readable exports, and no-paid-API discipline.
- Tasks MUST include validation for changed modules, routes, exports, caches, and
  local database migrations where applicable.
- Implementations MUST run the project’s requested syntax checks before handoff.
  For this repository, use `venv/bin/python -m py_compile config.py engine.py app.py`
  when Python source files change.
- Large rewrites MUST be justified in the plan with the smaller alternative that
  was rejected and the reason the rewrite is necessary.

## Governance

This constitution supersedes conflicting implementation preferences, templates,
and feature requests unless the user explicitly amends it. Amendments require a
documented rationale, a semantic version bump, and a Sync Impact Report describing
affected templates and follow-up work.

Versioning policy:
- MAJOR increments for backward-incompatible governance changes or principle
  removals/redefinitions.
- MINOR increments for new principles, new governance sections, or materially
  expanded platform constraints.
- PATCH increments for clarifications, wording fixes, or non-semantic refinements.

Compliance review expectations:
- Every specification, plan, task list, implementation, dashboard change, and
  export change MUST be checked against the Core Principles.
- Exceptions MUST be explicit, justified, and recorded in the relevant plan or
  specification.
- Data reliability, benchmark-relative framing, and “Not investment advice”
  language are mandatory gates for strategy or recommendation features.

**Version**: 1.0.0 | **Ratified**: 2026-05-19 | **Last Amended**: 2026-05-19
