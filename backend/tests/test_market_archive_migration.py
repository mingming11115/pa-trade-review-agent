from datetime import UTC, datetime
import pytest
from sqlalchemy import text
from test_market_archive import archive_db


@pytest.mark.asyncio
async def test_legacy_migration_backs_up_verifies_and_removes_only_after_finalize(archive_db,monkeypatch):
    archive,engine,factory = archive_db
    from app.market import migrate_archive
    monkeypatch.setattr(migrate_archive,"LEGACY_SCHEMA",archive.ARCHIVE_SCHEMA)
    monkeypatch.setattr(migrate_archive,"engine",engine)
    async with engine.begin() as c:
        await c.execute(text(f"CREATE TABLE {archive.table('market_bars')} (id int PRIMARY KEY,symbol text,period text,opened_at timestamptz,open double precision,high double precision,low double precision,close double precision,volume double precision,source text,updated_at timestamptz)"))
        await c.execute(text(f"INSERT INTO {archive.table('market_bars')} VALUES (1,'ES','1m','2026-09-30 10:50Z',100,102,99,101,NULL,'provider',now()),(2,'ES','1h','2026-09-29 10:00Z',110,112,109,111,4,'aggregate:1m',now())"))
    result = await migrate_archive.migrate_legacy_bars()
    assert result['verified'] and result['legacy_rows']==2 and not result['removed']
    assert (await migrate_archive.migrate_legacy_bars())['legacy_rows']==2
    result = await migrate_archive.migrate_legacy_bars(finalize=True)
    assert result['removed']
    async with engine.connect() as c:
        assert (await c.execute(text("SELECT to_regclass(:name)"),{'name':archive.ARCHIVE_SCHEMA+'.market_bars'})).scalar_one() is None
        assert (await c.execute(text(f"SELECT count(*) FROM {archive.table('legacy_bars_backup')}"))).scalar_one()==2
    bars=await archive.read_archive_bars('ES','1m',datetime(2026,9,30,10,tzinfo=UTC),datetime(2026,9,30,11,tzinfo=UTC))
    assert len(bars)==1 and bars[0].volume is None and bars[0].close==101
    hours=await archive.read_archive_bars('ES','1h',datetime(2026,9,29,10,tzinfo=UTC),datetime(2026,9,29,11,tzinfo=UTC))
    assert len(hours)==1 and hours[0].close==111


@pytest.mark.asyncio
async def test_migration_failure_preserves_legacy_table(archive_db,monkeypatch):
    archive,engine,_ = archive_db
    from app.market import migrate_archive
    monkeypatch.setattr(migrate_archive,'LEGACY_SCHEMA',archive.ARCHIVE_SCHEMA)
    monkeypatch.setattr(migrate_archive,'engine',engine)
    async with engine.begin() as c:
        await c.execute(text(f"CREATE TABLE {archive.table('market_bars')} (id int,symbol text,period text,opened_at timestamptz,open float,high float,low float,close float,volume float,source text,updated_at timestamptz)"))
        await c.execute(text(f"INSERT INTO {archive.table('market_bars')} VALUES (1,'GC','1m',now(),1,2,1,2,1,'provider',now())"))
    with pytest.raises(ValueError):
        await migrate_archive.migrate_legacy_bars(finalize=True)
    async with engine.connect() as c:
        assert (await c.execute(text(f"SELECT count(*) FROM {archive.table('market_bars')}"))).scalar_one()==1
