#!/data/data/com.termux/files/usr/bin/bash
set -e
pkg update
pkg install -y python python-pillow tesseract
python -m venv --system-site-packages .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
printf '\nInstall complete. Set BOT_TOKEN, then run ./run_bot.sh\n'
