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
NASA_API_KEY = config('NASA_API_KEY')  # Добавлен ключ NASA API

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
# Отключение логов от httpx
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
                temp = match.group(1)
                return float(temp)
            else:
                logger.error("Не удалось извлечь температуру воды из текста.")
                return None
        else:
            logger.error("Не удалось найти элемент с информацией о температуре воды.")
            return None

    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе данных: {e}")
        return None

# Функция для проверки изменения температуры воды
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

# Функция для получения текущей температуры воздуха
def get_temperature(lat, lon):
    logger.info("Получаем температуру для координат: (%s, %s)", lat, lon)
    url = f'http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru'
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200:
        if 'main' in data and 'temp' in data['main']:
            temp = data['main']['temp']
            logger.info("Текущая температура: %s°C", temp)
            return temp
        else:
            logger.error("Не удалось найти данные о температуре воздуха.")
            return None
    else:
        logger.error("Ошибка при получении данных: %s", response.status_code)
        return None

# Функция для получения прогноза на следующие 12 часов
def get_forecast(lat, lon):
    logger.info("Получаем прогноз на следующие 12 часов для координат: (%s, %s)", lat, lon)
    url = f'http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru'
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200:
        forecast = []
        for entry in data['list'][:4]:
            time_period = entry['dt_txt']
            temp = entry['main']['temp']
            description = entry['weather'][0]['description']
            forecast.append(f"{time_period}: {temp}°C, {description}")
        logger.info("Прогноз на следующие 12 часов: %s", forecast)
        return forecast
    else:
        logger.error("Ошибка при получении прогноза: %s", response.status_code)
        return None

# Функция для генерации шуточного прогноза с помощью OpenAI API
def generate_funny_forecast_with_openai(forecast):
    logger.info("Генерация шуточного прогноза через OpenAI для прогноза на 12 часов")
    forecast_text = "\n".join(forecast)
    messages = [
        {"role": "system", "content": "Ты — синоптик, который делает смешные прогнозы погоды. Пиши на русском языке. Не используй незакрытые или лишние символы форматирования."},
        {"role": "user", "content": f"Создай шуточный прогноз погоды на следующие 12 часов: \n{forecast_text}. Пожалуйста, завершай свои предложения и добавь немного юмора и эмодзи."}
    ]

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=500,
            temperature=0.5
        )
        forecast = response['choices'][0]['message']['content'].strip()
        logger.info("Сгенерированный прогноз: %s", forecast)
        return forecast
    except Exception as e:
        logger.error("Ошибка при генерации прогноза через OpenAI: %s", str(e))
        return "Прогноз не удалось создать, но я уверен, что погода будет интересной! 😄"

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
                if forecast_entries:
                    forecast_data.extend(forecast_entries)

                forecast = generate_funny_forecast_with_openai(forecast_data)
                forecast_message = f"Текущая температура воздуха: {temp}°C\n{forecast}"
                # Экранируем специальные символы HTML
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
        else:
            try:
                await bot.send_message(chat_id=chat_id, text="Не удалось получить координаты для прогноза.")
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение пользователю {chat_id}: {e}")

# Асинхронная функция для обработки команды /forecast
async def send_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        forecast_data = get_forecast(lat, lon)
        if forecast_data is not None:
            forecast = generate_funny_forecast_with_openai(forecast_data)
            forecast_message = f"Текущая температура воздуха: {temp}°C\n{forecast}"
            # Экранируем специальные символы HTML
            forecast_message = escape(forecast_message)
            await update.message.reply_text(forecast_message, parse_mode="HTML")
        else:
            await update.message.reply_text("Не удалось получить данные о прогнозе.")
    else:
        await update.message.reply_text("Локация не была отправлена. Пожалуйста, сначала отправьте свою локацию.")

# Асинхронная функция для обработки команды /horoscope
async def send_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in user_signs:
        sign = user_signs[chat_id]
        horoscope = generate_horoscope_with_openai(sign)
        horoscope_message = f"Ваш гороскоп на сегодня ({sign.title()}):\n{horoscope}"
        # Экранируем специальные символы HTML
        horoscope_message = escape(horoscope_message)
        await update.message.reply_text(horoscope_message, parse_mode="HTML")
    else:
        await update.message.reply_text("Вы еще не установили свой знак зодиака. Пожалуйста, используйте /sign <ваш_знак>, чтобы установить его.")

