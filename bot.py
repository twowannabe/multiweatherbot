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
import datetime
import pytz
import re
from asyncio import Queue

# Настройки для подключения к API и боту
TELEGRAM_TOKEN = config('TELEGRAM_TOKEN')
API_KEY = config('OPENWEATHERMAP_API_KEY')
NASA_API_KEY = config('NASA_API_KEY')

# Инициализация бота и приложения Telegram
bot = Bot(token=TELEGRAM_TOKEN)
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Глобальные переменные для хранения данных пользователей и очереди сообщений
chat_location = {}
monitoring_chats = {}
previous_temperature = None
message_queue = Queue()

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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
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
    try:
        response = requests.get(url)
        data = response.json()

        if response.status_code == 200 and 'main' in data and 'temp' in data['main']:
            temperature = data['main']['temp']
            logger.info(f"Температура воздуха успешно получена: {temperature}°C")
            return temperature
        logger.error(f"Ошибка получения данных о температуре: {response.status_code}")
        return None
    except requests.RequestException as e:
        logger.error(f"Ошибка запроса к OpenWeatherMap: {e}")
        return None

# Функция для получения прогноза погоды
def get_forecast(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru'
    logger.info(f"Запрос прогноза погоды по URL: {url}")
    try:
        response = requests.get(url)
        data = response.json()

        if response.status_code == 200:
            forecast_data = [
                f"{entry['dt_txt']}: {entry['main']['temp']}°C, {entry['weather'][0]['description']}"
                for entry in data['list'][:4]
            ]
            logger.info(f"Прогноз погоды успешно получен: {forecast_data}")
            return forecast_data
        logger.error(f"Ошибка получения прогноза: {response.status_code}")
        return None
    except requests.RequestException as e:
        logger.error(f"Ошибка запроса к OpenWeatherMap для прогноза: {e}")
        return None

# Функция для получения данных о солнечных вспышках
def get_solar_flare_activity():
    # Определение временного диапазона: вчера, сегодня и завтра
    now = datetime.datetime.now(datetime.timezone.utc)
    yesterday = (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    tomorrow = (now + datetime.timedelta(days=1)).strftime('%Y-%m-%d')

    # Запрос данных о солнечных вспышках за период с вчерашнего дня по завтрашний день включительно
    url = f"https://api.nasa.gov/DONKI/FLR?startDate={yesterday}&endDate={tomorrow}&api_key={NASA_API_KEY}"
    logger.info(f"Запрос данных о солнечных вспышках по URL: {url}")

    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Данные о солнечных вспышках получены: {data}")

        if data:
            past_flares = []
            future_flares = []

            # Определяем временную зону GMT+1
            gmt_plus_one = pytz.timezone('Europe/Brussels')

            for event in data:
                class_type = event.get('classType', 'неизвестный')
                begin_time = event.get('beginTime', 'неизвестное время')

                # Парсинг времени начала вспышки
                try:
                    begin_time_iso = begin_time.replace('Z', '+00:00')
                    dt_begin = datetime.datetime.fromisoformat(begin_time_iso)
                    dt_begin = dt_begin.astimezone(gmt_plus_one)  # Конвертация в GMT+1
                    logger.info(f"Вспышка класса {class_type} в {dt_begin}")
                except ValueError as e:
                    logger.error(f"Ошибка парсинга времени начала вспышки: {e}")
                    dt_begin = None

                # Разделение вспышек на прошедшие и ожидаемые
                if dt_begin:
                    # Форматирование времени
                    begin_time_formatted = dt_begin.strftime('%d.%m.%Y %H:%M GMT+1')

                    # Определение интенсивности и эмодзи
                    if class_type.startswith('A') or class_type.startswith('B'):
                        intensity = 'низкая'
                        emoji = '🟢'
                    elif class_type.startswith('C'):
                        intensity = 'средняя'
                        emoji = '🟡'
                    elif class_type.startswith('M'):
                        intensity = 'высокая'
                        emoji = '🟠'
                    elif class_type.startswith('X'):
                        intensity = 'очень высокая'
                        emoji = '🔴'
                    else:
                        intensity = 'неизвестная'
                        emoji = '⚪'

                    if dt_begin < now:
                        # Прошедшие вспышки
                        status = "произошла"
                        flare_event = f"{emoji} Вспышка класса {class_type} ({intensity} интенсивность) {status} в {begin_time_formatted}"
                        past_flares.append(flare_event)
                    else:
                        # Ожидаемые вспышки
                        status = "ожидается"
                        flare_event = f"{emoji} Вспышка класса {class_type} ({intensity} интенсивность) {status} в {begin_time_formatted}"
                        future_flares.append(flare_event)

            # Формируем итоговое сообщение
            flare_messages = []

            if past_flares:
                flare_messages.append("*Произошли следующие солнечные вспышки за вчера и сегодня:*")
                flare_messages.extend(past_flares)

            if future_flares:
                flare_messages.append("*Ожидаются следующие солнечные вспышки сегодня и завтра:*")
                flare_messages.extend(future_flares)

            if flare_messages:
                # Соединяем все части сообщения
                final_message = "\n".join(flare_messages)
                return final_message
            else:
                logger.info("Вспышки не найдены за вчера, сегодня и завтра")
                return "Вспышек на Солнце за вчера, сегодня и завтра не зафиксировано."

        logger.info("Нет данных о солнечных вспышках")
        return "Нет данных о солнечных вспышках."
    except requests.RequestException as e:
        logger.error(f"Ошибка получения данных о солнечных вспышках: {e}")
        return "Ошибка получения данных о солнечных вспышках."

# Проверка изменения температуры воды
async def check_water_temperature():
    global previous_temperature
    logger.info("Проверка изменения температуры воды")
    current_temperature = get_water_temperature()

    if current_temperature is not None:
        if previous_temperature is None:
            previous_temperature = current_temperature
        elif current_temperature < previous_temperature:
            message = f"Температура воды упала! Сейчас: {current_temperature}°C, ранее: {previous_temperature}°C."
            logger.info("Отправка уведомления о снижении температуры воды")
            await send_notification_to_all_users(message)
        previous_temperature = current_temperature

# Отправка уведомлений всем пользователям через очередь
async def send_notification_to_all_users(message):
    logger.info("Отправка уведомлений всем пользователям")
    for chat_id in monitoring_chats.keys():
        await message_queue.put((chat_id, message))

# Обработчик очереди для отправки сообщений
async def process_queue():
    while True:
        chat_id, message = await message_queue.get()
        try:
            await application.bot.send_message(chat_id=chat_id, text=message)
            logger.info(f"Сообщение успешно отправлено пользователю {chat_id}")
            # Добавляем небольшую задержку для предотвращения превышения лимитов
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения пользователю {chat_id}: {e}")
        finally:
            message_queue.task_done()

# Обработчики команд и локации
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Команда /start от пользователя {chat_id}")
    await update.message.reply_text(
        "Бот запущен! Пожалуйста, отправьте свою локацию для получения прогноза погоды."
    )
    if chat_id not in monitoring_chats:
        monitoring_chats[chat_id] = None

async def temp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def water(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Команда /water от пользователя {chat_id}")
    temperature = get_water_temperature()
    if temperature is not None:
        await update.message.reply_text(f"Температура воды в Будве: {temperature}°C")
    else:
        await update.message.reply_text("Не удалось получить данные о температуре воды.")

async def forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Команда /forecast от пользователя {chat_id}")
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        forecast_data = get_forecast(lat, lon)
        if temp is not None and forecast_data is not None:
            forecast = "\n".join(forecast_data)
            forecast_message = f"Текущая температура воздуха: {temp}°C\nПрогноз погоды:\n{forecast}"
            forecast_message = escape(forecast_message)
            await update.message.reply_text(forecast_message, parse_mode="HTML")
        else:
            await update.message.reply_text("Не удалось получить данные о прогнозе.")
    else:
        await update.message.reply_text("Локация не отправлена. Пожалуйста, сначала отправьте свою локацию.")

async def solarflare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Команда /solarflare от пользователя {chat_id}")
    flare_events = get_solar_flare_activity()
    if flare_events:
        await update.message.reply_text(flare_events, parse_mode="Markdown")
    else:
        await update.message.reply_text("В ближайшие 12 часов вспышек на солнце не ожидается.")

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

# Отправка прогноза всем пользователям (для утренних уведомлений)
async def send_forecast_to_all_users():
    logger.info("Автоматическая отправка прогноза всем пользователям")
    for chat_id, (lat, lon) in monitoring_chats.items():
        temp = get_temperature(lat, lon)
        forecast_data = get_forecast(lat, lon)
        if temp is not None and forecast_data is not None:
            forecast = "\n".join(forecast_data)
            forecast_message = f"Текущая температура воздуха: {temp}°C\nПрогноз погоды:\n{forecast}"
            await message_queue.put((chat_id, forecast_message))
        else:
            await message_queue.put((chat_id, "Не удалось получить данные о прогнозе."))

# Отправка прогноза солнечных вспышек всем пользователям
async def send_solar_flare_forecast_to_all_users():
    logger.info("Автоматическая отправка уведомлений о солнечных вспышках всем пользователям")
    flare_events = get_solar_flare_activity()
    if flare_events:
        for chat_id in monitoring_chats.keys():
            await message_queue.put((chat_id, flare_events))
    else:
        for chat_id in monitoring_chats.keys():
            await message_queue.put((chat_id, "В ближайшие 12 часов вспышек на солнце не ожидается."))

# Планирование автоматических уведомлений
def schedule_tasks():
    # Получаем текущий активный цикл событий
    loop = asyncio.get_running_loop()
    # Проверка изменения температуры воды каждые 60 минут
    schedule.every(60).minutes.do(lambda: asyncio.run_coroutine_threadsafe(check_water_temperature(), loop))
    # Отправка прогноза всем пользователям в утреннее время (например, в 08:00)
    schedule.every().day.at("08:00").do(lambda: asyncio.run_coroutine_threadsafe(send_forecast_to_all_users(), loop))
    # Отправка уведомлений о солнечных вспышках каждые 12 часов
    schedule.every(12).hours.do(lambda: asyncio.run_coroutine_threadsafe(send_solar_flare_forecast_to_all_users(), loop))

# Запуск планировщика в отдельном потоке
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

# Функция, вызываемая при старте приложения
async def on_startup(application):
    logger.info("Запуск фоновых задач")
    application.create_task(process_queue())
    schedule_tasks()
    threading.Thread(target=run_scheduler, daemon=True).start()

# Главный блок программы
if __name__ == '__main__':
    logger.info("Запуск бота и планировщика")

    # Загрузка всех сохранённых локаций пользователей
    monitoring_chats = load_all_locations()
    chat_location = monitoring_chats.copy()

    # Логирование информации о всех пользователях при запуске
    logger.info("Список пользователей в monitoring_chats при запуске:")
    for chat_id, location in monitoring_chats.items():
        logger.info(f"Пользователь {chat_id} с локацией: {location}")

    # Регистрация всех хэндлеров
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('temp', temp_command))
    application.add_handler(CommandHandler('water', water))
    application.add_handler(CommandHandler('forecast', forecast))
    application.add_handler(CommandHandler('solarflare', solarflare))
    application.add_handler(MessageHandler(filters.LOCATION, location_handler))

    # Регистрация функции on_startup
    application.run_polling(on_startup=on_startup)
