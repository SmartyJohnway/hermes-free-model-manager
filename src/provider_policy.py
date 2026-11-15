"""M1 provider-specific free eligibility policies; fail closed before live account proof."""
from __future__ import annotations

import re

from core import classify_free

PROVIDER_EVIDENCE = {
    "openrouter": "https://openrouter.ai/collections/free-models",
    "kilo_custom": "https://github.com/NousResearch/hermes-agent",
    "nous": "https://portal.nousresearch.com/compare",
    "groq": "https://console.groq.com/docs/billing-faqs",
    "nvidia": "https://docs.api.nvidia.com/nim/docs/product",
    "agnesai": "https://github.com/AgnesAI-Labs/AgnesAI-Models",
    "cloudflare": "https://developers.cloudflare.com/workers-ai/platform/pricing/",
}

# Official Cloudflare Workers AI pricing page: paid billing method required.
# This list is a DENY list, not a positive allowlist of free models.
CLOUDFLARE_PAID_REQUIRED = frozenset({
    "@cf/moonshotai/kimi-k2.6",
    "@cf/moonshotai/kimi-k2.7-code",
    "@cf/zai-org/glm-5.2",
    "@cf/zai-org/glm-5.3",
    "@cf/zai-org/glm-5.3-flash",
    "@cf/deepseek-ai/deepseek-v4-flash-0731",
    "@cf/deepseek-ai/deepseek-v4-pro-0813",
})

SPECIALIZED_PATTERN = re.compile(
    r"(^|[/_.:-])(embed(?:ding|dings)?|rerank(?:er)?|whisper|tts|transcri(?:be|ption)|"
    r"speech|audio|image|video|lyria|guard|moderation|jailbreak|safety|music)"
    r"(?=$|[/_.:0-9-])", re.IGNORECASE
)


def chat_hint(model: str, supported_parameters: object = None,
              output_modalities: object = None) -> str:
    """Conservative modality hint only; M2 must still validate tool calls."""
    name = model.lower()
    if SPECIALIZED_PATTERN.search(name):
        return "non_chat_or_specialized"
    if isinstance(output_modalities, list) and output_modalities:
        if "text" not in {str(x).lower() for x in output_modalities}:
            return "non_chat_or_specialized"
    if isinstance(supported_parameters, list):
        return "tools_advertised" if "tools" in supported_parameters else "tools_not_advertised"
    return "chat_undetermined"


def evaluate_model(provider: str, model: str, *,
                   pricing: object = None, is_free_flag: object = None,
                   billing_mode: object = None, source: str = "unknown",
                   supported_parameters: object = None,
                   output_modalities: object = None) -> dict:
    """No account, inference or billing calls. Catalog eligibility is not live availability."""
    name = provider.lower()
    model_name = model.lower()
    if name == "cloudflare" and model_name in CLOUDFLARE_PAID_REQUIRED:
        status, why = "paid_required", "official_cloudflare_paid_billing_requirement"
    elif name in ("ollama-local", "ollama-launch", "ollama"):
        status, why = "local", "local_not_cloud_free"
    else:
        if is_free_flag is True and name == "kilo_custom":
            # Kilo says isFree is authoritative even for an auto-router with nonzero
            # underlying model pricing. This is not a validated account entitlement.
            status, why = "catalog_free", "kilo_isFree_true"
        else:
            calculated = classify_free(model, pricing)
            if calculated == "catalog_free":
                status, why = calculated, "zero_prompt_completion_and_request_price"
            elif calculated == "paid":
                status, why = calculated, "positive_catalog_price"
            elif billing_mode == "subscription":
                status, why = "subscription_unverified", "subscription_billing_not_free"
            elif name == "groq":
                status, why = "free_plan_candidate", "groq_free_tier_requires_account_proof"
            elif name == "agnesai":
                status, why = "free_plan_candidate", "agnes_free_default_requires_account_proof"
            elif name == "nvidia":
                status, why = "development_trial_candidate", "nvidia_developer_trial_not_unlimited"
            elif name == "cloudflare":
                status, why = "daily_allowance_candidate", "cloudflare_model_and_plan_requires_proof"
            elif name == "ollama-cloud":
                status, why = "cloud_plan_unverified", "remote_ollama_account_access_unknown"
            elif calculated == "free_tag_unverified":
                status, why = calculated, "free_suffixed_id_without_pricing"
            else:
                status, why = "unknown", "missing_free_price_or_plan_evidence"
    capability = chat_hint(model, supported_parameters, output_modalities)
    # M1 never claims the account can use a model; M2 validates quota + tool calls.
    return {
        "eligibility": status, "reason": why, "chat_hint": capability,
        "catalog_free_candidate": status == "catalog_free"
                                  and capability != "non_chat_or_specialized",
        "live_usable": False, "eligible_for_auto_apply": False,
        "documentation": PROVIDER_EVIDENCE.get(name, ""),
        "evidence_source": source,
    }
