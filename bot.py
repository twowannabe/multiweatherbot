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
from bs4 import BeautifulSoup
from html import escape
import asyncio
import psycopg2
from psycopg2.extras import RealDictCursor
import re

# Настройки для подключения к API и боту
TELEGRAM_TOKEN = config('TELEGRAM_TOKEN')
API_KEY = config('OPENWEATHERMAP_API_KEY')

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

# Функция для подключения к базе данных
def get_db_connection():
    logger.info("Подключение к базе данных")
    return psycopg2.connect(
        host=config('DB_HOST'),
        port=config('DB_PORT'),
        dbname=config('DB_NAME'),
        user=config('DB_USER'),
        password=config('DB_PASSWORD')
    )

# Сохранение локации пользователя в базе данных
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

# Загрузка всех локаций пользователей из базы данных при старте
def load_all_locations():
    logger.info("Загрузка всех локаций пользователей из базы данных")
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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
    }
    logger.info(f"Запрос температуры воды по URL: {url}")

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        temp_element = soup.find('div', id='weather-now-number')

        if temp_element:
            temp_text = temp_element.get_text(strip=True)
            match = re.search(r'([-+]?\d+)', temp_text)
            if match:
                temperature = float(match.group(1))
                logger.info(f"Температура воды успешно получена: {temperature}°C")
                return temperature
        logger.warning("Температура воды не найдена на странице")
        return None
    except requests.RequestException as e:
        logger.error(f"Ошибка получения температуры воды: {e}")
        return None

