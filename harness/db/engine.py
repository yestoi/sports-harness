from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
