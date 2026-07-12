"""Main module for the URL shortener FastAPI application."""

from fastapi import FastAPI
from app.database import init_db
from app.routers import router


app = FastAPI()

init_db()

app.include_router(router)