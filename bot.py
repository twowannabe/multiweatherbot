import requests
import re
import logging
from decouple import config
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    JobQueue,
)
from bs4 import BeautifulSoup
from html import escape
import asyncio
import psycopg2
from psycopg2.extras import RealDictCursor
import datetime
from zoneinfo import ZoneInfo

# ---------------------- Настройки ----------------------
TELEGRAM_TOKEN = config('TELEGRAM_TOKEN')
API_KEY = config('OPENWEATHERMAP_API_KEY')
NASA_API_KEY = config('NASA_API_KEY')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ---------------------- Приложение и JobQueue ----------------------
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Создаём JobQueue с таймзоной Europe/Moscow
# job_queue = application.job_queue
# application.job_queue = job_queue
# job_queue.start()

bot = Bot(token=TELEGRAM_TOKEN)

# ---------------------- Глобальные переменные ----------------------
chat_location = {}
monitoring_chats = {}
previous_temperature = None

# ---------------------- Работа с базой ----------------------
def get_db_connection():
    logger.info("Подключение к базе данных")
    return psycopg2.connect(
        host=config('DB_HOST'),
        port=config('DB_PORT'),
        dbname=config('DB_NAME'),
        user=config('DB_USER'),
        password=config('DB_PASSWORD')
    )

def save_location_to_db(chat_id, lat, lon):
    logger.info(f"Сохранение локации пользователя {chat_id} в базе данных: {lat}, {lon}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_locations (chat_id, latitude, longitude) VALUES (%s, %s, %s) "
        "ON CONFLICT (chat_id) DO UPDATE SET latitude = %s, longitude = %s",
        (chat_id, lat, lon, lat, lon)
    )
    conn.commit()
    cursor.close()
    conn.close()

def load_all_locations():
    logger.info("Загрузка всех локаций пользователей из базы данных")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT chat_id, latitude, longitude FROM user_locations")
    locations = cursor.fetchall()
    cursor.close()
    conn.close()
    return {row['chat_id']: (row['latitude'], row['longitude']) for row in locations}

# ---------------------- Функции для погоды ----------------------
def get_water_temperature():
    url = 'https://world-weather.ru/pogoda/montenegro/budva/water/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        temp_element = soup.find('div', id='weather-now-number')
        if temp_element:
            match = re.search(r'([-+]?\d+)', temp_element.get_text(strip=True))
            if match:
                return float(match.group(1))
        return None
    except requests.RequestException as e:
        logger.error(f"Ошибка получения температуры воды: {e}")
        return None

