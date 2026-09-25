from vidgen.config import get_settings
from vidgen.models import Scene, Script


def test_presets_load():
    s = get_settings()
    assert s.preset("short").orientation == "portrait"
    assert s.preset("long").orientation == "landscape"
    assert set(s.pipeline.voices) == {"vi", "en"}


def test_script_word_count():
    script = Script(
        title="t", hook="h", lang="en", format="short",
        scenes=[Scene(id=1, narration="one two three", visual_query="ocean")],
    )
    assert script.word_count == 3


def test_write_atomic_replaces_and_leaves_no_temp(tmp_path):
    from vidgen.fsutil import write_atomic

    target = tmp_path / "script.json"
    write_atomic(target, "một")
    write_atomic(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in tmp_path.iterdir()] == ["script.json"]


def test_write_atomic_concurrent_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from vidgen.fsutil import write_atomic

    target = tmp_path / "state.json"
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: write_atomic(target, f"v{i}" * 1000), range(40)))
    content = target.read_text(encoding="utf-8")
    assert content == content[:len(content) // 1000] * 1000  # one writer's full payload, never mixed
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_doctor_ollama_down_is_not_fatal_when_gemini_usable(monkeypatch):
    from vidgen import doctor

    s = get_settings().model_copy(deep=True)
    s.pipeline.llm.providers = ["ollama", "gemini"]
    s.secrets.gemini_api_key = "k"
    monkeypatch.setattr(doctor, "_ollama_models", lambda url: None)  # Ollama offline
    monkeypatch.setattr(doctor, "_http_ok", lambda url: False)
    checks = {c.name: c for c in doctor.run_checks(s)}
    assert not checks["Ollama"].ok and not checks["Ollama"].required
    assert checks["LLM available"].ok and checks["LLM available"].detail == "gemini"


def test_doctor_requires_some_llm(monkeypatch):
    from vidgen import doctor

    s = get_settings().model_copy(deep=True)
    s.pipeline.llm.providers = ["ollama"]
    monkeypatch.setattr(doctor, "_ollama_models", lambda url: [])  # up, but model not pulled
    monkeypatch.setattr(doctor, "_http_ok", lambda url: False)
    checks = {c.name: c for c in doctor.run_checks(s)}
    assert "ollama pull" in checks["Ollama"].detail
    assert not checks["LLM available"].ok and checks["LLM available"].required
