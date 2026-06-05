# Feature Specification: Market Context Polish

**Feature Branch**: `001-market-context-polish`

**Created**: 2026-05-19

**Status**: Draft

**Input**: User description: "Implement a market context layer and UI status polish. Improve benchmark awareness, market context, and dashboard signal hierarchy without changing portfolio holdings or redesigning the entire dashboard."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - View Market Context Separately (Priority: P1)

As the investor, I want SPY, QQQ, and GLD shown as market context indicators
separate from my holdings so I can understand benchmark and macro backdrop
without confusing those tickers with owned positions.

**Why this priority**: The portfolio is benchmark-relative and wealth-management
oriented. Market context must be visible but must not contaminate portfolio
value, weights, goals, allocation policy, or family-office net worth.

**Independent Test**: Load the dashboard after a scan or cached report and verify
that SPY, QQQ, and GLD appear only in a clearly labeled market-context surface,
while portfolio value, holdings, weights, and policy calculations remain based
only on actual holdings.

**Acceptance Scenarios**:

1. **Given** a dashboard report exists, **When** the user views the dashboard,
   **Then** SPY, QQQ, and GLD are visible under a label that states
   “Market Context — Not Portfolio Holdings”.
2. **Given** SPY, QQQ, and GLD market data is available, **When** the user reviews
   the market context area, **Then** each ticker shows latest official close,
   daily change, YTD return when available, and source.
3. **Given** market context data is unavailable or stale, **When** the dashboard
   loads, **Then** the market context area shows unavailable/stale status without
   affecting any portfolio calculations.

---

### User Story 2 - Review Market Context News Separately (Priority: P2)

As the investor, I want market news grouped separately from portfolio news so I
can distinguish broad-market, growth, and safe-haven context from company-specific
portfolio headlines.

**Why this priority**: Market news is useful for narrative and regime context,
but mixing it into portfolio news would reduce signal clarity and make the
holdings feed harder to interpret.

**Independent Test**: Open the research/monitoring area and verify that market
context news appears in a separate section grouped by Broad Market/SPY,
Nasdaq/Growth/QQQ, and Gold/Safe Haven/GLD, with deduplicated delayed/source
labels.

**Acceptance Scenarios**:

1. **Given** market news is available, **When** the user opens Market Context
   News, **Then** headlines are grouped into Broad Market / SPY,
   Nasdaq / Growth / QQQ, and Gold / Safe Haven / GLD.
2. **Given** a headline appears from multiple sources, **When** the market news
   feed is rendered, **Then** the duplicate headline appears only once within
   the market context news surface.
3. **Given** market context news exists, **When** the user reviews Portfolio News,
   **Then** market context headlines are not mixed into the portfolio ticker news
   feed.

---

### User Story 3 - Understand Module and Layer Status (Priority: P3)

As the investor, I want module status dots and active layer outlines to reflect
real scan state so I can quickly tell which modules are loaded, scanning,
inactive, or failed.

**Why this priority**: The dashboard has many modules. Accurate status indicators
reduce horizontal noise and support signal-over-noise hierarchy.

**Independent Test**: Load cached data, run a scan, run a single module, and
open modules in different groups. Verify dot colors and the active group outline
follow the actual state and selected module.

**Acceptance Scenarios**:

1. **Given** a module has data from the latest report, **When** navigation is
   shown, **Then** its status dot is green.
2. **Given** a module is currently scanning, **When** navigation is shown,
   **Then** its status dot is amber.
3. **Given** a module has no data loaded, **When** navigation is shown,
   **Then** its status dot is gray.
4. **Given** a module failed, **When** navigation is shown, **Then** its status
   dot is red.
5. **Given** a module is selected, **When** the grouped navigation is shown,
   **Then** only that module’s parent layer has the active blue outline.

---

### User Story 4 - Preserve Holding Signal Hierarchy (Priority: P3)

As the investor, I want all holding cards to share a consistent subtle outline,
with concentrated-position risk shown as a separate warning badge, so the holdings strip
communicates both portfolio ownership and concentration risk clearly.

**Why this priority**: A concentrated position requires special review, but making only
that position visually outlined makes the holdings strip look inconsistent and can confuse
ownership status with warning status.

**Independent Test**: Review the holdings strip on desktop and mobile and verify
that every actual holding has a subtle green outline, selected or highlighted
holdings have a stronger glow, and any concentrated position has a separate concentration warning
badge.

**Acceptance Scenarios**:

1. **Given** the holdings strip is visible, **When** the user reviews holdings,
   **Then** all portfolio holding cards have a subtle green outline.
2. **Given** a holding is selected or highlighted, **When** the holdings strip is
   visible, **Then** that card has a stronger green glow than the default outline.
3. **Given** a holding remains concentrated, **When** holdings are shown,
   **Then** that holding displays a separate concentration warning badge without being
   the only card with an outline.

### Edge Cases

- Market context ticker data is stale, unavailable, or falls back to a delayed
  source.
- One or more context tickers have no YTD return available.
- Market context news sources return duplicate headlines or no headlines.
- A cached report lacks the new market context module because it predates the
  feature.
