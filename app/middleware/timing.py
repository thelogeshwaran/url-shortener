import time


async def timing(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    execution_time = (time.perf_counter() - start) * 1000
    response.headers['X-Response-Time'] = str(round(execution_time, 3))
    return response
