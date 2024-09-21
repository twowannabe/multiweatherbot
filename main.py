import requests
import schedule
import time
import logging
from decouple import config
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext, MessageHandler, Filters
import threading
import openai
from bs4 import BeautifulSoup
from html import escape  # Для экранирования специальных символов HTML

# Загрузка ключей из .env файла
TELEGRAM_TOKEN = config('TELEGRAM_TOKEN')
API_KEY = config('OPENWEATHERMAP_API_KEY')
OPENAI_API_KEY = config('OPENAI_API_KEY')

# Инициализация OpenAI API
openai.api_key = OPENAI_API_KEY

# Инициализация бота
bot = Bot(token=TELEGRAM_TOKEN)
updater = Updater(token=TELEGRAM_TOKEN, use_context=True)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# Глобальные переменные для хранения chat_id, координат, предыдущей температуры воды и знаков зодиака пользователей
chat_location = {}
monitoring_chats = {}
previous_temperature = None
user_signs = {}  # Новый словарь для хранения знаков зодиака пользователей

# Функция для получения температуры воды
def get_water_temperature():
    url = 'https://world-weather.ru/pogoda/montenegro/budva/water/'  # URL страницы
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, как Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        soup = BeautifulSoup(response.content, 'html.parser')

        # Найти элемент с температурой воды
        temp_element = soup.find('div', id='weather-now-number')

        if temp_element:
            temp = temp_element.get_text(strip=True).replace("°C", "").replace("+", "")
            return float(temp)
        else:
            return None
    else:
        logger.error(f"Ошибка при запросе данных: {response.status_code}")
        return None

# Функция для проверки изменения температуры воды и отправки уведомлений
def check_water_temperature():
    global previous_temperature
    current_temperature = get_water_temperature()

    if current_temperature is not None:
        if previous_temperature is None:
            previous_temperature = current_temperature
        elif current_temperature < previous_temperature:
            message = f"Температура воды упала! Сейчас: {current_temperature}°C, предыдущая: {previous_temperature}°C."
            send_notification_to_all_users(message)
        previous_temperature = current_temperature

# Функция для отправки уведомления всем пользователям
def send_notification_to_all_users(message):
    for chat_id in monitoring_chats.keys():
        bot.send_message(chat_id=chat_id, text=message)

# Функция для обработки команды /water
def water(update, context):
    chat_id = update.message.chat_id
    temperature = get_water_temperature()
    if temperature is not None:
        context.bot.send_message(chat_id=chat_id, text=f"Температура воды в Будве: {temperature}°C")
    else:
        context.bot.send_message(chat_id=chat_id, text="Не удалось получить температуру воды.")

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
        for entry in data['list'][:4]:  # Получаем данные за ближайшие 12 часов (4 временных периода по 3 часа)
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
def generate_funny_forecast_with_openai(forecast, horoscope=None):
    logger.info("Генерация шуточного прогноза через OpenAI для прогноза на 12 часов")
    forecast_text = "\n".join(forecast)
    messages = [
        {"role": "system", "content": "Ты — синоптик, который делает смешные прогнозы погоды. Пиши на русском языке. Не используй незакрытые или лишние символы форматирования."},
        {"role": "user", "content": f"Создай шуточный прогноз погоды на следующие 12 часов: \n{forecast_text}. Пожалуйста, завершай свои предложения и добавь немного юмора и эмодзи."}
    ]

    # Если предоставлен гороскоп, включаем его в запрос
    if horoscope:
        messages.append({"role": "user", "content": f"Также включи следующий гороскоп в прогноз:\n{horoscope}"})

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=500,  # Увеличено до 500
            temperature=0.5
        )
        forecast = response['choices'][0]['message']['content'].strip()
        logger.info("Сгенерированный прогноз: %s", forecast)
        return forecast
    except Exception as e:
        logger.error("Ошибка при генерации прогноза через OpenAI: %s", str(e))
        return "Прогноз не удалось создать, но я уверен, что погода будет интересной! 😄"

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
            max_tokens=150,
            temperature=0.7
        )
        horoscope = response['choices'][0]['message']['content'].strip()
        logger.info("Сгенерированный гороскоп: %s", horoscope)
        return horoscope
    except Exception as e:
        logger.error("Ошибка при генерации гороскопа через OpenAI: %s", str(e))
        return "Не удалось создать гороскоп, но сделайте этот день незабываемым! 😊"

# Функция для отправки утреннего прогноза
def send_morning_forecast():
    logger.info("Отправка утреннего прогноза для всех пользователей")
    for chat_id, (lat, lon) in monitoring_chats.items():
        temp = get_temperature(lat, lon)
        if temp is not None:
            forecast_data = [f"Текущая температура: {temp}°C"]
            forecast_entries = get_forecast(lat, lon)
            if forecast_entries:
                forecast_data.extend(forecast_entries)

            # Проверяем, установил ли пользователь знак зодиака
            horoscope = None
            if chat_id in user_signs:
                sign = user_signs[chat_id]
                horoscope = generate_horoscope_with_openai(sign)

            forecast = generate_funny_forecast_with_openai(forecast_data, horoscope)
            forecast_message = f"Текущая температура воздуха: {temp}°C\n{forecast}"
            # Экранируем специальные символы HTML
            forecast_message = escape(forecast_message)
            bot.send_message(chat_id=chat_id, text=forecast_message, parse_mode="HTML")
        else:
            bot.send_message(chat_id=chat_id, text="Не удалось получить данные о температуре.")

