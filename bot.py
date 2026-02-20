import requests
import re
import logging
import asyncio
import datetime
import pytz
import tzlocal

from decouple import config
from bs4 import BeautifulSoup
from html import escape

import psycopg2
from psycopg2.extras import RealDictCursor

from telegram import Update
from telegram.error import RetryAfter
from telegram.ext import (
    AIORateLimiter,
    Application,
    CommandHandler,
    ContextTypes,
)

# ====================== НАСТРОЙКИ ======================
TELEGRAM_TOKEN = config("TELEGRAM_TOKEN")
API_KEY = config("OPENWEATHERMAP_API_KEY")
NASA_API_KEY = config("NASA_API_KEY")
GROK_API_KEY = config("GROK_API_KEY")

# ====================== ЛОГИ ======================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ====================== TIMEZONE ======================
tzlocal.get_localzone = lambda: pytz.timezone("Europe/Moscow")
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# ====================== APP ======================
application = Application.builder().token(TELEGRAM_TOKEN).rate_limiter(AIORateLimiter(max_retries=3)).build()
bot = application.bot

# ====================== ГЛОБАЛЬНЫЕ ======================
chat_location = {}
monitoring_chats = {}
previous_water_temperature = None

last_water_request = {}
last_temp_request = {}

# ====================== DB ======================
def get_db_connection():
    return psycopg2.connect(
        host=config("DB_HOST"),
        port=config("DB_PORT"),
        dbname=config("DB_NAME"),
        user=config("DB_USER"),
        password=config("DB_PASSWORD"),
    )


def load_all_locations():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT chat_id, latitude, longitude FROM user_locations")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["chat_id"]: (r["latitude"], r["longitude"]) for r in rows}


# ====================== SAFE SEND ======================
async def safe_send_message(chat_id: int, text: str, **kwargs):
    while True:
        try:
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)
            await asyncio.sleep(1.2)
            return
        except RetryAfter as e:
            logger.warning(f"Flood control, wait {e.retry_after}s")
            await asyncio.sleep(e.retry_after + 1)
        except Exception as e:
            logger.error(f"Send error to {chat_id}: {e}")
            return


# ====================== WEATHER ======================
def get_water_temperature():
    url = "https://world-weather.ru/pogoda/montenegro/budva/water/"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        el = soup.find("div", id="weather-now-number")
        if el:
            m = re.search(r"([-+]?\d+)", el.text)
            if m:
                return float(m.group(1))
    except Exception as e:
        logger.error(f"Water temp error: {e}")

    return None


def get_temperature(lat, lon):
    url = (
        "https://api.openweathermap.org/data/2.5/weather"
        f"?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru"
    )
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        return data["main"]["temp"]
    except Exception:
        return None


def get_forecast(lat, lon):
    url = (
        "https://api.openweathermap.org/data/2.5/forecast"
        f"?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru"
    )
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        return [
            f"{e['dt_txt']}: {e['main']['temp']}°C, {e['weather'][0]['description']}"
            for e in data["list"][:4]
        ]
    except Exception:
        return None


# ====================== GROK ======================
def grok_ask(prompt: str) -> str | None:
    try:
        r = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "grok-2-latest",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
            },
            timeout=20,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Grok error: {e}")
        return None


# ====================== SOLAR ======================
def get_solar_flare_activity():
    now = datetime.datetime.now(datetime.timezone.utc)
    start = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")

    url = (
        "https://api.nasa.gov/DONKI/FLR"
        f"?startDate={start}&endDate={end}&api_key={NASA_API_KEY}"
    )

    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()

        lines = []
        for e in data:
            cls = e.get("classType", "?")
            t = e.get("beginTime", "")
            dt = datetime.datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(
                MOSCOW_TZ
            )
            emoji = "🟢"
            if cls.startswith("C"):
                emoji = "🟡"
            elif cls.startswith("M"):
                emoji = "🟠"
            elif cls.startswith("X"):
                emoji = "🔴"

            lines.append(
                f"{emoji} {cls} — {dt.strftime('%d.%m.%Y %H:%M GMT+3')}"
            )

        return (
            "*Солнечные вспышки за последние 3 дня:*\n" + "\n".join(lines)
            if lines
            else "Солнечных вспышек не было."
        )

    except Exception as e:
        logger.error(f"Solar error: {e}")
        return "Ошибка получения данных о солнечных вспышках."


