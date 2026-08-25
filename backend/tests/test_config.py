from app.core.config import get_settings


def test_collector_only_allows_es_and_nq(monkeypatch):
    monkeypatch.setenv("COLLECTOR_SYMBOLS", "ES,MES,NQ,GC")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.collector_symbols == ("ES", "NQ")
    get_settings.cache_clear()


def test_live_websocket_configuration_defaults(monkeypatch):
    for name in ("LIVE_WS_ENABLED", "LIVE_WS_URL", "LIVE_WS_SYMBOLS"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.live_ws_enabled is False
    assert settings.live_ws_url == "wss://dbws.massiveprivateserver.site/live"
    assert settings.live_ws_symbols == ("ES.c.0", "NQ.c.0")
    get_settings.cache_clear()


def test_live_websocket_configuration_reads_environment(monkeypatch):
    monkeypatch.setenv("LIVE_WS_ENABLED", "true")
    monkeypatch.setenv("LIVE_WS_URL", "ws://example.test/live")
    monkeypatch.setenv("LIVE_WS_SYMBOLS", " ES.c.0, NQ.c.0, ")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.live_ws_enabled is True
    assert settings.live_ws_url == "ws://example.test/live"
    assert settings.live_ws_symbols == ("ES.c.0", "NQ.c.0")
    get_settings.cache_clear()
