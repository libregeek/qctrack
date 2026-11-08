import pytest


@pytest.fixture(autouse=True)
def private_data_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep every test's result cache out of the real ~/.local/share and out of other tests."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path_factory.mktemp("data")))
