"""T 系统启动入口：python run.py（或 uvicorn app.main:create_app --factory）"""
import uvicorn

from app.config import settings
from app.main import create_app, init_db

app = create_app()

if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host=settings.host, port=settings.port)
