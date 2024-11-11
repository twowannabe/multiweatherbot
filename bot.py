import requests
import schedule
import time
import logging
from decouple import config
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import threading
import openai
from bs4 import BeautifulSoup
from html import escape
import re
import asyncio
import psycopg2
from psycopg2.extras import RealDictCursor

# Настройки для подключения к API и боту
TELEGRAM_TOKEN = config('TELEGRAM_TOKEN')
API_KEY = config('OPENWEATHERMAP_API_KEY')
OPENAI_API_KEY = config('OPENAI_API_KEY')
NASA_API_KEY = config('NASA_API_KEY')

openai.api_key = OPENAI_API_KEY
bot = Bot(token=TELEGRAM_TOKEN)
application = Application.builder().token(TELEGRAM_TOKEN).build()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Глобальные переменные для хранения данных пользователей
chat_location = {}
monitoring_chats = {}
previous_temperature = None
user_signs = {}

# Функция для подключения к базе данных
def get_db_connection():
    return psycopg2.connect(
        host=config('DB_HOST'),
        port=config('DB_PORT'),
        dbname=config('DB_NAME'),
        user=config('DB_USER'),
        password=config('DB_PASSWORD')
    )

# Сохранение локации пользователя в базе данных
def save_location_to_db(chat_id, lat, lon):
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

# Загрузка всех локаций пользователей из базы данных при старте
def load_all_locations():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT chat_id, latitude, longitude FROM user_locations")
    locations = cursor.fetchall()
    cursor.close()
    conn.close()
    return {row['chat_id']: (row['latitude'], row['longitude']) for row in locations}

# Функция для получения температуры воды
def get_water_temperature():
    url = 'https://world-weather.ru/pogoda/montenegro/budva/water/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        temp_element = soup.find('div', id='weather-now-number')

        if temp_element:
            temp_text = temp_element.get_text(strip=True)
            match = re.search(r'([-+]?\d+)', temp_text)
            if match:
                return float(match.group(1))
        return None
    except requests.RequestException as e:
        logger.error(f"Ошибка получения температуры воды: {e}")
        return None

# Проверка изменения температуры воды
def check_water_temperature():
    global previous_temperature
    current_temperature = get_water_temperature()

    if current_temperature is not None:
        if previous_temperature is None:
            previous_temperature = current_temperature
        elif current_temperature < previous_temperature:
            message = f"Температура воды упала! Сейчас: {current_temperature}°C, ранее: {previous_temperature}°C."
            asyncio.run(send_notification_to_all_users(message))
        previous_temperature = current_temperature

# Отправка уведомлений всем пользователям
async def send_notification_to_all_users(message):
    for chat_id in monitoring_chats.keys():
        try:
            await application.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {chat_id}: {e}")

# Функция для получения текущей температуры по координатам
def get_temperature(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru'
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200 and 'main' in data and 'temp' in data['main']:
        return data['main']['temp']
    logger.error(f"Ошибка получения данных о температуре: {response.status_code}")
    return None

# Получение прогноза погоды по координатам
def get_forecast(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru'
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200:
        return [f"{entry['dt_txt']}: {entry['main']['temp']}°C, {entry['weather'][0]['description']}" for entry in data['list'][:4]]
    logger.error(f"Ошибка получения прогноза: {response.status_code}")
    return None

# Генерация прогноза с юмором с использованием OpenAI
def generate_funny_forecast_with_openai(forecast):
    forecast_text = "\n".join(forecast)
    messages = [
        {"role": "system", "content": "You are a humorous weather forecaster. Write in Russian with humor and emojis."},
        {"role": "user", "content": f"Create a funny forecast for the next 12 hours: \n{forecast_text}."}
    ]

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=500,
            temperature=0.5
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"Ошибка генерации прогноза через OpenAI: {e}")
        return "Не удалось создать прогноз, но погода точно будет интересной! 😄"

# Генерация гороскопа с использованием OpenAI
def generate_horoscope_with_openai(sign):
    messages = [
        {"role": "system", "content": "You are an astrologer who writes daily horoscopes. Write in Russian with humor and emojis."},
        {"role": "user", "content": f"Write a horoscope for today for the zodiac sign {sign}."}
    ]

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=300,
            temperature=0.7
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"Ошибка генерации гороскопа через OpenAI: {e}")
        return "Не удалось создать гороскоп, но сделай этот день незабываемым! 😊"

# Получение данных о вспышках на солнце
def get_solar_flare_activity():
    url = f"https://api.nasa.gov/DONKI/FLR?startDate={time.strftime('%Y-%m-%d')}&api_key={NASA_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        if data:
            flare_events = [f"Класс {event.get('classType', 'неизвестный')} вспышки ожидается в {event.get('beginTime', 'неизвестное время')}" for event in data]
            return flare_events if flare_events else None
        return None
    except requests.RequestException as e:
        logger.error(f"Ошибка получения данных о солнечных вспышках: {e}")
        return None

# Отправка прогноза солнечных вспышек
async def send_solar_flare_forecast():
    flare_events = get_solar_flare_activity()
    if flare_events:
        message = "Внимание! Вспышки на солнце ожидаются в ближайшие 12 часов:\n" + "\n".join(flare_events)
    else:
        message = "В ближайшие 12 часов вспышек на солнце не ожидается."

    for chat_id in monitoring_chats.keys():
        try:
            await application.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {chat_id}: {e}")

# Обработчик локации пользователя
async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message.location:
        lat = update.message.location.latitude
        lon = update.message.location.longitude
        chat_location[chat_id] = (lat, lon)
        monitoring_chats[chat_id] = (lat, lon)
        save_location_to_db(chat_id, lat, lon)
        await update.message.reply_text("Локация принята! Теперь вы будете получать утренние прогнозы.")
    else:
        await update.message.reply_text("Не удалось получить локацию. Попробуйте снова.")

# Загрузка всех сохраненных данных о пользователях
monitoring_chats = load_all_locations()
chat_location = monitoring_chats.copy()

# Планирование автоматических уведомлений
def schedule_morning_forecast(time_str):
    schedule.every().day.at(time_str).do(lambda: asyncio.run(send_morning_forecast()))

def schedule_water_check():
    schedule.every(60).minutes.do(check_water_temperature)

def schedule_solar_flare_check():
    schedule.every(12).hours.do(lambda: asyncio.run(send_solar_flare_forecast()))

# Запуск планировщика
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

# Главный блок программы
if __name__ == '__main__':
    schedule_morning_forecast("08:00")
    schedule_water_check()
    schedule_solar_flare_check()
    threading.Thread(target=run_scheduler, daemon=True).start()
    application.run_polling()
