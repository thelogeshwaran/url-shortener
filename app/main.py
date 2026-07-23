"""Main module for the URL shortener FastAPI application."""

from fastapi import FastAPI
from app.middleware.logging import log_requests
from app.middleware.blacklist import blacklist
from app.middleware.auth import check_api_key
from app.middleware.authorization import authorization
from app.middleware.timing import timing
from app.routers import router
from app.middleware.timed import timed


app = FastAPI()

app.middleware("http")(timed(authorization))

app.middleware("http")(timed(check_api_key))

app.middleware("http")(timed(blacklist))

app.middleware("http")(timed(log_requests))

app.middleware("http")(timed(timing))

app.include_router(router)
