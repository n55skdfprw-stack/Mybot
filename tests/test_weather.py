"""Тесты шестого этапа — «Погода» и время в городах. Сегодня — четверг, 24 сентября 2026."""

from datetime import date, datetime, timedelta

from alfred.services.weather import FORECAST_URL, GEO_URL, WeatherService

from .test_finance import make as make_fin, run, say
from .test_alfred import style_ok

CITIES = {
    "москва": {"name": "Москва", "latitude": 55.75, "longitude": 37.62, "timezone": "Europe/Moscow",
               "country": "Россия"},
    "токио": {"name": "Токио", "latitude": 35.69, "longitude": 139.69, "timezone": "Asia/Tokyo",
              "country": "Япония"},
    "казань": {"name": "Казань", "latitude": 55.79, "longitude": 49.12, "timezone": "Europe/Moscow",
               "country": "Россия"},
}


def forecast_json(rain_at: int = 15, t_min: float = 12, t_max: float = 17, wind: float = 4, code_now: int = 3):
    start = datetime(2026, 9, 24)
    times, temps, codes, probs, winds = [], [], [], [], []
    for i in range(24 * 7):
        t = start + timedelta(hours=i)
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
        temps.append(15 - (i % 24) / 6)
        rainy = (i == rain_at)
        codes.append(61 if rainy else 3)
        probs.append(70 if rainy else 10)
        winds.append(wind)
    days = [start.date() + timedelta(days=i) for i in range(7)]
    return {
        "current": {"time": "2026-09-24T09:30", "temperature_2m": 12.4, "apparent_temperature": 10.1,
                    "weather_code": code_now, "wind_speed_10m": wind, "wind_gusts_10m": wind + 2, "is_day": 1},
        "hourly": {"time": times, "temperature_2m": temps, "weather_code": codes,
                   "precipitation_probability": probs, "wind_speed_10m": winds},
        "daily": {"time": [d.isoformat() for d in days], "weather_code": [61] + [3] * 6,
                  "temperature_2m_max": [t_max] * 7, "temperature_2m_min": [t_min] * 7,
                  "precipitation_probability_max": [70] + [10] * 6, "wind_speed_10m_max": [wind] * 7,
                  "wind_gusts_10m_max": [wind + 2] * 7},
    }


def make(tmp_path, ok=True, **fc):
    a, llm = make_fin(tmp_path)
    calls = []

    async def fetch(url, params):
        calls.append((url, dict(params)))
        if not ok:
            raise ConnectionError("нет сети")
        if url == GEO_URL:
            found = CITIES.get(params["name"].lower())
            return {"results": [found]} if found else {}
        assert url == FORECAST_URL
        return forecast_json(**fc)

    a.weather = WeatherService(fetch=fetch)
    return a, llm, calls


def test_menu_weather_hourly(tmp_path):
    a, llm, calls = make(tmp_path)
    r = run(a.handle_text("🌤 Погода"))
    style_ok(r.text)
    lines = r.text.split("\n")
    assert lines[0] == "🎩 Погода, Сэр!"
    assert lines[2] == "📍 Санкт-Петербург · ↑+17° ↓+12°"
    assert lines[3] == "☁️ Сейчас +12°, пасмурно"
    assert not [l for l in lines if l[:2].isdigit()]          # по часам — только по кнопке
    assert "🌧 После 15:00 ожидается дождь!" in r.text
    assert "☂️ Позвольте напомнить, Сэр: возьмите зонт!" in r.text
    assert r.buttons == [[("🕐 По часам", "wx:h"), ("📅 По дням", "wx:d")]]
    r = run(a.handle_callback_async("wx:h"))
    assert r.edit and r.text.startswith("🎩 Погода по часам, Сэр!\n\n📍 Санкт-Петербург")
    hours = [l for l in r.text.split("\n") if l[:2].isdigit()]
    assert len(hours) == 12 and hours[0].startswith("10:00") and "15:00 🌦 +13° · 💧70%" in hours
    assert r.buttons == [[("🌤 Сейчас", "wx:n"), ("📅 По дням", "wx:d")]]
    assert calls[0][0] == FORECAST_URL           # Питер — без поиска города


def test_days_button(tmp_path):
    a, llm, _ = make(tmp_path)
    r = run(a.handle_callback_async("wx:d"))
    assert r.edit and r.text.startswith("🎩 Погода по дням, Сэр!\n\n📍 Санкт-Петербург")
    assert "🌦 Сегодня: +17° / +12° · 💧70%" in r.text and "☁️ Завтра: +17° / +12°" in r.text
    assert "☁️ Сб, 26 сен: +17° / +12°" in r.text


