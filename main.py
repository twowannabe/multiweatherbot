import requests
from decouple import config
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from bs4 import BeautifulSoup
from time import sleep
import threading

# Load environment variables
TELEGRAM_TOKEN = config('TELEGRAM_TOKEN')

# Initialize the bot
bot = Bot(token=TELEGRAM_TOKEN)
updater = Updater(token=TELEGRAM_TOKEN, use_context=True)

# Global variable to store the chat ID
chat_id = None
last_temp = None

# Function to get the current water temperature
def get_water_temperature():
    url = 'https://seatemperature.info/budva-water-temperature.html'
    response = requests.get(url)
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        try:
            # Поиск всех <div> с классом 'x5'
            div_elements = soup.find_all('div', class_='x5')
            print(f"Найдено {len(div_elements)} <div> элементов с классом 'x5'")

            for div in div_elements:
                strong_element = div.find('strong')
                if strong_element and 'Water temperature in Budva today is' in strong_element.text:
                    temp_text = strong_element.text
                    temperature = float(temp_text.split()[-1].replace('°C', '').strip())
                    return temperature

            print("Ошибка: Не удалось найти элемент <strong> с температурой.")
            return None
        except Exception as e:
            print("Ошибка: Проблема с нахождением элемента по пути.", e)
            return None
    else:
        print(f"Ошибка: Не удалось получить данные (статус код {response.status_code})")
        return None

# Function to check temperature and send a message
def check_temperature():
    global last_temp
    current_temp = get_water_temperature()
    if current_temp and chat_id:
        if last_temp is None:
            last_temp = current_temp
        if current_temp != last_temp:
            bot.send_message(chat_id=chat_id, text=f"Water temperature has changed to {current_temp}°C")
            last_temp = current_temp

# Function to start the bot and capture the chat ID
def start(update: Update, context: CallbackContext):
    global chat_id
    chat_id = update.message.chat_id
    update.message.reply_text("Bot started! You will receive updates when the water temperature changes.")
    # Start a background thread to check the temperature
    threading.Thread(target=temperature_monitor).start()

# Function to monitor temperature in the background
def temperature_monitor():
    while True:
        check_temperature()
        sleep(3600)  # Check every hour

# Set up the command handler
start_handler = CommandHandler('start', start)
updater.dispatcher.add_handler(start_handler)

# Start polling for updates from Telegram
updater.start_polling()

# Keep the script running
updater.idle()
