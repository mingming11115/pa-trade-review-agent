# SDD ledger — plan: docs/issues/trade-replay/plan.md (2026-10-03 revision)
Ruling: Work in original directory per explicit user request and approved plan; preserve unrelated dirty baseline, no blanket commits. Cost: diff review must isolate this task.
Pre-flight: tasks 1→3 share order contracts; tasks 2→4 share save/resume APIs; tasks 2→5 share immutable version; task 3→4 share navigation guard. No conflicting interface.
Task 1 backend: 20 tests passed; front OrderTicket RED→GREEN. Conditional order trigger preserved and ambiguous pretrigger limit touch deferred.
Task 2 core: saved/resume/discard test RED→GREEN 9 tests. Immutable snapshots retain open position; resumed order IDs remapped.
Ruling: Use a focused chart wrapper and shared controller, not relocate chart code into App. Cost: both wrapper and base chart require interaction tests.
Tasks 3–4 core: original chart wrapper, shared controller, exit dialog and record list implemented. 62 targeted front tests passed and tsc green before analysis UI.
Task 5 core: saved replay analysis persists task/run and input in one transaction, manager reads durable input; model context includes fills/open positions rather than fabricated closed trades. Analysis tests RED→GREEN; front analysis detail RED→GREEN.
Ruling: Freeze analysis inputs in ReplayAnalysisInput on first analysis; store query/bars on run rather than transient FrozenInputSnapshot registry. This survives restart and reuses run terminal handling. Cost: separate saved-version input table.
Ruling: Create replay tasks/runs atomically under saved version lock rather than separate repository create_task/create_runs transactions; avoids orphan task or duplicate billable runs. Cost: replay analysis adapter directly maps ORM records, tested at transaction boundary.
Final reviewer: integrated_replay_review (gpt-6-astra, fresh read-only). No critical; three important plus initial save-attempt finding fixed.
Final fixed: stale save attempt RED→GREEN for failed-before-commit and committed-lost-response then continue/advance.
Final fixed: triggered stop-limit cannot change side RED→GREEN both buy/sell.
Final fixed: create cancellation generation RED→GREEN; resume unmount covered.
Final fixed: completed legacy rows conversion RED→GREEN.
Final fixed: original period comparison, initial period propagation RED→GREEN and App integration; browser popstate guard RED→GREEN.
Final minor (deferred): pending intrabar stop-limit ambiguity uses conservative trigger state but lacks a detailed deferred-price explanation.
Ruling: Regrade timeframe selection and harmless More-menu exit prompts as functional integration defects and fix them. Cost: broader App navigation regression tests required; tests pass.
Ruling: Saved versions remain immutable; when an unknown save is followed by changed revision/name, use a new operation and preserve any earlier committed version. Cost: rare lost-response retry can retain an additional valid saved version, never discard later trades.
Ruling: Prioritize a one-minute cached read before expanding the market search; same five-attempt seven-day bound. Browser showed 25-second repeated backfills when advancing a 50-minute sliding window. Cost: one extra small query on empty intervals; regression green.
Browser original app5173/8000: buy -> advance filled -> save with position -> record detail -> resume -> sell reduce -> flat -> discard -> original saved position still1. Desktop1280 and narrow viewport checked. No live model execution; isolated manager tests verify successful/failed durable terminal states and HTTP trace.
Final suite before last isolated front control: make test370 backend/121 front; build green. Last previous-candle control validated locally; full frontend/build rerun pending output.
Tasks 1–6 complete: latest backend370 passed; final frontend122 passed/24 files; final tsc+Vite build passed; diff-check passed. Latest evidence and exhaustive rulings copied to docs/issues/trade-replay/verification.md. Working files retained uncommitted per approved plan to avoid mixing unrelated baseline.
