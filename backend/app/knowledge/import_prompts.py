"""Explicit, non-overwriting initial import: python -m app.knowledge.import_prompts."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path

from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
from app.core.database import engine, ensure_schema
from app.core.errors import AppError
from app.core.logging_context import TraceContextFilter, normalize_trace_id, reset_trace_context, set_trace_context
from app.knowledge.service import KnowledgeService


async def run_import(prompt_dir: Path) -> int:
    tokens = set_trace_context(normalize_trace_id(None))
    try:
        await ensure_schema()
        result = await KnowledgeService().import_default_prompts(prompt_dir)
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 2 if result.different else 0
    except AppError as exc:
        print(json.dumps({'code': exc.code, 'message': exc.message, 'details': exc.details}, ensure_ascii=False))
        return 1
    finally:
        reset_trace_context(tokens)
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description='Import default prompt v1 without overwriting existing entries.')
    parser.add_argument('--prompt-dir', type=Path, default=PROMPT_DIR)
    args = parser.parse_args()
    handler = logging.StreamHandler()
    handler.addFilter(TraceContextFilter())
    handler.setFormatter(logging.Formatter('%(levelname)s %(name)s trace_id=%(trace_id)s %(message)s'))
    logging.getLogger('app.knowledge').addHandler(handler)
    logging.getLogger('app.knowledge').setLevel(logging.INFO)
    raise SystemExit(asyncio.run(run_import(args.prompt_dir)))


if __name__ == '__main__':
    main()
