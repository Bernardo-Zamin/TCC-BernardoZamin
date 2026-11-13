"""Models under test, prices and cost calculation.

Prices were copied by hand from https://deepinfra.com/pricing and
https://platform.claude.com/docs/en/about-claude/pricing and may change. Token
counts come from the `usage` field of each response.
"""

from dataclasses import dataclass

DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"


@dataclass(frozen=True)
class ModelSpec:
    key: str  # short name used in scripts and results
    model_id: str  # id required by the provider's API
    usd_per_m_input: float
    usd_per_m_output: float
    # DeepInfra goes through the shim; Anthropic uses OpenClaw's native provider.
    provider: str = "deepinfra"
    # Anthropic bills cache reads and writes apart from `input`.
    usd_per_m_cache_read: float = 0.0
    usd_per_m_cache_write: float = 0.0


MODELS: dict[str, ModelSpec] = {
    "qwen3-32b": ModelSpec(
        key="qwen3-32b",
        model_id="Qwen/Qwen3-32B",
        usd_per_m_input=0.10,
        usd_per_m_output=0.30,
    ),
    "deepseek-flash": ModelSpec(
        key="deepseek-flash",
        model_id="deepseek-ai/DeepSeek-V4-Flash",
        usd_per_m_input=0.09,
        usd_per_m_output=0.18,
    ),
    "gpt-oss-120b": ModelSpec(
        key="gpt-oss-120b",
        model_id="openai/gpt-oss-120b",
        usd_per_m_input=0.037,
        usd_per_m_output=0.17,
    ),
    "claude-sonnet-5": ModelSpec(
        key="claude-sonnet-5",
        model_id="claude-sonnet-5",
        usd_per_m_input=2.0,
        usd_per_m_output=10.0,
        provider="anthropic",
        usd_per_m_cache_read=0.20,
        usd_per_m_cache_write=2.50,
    ),
}


def cost_usd(spec: ModelSpec, prompt_tokens: int, completion_tokens: int,
             cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Cost of one call. `prompt_tokens` excludes cached tokens."""
    return (
        prompt_tokens / 1_000_000 * spec.usd_per_m_input
        + completion_tokens / 1_000_000 * spec.usd_per_m_output
        + cache_read_tokens / 1_000_000 * spec.usd_per_m_cache_read
        + cache_write_tokens / 1_000_000 * spec.usd_per_m_cache_write
    )


def is_known(spec: ModelSpec) -> bool:
    """False for a model with no price, which the spending cap could not see."""
    return spec.key in MODELS


def resolve(key_or_id: str) -> ModelSpec:
    """Find a model by short name or full id. Unknown ids get a price of zero."""
    if key_or_id in MODELS:
        return MODELS[key_or_id]
    for spec in MODELS.values():
        if spec.model_id == key_or_id:
            return spec
    return ModelSpec(key=key_or_id, model_id=key_or_id,
                     usd_per_m_input=0.0, usd_per_m_output=0.0)
