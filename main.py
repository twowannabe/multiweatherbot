import requests
from decouple import config
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext, MessageHandler, Filters
from time import sleep
import threading

# Загрузка ключей из .env файла
TELEGRAM_TOKEN = config('TELEGRAM_TOKEN')
API_KEY = config('OPENWEATHERMAP_API_KEY')

# Инициализация бота
bot = Bot(token=TELEGRAM_TOKEN)
updater = Updater(token=TELEGRAM_TOKEN, use_context=True)

# Глобальные переменные для хранения chat_id и координат
chat_location = {}
monitoring_chats = {}

# Функция для получения температуры воздуха
def get_temperature(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={API_KEY}&units=metric'
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200:
        if 'main' in data and 'temp' in data['main']:
            temp = data['main']['temp']
            return temp
        else:
            print("Не удалось найти данные о температуре воздуха.")
            return None
    else:
        print(f"Ошибка при получении данных: {response.status_code}")
        return None

# Функция для генерации шуточного прогноза погоды
def generate_funny_forecast(temp):
    if temp <= 0:
        return "На улице такой дубак, что лучше не выходить без костюма белого медведя!"
    elif temp <= 10:
        return "Слегка прохладно, не забудь шарф, а то превратишься в ледышку!"
    elif temp <= 20:
        return "Неплохо, но куртка не помешает. Погода как будто не может определиться: весна или осень."
    elif temp <= 30:
        return "Отличная погода! Можно гулять, но не забудь солнцезащитные очки!"
    else:
        return "Жара такая, что яйца можно жарить прямо на асфальте. Осторожно, не перегрейся!"

# Функция для отправки прогноза погоды раз в час
def send_hourly_forecast():
    while True:
        for chat_id, (lat, lon) in monitoring_chats.items():
            temp = get_temperature(lat, lon)
            if temp is not None:
                forecast = generate_funny_forecast(temp)
                bot.send_message(chat_id=chat_id, text=f"Текущая температура: {temp}°C\n{forecast}")
        sleep(3600)  # Отправка прогноза каждые 60 минут

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
    update.message.reply_text(f"Локация получена! Теперь вы будете получать прогноз погоды каждый час.")

# Настройка обработчиков команд и сообщений
start_handler = CommandHandler('start', start)
temp_handler = CommandHandler('temp', temp)
location_handler = MessageHandler(Filters.location, location_handler)

updater.dispatcher.add_handler(start_handler)
updater.dispatcher.add_handler(temp_handler)
updater.dispatcher.add_handler(location_handler)

# Запуск фонового процесса для отправки прогнозов
threading.Thread(target=send_hourly_forecast, daemon=True).start()

# Запуск бота
updater.start_polling()
updater.idle()
