import pytest


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path_factory, monkeypatch):
    """Tests never read or overwrite the user's saved browser sessions."""
    settings = tmp_path_factory.mktemp("anna-settings")
    monkeypatch.setattr("anna.session.config_path", lambda: settings / "config.json")

    monkeypatch.setattr("anna.library.config_path", lambda: settings / "config.json")
