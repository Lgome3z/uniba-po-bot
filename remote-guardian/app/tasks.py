import asyncio
import json
import time
import requests
import serial

from config import USB_PORT, BAUD_RATE, MACKEREL_API_KEY, MACKEREL_URL, MACKEREL_HOST_ID
from state import gateway_state

async def read_serial_loop():
    """Background task reading from USB Serial with auto-reconnect."""
    ser = None
    while True:
        try:
            if not ser or not ser.is_open:
                print(f"Attempting to connect to {USB_PORT}...")
                # In a real asyncio app, you might use aiofiles or run serial in an executor,
                # but for simplicity and low throughput, we can use a small timeout and yield.
                ser = serial.Serial(USB_PORT, BAUD_RATE, timeout=0.1)
                print(f"Connected to {USB_PORT}")

            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8').strip()
                if line:
                    try:
                        data = json.loads(line)
                        if data.get("type") == "sensor.reading":
                            gateway_state["sensor_data"] = data
                            gateway_state["last_seen_at"] = int(time.time())
                            gateway_state["online"] = True
                            print(f"Read sensor data: {data}")
                    except json.JSONDecodeError:
                        print(f"Malformed JSON from serial: {line}")
                        
        except serial.SerialException as e:
            print(f"Serial Error: {e}. Retrying in 2 seconds...")
            if ser:
                ser.close()
            gateway_state["online"] = False
            await asyncio.sleep(2)
            
        await asyncio.sleep(0.01) # Yield control

async def mackerel_exporter_loop():
    """Background task posting metrics to Mackerel."""
    while True:
        await asyncio.sleep(60) # Post metrics every 60 seconds
        
        if not MACKEREL_API_KEY or MACKEREL_API_KEY == "your_mackerel_api_key_here":
            print("Mackerel exporter: API key not set or is placeholder, skipping.")
            continue
            
        # Mock input for testing without M5
        gateway_state["online"] = True
        gateway_state["sensor_data"] = {
            "temperature_c": 24.5,
            "humidity_percent": 50.0,
            "co2_ppm": 800.0
        }
        gateway_state["last_seen_at"] = int(time.time())

        # Original check commented out:
        # if not gateway_state["online"] or not gateway_state["sensor_data"]:
        #     print("Mackerel exporter: Gateway offline or no data, skipping.")
        #     continue
            
        # Prepare metrics according to step 2
        now = int(time.time())
        data = gateway_state["sensor_data"]
        metrics = []
        
        if "temperature_c" in data:
            metrics.append({"name": "custom.physical.temperature", "time": now, "value": data["temperature_c"]})
        if "humidity_percent" in data:
            metrics.append({"name": "custom.physical.humidity", "time": now, "value": data["humidity_percent"]})
        if "co2_ppm" in data:
            metrics.append({"name": "custom.physical.co2_ppm", "time": now, "value": data["co2_ppm"]})
            
        metrics.append({"name": "custom.physical.last_seen", "time": now, "value": gateway_state["last_seen_at"]})
        
        if not metrics:
            continue
            
        try:
            headers = {
                "X-Api-Key": MACKEREL_API_KEY,
                "Content-Type": "application/json"
            }
            # Using requests in thread/executor if we care about blocking, but standard requests is fine for basic impl
            response = requests.post(MACKEREL_URL, headers=headers, json=metrics, timeout=5)
            if response.status_code == 200:
                print(f"Successfully posted {len(metrics)} metrics to Mackerel.")
            else:
                print(f"Failed to post to Mackerel: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"Error posting to Mackerel: {e}")

async def monitor_online_status():
    """Background task to mark the gateway offline if no data is seen for 15s."""
    while True:
        if gateway_state["online"]:
            if int(time.time()) - gateway_state["last_seen_at"] > 15:
                print("No data received for 15+ seconds. Marking as offline.")
                gateway_state["online"] = False
        await asyncio.sleep(5)
