"""Main module for the URL shortener FastAPI application."""

from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app import cache
from app.socketio_server import sio
from app.thumbnails import THUMBNAIL_DIR
from app.middleware.logging import log_requests
from app.middleware.blacklist import blacklist
from app.middleware.rate_limit import rate_limit, rate_limit_api
from app.middleware.auth import check_api_key
from app.middleware.authorization import authorization
from app.middleware.timing import timing
from app.routers import router
from app.middleware.timed import timed
from app.middleware.rate_limit_tier import rate_limit_tier


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache.start_periodic_flush()
    yield
    await cache.stop_periodic_flush()


app = FastAPI(lifespan=lifespan)

# StaticFiles requires the directory to exist at mount time -- normally
# created lazily by generate_thumbnail_for_user(), so a fresh checkout
# with zero thumbnails generated yet would otherwise fail to start.
THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/uploads/thumbnails', StaticFiles(directory=THUMBNAIL_DIR), name='thumbnails')

app.middleware("http")(timed(authorization))

app.middleware("http")(timed(rate_limit_tier))

app.middleware("http")(timed(check_api_key))

app.middleware("http")(timed(blacklist))

app.middleware("http")(timed(rate_limit))

app.middleware("http")(timed(rate_limit_api))

app.middleware("http")(timed(log_requests))

app.middleware("http")(timed(timing))

app.include_router(router)

# Additive only -- `app` above is untouched and still the one every test
# and existing deployment uses. This wraps it with Socket.IO's own ASGI
# routing: requests to /socket.io/* are handled by `sio`, everything
# else falls through to `app` unchanged. Run with `uvicorn app.main:socket_app`
# instead of `app.main:app` to get Socket.IO support active.
socket_app = socketio.ASGIApp(sio, other_asgi_app=app)
