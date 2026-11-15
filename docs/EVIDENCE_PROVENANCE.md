# Evidence provenance and availability

This document distinguishes **locally inspectable artifacts** from historical account observations whose original reports are not included in the M6 installation.

## Available local artifacts
- `../reports/eligibility_catalog.json`: catalog and eligibility candidate snapshot, generated 2026-11-14T17:38:53Z; candidate metadata is not a live account entitlement guarantee.
- `../reports/tool_calling_evidence.json`: offline aggregated Tool Calling results, generated 2026-11-13T07:42:00Z; not a new live test.
- `../reports/manual_tool_calling/`: saved bounded manual Tool Calling batch results; inspect individual JSON before claiming a model passed.
- `MODEL_VALIDATION.md`: current 7/30-day evidence freshness policy.
- `../state/ai_recommendation_request.json`: generated recommendation request and source references; not independent proof of vendor account eligibility.
- `VALIDATION.md`: release smoke and isolated-test summary.

## Historical reports unavailable in the official installation
The original Browser Use account audit, Z.ai/NVIDIA account audit, Cloudflare pagination review, and historical workflow specification referenced by older documents were **not found** in the M6 installation or archived R&D folder during the 2026-11-15 inspection. Their previous claims must not be presented as independently reverified from those missing reports.

Use the local JSON snapshots for historical candidate and synthetic Tool Calling data, and refresh current vendor eligibility from authorized sources when needed. Do not assume every catalog entry is free or presently usable. Account-plan evidence may require a new bounded check.

## Safe operation
M6 is a companion Manager, not a Hermes Desktop fork. Read `AI_WORKFLOW.md` and root `AI_RECOMMENDATION_AGENT_GUIDE.md`. Read-only status, catalog and preview require no production write. Never apply or restore production Hermes configuration without the user's explicit authorization.