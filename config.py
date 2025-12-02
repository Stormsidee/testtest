import os
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Конфигурация Home Assistant
HA_TOKEN = os.getenv("HA_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI1NDg3MGRlYmNjMjE0NDdiYmFkMzBjZTJmNDE3NWY3ZiIsImlhdCI6MTc2NDM5OTg1MCwiZXhwIjoyMDc5NzU5ODUwfQ.yPs9CJV5-vnJMcy-ogoiWVrGjTZaY0VxDFSMLUnpFZU")
HA_URL = os.getenv("HA_URL", "http://37.208.73.212:8123")

# Конфигурация Telegram Bot
BOT_TOKEN = os.getenv("BOT_TOKEN", "8536434594:AAG3n0tCFBlg5NNEAgviT5IfutQNgolH5SU")
BOT_USERNAME = os.getenv("BOT_USERNAME", "your_bot_username")

# Конфигурация Mini App
MINI_APP_PORT = int(os.getenv("MINI_APP_PORT", 9123))
MINI_APP_URL = os.getenv("MINI_APP_URL", f"http://your-server.com:{MINI_APP_PORT}")
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-for-auth")

# Дополнительные настройки
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# CORS настройки
ALLOWED_ORIGINS = [
    "https://your-server.com",
    "http://localhost:8080",
    "https://web.telegram.org",
    "https://telegram.org",
    "https://*.telegram.org",
]
