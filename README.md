# Weather & Horoscope Telegram Bot

This repository contains a Telegram bot that provides weather forecasts, water temperatures, solar flare notifications, and daily horoscopes. The bot leverages multiple APIs including OpenWeatherMap, NASA's DONKI, and OpenAI to provide informative and entertaining responses. Below are details on its capabilities, setup, and usage.

## Features

- **Weather Information**: Get current temperature, 12-hour forecast, and water temperature for specified locations.
- **Solar Activity Updates**: Notify users of upcoming solar flares using NASA's DONKI API, as well as provide on-demand solar flare information.
- **Horoscopes**: Generate daily horoscopes with a hint of humor using OpenAI's GPT model.
- **Location-Based Forecasts**: Track and provide weather information based on user location.
- **Humorous Weather Forecasts**: Generate funny weather forecasts using OpenAI's API.

## Requirements

To run the bot, you need the following:

- Python 3.8+
- A `.env` file with the following variables:
  - `TELEGRAM_TOKEN`: Your Telegram bot token
  - `OPENWEATHERMAP_API_KEY`: API key for OpenWeatherMap
  - `OPENAI_API_KEY`: API key for OpenAI
  - `NASA_API_KEY`: API key for NASA's DONKI

## Installation

1. Clone the repository:
   ```sh
   git clone <repository-url>
   cd <repository-directory>
   ```

2. Install required Python packages:
   ```sh
   pip install -r requirements.txt
   ```

3. Create a `.env` file in the root of the directory with the required API keys:
   ```sh
   TELEGRAM_TOKEN=your_telegram_token
   OPENWEATHERMAP_API_KEY=your_openweathermap_api_key
   OPENAI_API_KEY=your_openai_api_key
   NASA_API_KEY=your_nasa_api_key
   ```

## Running the Bot

To run the bot, use the following command:

```sh
python bot.py
```

The bot will start polling and automatically send notifications based on the scheduled tasks.

## Command List

- `/start` - Starts the bot and requests user location.
- `/temp` - Provides the current air temperature based on the user's location.
- `/forecast` - Gives a 12-hour weather forecast and generates a humorous version.
- `/water` - Provides the current water temperature in Budva.
- `/sign <your_sign>` - Sets your zodiac sign for daily horoscopes.
- `/horoscope` - Sends your daily horoscope.
- `/solarflare` - Provides information about expected solar flares in the next 12 hours.

## Scheduling

The bot uses the `schedule` library to perform regular tasks:

- **Daily Morning Weather Forecast**: Sent at 08:00 (configurable).
- **Water Temperature Monitoring**: Every 60 minutes.
- **Solar Flare Notification**: Every 12 hours.

## How It Works

- The bot utilizes OpenWeatherMap to retrieve current weather and forecasts.
- Water temperature data is scraped from a website using BeautifulSoup.
- Daily horoscopes and humorous forecasts are generated with OpenAI's GPT model.
- Users' locations are used to provide tailored weather updates.
- Solar flare activity is checked using NASA's DONKI API, and notifications are sent when activity is detected.

## Contributing

Contributions are not allowed without explicit permission. Please contact the repository owner if you have suggestions or improvements.

## License

This project is licensed for personal use only. You are free to use and modify the code for personal purposes, but redistribution, commercial use, and publication are prohibited. The original author (Volodymyr Kozlov) must be credited in any modifications. See the `LICENSE` file for more details.
