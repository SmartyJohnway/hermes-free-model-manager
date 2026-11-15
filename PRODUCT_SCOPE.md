# Product scope

Hermes Free Model Manager is a companion to an installed Hermes application, not a Desktop fork.

## Functions
1. Read installed Hermes configuration and display current primary/fallback.
2. Discover candidate models with user-initiated metadata GET.
3. Preserve catalog, account eligibility and Tool Calling as separate evidence categories.
4. Export AI Request JSON; accept and validate ChatGPT recommendation JSON.
5. Stage and preview changes; apply or restore only with explicit user approval.
6. Protect production settings with baseline SHA-256, backup and transaction checks.

## Non-goals
- Automatic fallback ranking, primary selection, model inference on a schedule, or claiming all listed models are free.
- Public release or distribution of user account evidence and private state.

## Compatibility
JSON schema `hermes-free-manager-ai-request.v1` and `hermes-free-manager-ai-recommendation.v1` remain stable. Historical snapshot names, transaction status strings and backup directories remain intact where persisted data or official Hermes integrations require them. Their legacy labels are not current milestone requirements.

## Documentation policy
Operational instructions describe functions rather than development gates or milestone identifiers. Historical development and test scripts are archived separately from production.
