"""intro_frame.py: первый ВИДИМЫЙ кадр — не чёрный, не пустой, не середина появления; рамка —
та же, что у остальных кадров записи. Всё без ffmpeg: на синтетических кадрах."""
import sys
from pathlib import Path

import pytest

# ⚠️ numpy есть только в venv видео (`~/asr-stack/video-venv`), а не в венве сайта, откуда идёт
# общий прогон тестов. Импорт наверху файла ронял бы СБОР всего набора, а не пропускал этот файл.
np = pytest.importorskip("numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import intro_frame as intro  # noqa: E402
from slides_from_video import Params  # noqa: E402

P = Params()


def flat(value: int) -> np.ndarray:
    return np.full((P.height, P.width), value, dtype=np.uint8)


def textured(seed: int, level: int = 140) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = flat(level).astype(np.int16)
    img[::7, :] = level - 60           # «строки текста»
    img[:, ::11] += rng.integers(-40, 40, size=(P.height, (P.width + 10) // 11), dtype=np.int16)
    return np.clip(img, 0, 255).astype(np.uint8)


def test_black_start_and_fade_in_are_skipped_first_stable_visible_frame_wins():
    splash = textured(1)
    fade = (splash.astype(np.int16) // 2).astype(np.uint8)  # ещё проявляется: заметно отличается от следующего
    frames = np.stack([flat(0), flat(4), fade, splash, splash, splash])
    assert intro.first_visible(frames, P) == 3


def test_dark_or_empty_video_start_gives_nothing():
    assert intro.first_visible(np.stack([flat(0)] * 5), P) is None
    assert intro.first_visible(np.stack([flat(200)] * 5), P) is None   # ровная заливка — не содержимое


def test_video_that_opens_on_a_slide_picks_frame_zero():
    slide = textured(2)
    assert intro.first_visible(np.stack([slide, slide, slide]), P) == 0


def test_live_splash_is_taken_at_once_stillness_is_not_required():
    """Титульную карточку часто делают живой: докладчик в кадре шевелится, элементы летят.
    Требование неподвижности уводило выбор на 29-ю секунду (ловилось 14.09)."""
    a, b = textured(3), textured(4)          # та же яркость, разный рисунок — движение в кадре
    assert intro.first_visible(np.stack([a, b, a, b, b, b]), P) == 0


def test_fade_from_black_runs_up_to_full_brightness_of_the_same_picture():
    """Наплыв из черноты: соседние кадры — ТА ЖЕ картинка, только ярче; доводим до проявленной."""
    splash = textured(5, level=150)
    steps = [(splash.astype(np.int16) * k // 10).astype(np.uint8) for k in (3, 6, 9)]
    frames = np.stack([flat(0), *steps, splash, splash])
    assert intro.first_visible(frames, P) == 4


def test_crossfade_into_another_scene_does_not_move_the_choice():
    """Карточка через секунду уходит кроссфейдом в план зала — заставка это всё равно ПЕРВЫЙ
    кадр (ловилось 14.09: выбор уезжал в середину наплыва, где заголовок просвечивал)."""
    card, hall = textured(6, level=200), textured(7, level=120)
    blend = ((card.astype(np.int16) + hall.astype(np.int16)) // 2).astype(np.uint8)
    assert intro.first_visible(np.stack([card, blend, hall, hall]), P) == 0


def test_crop_matches_the_records_frame_layout():
    assert intro.crop_from_layout({"bbox": [0.1, 0.2, 0.9, 0.8]}) == "crop=iw*0.8200:ih*0.6200:iw*0.0900:ih*0.1900"
    assert intro.crop_from_layout(None) == "crop=iw*1.0000:ih*1.0000:iw*0.0000:ih*0.0000"
