from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.repositories import UserRepository
from app.service import UserService


async def authorization(request, call_next):

    user = getattr(request.state, "user", None)
    
    if user and user.tier != 'enterprise' and request.url.path == '/shorten/batch':
        return JSONResponse(status_code=403, content={'detail': 'Bulk creation requires the enterprise tier'})

    return await call_next(request)
