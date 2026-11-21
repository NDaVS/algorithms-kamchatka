# Handoff — v4.8.1

Read AGENTS → STATUS → 07_IMPLEMENTATION_PLAN → this file → CHANGELOG. Restore `git clone -b work/v4.8.1 SnowSite-v4.8.1-history.bundle SnowSite-Kamchatka`. Verify clean tree and manifest. Canonical apps/packages/workers; legacy wrappers only. No remote push. Preserve older audits/benchmarks.

## Completed this iteration
- Restored v4.8 `dd1b56a`, original 328 manifest entries and 104 baseline tests PASS. Added `tests/v481-acceptance.test.mjs` for outstanding B07/B09/B21 proof. All three focused checks pass against existing production code. Actual HTTP abort kills deliberately stalled child; next solve succeeds. Missing Python test verifies typed HTTP/CLI error and recovery. Million m³/Q=1 returns compact assignment; 1m+1 rejected; measured maximum RSS, no hard OS memory-limit assertion.
- Final 107/107 tests, graph unchanged, six schemas and 26 instances PASS. Raw evidence in `docs/benchmarks/v481/`. No runtime/data/math/API/schema change, no platform/research/deployment work. Previous v4.7 ranking proof and v4.8 SDK evidence unchanged.
- Updated README/README_FIRST, AGENTS, STATUS, HANDOFF, live 07 plan and 08 acceptance status, API/ALGORITHM/ARCHITECTURE/CODE_MAP and history. Marked legacy planning snapshots historical. No new tasks added.

## Remaining existing tasks and blockers (not active work)
1. **B22/A14 browser gate BLOCKED_ENVIRONMENT.** v4.8 Playwright initial navigation blocked by policy, cannot validate actual click/import/map behavior here. If owner later authorizes a working browser environment, use existing `python tools/acceptance.py --mode browser --output runtime/owner-browser-unique`; do not bypass policy or retry in the same environment.
2. **Windows and GitHub Actions PAUSED_BY_OWNER.** Owner reports existing local Windows launch works; not formal platform/CI PASS. Preserve START.bat and workflows unchanged. No remote operations or repetitive platform tests.
3. **A08-DATA / S5-SITES and existing GIS-01…07 WAIT_DATA / DEFERRED_RESEARCH.** Real site leads, verified access geometry, capacity, status, road restrictions, demand/trucks/receiving and economics require research/actual inputs. [DEFERRED_SITE_DATA_PLAN](DEFERRED_SITE_DATA_PLAN.md). Do not fabricate, start synthetic GIS expansion or add tasks. Resume explicit existing research track only after owner asks.
4. **R5 WAIT_REQUIREMENTS.** Optional deployment and hard process-tree/Python resource limits depend on target host, workload/quotas, persistence, authorization and ownership. B09 scoped local acceptance does not prove native memory cap; do not add arbitrary platform-specific limits or production infrastructure. Larger scale workloads not covered by seed719 guarantee; don't reopen accepted benchmark without concrete need.

## Next action
Review these remaining items with the owner. No unblocked agreed new implementation task remains under current no-research/no-new-backlog instruction. Stop at this tested state; do not treat lack of external data/platform requirements as a reason to generate more versions. If an actual defect is demonstrated, fix the specific existing criterion, run tests, append history and regenerate manifest. If a blocked task is explicitly restarted, follow its existing plan. No publication.
