#!/bin/bash
# Start Seren-LS (Less Strict Bot)
# Make sure to set TELEGRAM_BOT_TOKEN in config.py first

mkdir -p data_ls/signal_tracker data_ls/daily data_ls/lifetime logs_ls

echo "Starting Seren-LS..."
nohup python main.py >> logs_ls/seren_ls.log 2>&1 &
echo "Seren-LS started. PID: $!"
echo "Logs: tail -f logs_ls/seren_ls.log"
