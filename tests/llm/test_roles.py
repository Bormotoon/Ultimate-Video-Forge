import pytest

from studio.core.settings import SettingsError, load_settings
from studio.llm.roles import role_provider


def test_role_routing_and_managed_endpoint(tmp_path):
    settings = {
        "text": {"model": "default"},
        "llm": {"roles": {"article": {"model": "writer", "base_url": "http://127.0.0.1:9000"}}},
    }
    provider = role_provider(settings, "text", "article", tmp_path)
    assert provider.model == "writer" and provider.base_url.endswith(":9000")
    assert role_provider(settings, "text", "proofread", tmp_path).model == "default"
    settings["llm"]["managed"] = True
    settings["llm"]["port"] = 8090
    assert role_provider(settings, "text", "article", tmp_path).base_url.endswith(":8090")


def test_invalid_role_and_term_policy_rejected():
    for overrides in (
        ["llm.roles={bogus: {model: local}}"],
        ["text.term_check_network=true"],
        ["text.term_check=true"],
        ["text.term_max_candidates=0"],
    ):
        with pytest.raises(SettingsError):
            load_settings([], overrides)
