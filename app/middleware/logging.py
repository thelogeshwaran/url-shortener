import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler

# anchor to the project root regardless of the process's working directory
LOG_DIR = Path(__file__).resolve().parents[2] / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger('requests')
logger.setLevel(logging.INFO)

handler = RotatingFileHandler(LOG_DIR / 'request.log')
handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
logger.addHandler(handler)


async def log_requests(request, call_next):
    response = await call_next(request)

    ip = request.client.host if request.client else '-'
    user_agent = request.headers.get('user-agent', '-')
    logger.info('%s %s | UA: %s | IP: %s', request.method, request.url, user_agent, ip)

    return response
