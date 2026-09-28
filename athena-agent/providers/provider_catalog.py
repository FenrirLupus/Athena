"""Provider catalog — every provider Athena can use, with known defaults.

Each entry carries the provider's default base_url and the canonical
key name(s). The catalog feeds:

  - `athena setup` (CLI) / `athena provider add` — pick a provider, its
    base_url is selected AUTOMATICALLY from here, and the user provides
    only the api_key (which goes to the .secret store)
  - the GUI settings page — same catalog, same shape

The catalog is STATIC knowledge (where providers live, what key they use).
Credentials live in the .secret store; config (base_url, models) lives in
authentication.json; the chain lives in config.yaml.

Names align with the .secret KNOWN_PROVIDERS registry (providers that
take an api_key + base_url). Local endpoints are marked local=True.
"""
from __future__ import annotations

PROVIDER_CATALOG: dict[str, dict] = {
    # name: {"base_url": ..., "key_env": [...canonical key names...], "local": bool}
    # ---- Local endpoints -------------------------------------------------
    # THE 08-15 PORTABILITY FIX: all local defaults use localhost — a
    # machine-specific LAN IP (10.x) must never ship in the catalog (it
    # would point every clone at a nonexistent host). Users with a remote
    # endpoint override base_url in their provider config.
    "lmstudio":          {"base_url": "http://localhost:1234/v1", "key_env": ["LMSTUDIO_API_KEY"], "local": True},
    "ollama":            {"base_url": "http://localhost:11434/v1", "key_env": ["OLLAMA_API_KEY"], "local": True},
    "vllm":              {"base_url": "http://localhost:8000/v1", "key_env": ["VLLM_API_KEY"], "local": True},
    "localai":           {"base_url": "http://localhost:8080/v1", "key_env": ["LOCALAI_API_KEY"], "local": True},
    # ---- OpenAI-compatible clouds ----------------------------------------
    "openai":            {"base_url": "https://api.openai.com/v1", "key_env": ["OPENAI_API_KEY"]},
    "openai-api":        {"base_url": "https://api.openai.com/v1", "key_env": ["OPENAI_API_KEY"]},
    "openrouter":        {"base_url": "https://openrouter.ai/api/v1", "key_env": ["OPENROUTER_API_KEY"]},
    "fireworks":         {"base_url": "https://api.fireworks.ai/inference/v1", "key_env": ["FIREWORKS_API_KEY"]},
    "groq":              {"base_url": "https://api.groq.com/openai/v1", "key_env": ["GROQ_API_KEY"]},
    "together":          {"base_url": "https://api.together.xyz/v1", "key_env": ["TOGETHER_API_KEY"]},
    "deepinfra":         {"base_url": "https://api.deepinfra.com/v1/openai", "key_env": ["DEEPINFRA_API_KEY"]},
    "novita":            {"base_url": "https://api.novita.ai/v3/openai", "key_env": ["NOVITA_API_KEY"]},
    "scaleway":          {"base_url": "https://api.scaleway.ai/v1", "key_env": ["SCALEWAY_API_KEY"]},
    "cerebras":          {"base_url": "https://api.cerebras.ai/v1", "key_env": ["CEREBRAS_API_KEY"]},
    "samba":             {"base_url": "https://api.sambanova.ai/v1", "key_env": ["SAMBA_API_KEY"]},
    "nvidia":            {"base_url": "https://integrate.api.nvidia.com/v1", "key_env": ["NVIDIA_API_KEY"]},
    "arcee":             {"base_url": "https://api.arcee.ai/api/v1", "key_env": ["ARCEE_API_KEY"]},
    "gmi":               {"base_url": "https://api.gmi-serving.com/v1", "key_env": ["GMI_API_KEY"]},
    "kilocode":          {"base_url": "https://api.kilo.ai/api/gateway", "key_env": ["KILOCODE_API_KEY"]},
    "ai-gateway":        {"base_url": "https://ai-gateway.vercel.sh/v1", "key_env": ["AI_GATEWAY_API_KEY"]},
    "huggingface":       {"base_url": "https://router.huggingface.co/v1", "key_env": ["HUGGINGFACE_API_KEY"]},
    "perplexity":        {"base_url": "https://api.perplexity.ai", "key_env": ["PERPLEXITY_API_KEY"]},
    "replicate":         {"base_url": "https://api.replicate.com/v1", "key_env": ["REPLICATE_API_KEY"]},
    "ollama-cloud":      {"base_url": "https://ollama.com/api", "key_env": ["OLLAMA_CLOUD_API_KEY"]},
    "qstash":            {"base_url": "https://qstash.upstash.io/v1", "key_env": ["QSTASH_API_KEY"]},
    "nous":              {"base_url": "https://api.nousresearch.com/v1", "key_env": ["NOUS_API_KEY"]},
    # ---- Direct API clouds ------------------------------------------------
    "anthropic":         {"base_url": "https://api.anthropic.com", "key_env": ["ANTHROPIC_API_KEY"]},
    "gemini":            {"base_url": "https://generativelanguage.googleapis.com/v1beta", "key_env": ["GEMINI_API_KEY"]},
    "google":            {"base_url": "https://generativelanguage.googleapis.com/v1beta", "key_env": ["GOOGLE_API_KEY"]},
    "deepseek":          {"base_url": "https://api.deepseek.com/v1", "key_env": ["DEEPSEEK_API_KEY"]},
    "xai":               {"base_url": "https://api.x.ai/v1", "key_env": ["XAI_API_KEY"]},
    "grok":              {"base_url": "https://api.x.ai/v1", "key_env": ["GROK_API_KEY"]},
    "mistral":           {"base_url": "https://api.mistral.ai/v1", "key_env": ["MISTRAL_API_KEY"]},
    "cohere":            {"base_url": "https://api.cohere.com/v1", "key_env": ["COHERE_API_KEY"]},
    "xiaomi":            {"base_url": "https://api.xiaomimimo.com/v1", "key_env": ["XIAOMI_API_KEY"]},
    "upstage":           {"base_url": "https://api.upstage.ai/v1/solar", "key_env": ["UPSTAGE_API_KEY"]},
    "stepfun":           {"base_url": "https://api.stepfun.com/v1", "key_env": ["STEPFUN_API_KEY"]},
    "zai":               {"base_url": "https://api.z.ai/api/paas/v4", "key_env": ["ZAI_API_KEY"]},
    "zhipu":             {"base_url": "https://open.bigmodel.cn/api/paas/v4", "key_env": ["ZHIPU_API_KEY"]},
    "moonshot":          {"base_url": "https://api.moonshot.ai/v1", "key_env": ["MOONSHOT_API_KEY"]},
    "kimi":              {"base_url": "https://api.moonshot.ai/v1", "key_env": ["KIMI_API_KEY"]},
    "kimi-coding":       {"base_url": "https://api.kimi.com/coding/v1", "key_env": ["KIMI_CODING_API_KEY"]},
    "minimax":           {"base_url": "https://api.minimax.io/v1", "key_env": ["MINIMAX_API_KEY"]},
    "qwen":              {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "key_env": ["QWEN_API_KEY"]},
    "alibaba":           {"base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "key_env": ["ALIBABA_API_KEY"]},
    "baidu":             {"base_url": "https://qianfan.baidubce.com/v2", "key_env": ["BAIDU_API_KEY"]},
    "tencent":           {"base_url": "https://api.hunyuan.cloud.tencent.com/v1", "key_env": ["TENCENT_API_KEY"]},
    "tencent-tokenhub":  {"base_url": "https://tokenhub.tencentmaas.com/v1", "key_env": ["TENCENT_TOKENHUB_API_KEY"]},
    # ---- Managed / SDK endpoints (base_url may need the user's region) ----
    "azure":             {"base_url": "https://your-resource.openai.azure.com/openai/v1", "key_env": ["AZURE_API_KEY"]},
    "azure-foundry":     {"base_url": "https://your-resource.openai.azure.com/openai/v1", "key_env": ["AZURE_FOUNDRY_API_KEY"]},
    "bedrock":           {"base_url": "https://bedrock-runtime.us-east-1.amazonaws.com", "key_env": ["BEDROCK_BASE_URL"]},
    "vertex":            {"base_url": "https://us-central1-aiplatform.googleapis.com/v1", "key_env": ["VERTEX_API_KEY"]},
    # ---- OpenCode ----------------------------------------------------------
    "opencode-zen":      {"base_url": "https://opencode.ai/zen/v1", "key_env": ["OPENCODE_ZEN_API_KEY"]},
    "opencode-go":       {"base_url": "https://opencode.ai/zen/go/v1", "key_env": ["OPENCODE_GO_API_KEY"]},
    # ---- Athena HERSELF (the Operator's spec: her own MCP is her own provider).
    # The conversion layer: /mcp speaks the provider schema, so a runtime
    # can select athena as a provider — her own base_url, her own key.
    # Standard local bind (the Operator's): 127.0.0.1:51420.
    "athena":            {"base_url": "http://127.0.0.1:51420/mcp", "key_env": ["ATHENA_API_KEY"]},
    # ---- Custom catch-all (unknown providers) ------------------------------
    "custom":            {"base_url": "", "key_env": ["CUSTOM_API_KEY"]},
}


def list_catalog() -> dict[str, dict]:
    """Return the catalog (name -> {base_url, key_env, local?})."""
    return {k: dict(v) for k, v in PROVIDER_CATALOG.items()}


def get_catalog_entry(name: str) -> dict | None:
    entry = PROVIDER_CATALOG.get(name)
    return dict(entry) if entry else None




def suggested_model(name: str) -> str:
    """A sensible default active model for a provider, if known."""
    suggestions = {
        "lmstudio": "lmstudio-community/qwen3.5-4b",
        "opencode-zen": "deepseek-v4-flash-free",
        "opencode-go": "deepseek-v4-flash",
        "deepseek": "deepseek-chat",
        "anthropic": "claude-sonnet-4-5",
        "openai-api": "gpt-4o",
        "openai": "gpt-4o",
        "gemini": "gemini-2.5-flash",
        "google": "gemini-2.5-flash",
        "xai": "grok-4",
        "grok": "grok-4",
        "nvidia": "nvidia/llama-3.3-nemotron-super-49b-v1",
        "mistral": "mistral-large-latest",
        "groq": "llama-3.3-70b-versatile",
        "openrouter": "openai/gpt-4o",
        "fireworks": "accounts/fireworks/models/llama-v3p1-70b-instruct",
        "qwen": "qwen-plus",
        "alibaba": "qwen-plus",
        "moonshot": "moonshot-v1-8k",
        "kimi": "moonshot-v1-8k",
        "minimax": "minimax-text-01",
        "cohere": "command-r-plus",
        "perplexity": "sonar",
    }
    return suggestions.get(name, "")
