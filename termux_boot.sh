#!/bin/bash
# Termux:Boot auto-start script
# Place this at: ~/.termux/boot/start-opencode.sh
# chmod +x ~/.termux/boot/start-opencode.sh

# Wait for network
sleep 30

# Start bot
cd ~/Opencode
bash start.sh &
