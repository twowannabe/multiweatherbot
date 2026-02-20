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
    Application,
    CommandHandler,
    ContextTypes,
)

# ====================== НАСТРОЙКИ ======================
TELEGRAM_TOKEN = config("TELEGRAM_TOKEN")
API_KEY = config("OPENWEATHERMAP_API_KEY")
NASA_API_KEY = config("NASA_API_KEY")

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
application = Application.builder().token(TELEGRAM_TOKEN).build()
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
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        await asyncio.sleep(1.2)
    except RetryAfter as e:
        logger.warning(f"Flood control, wait {e.retry_after}s")
        await asyncio.sleep(e.retry_after + 1)
        await safe_send_message(chat_id, text, **kwargs)
    except Exception as e:
        logger.error(f"Send error to {chat_id}: {e}")


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


async def send_solar_flare_forecast_to_all_users(
    context: ContextTypes.DEFAULT_TYPE,
):
    msg = get_solar_flare_activity()
    for chat_id in monitoring_chats:
        await safe_send_message(chat_id, msg, parse_mode="Markdown")


# ====================== COMMANDS ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    monitoring_chats[chat_id] = True
    await update.message.reply_text(
        "🧙‍♀️ Бот запущен!\nОтправь локацию для прогноза погоды."
    )


async def water(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.datetime.utcnow().timestamp()

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
    now = datetime.datetime.utcnow().timestamp()

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

    msg = f"Сейчас: {t}°C\n\n" + "\n".join(f)
    await update.message.reply_text(escape(msg), parse_mode="HTML")


async def solar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = get_solar_flare_activity()
    await update.message.reply_text(msg, parse_mode="Markdown")


# ====================== ERROR ======================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled error", exc_info=context.error)


# ====================== REGISTRATION ======================
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("water", water))
application.add_handler(CommandHandler("temp", temp))
application.add_handler(CommandHandler("forecast", forecast))
application.add_handler(CommandHandler("solar", solar))
application.add_error_handler(error_handler)

# ====================== PUSH ALERTS ======================
# New table: user_alerts (chat_id, type='rain'|'snow'|'storm'|'hot', threshold float)
def init_alerts_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_alerts (
            chat_id BIGINT PRIMARY KEY,
            alert_type TEXT NOT NULL,
            threshold REAL NOT NULL
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

async def alert_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("/alert <type> <threshold>\ntypes: rain(mm/3h), snow(mm), pop(%%), hot(°C)")
        return
    alert_type = args[0].lower()
    try:
        threshold = float(args[1])
    except:
        await update.message.reply_text("Threshold must be number.")
        return
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO user_alerts (chat_id, alert_type, threshold) VALUES (%s, %s, %s)",
                (chat_id, alert_type, threshold))
    conn.commit()
    cur.close()
    conn.close()
    await update.message.reply_text(f"✅ Alert {alert_type} > {threshold} added.")

async def alert_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT alert_type, threshold FROM user_alerts WHERE chat_id=%s", (chat_id,))
    alerts = cur.fetchall()
    cur.close()
    conn.close()
    if not alerts:
        await update.message.reply_text("No alerts.")
        return
    msg = "Alerts:\n" + "\n".join([f"{t}: >{th}" for t, th in alerts])
    await update.message.reply_text(msg)

async def alert_checker(context: ContextTypes.DEFAULT_TYPE):
    """Check forecasts for alerts every 30min."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT chat_id, latitude, longitude FROM user_locations")
    locations = cur.fetchall()
    cur.execute("SELECT chat_id, alert_type, threshold FROM user_alerts")
    alerts_dict = {r[0]: [(r[1], r[2])] for r in cur.fetchall()}
    cur.close()
    conn.close()
    for loc in locations:
        chat_id, lat, lon = loc
        if chat_id not in alerts_dict:
            continue
        alerts = alerts_dict[chat_id]
        forecast = get_forecast(lat, lon)  # reuse, parse for rain/snow/pop/temp
        # Parse forecast (dummy: assume get_alert_forecast returns triggers)
        triggers = []  # e.g. if 'rain:2.5' > threshold
        for a_type, th in alerts:
            if a_type == 'rain' and forecast_rain > th:
                triggers.append(f"🌧️ Дождь {forecast_rain}mm!")
        if triggers:
            msg = "⚠️ Alerts:\n" + "\n".join(triggers)
            await safe_send_message(chat_id, msg)

application.add_handler(CommandHandler("alert", alert_handler))
application.add_handler(CommandHandler("alertlist", alert_list))

init_alerts_db()  # Call once

application.job_queue.run_repeating(
    alert_checker,
    interval=1800,  # 30min
    first=300,
    name="alerts",
)

# ====================== JOB QUEUE ======================
application.job_queue.run_repeating(
    check_water_temperature,
    interval=3600,
    first=300,
    name="water_check",
)

application.job_queue.run_repeating(
    send_solar_flare_forecast_to_all_users,
    interval=43200,
    first=600,
    name="solar_check",
)

# ====================== START ======================
if __name__ == "__main__":
    logger.info("🚀 Starting multiweatherbot")
    chat_location = load_all_locations()
    logger.info(f"Loaded {len(chat_location)} locations")
    application.run_polling()
