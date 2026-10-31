"""Engine, async session factory, create_all (no migrations)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from chanakya.truth.models import Base

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str) -> AsyncEngine:
    global _engine, _factory
    kw = {"pool_pre_ping": True} if url.startswith("postgresql") else {}
    _engine = create_async_engine(url, **kw)
    _factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def engine() -> AsyncEngine:
    assert _engine is not None, "init_engine() first"
    return _engine


def session() -> AsyncSession:
    assert _factory is not None, "init_engine() first"
    return _factory()


async def create_all(drop: bool = False) -> None:
    async with engine().begin() as conn:
        if drop:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def dispose() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine, _factory = None, None
