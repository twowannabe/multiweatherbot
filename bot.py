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

# Configuration for API and bot
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

# Global variables to store user data
chat_location = {}
monitoring_chats = {}
previous_temperature = None

# Function to connect to the database
def get_db_connection():
    logger.info("Connecting to the database")
    return psycopg2.connect(
        host=config('DB_HOST'),
        port=config('DB_PORT'),
        dbname=config('DB_NAME'),
        user=config('DB_USER'),
        password=config('DB_PASSWORD')
    )

# Save user location to the database
def save_location_to_db(chat_id, lat, lon):
    logger.info(f"Saving user {chat_id} location to the database: {lat}, {lon}")
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

# Load all user locations from the database at startup
def load_all_locations():
    logger.info("Loading all user locations from the database")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT chat_id, latitude, longitude FROM user_locations")
    locations = cursor.fetchall()
    cursor.close()
    conn.close()
    return {row['chat_id']: (row['latitude'], row['longitude']) for row in locations}

# Function to get water temperature
def get_water_temperature():
    url = 'https://world-weather.ru/pogoda/montenegro/budva/water/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
    }
    logger.info(f"Requesting water temperature from URL: {url}")

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
                logger.info(f"Water temperature successfully retrieved: {temperature}°C")
                return temperature
        logger.warning("Water temperature not found on the page")
        return None
    except requests.RequestException as e:
        logger.error(f"Error fetching water temperature: {e}")
        return None