# ====================== JOBS ======================
async def check_water_temperature(context: ContextTypes.DEFAULT_TYPE):
    global previous_water_temperature

    current = get_water_temperature()
    if current is None:
        return

    if previous_water_temperature is not None and current < previous_water_temperature:
        msg = (
            f"🌊 Температура воды упала!\n"
            f"Было: {previous_water_temperature}°C\n"
            f"Стало: {current}°C"
        )
        for chat_id in monitoring_chats:
            await safe_send_message(chat_id, msg)

    previous_water_temperature = current



# ====================== COMMANDS ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    monitoring_chats[chat_id] = True
    await update.message.reply_text(
        "🧙‍♀️ Бот запущен!\nОтправь локацию для прогноза погоды."
    )


async def water(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()

    if chat_id in last_water_request and now - last_water_request[chat_id] < 30:
        return

    last_water_request[chat_id] = now

    t = get_water_temperature()
    if t is None:
        await update.message.reply_text("Не удалось получить температуру воды.")
    else:
        await update.message.reply_text(f"🌊 Температура воды в Будве: {t}°C")


async def temp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()

    if chat_id in last_temp_request and now - last_temp_request[chat_id] < 15:
        return

    last_temp_request[chat_id] = now

    if chat_id not in chat_location:
        await update.message.reply_text("Сначала отправь локацию.")
        return

    lat, lon = chat_location[chat_id]
    t = get_temperature(lat, lon)
    await update.message.reply_text(
        f"🌡 Температура воздуха: {t}°C" if t else "Ошибка получения данных."
    )


async def forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in chat_location:
        await update.message.reply_text("Сначала отправь локацию.")
        return

    lat, lon = chat_location[chat_id]
    t = get_temperature(lat, lon)
    f = get_forecast(lat, lon)

    if not f:
        await update.message.reply_text("Прогноз недоступен.")
        return

    raw = f"Сейчас: {t}°C\n" + "\n".join(f)
    prompt = (
        f"Вот прогноз погоды в Будве, Черногория:\n{raw}\n\n"
        "Напиши краткое человекочитаемое резюме на русском (2–3 предложения). "
        "Только суть, без лишних деталей."
    )
    summary = grok_ask(prompt)
    await update.message.reply_text(summary if summary else raw)


async def advice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in chat_location:
        await update.message.reply_text("Сначала отправь локацию.")
        return

    lat, lon = chat_location[chat_id]
    water = get_water_temperature()
    air = get_temperature(lat, lon)
    f = get_forecast(lat, lon)

    parts = []
    if water is not None:
        parts.append(f"Температура воды: {water}°C")
    if air is not None:
        parts.append(f"Температура воздуха: {air}°C")
    if f:
        parts.append("Прогноз:\n" + "\n".join(f))

    if not parts:
        await update.message.reply_text("Не удалось получить данные о погоде.")
        return

    prompt = (
        "Погода в Будве, Черногория:\n" + "\n".join(parts) + "\n\n"
        "Дай краткий совет: стоит ли купаться, что надеть, чем заняться на улице. "
        "2–3 предложения на русском."
    )
    msg = grok_ask(prompt) or "Не удалось получить совет."
    await update.message.reply_text(msg)


async def solar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = get_solar_flare_activity()
    await update.message.reply_text(raw, parse_mode="Markdown")
    prompt = (
        f"{raw}\n\n"
        "Объясни простым языком на русском: что это означает, есть ли влияние на людей "
        "и стоит ли беспокоиться. 2–3 предложения."
    )
    explanation = grok_ask(prompt)
    if explanation:
        await update.message.reply_text(explanation)


# ====================== ERROR ======================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled error", exc_info=context.error)


# ====================== REGISTRATION ======================
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("water", water))
application.add_handler(CommandHandler("temp", temp))
application.add_handler(CommandHandler("forecast", forecast))
application.add_handler(CommandHandler("advice", advice))
application.add_handler(CommandHandler("solar", solar))
application.add_error_handler(error_handler)


# ====================== JOB QUEUE ======================
application.job_queue.run_repeating(
    check_water_temperature,
    interval=3600,
    first=300,
    name="water_check",
)


# ====================== START ======================
if __name__ == "__main__":
    logger.info("🚀 Starting multiweatherbot")
    chat_location = load_all_locations()
    logger.info(f"Loaded {len(chat_location)} locations")
    application.run_polling()
