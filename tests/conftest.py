import pytest


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    """Tests never read or overwrite the user's saved browser sessions."""
    monkeypatch.setattr("anna.session.config_path", lambda: tmp_path / "config.json")
