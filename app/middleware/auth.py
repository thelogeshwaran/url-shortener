import logging
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.repositories import UserRepository
from app.service import UserService
from logging.handlers import RotatingFileHandler


# anchor to the project root regardless of the process's working directory
LOG_DIR = Path(__file__).resolve().parents[2] / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger('requests')
logger.setLevel(logging.INFO)

handler = RotatingFileHandler(LOG_DIR / 'request.log')
handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
logger.addHandler(handler)

exclued_path = [
    "/health",
    "/shorten",
    "/redirect"
]


async def check_api_key(request, call_next):
    if request.url.path in exclued_path:
        return await call_next(request)

    apikey = request.headers.get('x-api-key')
    try:
        user = UserService(UserRepository()).get_user_by_api_key(apikey)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={'detail': e.detail})

    if user is None:  # key missing entirely
        return JSONResponse(status_code=401, content={'detail': 'Unauthorized'})

    if request.url.path == '/shorten/batch' and user.tier != 'enterprise':
        return JSONResponse(status_code=403, content={'detail': 'Bulk creation requires the enterprise tier'})

    request.state.user = user
    return await call_next(request)
