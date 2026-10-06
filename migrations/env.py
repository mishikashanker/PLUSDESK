import os

from alembic import context
from dotenv import load_dotenv

from app.database import build_engine
from app.models import Base

load_dotenv()
url = os.environ.get("DATABASE_URL", "sqlite:///./data/pulsedesk.db")
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = build_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata,
                          render_as_batch=engine.dialect.name == "sqlite", compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