# Функция для получения температуры воздуха по координатам
def get_temperature(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru'
    logger.info(f"Запрос температуры воздуха по URL: {url}")
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200 and 'main' in data and 'temp' in data['main']:
        temperature = data['main']['temp']
        logger.info(f"Температура воздуха успешно получена: {temperature}°C")
        return temperature
    logger.error(f"Ошибка получения данных о температуре: {response.status_code}")
    return None

# Функция для получения прогноза погоды
def get_forecast(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru'
    logger.info(f"Запрос прогноза погоды по URL: {url}")
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200:
        forecast_data = [f"{entry['dt_txt']}: {entry['main']['temp']}°C, {entry['weather'][0]['description']}" for entry in data['list'][:4]]
        logger.info(f"Прогноз погоды успешно получен: {forecast_data}")
        return forecast_data
    logger.error(f"Ошибка получения прогноза: {response.status_code}")
    return None

# Проверка изменения температуры воды
def check_water_temperature():
    global previous_temperature
    logger.info("Проверка изменения температуры воды")
    current_temperature = get_water_temperature()

    if current_temperature is not None:
        if previous_temperature is None:
            previous_temperature = current_temperature
        elif current_temperature < previous_temperature:
            message = f"Температура воды упала! Сейчас: {current_temperature}°C, ранее: {previous_temperature}°C."
            logger.info("Отправка уведомления о снижении температуры воды")
            asyncio.run(send_notification_to_all_users(message))
        previous_temperature = current_temperature

# Отправка уведомлений всем пользователям
async def send_notification_to_all_users(message):
    logger.info("Отправка уведомлений всем пользователям")
    for chat_id in monitoring_chats.keys():
        try:
            await application.bot.send_message(chat_id=chat_id, text=message)
            logger.info(f"Сообщение успешно отправлено пользователю {chat_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {chat_id}: {e}")

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Команда /start от пользователя {chat_id}")
    await update.message.reply_text(
        "Бот запущен! Пожалуйста, отправьте свою локацию для получения прогноза погоды."
    )
    if chat_id not in monitoring_chats:
        monitoring_chats[chat_id] = None

# Обработчик команды /temp
async def temp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Команда /temp от пользователя {chat_id}")
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        if temp is not None:
            await update.message.reply_text(f"Текущая температура воздуха: {temp}°C")
        else:
            await update.message.reply_text("Не удалось получить данные о температуре.")
    else:
        await update.message.reply_text("Локация не отправлена. Пожалуйста, сначала отправьте свою локацию.")

# Обработчик команды /water
async def water(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Команда /water от пользователя {chat_id}")
    temperature = get_water_temperature()
    if temperature is not None:
        await update.message.reply_text(f"Температура воды в Будве: {temperature}°C")
    else:
        await update.message.reply_text("Не удалось получить данные о температуре воды.")

# Обработчик команды /forecast
async def send_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Команда /forecast от пользователя {chat_id}")
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        forecast_data = get_forecast(lat, lon)
        if forecast_data is not None:
            forecast = "\n".join(forecast_data)
            forecast_message = f"Текущая температура воздуха: {temp}°C\n{forecast}"
            forecast_message = escape(forecast_message)
            await update.message.reply_text(forecast_message, parse_mode="HTML")
        else:
            await update.message.reply_text("Не удалось получить данные о прогнозе.")
    else:
        await update.message.reply_text("Локация не отправлена. Пожалуйста, сначала отправьте свою локацию.")

# Функция для получения прогноза солнечных вспышек
def get_solar_flare_forecast():
    url = "https://services.swpc.noaa.gov/text/3-day-forecast.txt"
    logger.info(f"Запрос прогноза солнечных вспышек по URL: {url}")

    try:
        response = requests.get(url)
        response.raise_for_status()
        text_data = response.text
        logger.info("Данные прогноза солнечных вспышек успешно получены")

        # Парсим текстовые данные
        forecast = parse_swpc_flare_forecast(text_data)

        return forecast
    except requests.RequestException as e:
        logger.error(f"Ошибка получения прогноза солнечных вспышек: {e}")
        return "Ошибка получения прогноза солнечных вспышек."
    except Exception as e:
        logger.error(f"Ошибка при разборе прогноза солнечных вспышек: {e}")
        return "Ошибка при разборе прогноза солнечных вспышек."

# Функция для парсинга прогноза солнечных вспышек
def parse_swpc_flare_forecast(text_data):
    lines = text_data.splitlines()
    forecast_data = []
    capture = False

    for line in lines:
        if "IIA. Geophysical Activity Summary" in line:
            capture = False
        if capture:
            forecast_data.append(line.strip())
        if "IB. Solar Activity Forecast" in line:
            capture = True

    if forecast_data:
        forecast_text = '\n'.join(forecast_data)
        logger.info(f"Прогноз солнечной активности:\n{forecast_text}")
        return forecast_text
    else:
        logger.warning("Данные прогноза не найдены в ответе SWPC")
        return "Нет данных о прогнозе солнечной активности."

# Обработчик команды /solarflare
async def send_solar_flare_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Обработка команды /solarflare")
    forecast = get_solar_flare_forecast()
    await update.message.reply_text(forecast)

# Отправка прогноза солнечных вспышек всем пользователям
async def send_solar_flare_forecast_to_all_users():
    logger.info("Отправка прогноза солнечных вспышек всем пользователям")
    forecast = get_solar_flare_forecast()
    for chat_id in monitoring_chats.keys():
        try:
            await application.bot.send_message(chat_id=chat_id, text=forecast)
            logger.info(f"Прогноз успешно отправлен пользователю {chat_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить прогноз пользователю {chat_id}: {e}")

# Обработчик локации пользователя
async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Получена локация от пользователя {chat_id}")
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

# Логирование информации о всех пользователях в monitoring_chats
logger.info("Список пользователей в monitoring_chats при запуске:")
for chat_id, location in monitoring_chats.items():
    logger.info(f"Пользователь {chat_id} с локацией: {location}")

# Регистрация всех хэндлеров
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('temp', temp))
application.add_handler(CommandHandler('water', water))
application.add_handler(CommandHandler('forecast', send_forecast))
application.add_handler(CommandHandler('solarflare', send_solar_flare_forecast))
application.add_handler(MessageHandler(filters.LOCATION, location_handler))

# Планирование автоматических уведомлений
def schedule_morning_forecast(time_str):
    schedule.every().day.at(time_str).do(lambda: asyncio.run(send_forecast_to_all_users()))

def schedule_water_check():
    schedule.every(60).minutes.do(check_water_temperature)

# Планирование автоматической отправки прогноза солнечных вспышек
def schedule_solar_flare_check():
    # Отправляем прогноз каждый день в 09:00
    schedule.every().day.at("09:00").do(lambda: asyncio.run(send_solar_flare_forecast_to_all_users()))

# Запуск планировщика
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

# Главный блок программы
if __name__ == '__main__':
    logger.info("Запуск бота и планировщика")
    # schedule_morning_forecast("08:00")  # Раскомментируйте, если у вас есть функция send_forecast_to_all_users
    schedule_water_check()
    schedule_solar_flare_check()
    threading.Thread(target=run_scheduler, daemon=True).start()
    application.run_polling()