# Функция для генерации гороскопа с помощью OpenAI API
def generate_horoscope_with_openai(sign):
    logger.info("Генерация гороскопа для знака: %s", sign)
    messages = [
        {"role": "system", "content": "Ты — астролог, который пишет ежедневные гороскопы. Пиши на русском языке. Пиши позитивно и увлекательно."},
        {"role": "user", "content": f"Напиши гороскоп на сегодня для знака зодиака {sign}. Пиши кратко, дружелюбно и добавь немного юмора и эмодзи."}
    ]

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=300,
            temperature=0.7
        )
        horoscope = response['choices'][0]['message']['content'].strip()
        finish_reason = response['choices'][0].get('finish_reason', 'unknown')
        logger.info("Сгенерированный гороскоп: %s", horoscope)
        logger.info("Причина завершения генерации: %s", finish_reason)
        return horoscope
    except Exception as e:
        logger.error("Ошибка при генерации гороскопа через OpenAI: %s", str(e))
        return "Не удалось создать гороскоп, но сделайте этот день незабываемым! 😊"

# Асинхронная функция для обработки команды /sign
async def set_sign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.args:
        sign_name = context.args[0].lower()
        zodiac_signs = ['овен', 'телец', 'близнецы', 'рак', 'лев', 'дева',
                        'весы', 'скорпион', 'стрелец', 'козерог', 'водолей', 'рыбы']

        if sign_name in zodiac_signs:
            user_signs[chat_id] = sign_name
            await update.message.reply_text(f"Ваш знак зодиака установлен как {sign_name.title()}.")
        else:
            await update.message.reply_text("Неверный знак зодиака. Пожалуйста, введите один из 12 знаков зодиака.")
    else:
        await update.message.reply_text("Пожалуйста, укажите ваш знак зодиака, используя /sign <ваш_знак>.")

# Функция для получения данных о солнечных вспышках
def check_solar_flare_activity():
    logger.info("Проверяем наличие солнечных вспышек")
    url = f"https://api.nasa.gov/DONKI/FLR?startDate={time.strftime('%Y-%m-%d')}&api_key={NASA_API_KEY}"

    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        if data:
            flare_events = []
            for event in data:
                flare_time = event.get("beginTime", "неизвестно")
                intensity = event.get("classType", "неизвестно")
                flare_events.append(f"Вспышка класса {intensity} ожидается {flare_time}")
            logger.info("Обнаружены солнечные вспышки: %s", flare_events)
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

# Асинхронная функция для обработки команды /water
async def water(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    temperature = get_water_temperature()
    if temperature is not None:
        await update.message.reply_text(f"Температура воды в Будве: {temperature}°C")
    else:
        await update.message.reply_text("Не удалось получить температуру воды.")

# Асинхронная функция для обработки команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "Бот запущен! Пожалуйста, отправьте вашу локацию, чтобы я мог отслеживать температуру воздуха и отправлять прогнозы погоды."
    )
    if chat_id not in monitoring_chats:
        monitoring_chats[chat_id] = None

# Асинхронная функция для обработки команды /temp
async def temp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        if temp is not None:
            await update.message.reply_text(f"Текущая температура воздуха: {temp}°C")
        else:
            await update.message.reply_text("Не удалось получить данные о температуре.")
    else:
        await update.message.reply_text("Локация не была отправлена. Пожалуйста, сначала отправьте свою локацию.")

# Асинхронная функция для обработки получения локации
async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message.location:
        lat = update.message.location.latitude
        lon = update.message.location.longitude
        chat_location[chat_id] = (lat, lon)
        monitoring_chats[chat_id] = (lat, lon)
        await update.message.reply_text(f"Локация получена! Теперь вы будете получать прогноз погоды каждое утро.")
    else:
        await update.message.reply_text("Не удалось получить вашу локацию. Пожалуйста, попробуйте снова.")

# Настройка обработчиков команд и сообщений
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('temp', temp))
application.add_handler(CommandHandler('forecast', send_forecast))
application.add_handler(CommandHandler('water', water))
application.add_handler(MessageHandler(filters.LOCATION, location_handler))
application.add_handler(CommandHandler('sign', set_sign))
application.add_handler(CommandHandler('horoscope', send_horoscope))

# Функция для планирования отправки утреннего прогноза
def schedule_morning_forecast(time_str):
    logger.info("Запланированная отправка прогноза на %s", time_str)
    schedule.every().day.at(time_str).do(lambda: asyncio.run(send_morning_forecast()))

# Планирование проверки температуры воды каждые 60 минут
def schedule_water_check():
    logger.info("Запланированная проверка температуры воды каждые 60 минут")
    schedule.every(60).minutes.do(check_water_temperature)

# Планирование проверки солнечной активности каждые 12 часов
def schedule_solar_flare_check():
    logger.info("Запланированная проверка солнечной активности каждые 12 часов")
    schedule.every(12).hours.do(lambda: asyncio.run(notify_solar_flare()))

# Функция для запуска планировщика в отдельном потоке
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

# Запуск бота
if __name__ == '__main__':
    # Запуск планирования задач
    schedule_morning_forecast("08:00")  # Задайте время для утреннего прогноза
    schedule_water_check()
    schedule_solar_flare_check()

    threading.Thread(target=run_scheduler, daemon=True).start()
    application.run_polling()
