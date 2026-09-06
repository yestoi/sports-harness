from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True,
                         connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"})


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
