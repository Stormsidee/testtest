#!/bin/bash

# Активируем виртуальное окружение (если есть)
if [ -d "venv" ]; then
    source .venv/Scripts/activate
fi

# Устанавливаем зависимости
pip install -r requirements.txt

echo "Запуск Telegram Bot"
nohup python3 Homeassistant1.4.py &

# Запускаем API сервер и Telegram бота в фоне
echo "Запуск Home Assistant Mini App..."
nohup python3 api_server.py &
API_PID=$!

echo "API сервер запущен с PID: $API_PID"
echo "Мини-приложение доступно по адресу: http://localhost:8080"
echo ""
echo "Для остановки нажмите Ctrl+C"
echo ""

# Ждем завершения
wait $API_PID
