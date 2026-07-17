#!/bin/bash
set -e

echo "Setting up PO-Bot Gateway..."

# Copy udev rules
if [ -f "99-po-bot-m5.rules" ]; then
    echo "Installing udev rules..."
    sudo cp 99-po-bot-m5.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    echo "udev rules installed and triggered."
else
    echo "Warning: 99-po-bot-m5.rules not found in the current directory."
fi

echo "Setup complete."
