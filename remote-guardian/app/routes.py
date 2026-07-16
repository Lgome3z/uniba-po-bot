from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from state import gateway_state

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/api/status")
def get_status():
    return gateway_state

@router.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "online": gateway_state["online"],
            "last_seen_at": gateway_state["last_seen_at"],
            "sensor_data": gateway_state["sensor_data"]
        }
    )