# Функция для отправки прогноза по команде /forecast
def send_forecast(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        forecast_data = get_forecast(lat, lon)
        if forecast_data is not None:
            # Проверяем, установил ли пользователь знак зодиака
            horoscope = None
            if chat_id in user_signs:
                sign = user_signs[chat_id]
                horoscope = generate_horoscope_with_openai(sign)

            forecast = generate_funny_forecast_with_openai(forecast_data, horoscope)
            forecast_message = f"Текущая температура воздуха: {temp}°C\n{forecast}"
            # Экранируем специальные символы HTML
            forecast_message = escape(forecast_message)
            update.message.reply_text(forecast_message, parse_mode="HTML")
        else:
            update.message.reply_text("Не удалось получить данные о прогнозе.")
    else:
        update.message.reply_text("Локация не была отправлена. Пожалуйста, сначала отправьте свою локацию.")

# Функция для обработки команды /horoscope
def send_horoscope(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    if chat_id in user_signs:
        sign = user_signs[chat_id]
        horoscope = generate_horoscope_with_openai(sign)
        horoscope_message = f"Ваш гороскоп на сегодня ({sign.title()}):\n{horoscope}"
        # Экранируем специальные символы HTML
        horoscope_message = escape(horoscope_message)
        update.message.reply_text(horoscope_message, parse_mode="HTML")
    else:
        update.message.reply_text("Вы еще не установили свой знак зодиака. Пожалуйста, используйте /sign <ваш_знак>, чтобы установить его.")

# Функция для обработки команды /sign
def set_sign(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    if context.args:
        sign_name = context.args[0].lower()
        zodiac_signs = ['овен', 'телец', 'близнецы', 'рак', 'лев', 'дева',
                        'весы', 'скорпион', 'стрелец', 'козерог', 'водолей', 'рыбы']

        if sign_name in zodiac_signs:
            user_signs[chat_id] = sign_name
            update.message.reply_text(f"Ваш знак зодиака установлен как {sign_name.title()}.")
        else:
            update.message.reply_text("Неверный знак зодиака. Пожалуйста, введите один из 12 знаков зодиака.")
    else:
        update.message.reply_text("Пожалуйста, укажите ваш знак зодиака, используя /sign <ваш_знак>.")

# Функция для планирования отправки утреннего прогноза
def schedule_morning_forecast(time_str):
    logger.info("Запланированная отправка прогноза на %s", time_str)
    schedule.every().day.at(time_str).do(send_morning_forecast)

# Функция для планирования проверки температуры воды каждые 60 минут
def schedule_water_check():
    logger.info("Запланированная проверка температуры воды каждые 60 минут")
    schedule.every(60).minutes.do(check_water_temperature)

# Функция для обработки команды /start
def start(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    update.message.reply_text("Бот запущен! Пожалуйста, отправьте вашу локацию, чтобы я мог отслеживать температуру воздуха и отправлять прогнозы погоды.")
    if chat_id not in monitoring_chats:
        monitoring_chats[chat_id] = None

# Функция для обработки команды /temp
def temp(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id

    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        if temp is not None:
            update.message.reply_text(f"Текущая температура воздуха: {temp}°C")
        else:
            update.message.reply_text("Не удалось получить данные о температуре.")
    else:
        update.message.reply_text("Локация не была отправлена. Пожалуйста, сначала отправьте свою локацию.")

# Функция для обработки получения локации
def location_handler(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    lat = update.message.location.latitude
    lon = update.message.location.longitude
    chat_location[chat_id] = (lat, lon)
    monitoring_chats[chat_id] = (lat, lon)
    update.message.reply_text(f"Локация получена! Теперь вы будете получать прогноз погоды каждое утро.")

# Настройка обработчиков команд и сообщений
start_handler = CommandHandler('start', start)
temp_handler = CommandHandler('temp', temp)
forecast_handler = CommandHandler('forecast', send_forecast)
water_handler = CommandHandler('water', water)
location_message_handler = MessageHandler(Filters.location, location_handler)
sign_handler = CommandHandler('sign', set_sign)
horoscope_handler = CommandHandler('horoscope', send_horoscope)

# Добавление обработчиков в диспетчер
updater.dispatcher.add_handler(start_handler)
updater.dispatcher.add_handler(temp_handler)
updater.dispatcher.add_handler(forecast_handler)
updater.dispatcher.add_handler(water_handler)
updater.dispatcher.add_handler(location_message_handler)
updater.dispatcher.add_handler(sign_handler)
updater.dispatcher.add_handler(horoscope_handler)

# Запуск планирования задач
schedule_morning_forecast("08:00")  # Задайте время для утреннего прогноза
schedule_water_check()  # Запускаем проверку температуры воды каждые 60 минут

# Функция для запуска планировщика в отдельном потоке
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()

# Запуск бота
updater.start_polling()
updater.idle()
