import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

from tasks import read_serial_loop, mackerel_exporter_loop, monitor_online_status, send_loop
from routes import router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    task1 = asyncio.create_task(read_serial_loop())
    task2 = asyncio.create_task(mackerel_exporter_loop())
    task3 = asyncio.create_task(monitor_online_status())
    task4 = asyncio.create_task(send_loop())
    yield
    # Shutdown
    task1.cancel()
    task2.cancel()
    task3.cancel()
    task4.cancel()

app = FastAPI(title="PO-Bot Gateway API", lifespan=lifespan)

app.include_router(router)