# Function to get air temperature by coordinates
def get_temperature(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=en'
    logger.info(f"Requesting air temperature from URL: {url}")
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200 and 'main' in data and 'temp' in data['main']:
        temperature = data['main']['temp']
        logger.info(f"Air temperature successfully retrieved: {temperature}°C")
        return temperature
    logger.error(f"Error fetching temperature data: {response.status_code}")
    return None

# Function to get weather forecast
def get_forecast(lat, lon):
    url = f'http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=en'
    logger.info(f"Requesting weather forecast from URL: {url}")
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200:
        forecast_data = [f"{entry['dt_txt']}: {entry['main']['temp']}°C, {entry['weather'][0]['description']}" for entry in data['list'][:4]]
        logger.info(f"Weather forecast successfully retrieved: {forecast_data}")
        return forecast_data
    logger.error(f"Error fetching forecast: {response.status_code}")
    return None

# Check for changes in water temperature
def check_water_temperature():
    global previous_temperature
    logger.info("Checking for changes in water temperature")
    current_temperature = get_water_temperature()

    if current_temperature is not None:
        if previous_temperature is None:
            previous_temperature = current_temperature
        elif current_temperature < previous_temperature:
            message = f"Water temperature has dropped! Current: {current_temperature}°C, previous: {previous_temperature}°C."
            logger.info("Sending notification about water temperature drop")
            asyncio.run(send_notification_to_all_users(message))
        previous_temperature = current_temperature

# Send notifications to all users
async def send_notification_to_all_users(message):
    logger.info("Sending notifications to all users")
    for chat_id in monitoring_chats.keys():
        try:
            await application.bot.send_message(chat_id=chat_id, text=message)
            logger.info(f"Message successfully sent to user {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send message to user {chat_id}: {e}")

# Handler for /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Received /start command from user {chat_id}")
    await update.message.reply_text(
        "Bot started! Please send your location to receive weather forecasts."
    )
    if chat_id not in monitoring_chats:
        monitoring_chats[chat_id] = None

# Handler for /temp command
async def temp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Received /temp command from user {chat_id}")
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        if temp is not None:
            await update.message.reply_text(f"Current air temperature: {temp}°C")
        else:
            await update.message.reply_text("Failed to retrieve temperature data.")
    else:
        await update.message.reply_text("Location not sent. Please send your location first.")

# Handler for /water command
async def water(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Received /water command from user {chat_id}")
    temperature = get_water_temperature()
    if temperature is not None:
        await update.message.reply_text(f"Water temperature in Budva: {temperature}°C")
    else:
        await update.message.reply_text("Failed to retrieve water temperature data.")

# Handler for /forecast command
async def send_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Received /forecast command from user {chat_id}")
    if chat_id in chat_location:
        lat, lon = chat_location[chat_id]
        temp = get_temperature(lat, lon)
        forecast_data = get_forecast(lat, lon)
        if forecast_data is not None:
            forecast = "\n".join(forecast_data)
            forecast_message = f"Current air temperature: {temp}°C\n{forecast}"
            forecast_message = escape(forecast_message)
            await update.message.reply_text(forecast_message, parse_mode="HTML")
        else:
            await update.message.reply_text("Failed to retrieve forecast data.")
    else:
        await update.message.reply_text("Location not sent. Please send your location first.")

# Function to get geomagnetic forecast from SWPC
def get_geomagnetic_forecast():
    url = "https://services.swpc.noaa.gov/text/3-day-solar-geomag-predictions.txt"
    logger.info(f"Fetching geomagnetic forecast from URL: {url}")

    try:
        response = requests.get(url)
        response.raise_for_status()
        text_data = response.text
        logger.info("Geomagnetic forecast data successfully retrieved")

        # Parse the text data
        forecast = parse_swpc_geomagnetic_forecast(text_data)

        return forecast
    except requests.RequestException as e:
        logger.error(f"Error fetching geomagnetic forecast: {e}")
        return "Error fetching geomagnetic forecast."
    except Exception as e:
        logger.error(f"Error parsing geomagnetic forecast: {e}")
        return "Error parsing geomagnetic forecast."

# Function to parse the SWPC geomagnetic forecast text
def parse_swpc_geomagnetic_forecast(text_data):
    lines = text_data.splitlines()
    forecast_data = []
    capture = False

    for line in lines:
        if line.strip() == '':
            continue  # Skip empty lines
        if "Geomagnetic Activity Forecast" in line:
            capture = True
            forecast_data.append(line.strip())
            continue
        if capture:
            forecast_data.append(line.strip())

    if forecast_data:
        forecast_text = '\n'.join(forecast_data)
        logger.info(f"Geomagnetic forecast:\n{forecast_text}")
        return forecast_text
    else:
        logger.warning("Forecast data not found in SWPC response")
        return "No geomagnetic forecast data found."

# Handler for /solarflare command
async def send_geomagnetic_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Processing /solarflare command")
    forecast = get_geomagnetic_forecast()
    await update.message.reply_text(forecast)

# Send geomagnetic forecast to all users
async def send_geomagnetic_forecast_to_all_users():
    logger.info("Sending geomagnetic forecast to all users")
    forecast = get_geomagnetic_forecast()
    for chat_id in monitoring_chats.keys():
        try:
            await application.bot.send_message(chat_id=chat_id, text=forecast)
            logger.info(f"Forecast successfully sent to user {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send forecast to user {chat_id}: {e}")

# Handler for user location
async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Received location from user {chat_id}")
    if update.message.location:
        lat = update.message.location.latitude
        lon = update.message.location.longitude
        chat_location[chat_id] = (lat, lon)
        monitoring_chats[chat_id] = (lat, lon)
        save_location_to_db(chat_id, lat, lon)
        await update.message.reply_text("Location accepted! You will now receive morning forecasts.")
    else:
        await update.message.reply_text("Failed to receive location. Please try again.")

# Load all saved user data
monitoring_chats = load_all_locations()
chat_location = monitoring_chats.copy()

# Log information about all users in monitoring_chats
logger.info("List of users in monitoring_chats at startup:")
for chat_id, location in monitoring_chats.items():
    logger.info(f"User {chat_id} with location: {location}")

# Register all handlers
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('temp', temp))
application.add_handler(CommandHandler('water', water))
application.add_handler(CommandHandler('forecast', send_forecast))
application.add_handler(CommandHandler('solarflare', send_geomagnetic_forecast))
application.add_handler(MessageHandler(filters.LOCATION, location_handler))

# Schedule morning forecast (define send_forecast_to_all_users if needed)
def schedule_morning_forecast(time_str):
    schedule.every().day.at(time_str).do(lambda: asyncio.run(send_forecast_to_all_users()))

# Schedule water temperature check
def schedule_water_check():
    schedule.every(60).minutes.do(check_water_temperature)

# Schedule geomagnetic forecast
def schedule_geomagnetic_forecast():
    # Send the forecast every day at 09:00
    schedule.every().day.at("09:00").do(lambda: asyncio.run(send_geomagnetic_forecast_to_all_users()))

# Run the scheduler
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

# Main block of the program
if __name__ == '__main__':
    logger.info("Starting bot and scheduler")
    # schedule_morning_forecast("08:00")  # Uncomment if you have the send_forecast_to_all_users function
    schedule_water_check()
    schedule_geomagnetic_forecast()
    threading.Thread(target=run_scheduler, daemon=True).start()
    application.run_polling()
