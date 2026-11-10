from pathlib import Path
from types import SimpleNamespace

import pytest

from codemerge.llm import clients


@pytest.fixture(autouse=True)
def clear_client_cache(monkeypatch):
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    clients.get_groq_client.cache_clear()
    yield
    clients.get_groq_client.cache_clear()


def test_groq_uses_only_its_explicit_credential_and_endpoint(monkeypatch):
    monkeypatch.setattr(clients, "OpenAI", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setenv("OPENAI_API_KEY", "test-other-provider-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    assert not clients.groq_available()
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        clients.get_groq_client()

    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    assert clients.groq_available()
    client = clients.get_groq_client()
    assert client.api_key == "test-groq-key"
    assert client.base_url == clients.GROQ_BASE_URL


def test_groq_does_not_discover_workspace_dotenv(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(clients, "OpenAI", object())
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GROQ_API_KEY=test-untrusted-key\n", encoding="utf-8")
    assert not clients.groq_available()
