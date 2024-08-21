import requests
import schedule
import time
import logging
from decouple import config
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext, MessageHandler, Filters
import threading
import openai

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

# Глобальные переменные для хранения chat_id и координат
chat_location = {}
monitoring_chats = {}

# Функция для получения температуры воздуха
def get_temperature(lat, lon):
    logger.info("Получаем температуру для координат: (%s, %s)", lat, lon)
    url = f'http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={API_KEY}&units=metric'
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

# Функция для генерации шуточного прогноза с помощью OpenAI API
def generate_funny_forecast_with_openai(temp):
    logger.info("Генерация шуточного прогноза через OpenAI для температуры: %s°C", temp)
    prompt = f"Создай шуточный прогноз погоды для температуры {temp}°C в Черногории. Учти, что большую часть года температура здесь колеблется от 10 до 40°C."

    try:
        response = openai.Completion.create(
            engine="gpt-4o",
            prompt=prompt,
            max_tokens=50,
            temperature=0.7
        )
        forecast = response.choices[0].text.strip()
        logger.info("Сгенерированный прогноз: %s", forecast)
        return forecast
    except Exception as e:
        logger.error("Ошибка при генерации прогноза через OpenAI: %s", str(e))
        return "Прогноз не удалось создать, но я уверен, что погода будет интересной!"

# Функция для отправки утреннего прогноза
def send_morning_forecast():
    logger.info("Отправка утреннего прогноза для всех пользователей")
    for chat_id, (lat, lon) in monitoring_chats.items():
        temp = get_temperature(lat, lon)
        if temp is not None:
            forecast = generate_funny_forecast_with_openai(temp)
            bot.send_message(chat_id=chat_id, text=f"Доброе утро! Текущая температура: {temp}°C\n{forecast}")

# Функция для отправки прогноза по команде /forecast
def send_forecast(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        if temp is not None:
            forecast = generate_funny_forecast_with_openai(temp)
            update.message.reply_text(f"Текущая температура: {temp}°C\n{forecast}")
        else:
            update.message.reply_text("Не удалось получить данные о температуре.")
    else:
        update.message.reply_text("Локация не была отправлена. Пожалуйста, сначала отправьте свою локацию.")

# Планирование отправки утреннего прогноза
def schedule_morning_forecast(time_str):
    logger.info("Запланированная отправка прогноза на %s", time_str)
    schedule.every().day.at(time_str).do(send_morning_forecast)

# Функция для обработки команды /start
def start(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    update.message.reply_text("Бот запущен! Пожалуйста, отправьте вашу локацию, чтобы я мог отслеживать температуру воздуха и отправлять прогнозы погоды.")
    if chat_id not in monitoring_chats:
        monitoring_chats[chat_id] = None  # Добавляем пользователя в список мониторинга

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
location_handler = MessageHandler(Filters.location, location_handler)

updater.dispatcher.add_handler(start_handler)
updater.dispatcher.add_handler(temp_handler)
updater.dispatcher.add_handler(forecast_handler)
updater.dispatcher.add_handler(location_handler)

# Запуск планирования задач
schedule_morning_forecast("08:00")  # Задайте время, в которое вы хотите получать прогнозы

# Функция для запуска планировщика в отдельном потоке
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()

# Запуск бота
updater.start_polling()
updater.idle()
