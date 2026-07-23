import logging
import time
from pathlib import Path
from logging.handlers import RotatingFileHandler

# anchor to the project root regardless of the process's working directory
LOG_DIR = Path(__file__).resolve().parents[2] / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

timing_logger = logging.getLogger('timing')
timing_logger.setLevel(logging.INFO)

handler = RotatingFileHandler(LOG_DIR / 'timing.log')
handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
timing_logger.addHandler(handler)

def timed(mw):
    async def wrapper(request, call_next):
        start = time.perf_counter()
        response = await mw(request, call_next)
        elapsed_ms = (time.perf_counter() - start) * 1000
        timing_logger.info("%s took %.2fms", mw.__name__, elapsed_ms)
        return response
    return wrapper
