"""Cross-platform local launcher: python run.py"""
import argparse
import os
from pathlib import Path
from alembic import command
from alembic.config import Config
import uvicorn

if __name__ == '__main__':
    os.chdir(Path(__file__).resolve().parent)
    Path('data').mkdir(exist_ok=True)
    parser = argparse.ArgumentParser(description='Run PulseDesk')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8000)
    args = parser.parse_args()
    command.upgrade(Config('alembic.ini'),'head')
    uvicorn.run('app.main:create_app',factory=True,host=args.host,port=args.port,access_log=False)
