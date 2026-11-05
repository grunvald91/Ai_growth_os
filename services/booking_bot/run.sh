#!/bin/bash

echo "Starting Telegram Booking Bot..."
echo ""

# Activate virtual environment
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    echo "Virtual environment not found!"
    echo "Please create it first: python3 -m venv venv"
    exit 1
fi

# Run the bot
python -m bot.main
