"""Main module for the URL shortener FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from app import cache
from app.middleware.logging import log_requests
from app.middleware.blacklist import blacklist
from app.middleware.rate_limit import rate_limit, rate_limit_api
from app.middleware.auth import check_api_key
from app.middleware.authorization import authorization
from app.middleware.timing import timing
from app.routers import router
from app.middleware.timed import timed


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache.start_periodic_flush()
    yield
    await cache.stop_periodic_flush()


app = FastAPI(lifespan=lifespan)

app.middleware("http")(timed(authorization))

app.middleware("http")(timed(check_api_key))

app.middleware("http")(timed(blacklist))

app.middleware("http")(timed(rate_limit))

app.middleware("http")(timed(rate_limit_api))

app.middleware("http")(timed(log_requests))

app.middleware("http")(timed(timing))

app.include_router(router)
