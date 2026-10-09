"""Per-task model routing preserving managed-server ownership."""

from studio.llm.providers import LlamaProvider
from studio.llm.session import model_identity


def role_provider(settings, section, role, cache_dir, factory=LlamaProvider):
    config = settings.get(section, {})
    override = settings.get("llm", {}).get("roles", {}).get(role, {})
    endpoint = override.get("base_url", config.get("base_url", "http://127.0.0.1:8080"))
    if settings.get("llm", {}).get("managed"):
        endpoint = f"http://127.0.0.1:{settings['llm'].get('port', 8080)}"
    return factory(
        endpoint,
        override.get("model", config.get("model", "local")),
        cache_dir,
        model_identity(settings),
        config.get("request_timeout_s", 600),
    )
