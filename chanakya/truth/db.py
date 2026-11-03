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


# Columns added after the first deploy (create_all never alters an existing table).
_ADDED_COLUMNS = [
    ("products", "info", "JSONB", "JSON", "'{}'"),
    ("staff", "lang_set", "BOOLEAN", "BOOLEAN", "false"),
    ("staff", "name_set", "BOOLEAN", "BOOLEAN", "false"),
    ("staff", "name_asked", "BOOLEAN", "BOOLEAN", "false"),
]


async def create_all(drop: bool = False) -> None:
    async with engine().begin() as conn:
        if drop:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        pg = conn.dialect.name == "postgresql"
        for table, col, pg_type, lite_type, default in _ADDED_COLUMNS:
            if pg:
                await conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {pg_type} DEFAULT {default}"
                )
            else:
                cols = [r[1] for r in (await conn.exec_driver_sql(f"PRAGMA table_info({table})")).fetchall()]
                if col not in cols:
                    await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {lite_type} DEFAULT {default}")


async def dispose() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine, _factory = None, None
