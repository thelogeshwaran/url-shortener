from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.repositories import UserRepository
from app.service import UserService


exclued_path = [
    "/health",
    "/shorten",
    "/redirect",
    "/lookup"
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

    request.state.user = user
    return await call_next(request)
