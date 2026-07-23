import logging
from pathlib import Path
from fastapi.responses import JSONResponse
from logging.handlers import RotatingFileHandler
import json


# anchor to the project root regardless of the process's working directory
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
BLACKLIST_FILE = PROJECT_ROOT / 'blacklist.json'

logger = logging.getLogger('blacklist')
logger.setLevel(logging.ERROR)

handler = RotatingFileHandler(LOG_DIR / 'blacklist.log')
handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
logger.addHandler(handler)


async def blacklist(request, call_next):
    
    try:
        with open(BLACKLIST_FILE, 'r') as f:
            BLACKLIST = json.load(f)
    except Exception as e:
        logger.error(f"Error loading blacklist: {e}")
        BLACKLIST = {
            "blocked_keys": []
        }   
    
    apikey = request.headers.get('x-api-key')
    if apikey in BLACKLIST['blocked_keys']:
        return JSONResponse(status_code=403, content={'detail': 'This API key has been blocked.'})

    return await call_next(request)
