"""Main module for the URL shortener FastAPI application."""

from fastapi import FastAPI
from app.middleware.logging import log_requests
from app.middleware.auth import check_api_key
from app.routers import router


app = FastAPI()

app.middleware("http")(log_requests)

app.middleware("http")(check_api_key)



app.include_router(router)
