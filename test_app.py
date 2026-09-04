import app


def test_readyz_accepts_service_role_alias(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")
    app.get_supabase_client.cache_clear()

    assert app.readyz()["status"] == "ready"
    assert app.readyz()["supabase_write_key_configured"] is True


def test_get_supabase_client_reads_common_key_aliases(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    app.get_supabase_client.cache_clear()

    client = app.get_supabase_client(require_write=False)
    assert client is not None