def get_temperature(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru'
    try:
        response = requests.get(url)
        data = response.json()
        if response.status_code == 200 and 'main' in data and 'temp' in data['main']:
            return data['main']['temp']
        return None
    except Exception as e:
        logger.error(f"Ошибка получения температуры: {e}")
        return None

def get_forecast(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru'
    try:
        response = requests.get(url)
        data = response.json()
        if response.status_code == 200:
            forecast_data = [f"{entry['dt_txt']}: {entry['main']['temp']}°C, {entry['weather'][0]['description']}" for entry in data['list'][:4]]
            return forecast_data
        return None
    except Exception as e:
        logger.error(f"Ошибка получения прогноза: {e}")
        return None

# ---------------------- Проверка воды ----------------------
def check_water_temperature():
    global previous_temperature
    current_temperature = get_water_temperature()
    if current_temperature is not None:
        if previous_temperature is None:
            previous_temperature = current_temperature
        elif current_temperature < previous_temperature:
            message = f"Температура воды упала! Сейчас: {current_temperature}°C, ранее: {previous_temperature}°C."
            job_queue.run_once(lambda ctx: asyncio.create_task(send_notification_to_all_users(message)), 0)
        previous_temperature = current_temperature

async def send_notification_to_all_users(message):
    for chat_id in monitoring_chats.keys():
        try:
            await bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {chat_id}: {e}")

# ---------------------- Команды бота ----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "Бот запущен! Пожалуйста, отправьте свою локацию для получения прогноза погоды."
    )
    if chat_id not in monitoring_chats:
        monitoring_chats[chat_id] = None

async def temp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        t = get_temperature(lat, lon)
        if t is not None:
            await update.message.reply_text(f"Текущая температура воздуха: {t}°C")
        else:
            await update.message.reply_text("Не удалось получить данные о температуре.")
    else:
        await update.message.reply_text("Локация не отправлена.")

async def water(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = get_water_temperature()
    if t is not None:
        await update.message.reply_text(f"Температура воды в Будве: {t}°C")
    else:
        await update.message.reply_text("Не удалось получить данные о температуре воды.")

async def send_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp_val = get_temperature(lat, lon)
        forecast_data = get_forecast(lat, lon)
        if forecast_data:
            forecast = "\n".join(forecast_data)
            message = f"Текущая температура воздуха: {temp_val}°C\n{forecast}"
            await update.message.reply_text(escape(message), parse_mode="HTML")
        else:
            await update.message.reply_text("Не удалось получить прогноз.")
    else:
        await update.message.reply_text("Локация не отправлена.")

# ---------------------- Солнечные вспышки ----------------------
def get_solar_flare_activity():
    now = datetime.datetime.now(datetime.timezone.utc)
    three_days_ago = (now - datetime.timedelta(days=2)).strftime('%Y-%m-%d')
    today = now.strftime('%Y-%m-%d')
    url = f"https://api.nasa.gov/DONKI/FLR?startDate={three_days_ago}&endDate={today}&api_key={NASA_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        flare_events = []
        tz = ZoneInfo("Europe/Brussels")
        for event in data:
            class_type = event.get('classType', 'неизвестный')
            begin_time = event.get('beginTime', 'неизвестное время')
            try:
                dt = datetime.datetime.fromisoformat(begin_time.replace('Z', '+00:00'))
                dt = dt.astimezone(tz)
                time_str = dt.strftime('%d.%m.%Y %H:%M GMT+1')
            except Exception:
                time_str = begin_time
            emoji = '⚪'
            if class_type.startswith('A') or class_type.startswith('B'): emoji='🟢'
            elif class_type.startswith('C'): emoji='🟡'
            elif class_type.startswith('M'): emoji='🟠'
            elif class_type.startswith('X'): emoji='🔴'
            flare_events.append(f"{emoji} Вспышка класса {class_type} произошла в {time_str}")
        if flare_events:
            return "*Солнечные вспышки за последние 3 дня:*\n" + "\n".join(flare_events)
        return "Солнечных вспышек за последние 3 дня не зафиксировано."
    except Exception as e:
        logger.error(f"Ошибка получения данных о солнечных вспышках: {e}")
        return "Ошибка получения данных о солнечных вспышках."

async def send_solar_flare_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = get_solar_flare_activity()
    await update.message.reply_text(message, parse_mode="Markdown")

async def send_solar_flare_forecast_to_all_users():
    message = get_solar_flare_activity()
    for chat_id in monitoring_chats.keys():
        try:
            await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {chat_id}: {e}")

# ---------------------- Планирование через JobQueue ----------------------
application.job_queue.run_repeating(
    check_water_temperature,
    interval=60*60,
    first=0,
    name="water_check",
    job_kwargs={"tzinfo": ZoneInfo("Europe/Moscow")}
)

application.job_queue.run_repeating(
    lambda ctx: asyncio.create_task(send_solar_flare_forecast_to_all_users()),
    interval=12*60*60,
    first=0,
    name="solar_check",
    job_kwargs={"tzinfo": ZoneInfo("Europe/Moscow")}
)

# ---------------------- Регистрация команд ----------------------
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('temp', temp))
application.add_handler(CommandHandler('water', water))
application.add_handler(CommandHandler('forecast', send_forecast))
application.add_handler(CommandHandler('solar', send_solar_flare_forecast))

# ---------------------- Запуск бота ----------------------
if __name__ == '__main__':
    # Загружаем все сохранённые локации при старте
    chat_location = load_all_locations()
    application.run_polling()