def test_calm_weather_has_no_advice(tmp_path):
    a, llm, _ = make(tmp_path, rain_at=-1)
    r = run(a.handle_text("🌤 Погода"))
    assert "☂️" not in r.text and "🌧 После" not in r.text


def test_advice_rules(tmp_path):
    a, llm, _ = make(tmp_path, rain_at=-1, t_min=-2, t_max=9, wind=11)
    r = run(a.handle_text("🌤 Погода"))
    assert "💨 Сильный ветер: до 13 м/с!" in r.text
    assert "🧊 Около нуля — возможен гололёд, будьте осторожны!" in r.text
    assert "🧥 Днём и вечером большая разница — оденьтесь слоями!" in r.text


def test_tomorrow(tmp_path):
    a, llm, _ = make(tmp_path, rain_at=24 + 12)
    r = say(a, llm, "Какая погода завтра?", intent="SHOW_WEATHER")
    assert r.text.startswith("🎩 Погода на завтра, Сэр!\n\n📍 Санкт-Петербург")
    assert "🌧 После 12:00 ожидается дождь!" in r.text and "12:00 🌦" not in r.text


def test_week(tmp_path):
    a, llm, _ = make(tmp_path)
    r = say(a, llm, "Погода на неделю", intent="SHOW_WEATHER")
    assert r.text.startswith("🎩 Погода по дням, Сэр!")


def test_other_city_once(tmp_path):
    a, llm, _ = make(tmp_path)
    r = say(a, llm, "Какая погода в Москве?", intent="SHOW_WEATHER", city="Москве")   # ИИ не просклонял
    assert "📍 Москва" in r.text
    assert r.buttons == [[("🕐 По часам", "wx:h:Москве"), ("📅 По дням", "wx:d:Москве")]]
    r = run(a.handle_callback_async("wx:h:Москве"))
    assert "📍 Москва" in r.text
    r = run(a.handle_callback_async("wx:d:Москве"))
    assert "📍 Москва" in r.text
    assert a.users.city(a.user_id) == "Санкт-Петербург"     # город не сменился


def test_set_city_and_home(tmp_path):
    a, llm, _ = make(tmp_path)
    r = say(a, llm, "Я в Казани", intent="SET_CITY", city="Казань")
    style_ok(r.text)
    assert "📍 Теперь погода и утренняя сводка — для: Казань" in r.text
    r = run(a.handle_text("🌤 Погода"))
    assert "📍 Казань" in r.text
    assert "Погода: Казань" in run(a.morning_weather())
    r = say(a, llm, "Я дома", intent="SET_CITY", city="HOME")
    assert r.text == "🎩 С возвращением, Сэр!\n\n📍 Погода снова для: Санкт-Петербург"
    assert a.users.city(a.user_id) == "Санкт-Петербург"


def test_unknown_city(tmp_path):
    a, llm, _ = make(tmp_path)
    r = say(a, llm, "Я в Хогвартсе", intent="SET_CITY", city="Хогвартс")
    assert r.text == "🎩 Сэр, не нашёл город «Хогвартс»!"


def test_no_internet(tmp_path):
    a, llm, _ = make(tmp_path, ok=False)
    r = run(a.handle_text("🌤 Погода"))
    assert r.text.startswith("🎩 Прошу прощения, Сэр!")
    assert run(a.morning_weather()) is None


def test_time_in_city(tmp_path):
    a, llm, _ = make(tmp_path)
    r = say(a, llm, "Сколько сейчас времени в Токио?", intent="TIME_IN_CITY", city="Токио")
    assert r.text == "🎩 Разумеется, Сэр!\n\n🕐 Токио: 18:00\nЧетверг, 24 сентября · +6 ч от вас"


def test_morning_summary_with_weather(tmp_path):
    a, llm, _ = make(tmp_path)
    say(a, llm, "Купить молоко", intent="CREATE_TASK", title="Купить молоко")
    w = run(a.morning_weather())
    r = a.check_message("morning", weather=w)
    style_ok(r.text)
    assert r.text.startswith("🎩 Добрый день, Сэр!\n\n☁️ Погода: Санкт-Петербург\nСейчас +12°, днём до +17°, пасмурно\n"
                             "💨 Ветер 4 м/с\n\n🌧 После 15:00 ожидается дождь!")
    assert "Вот актуальный список дел!\n\n⭕ Купить молоко" in r.text


def test_morning_weather_without_tasks(tmp_path):
    a, llm, _ = make(tmp_path)
    r = a.check_message("morning", weather=run(a.morning_weather()))
    assert r and "Погода: Санкт-Петербург" in r.text and "список дел" not in r.text
    assert a.check_message("morning") is None       # без погоды и без дел — молчим
