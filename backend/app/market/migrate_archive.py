"""Explicit, verified migration. The old table is dropped only with --finalize."""
from __future__ import annotations
import argparse
import asyncio
import json
import re
from typing import Any
from sqlalchemy import text
from app.core.database import engine
from app.market import archive

LEGACY_SCHEMA = "public"


async def migrate_legacy_bars(*, finalize: bool = False) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*",LEGACY_SCHEMA):
        raise ValueError("Invalid legacy schema")
    old = f'"{LEGACY_SCHEMA}"."market_bars"'
    backup = archive.table('legacy_bars_backup')
    async with engine.begin() as c:
        await c.execute(text("SELECT pg_advisory_xact_lock(724019221)"))
        await archive.ensure_archive_schema(c)
        exists = (await c.execute(text("SELECT to_regclass(:name)"),{'name':LEGACY_SCHEMA+'.market_bars'})).scalar_one()
        if not exists:
            return {'verified':True,'legacy_rows':0,'removed':False,'already_absent':True}
        await c.execute(text(f"LOCK TABLE {old} IN ACCESS EXCLUSIVE MODE"))
        invalid=(await c.execute(text(f"""SELECT count(*) FROM {old} WHERE
            symbol NOT IN ('ES','NQ','MES','MNQ','MGC') OR period NOT IN ('1m','1h')
            OR opened_at IS NULL OR open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
            OR open<=0 OR low<=0 OR high<greatest(open,close,low) OR low>least(open,close)
            OR (volume IS NOT NULL AND (volume<0 OR volume<>trunc(volume)))
            OR (period='1m' AND opened_at<>date_trunc('minute',opened_at))"""))).scalar_one()
        if invalid:
            raise ValueError(f"Legacy data requires review: {invalid} invalid rows")
        await c.execute(text(f"CREATE TABLE IF NOT EXISTS {backup} AS TABLE {old}"))
        differences=(await c.execute(text(f"""SELECT count(*) FROM (
          (TABLE {old} EXCEPT ALL TABLE {backup}) UNION ALL
          (TABLE {backup} EXCEPT ALL TABLE {old})) delta"""))).scalar_one()
        if differences:
            raise ValueError("Legacy table changed since backup; refusing to remove it")
        await c.execute(text(f"""INSERT INTO {archive.table('bars')}
            (root,continuous_symbol,instrument_id,ts_event_ns,timestamp_utc,
             open_raw,high_raw,low_raw,close_raw,volume,publisher_id,source,confirmed)
          SELECT symbol,symbol||'.legacy',0,(extract(epoch from opened_at)*1000000000)::bigint,opened_at,
             round(open::numeric*1000000000)::bigint,round(high::numeric*1000000000)::bigint,
             round(low::numeric*1000000000)::bigint,round(close::numeric*1000000000)::bigint,
             volume::bigint,0,'legacy:'||coalesce(source,'unknown'),true
          FROM {backup} WHERE period='1m'
          ON CONFLICT(root,ts_event_ns,instrument_id) DO NOTHING"""))
        mismatches=(await c.execute(text(f"""SELECT count(*) FROM {backup} l
          LEFT JOIN {archive.table('bars')} a ON a.root=l.symbol AND a.instrument_id=0
          AND a.ts_event_ns=(extract(epoch from l.opened_at)*1000000000)::bigint
          WHERE l.period='1m' AND (a.root IS NULL
           OR a.open_raw<>round(l.open::numeric*1000000000)::bigint
           OR a.high_raw<>round(l.high::numeric*1000000000)::bigint
           OR a.low_raw<>round(l.low::numeric*1000000000)::bigint
           OR a.close_raw<>round(l.close::numeric*1000000000)::bigint
           OR a.volume IS DISTINCT FROM l.volume::bigint)"""))).scalar_one()
        if mismatches:
            raise ValueError(f"Legacy migration verification failed: {mismatches} rows")
        count=(await c.execute(text(f"SELECT count(*) FROM {backup}"))).scalar_one()
        breakdown=(await c.execute(text(f"SELECT symbol,period,count(*) FROM {backup} GROUP BY symbol,period ORDER BY symbol,period"))).all()
        result={'verified':True,'legacy_rows':count,'counts':[list(row) for row in breakdown],'removed':finalize,'backup':f'{archive.ARCHIVE_SCHEMA}.legacy_bars_backup'}
        await c.execute(text(f"INSERT INTO {archive.table('metadata')}(key,value) VALUES ('legacy_migration',:value) ON CONFLICT(key) DO UPDATE SET value=excluded.value"),{'value':json.dumps(result)})
        if finalize:
            # No CASCADE: an unknown dependent object must prevent destructive cleanup.
            await c.execute(text(f"DROP TABLE {old}"))
        return result


async def _main(finalize: bool) -> None:
    try:
        print(json.dumps(await migrate_legacy_bars(finalize=finalize),ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--finalize',action='store_true')
    asyncio.run(_main(parser.parse_args().finalize))
