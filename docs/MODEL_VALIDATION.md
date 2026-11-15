# Model validation and evidence policy

- Catalog discovery is metadata-only and does not certify account entitlement.
- Account eligibility, advertised Tool Calling capability and actual bounded Tool Calling results must be recorded separately.
- A prior Tool Calling PASS is fresh for up to 7 days, merits retest consideration afterward, and is stale after 30 days; age alone is not a failure.
- Unknown Tool Calling status must not automatically exclude a model from ChatGPT recommendations.
- User must authorize actual POST probes. Respect provider-specific limits and stop on authentication, payment, retired endpoint or rate-limit responses.
- Synthetic Tool Calling PASS does not prove the full multi-turn Hermes Agent workflow.
- Current evidence locations: `../reports/eligibility_catalog.json`, `../reports/tool_calling_evidence.json`, `../reports/manual_tool_calling/`, and `EVIDENCE_PROVENANCE.md`.
