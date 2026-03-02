from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings


@lru_cache(maxsize=1)
def get_mysql_engine():
    return create_engine(settings.SOA_MYSQL_URL, pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def get_timeseries_engine():
    return create_engine(settings.SOA_TIMESERIES_URL, pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def _mysql_sessionmaker():
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=get_mysql_engine(),
    )


@lru_cache(maxsize=1)
def _timeseries_sessionmaker():
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=get_timeseries_engine(),
    )


def get_mysql_db():
    db = _mysql_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def get_timeseries_db():
    db = _timeseries_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
