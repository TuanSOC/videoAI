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
