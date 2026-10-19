# Smart Medicine Box v3.1

An IoT-based smart medicine reminder system that combines an ESP-based device with a Python backend, Flask API, Telegram bot, database, and scheduled reminders.

## Project Overview

The system is designed to help users manage medication schedules and receive reminders through a Telegram bot. The embedded device communicates with the backend service to exchange status information and support reminder-related functions.

## Main Features

- Medication reminder scheduling
- Telegram bot interaction
- Flask API for communication with the embedded device
- Database operations for storing and managing information
- Configurable device and network settings
- Security key configuration for API communication

## Technologies Used

- ESP-based embedded hardware
- Arduino IDE
- Python
- Flask
- Telegram Bot API
- SQLite or the database configured by the project

## Project Structure

```text
smart-medicine-box/
├── bot.py
├── config.py
├── database.py
├── esp_api.py
├── main.py
├── scheduler.py
├── telegram_handlers.py
├── MedicineBox_ESP32.ino
├── README.md
├── PROJECT_GUIDE_AR.md
├── SECURITY_NOTES.md
└── .gitignore
```

The files listed above represent the main project components. Additional files may be included as the project develops.

## Hardware Firmware

The Arduino sketch is available in the repository root:

[MedicineBox_ESP32.ino](MedicineBox_ESP32.ino)

Open the sketch in Arduino IDE and select the appropriate board and port before uploading it.

## Python Application

Main backend files:

- `main.py` — application entry point
- `config.py` — application configuration
- `bot.py` — Telegram bot functionality
- `telegram_handlers.py` — Telegram interaction handlers
- `database.py` — database operations
- `scheduler.py` — scheduled reminders
- `esp_api.py` — API endpoints for device communication

## Configuration Before Running

1. Configure your own Telegram bot token in `config.py`.
2. Set a strong, unique API secret in `config.py`.
3. Configure your Wi-Fi SSID and password in `MedicineBox_ESP32.ino`.
4. Set the firmware API key to match the backend API secret.
5. Configure `SERVER_HOST` with the LAN IP address of the computer running the Flask service.
6. Install the Python dependencies required by the project.
7. Start the backend service before testing communication with the device.

Use your own configuration values. Never publish real credentials in a public repository.

## Security

- Do not commit real Telegram bot tokens, Wi-Fi passwords, or API secrets.
- Keep local configuration and generated database files out of public commits when they contain private information.
- Use a unique API secret and replace any credentials that may have been exposed.

## Project Status

This repository documents the Smart Medicine Box v3.1 project. Hardware and software should be tested together in the intended environment before relying on the system for medication reminders.

## Safety Notice

This project is an educational and assistive prototype. It does not replace medical advice, prescribed medication instructions, or professional care.

## Author

**NadaAbdoMohammed**

