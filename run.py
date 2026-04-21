#!/usr/bin/env python
from api.main import app
import uvicorn
from config.settings import settings

if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=True,
    )
