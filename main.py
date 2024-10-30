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

# Загрузка ключей из .env файла
TELEGRAM_TOKEN = config('TELEGRAM_TOKEN')
API_KEY = config('OPENWEATHERMAP_API_KEY')
OPENAI_API_KEY = config('OPENAI_API_KEY')
NASA_API_KEY = config('NASA_API_KEY')

# Инициализация OpenAI API
openai.api_key = OPENAI_API_KEY

# Инициализация бота
bot = Bot(token=TELEGRAM_TOKEN)
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Глобальные переменные
chat_location = {}
monitoring_chats = {}
previous_temperature = None
user_signs = {}

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
            else:
                logger.error("Не удалось извлечь температуру воды из текста.")
                return None
        else:
            logger.error("Не удалось найти элемент с информацией о температуре воды.")
            return None
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе данных: {e}")
        return None

def check_water_temperature():
    global previous_temperature
    current_temperature = get_water_temperature()
    if current_temperature is not None:
        if previous_temperature is None:
            previous_temperature = current_temperature
        elif current_temperature < previous_temperature:
            message = f"Температура воды упала! Сейчас: {current_temperature}°C, предыдущая: {previous_temperature}°C."
            asyncio.run(send_notification_to_all_users(message))
        previous_temperature = current_temperature

# Асинхронная функция для отправки уведомления всем пользователям
async def send_notification_to_all_users(message):
    for chat_id in monitoring_chats.keys():
        try:
            await bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {chat_id}: {e}")

# Функция для получения данных о солнечных вспышках
def check_solar_flare_activity():
    url = f"https://api.nasa.gov/DONKI/FLR?startDate=2023-01-01&api_key={NASA_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data:
            flare_events = []
            for event in data:
                flare_time = event.get("beginTime")
                intensity = event.get("classType")
                flare_events.append(f"Вспышка {intensity} ожидается {flare_time}")
            return flare_events if flare_events else None
        else:
            logger.info("Нет вспышек на ближайшие дни")
            return None
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе данных о солнечных вспышках: {e}")
        return None

# Асинхронная функция для уведомления о солнечных вспышках
async def notify_solar_flare():
    solar_flare_events = check_solar_flare_activity()
    if solar_flare_events:
        message = "Внимание! В ближайшие дни ожидаются солнечные вспышки:\n" + "\n".join(solar_flare_events)
        for chat_id in monitoring_chats.keys():
            try:
                await bot.send_message(chat_id=chat_id, text=message)
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление пользователю {chat_id}: {e}")

# Функция для планирования отправки прогноза
def schedule_morning_forecast(time_str):
    schedule.every().day.at(time_str).do(lambda: asyncio.run(send_morning_forecast()))

# Планирование проверки солнечной активности
def schedule_solar_flare_check():
    schedule.every(12).hours.do(lambda: asyncio.run(notify_solar_flare()))

# Функция для планирования проверки температуры воды каждые 60 минут
def schedule_water_check():
    schedule.every(60).minutes.do(check_water_temperature)

# Асинхронная функция для отправки утреннего прогноза
async def send_morning_forecast():
    logger.info("Отправка утреннего прогноза для всех пользователей")
    for chat_id, coords in monitoring_chats.items():
        if coords:
            lat, lon = coords
            temp = get_temperature(lat, lon)
            if temp is not None:
                forecast_data = [f"Текущая температура: {temp}°C"]
                forecast_entries = get_forecast(lat, lon)
                forecast = generate_funny_forecast_with_openai(forecast_data + forecast_entries)
                forecast_message = f"Текущая температура воздуха: {temp}°C\n{forecast}"
                forecast_message = escape(forecast_message)
                try:
                    await bot.send_message(chat_id=chat_id, text=forecast_message, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Не удалось отправить прогноз пользователю {chat_id}: {e}")
            else:
                try:
                    await bot.send_message(chat_id=chat_id, text="Не удалось получить данные о температуре.")
                except Exception as e:
                    logger.error(f"Не удалось отправить сообщение пользователю {chat_id}: {e}")

# Запуск бота
if __name__ == '__main__':
    schedule_morning_forecast("08:00")
    schedule_water_check()
    schedule_solar_flare_check()

    # Функция для запуска планировщика в отдельном потоке
    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(1)

    threading.Thread(target=run_scheduler, daemon=True).start()
    application.run_polling()
