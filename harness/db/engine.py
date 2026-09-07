from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

#: The executor's loop must fail fast: a query that outlives the loop period is a stall, not a
#: slow query, and the next loop is more useful than this one finishing.
EXEC_STATEMENT_TIMEOUT_MS = 10_000
#: The settlement, benchmark and markout batches legitimately run for minutes.
BATCH_STATEMENT_TIMEOUT_MS = 900_000


def make_engine(url: str, statement_timeout_ms: int = 30000) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True,
                         connect_args={"connect_timeout": 5,
                                       "options": f"-c statement_timeout={statement_timeout_ms}"})


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
