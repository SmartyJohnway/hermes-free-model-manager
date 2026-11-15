# ChatGPT recommendation workflow

## Product responsibilities
The user chooses the primary model. ChatGPT evaluates fallback candidates and recommends an ordered list. The Manager validates input, previews changes, and applies only on explicit authorization. It is not an autonomous ranking engine.

## Read-only orientation
From the repository root, run `python -B scripts/manager_cli.py status`, `catalog`, `preview`, and `restore-info`. Inspect `docs/EVIDENCE_PROVENANCE.md` before interpreting candidate eligibility.

## Refresh and recommendation
1. On user request, `get` refreshes metadata using bounded GET, not inference POST.
2. `export` writes `state/ai_recommendation_request.json` with current catalog and Hermes baseline.
3. ChatGPT considers free-tier evidence, provider diversity, Tool Calling history, coding/agent suitability and uncertainty. Unknown evidence is not a model failure.
4. Create `state/ai_recommendation.json` matching schema `hermes-free-manager-ai-recommendation.v1`, the request ID, catalog hash, config baseline hash, and ordered fallback.
5. `validate` checks the proposal. `stage` writes only a Manager draft. `preview` compares the draft to official Hermes.
6. Only after explicit user approval, `apply-confirmed` uses native Hermes configuration transaction with SHA guard, backup and readback.
7. If necessary, `restore-info` inspects reversible transactions; `restore-confirmed` requires separate explicit approval.

## Evidence and risk
- Catalog snapshots are not current vendor account guarantees.
- Tool Calling PASS evidence is distinct from account free eligibility.
- Evidence older than 7 days merits retest consideration; older than 30 days is stale, not automatic FAIL.
- Optional real Tool Calling POST is a separate user-authorized action.
- Do not copy credentials or private Hermes configuration into recommendations.
- The historical Browser Use account audit is unavailable; the compatibility document is not original audit proof.

## Compatibility
Persisted schema identifiers and existing `reports/eligibility_catalog.json`, `reports/tool_calling_evidence.json`, and existing Request evidence paths remain supported. Their phase-derived filenames are legacy data contracts, not current product milestones.
