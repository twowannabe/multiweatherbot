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
            # Шаг 1: Найти элемент <center>
            center_element = soup.find('center')
            if center_element:
                print("Нашли элемент <center>")

                # Шаг 2: Найти первый <div> внутри <center>
                div1 = center_element.find_all('div')
                if len(div1) > 0:
                    print(f"Нашли {len(div1)} <div> внутри <center>")

                    # Шаг 3: Найти пятый <div> внутри первого <div>
                    div5 = div1[0].find_all('div')
                    if len(div5) > 4:
                        print(f"Нашли {len(div5)} <div> внутри первого <div>")

                        # Шаг 4: Найти первый <div> внутри пятого <div>
                        div1_inner = div5[4].find_all('div')
                        if len(div1_inner) > 0:
                            print(f"Нашли {len(div1_inner)} <div> внутри пятого <div>")

                            # Шаг 5: Найти второй <p> внутри этого <div>
                            p2 = div1_inner[0].find_all('p')
                            if len(p2) > 1:
                                print(f"Нашли {len(p2)} <p> внутри первого внутреннего <div>")

                                # Шаг 6: Найти <strong> внутри второго <p>
                                strong_element = p2[1].find('strong')
                                if strong_element:
                                    print("Нашли элемент <strong> с температурой")

                                    temp_text = strong_element.text
                                    temperature = float(temp_text.split()[-1].replace('°C', '').strip())
                                    return temperature
                                else:
                                    print("Ошибка: Не удалось найти элемент <strong> с температурой.")
                                    return None
                            else:
                                print("Ошибка: Не удалось найти второй <p> внутри первого внутреннего <div>.")
                                return None
                        else:
                            print("Ошибка: Не удалось найти первый внутренний <div> внутри пятого <div>.")
                            return None
                    else:
                        print("Ошибка: Не удалось найти пятый <div> внутри первого <div>.")
                        return None
                else:
                    print("Ошибка: Не удалось найти первый <div> внутри <center>.")
                    return None
            else:
                print("Ошибка: Не удалось найти элемент <center>.")
                return None
        except (AttributeError, IndexError) as e:
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
