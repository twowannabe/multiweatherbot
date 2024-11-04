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

TELEGRAM_TOKEN = config('TELEGRAM_TOKEN')
API_KEY = config('OPENWEATHERMAP_API_KEY')
OPENAI_API_KEY = config('OPENAI_API_KEY')
NASA_API_KEY = config('NASA_API_KEY')

openai.api_key = OPENAI_API_KEY
bot = Bot(token=TELEGRAM_TOKEN)
application = Application.builder().token(TELEGRAM_TOKEN).build()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

chat_location = {}
monitoring_chats = {}
previous_temperature = None
user_signs = {}

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
        return None
    except requests.RequestException as e:
        logger.error(f"Error fetching water temperature: {e}")
        return None

def check_water_temperature():
    global previous_temperature
    current_temperature = get_water_temperature()

    if current_temperature is not None:
        if previous_temperature is None:
            previous_temperature = current_temperature
        elif current_temperature < previous_temperature:
            message = f"Water temperature dropped! Current: {current_temperature}°C, Previous: {previous_temperature}°C."
            asyncio.run(send_notification_to_all_users(message))
        previous_temperature = current_temperature

async def send_notification_to_all_users(message):
    for chat_id in monitoring_chats.keys():
        try:
            await application.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            logger.error(f"Failed to send message to user {chat_id}: {e}")

def get_temperature(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru'
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200 and 'main' in data and 'temp' in data['main']:
        return data['main']['temp']
    logger.error(f"Error fetching temperature data: {response.status_code}")
    return None

def get_forecast(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=ru'
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200:
        return [f"{entry['dt_txt']}: {entry['main']['temp']}°C, {entry['weather'][0]['description']}" for entry in data['list'][:4]]
    logger.error(f"Error fetching forecast: {response.status_code}")
    return None

def generate_funny_forecast_with_openai(forecast):
    forecast_text = "\n".join(forecast)
    messages = [
        {"role": "system", "content": "You are a humorous weather forecaster. Write in Russian with humor and emojis."},
        {"role": "user", "content": f"Create a funny forecast for the next 12 hours: \n{forecast_text}."}
    ]

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=500,
            temperature=0.5
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"Error generating forecast via OpenAI: {e}")
        return "Failed to create forecast, but I'm sure the weather will be interesting! 😄"

def generate_horoscope_with_openai(sign):
    messages = [
        {"role": "system", "content": "You are an astrologer who writes daily horoscopes. Write in Russian with humor and emojis."},
        {"role": "user", "content": f"Write a horoscope for today for the zodiac sign {sign}."}
    ]

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=300,
            temperature=0.7
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"Error generating horoscope via OpenAI: {e}")
        return "Failed to create horoscope, but make today unforgettable! 😊"

async def send_morning_forecast():
    for chat_id, coords in monitoring_chats.items():
        if coords:
            lat, lon = coords
            temp = get_temperature(lat, lon)
            if temp is not None:
                forecast_data = [f"Current temperature: {temp}°C"]
                forecast_entries = get_forecast(lat, lon)
                if forecast_entries:
                    forecast_data.extend(forecast_entries)

                forecast = generate_funny_forecast_with_openai(forecast_data)
                forecast_message = f"Current air temperature: {temp}°C\n{forecast}"
                forecast_message = escape(forecast_message)
                try:
                    await application.bot.send_message(chat_id=chat_id, text=forecast_message, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Failed to send forecast to user {chat_id}: {e}")

async def send_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        forecast_data = get_forecast(lat, lon)
        if forecast_data is not None:
            forecast = generate_funny_forecast_with_openai(forecast_data)
            forecast_message = f"Current air temperature: {temp}°C\n{forecast}"
            forecast_message = escape(forecast_message)
            await update.message.reply_text(forecast_message, parse_mode="HTML")
        else:
            await update.message.reply_text("Failed to get forecast data.")
    else:
        await update.message.reply_text("Location not sent. Please send your location first.")

async def send_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in user_signs:
        sign = user_signs[chat_id]
        horoscope = generate_horoscope_with_openai(sign)
        horoscope_message = f"Your horoscope for today ({sign.title()}):\n{horoscope}"
        horoscope_message = escape(horoscope_message)
        await update.message.reply_text(horoscope_message, parse_mode="HTML")
    else:
        await update.message.reply_text("You haven't set your zodiac sign yet. Please use /sign <your_sign> to set it.")

async def set_sign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.args:
        sign_name = context.args[0].lower()
        zodiac_signs = ['овен', 'телец', 'близнецы', 'рак', 'лев', 'дева',
                        'весы', 'скорпион', 'стрелец', 'козерог', 'водолей', 'рыбы']

        if sign_name in zodiac_signs:
            user_signs[chat_id] = sign_name
            await update.message.reply_text(f"Your zodiac sign is set as {sign_name.title()}.")
        else:
            await update.message.reply_text("Invalid zodiac sign. Please enter one of the 12 zodiac signs.")
    else:
        await update.message.reply_text("Please specify your zodiac sign using /sign <your_sign>.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "Bot started! Please send your location to get weather forecasts."
    )
    if chat_id not in monitoring_chats:
        monitoring_chats[chat_id] = None

async def temp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        if temp is not None:
            await update.message.reply_text(f"Current air temperature: {temp}°C")
        else:
            await update.message.reply_text("Failed to get temperature data.")
    else:
        await update.message.reply_text("Location not sent. Please send your location first.")

async def water(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    temperature = get_water_temperature()
    if temperature is not None:
        await update.message.reply_text(f"Water temperature in Budva: {temperature}°C")
    else:
        await update.message.reply_text("Failed to get water temperature.")

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message.location:
        lat = update.message.location.latitude
        lon = update.message.location.longitude
        chat_location[chat_id] = (lat, lon)
        monitoring_chats[chat_id] = (lat, lon)
        await update.message.reply_text("Location received! You will now receive morning weather forecasts.")
    else:
        await update.message.reply_text("Failed to get your location. Please try again.")

application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('temp', temp))
application.add_handler(CommandHandler('forecast', send_forecast))
application.add_handler(CommandHandler('water', water))
application.add_handler(MessageHandler(filters.LOCATION, location_handler))
application.add_handler(CommandHandler('sign', set_sign))
application.add_handler(CommandHandler('horoscope', send_horoscope))

def schedule_morning_forecast(time_str):
    schedule.every().day.at(time_str).do(lambda: asyncio.run(send_morning_forecast()))

def schedule_water_check():
    schedule.every(60).minutes.do(check_water_temperature)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    schedule_morning_forecast("08:00")
    schedule_water_check()
    threading.Thread(target=run_scheduler, daemon=True).start()
    application.run_polling()
