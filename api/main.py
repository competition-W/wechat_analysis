from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
import sys

from config.settings import settings
from api.routes import router


def setup_logging():
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.LOG_LEVEL,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    logger.add(
        "logs/app_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="7 days",
        level=settings.LOG_LEVEL,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    )


app = FastAPI(
    title="企业微信客户群聊智能分析服务",
    description="基于LLM的企业微信群聊内容智能分析API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
async def startup_event():
    setup_logging()
    logger.info(f"服务启动: {settings.SERVICE_HOST}:{settings.SERVICE_PORT}")
    logger.info(f"LLM模型配置: sentiment={settings.LLM_MODEL_SENTIMENT}, summary={settings.LLM_MODEL_SUMMARY}")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("服务关闭")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=True,
    )
