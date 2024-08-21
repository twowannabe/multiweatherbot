import requests
from decouple import config
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from time import sleep
import threading

# Загрузка ключей из .env файла
TELEGRAM_TOKEN = config('TELEGRAM_TOKEN')
API_KEY = config('OPENWEATHERMAP_API_KEY')

# Установите координаты для Будвы
LAT = '42.28035359615054'
LON = '18.875357276170174'

# Инициализация бота
bot = Bot(token=TELEGRAM_TOKEN)
updater = Updater(token=TELEGRAM_TOKEN, use_context=True)

# Глобальная переменная для хранения chat_id
chat_id = None
last_temp = None

# Функция для получения температуры воды
def get_sea_temperature():
    url = f'http://api.openweathermap.org/data/2.5/weather?lat={LAT}&lon={LON}&appid={API_KEY}&units=metric'
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200:
        if 'main' in data and 'temp' in data['main']:
            sea_temp = data['main']['temp']
            return sea_temp
        else:
            print("Не удалось найти данные о температуре воды.")
            return None
    else:
        print(f"Ошибка при получении данных: {response.status_code}")
        return None

# Функция для проверки температуры и отправки уведомления
def check_temperature():
    global last_temp
    current_temp = get_sea_temperature()
    if current_temp and chat_id:
        if last_temp is None:
            last_temp = current_temp
        if current_temp != last_temp:
            bot.send_message(chat_id=chat_id, text=f"Температура воды изменилась: {current_temp}°C")
            last_temp = current_temp

# Функция для обработки команды /start
def start(update: Update, context: CallbackContext):
    global chat_id
    chat_id = update.message.chat_id
    update.message.reply_text("Бот запущен! Вы будете получать уведомления при изменении температуры воды.")
    threading.Thread(target=temperature_monitor).start()

# Функция для мониторинга температуры в фоновом режиме
def temperature_monitor():
    while True:
        check_temperature()
        sleep(3600)  # Проверка каждые 60 минут

# Функция для обработки команды /temp
def temp(update: Update, context: CallbackContext):
    current_temp = get_sea_temperature()
    if current_temp:
        update.message.reply_text(f"Текущая температура воды: {current_temp}°C")
    else:
        update.message.reply_text("Не удалось получить текущую температуру воды.")

# Настройка обработчиков команд
start_handler = CommandHandler('start', start)
temp_handler = CommandHandler('temp', temp)
updater.dispatcher.add_handler(start_handler)
updater.dispatcher.add_handler(temp_handler)

# Запуск бота
updater.start_polling()
updater.idle()