- A module has both old cached data and a latest-run error; the error state must
  be visible.
- The user views the dashboard on a phone with limited horizontal space.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST add SPY, QQQ, and GLD as market context tickers that
  are not portfolio holdings.
- **FR-002**: System MUST ensure SPY, QQQ, and GLD do not affect portfolio value,
  portfolio P&L, position weights, risk contribution, goals, family-office net
  worth, or allocation policy.
- **FR-003**: System MUST show a clearly labeled market context surface titled
  “Market Context — Not Portfolio Holdings”.
- **FR-004**: System MUST show latest official close, daily change, YTD return
  when available, and source for each market context ticker.
- **FR-005**: System MUST allow market context to inform macro regime, benchmark
  attribution, weekly narrative, CIO Brief, and institutional reports only as
  benchmark or market backdrop context.
- **FR-006**: System MUST provide a Market Context News section separate from
  Portfolio News.
- **FR-007**: System MUST group Market Context News into Broad Market / SPY,
  Nasdaq / Growth / QQQ, and Gold / Safe Haven / GLD.
- **FR-008**: System MUST deduplicate Market Context News by normalized headline
  and show delayed/source badges.
- **FR-009**: System MUST keep market context headlines out of Portfolio News.
- **FR-010**: System MUST restore module status dots with green for loaded/active,
  amber for loading/scanning, gray for inactive/not loaded, and red for
  error/failed.
- **FR-011**: System MUST ensure module status dots reflect actual latest
  scan/report state rather than marking every module green by default.
- **FR-012**: System MUST show the active blue outline only around the currently
  selected module’s parent layer.
- **FR-013**: System MUST give all actual portfolio holding cards a subtle green
  outline.
- **FR-014**: System MUST show selected or highlighted holding cards with a
  stronger green glow than ordinary holding cards.
- **FR-015**: System MUST show a separate concentration warning badge when a
  holding remains concentrated.
- **FR-016**: System MUST keep existing dashboard modules and routes working.
- **FR-017**: System MUST keep PDF and Markdown reports aligned when market
  context appears in reports.
- **FR-018**: If the feature includes signals, predictions, strategy, goals, or
  recommendations, outputs MUST be non-binding, risk-aware, benchmark-relative
  where relevant, and include “Not investment advice.”
- **FR-019**: If the feature depends on external or cached data, it MUST expose
  source status, fallback behavior, stale-data handling, and graceful failure
  behavior.
- **FR-020**: If the feature changes reportable analytics or hierarchy, PDF and
  Markdown exports MUST remain aligned and AI-readable.
- **FR-021**: The feature MUST preserve existing modules, routes, configuration,
  cache files, and local user data unless a migration plan is specified.
- **FR-022**: The feature MUST avoid paid data sources unless explicit approval and
  justification are included in this specification.

### Key Entities *(include if feature involves data)*

- **Market Context Ticker**: A non-holding benchmark/indicator ticker, including
  symbol, display label, latest official close, daily change, YTD return, source,
  freshness status, and delayed/stale indicators.
- **Market Context News Group**: A grouped market news collection for Broad
  Market / SPY, Nasdaq / Growth / QQQ, or Gold / Safe Haven / GLD, including
  normalized headline, source, date, URL when available, and delayed/source badge.
- **Module Status**: Navigation state for a dashboard module, including loaded,
  loading, inactive, or error status and the latest report/scan basis for that
  state.
- **Layer Status**: Group-level navigation state that identifies the single
  currently active layer and summarizes child-module status.
- **Holding Card Warning**: A portfolio holding display annotation separate from
  ownership styling, used for concentrated-position risk.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of market context tickers appear only in market context
  surfaces and never in holdings, portfolio value, weights, allocation policy,
  goals, risk contribution, or family-office net worth.
- **SC-002**: Users can identify SPY, QQQ, and GLD latest close, daily change,
  YTD return when available, and source within 10 seconds of opening the
  dashboard.
- **SC-003**: 100% of market context news headlines are shown outside the
  Portfolio News feed and grouped under the three required market context groups.
- **SC-004**: Module status dots show the correct state for loaded, scanning,
  inactive, and failed modules in all tested cached-report and scan states.
- **SC-005**: Exactly one layer shows the active blue outline at any time, and it
  matches the selected module’s parent layer.
- **SC-006**: 100% of actual holding cards show a subtle green outline, and
  concentrated-position risk appears as a separate warning badge.
- **SC-007**: Strategy or recommendation outputs can be interpreted as
  decision-support context without hard buy/sell instructions.
- **SC-008**: Report/export changes are readable in both browser/PDF form and
  Markdown form for ChatGPT/Claude review.

## Assumptions

- SPY, QQQ, and GLD are the initial market context tickers and are sufficient for
  this feature.
- Market context can use existing free or configured data sources and local cache;
  no new paid data source is approved.
- Market context should appear in the dashboard hierarchy without a full visual
  redesign.
- Cached reports created before this feature may lack market context data and
  should degrade gracefully until the next scan.
- Concentration warnings remain relevant and must remain visible as governance
  signals, not trading instructions.
