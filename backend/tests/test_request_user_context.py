from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import Request, Response

from app.core.user_context import get_current_user_id, reset_current_user_id, set_current_user_id
from app.main import request_context


@pytest.mark.anyio
@pytest.mark.parametrize("fails", [False, True])
async def test_request_identity_is_cleared_then_restored_even_on_failure(fails: bool) -> None:
    outer_user, authenticated_user = uuid.uuid4(), uuid.uuid4()
    token = set_current_user_id(outer_user)
    request = Request({"type": "http", "method": "GET", "path": "/probe", "headers": []})
    seen: list[uuid.UUID | None] = []

    async def downstream(_request: Request) -> Response:
        seen.append(get_current_user_id())
        set_current_user_id(authenticated_user)

        async def background() -> uuid.UUID | None:
            return get_current_user_id()

        seen.append(await asyncio.create_task(background()))
        if fails:
            raise RuntimeError("probe failure")
        return Response()

    try:
        response = await request_context(request, downstream)
        assert response.status_code == (500 if fails else 200)
        assert seen == [None, authenticated_user]
        assert get_current_user_id() == outer_user
    finally:
        reset_current_user_id(token)
