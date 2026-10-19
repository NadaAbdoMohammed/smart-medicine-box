# Smart Medicine Box v3.1

Smart medicine reminder project combining an ESP-based device with a Python/Flask service, Telegram bot, database, and scheduled reminders.

## Before running

1. In `config.py`, replace `PASTE_YOUR_TELEGRAM_BOT_TOKEN_HERE` with your own Telegram bot token.
2. In `config.py`, set `API_SECRET_KEY` to a long random secret.
3. In `esp8266/MedicineBox_ESP32/MedicineBox_ESP32.ino`, set `WIFI_SSID` and `WIFI_PASSWORD` to your own Wi-Fi credentials.
4. Set `API_KEY` in the sketch to exactly the same value as `API_SECRET_KEY` in `config.py`.
5. `SERVER_HOST` has been kept at its original value from the supplied project. If that address is not the IP address of the computer running the server on your own network, change it to your computer's LAN IPv4 address.
6. Follow the project instructions and install the dependencies required by the original code.

## Project files

- `main.py`: application entry point
- `config.py`: configuration
- `bot.py` and `telegram_handlers.py`: Telegram bot and handlers
- `database.py`: database operations
- `scheduler.py`: scheduled reminders
- `esp_api.py`: API endpoints
- `esp8266/MedicineBox_ESP32/MedicineBox_ESP32.ino`: embedded firmware

## Security

Do not commit real bot tokens, Wi-Fi passwords, or API secrets. This public copy replaces the original credential values with placeholders; project logic has not intentionally been changed.
