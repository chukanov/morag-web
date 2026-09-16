"""describe_slides: второй вопрос «кто на кадре» — сомнение трактуется как чужие лица."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
pytest.importorskip("httpx")
import describe_slides as ds  # noqa: E402


def test_people_answer_is_normalized_and_doubt_means_strangers():
    assert ds.normalize_people({"who": "Speaker", "setting": "STAGE", "count": "1"}) == \
        {"who": "speaker", "setting": "stage", "count": 1}
    assert ds.normalize_people({"who": "docent", "setting": "?", "count": None}) == \
        {"who": "mixed", "setting": "other", "count": 0}
    assert ds.normalize_people(None)["who"] == "mixed"


def test_people_prompt_names_every_answer_it_accepts():
    for word in ds.WHO | ds.SETTING:
        assert word in ds.PEOPLE_PROMPT, f"в промпте нет варианта {word}"
