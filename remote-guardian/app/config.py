import os

# Configuration
USB_PORT = os.environ.get("SERIAL_PORT", "/dev/po-bot-m5")
BAUD_RATE = 115200
MACKEREL_API_KEY = os.environ.get("MACKEREL_API_KEY")
MACKEREL_HOST_ID = os.environ.get("MACKEREL_HOST_ID")
MACKEREL_URL = "https://api.mackerelio.com/api/v0/services/guardian/tsdb"
