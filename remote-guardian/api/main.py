import asyncio
import random
from contextlib import asynccontextmanager
from fastapi import FastAPI

# Global dictionary to hold the most recent mock data
# In the real version, this will be populated by the USB serial reader
latest_data = {
    "camera_frame": None,
    "mic_level_db": 0
}

# A real, tiny 1x1 pixel black JPEG encoded in Base64
# This simulates the exact string format the M5Stack will send
MOCK_JPEG_B64 = "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="

async def simulate_m5stack_serial():
    """Background loop simulating incoming USB data."""
    while True:
        # Simulate ambient room noise fluctuating between 0dB (quiet) and 120dB (loud)
        latest_data["mic_level_db"] = random.randint(0, 120)
        
        # Simulate a fresh camera frame arriving from the M5Stack
        latest_data["camera_frame"] = MOCK_JPEG_B64
        
        # Wait 0.1 seconds (Simulating a smooth 10 frames-per-second stream)
        await asyncio.sleep(0.1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # This runs exactly once when the API boots up
    print("Starting M5Stack Hardware Simulator...")
    task = asyncio.create_task(simulate_m5stack_serial())
    yield
    # This cleans up the task when the API shuts down
    task.cancel()

# Initialize the API with our lifespan manager
app = FastAPI(title="Remote Guardian Edge API", lifespan=lifespan)

@app.get("/")
def read_root():
    return {"status": "online", "message": "Remote Guardian Gateway is active."}

@app.get("/sensor-stream")
def get_sensors():
    """Endpoint for the web dashboard to fetch the live video and audio data."""
    return latest_data
