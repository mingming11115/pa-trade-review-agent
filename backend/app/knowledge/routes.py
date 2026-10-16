from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Query
from app.admin.prompts import OrchestrationView, get_orchestration
from app.auth.service import UserPublic, current_user
from app.knowledge.contracts import KnowledgeDocument, KnowledgeUpdate, KnowledgeVersionPage, KnowledgeRollback, KnowledgeSummaryPublic, KnowledgePromptRef
from app.knowledge.service import KnowledgeService

router = APIRouter(prefix='/api/v1/knowledge', tags=['knowledge'])


def get_knowledge_service() -> KnowledgeService:
    return KnowledgeService()


@router.get('/orchestration', response_model=OrchestrationView)
async def orchestration(_: UserPublic = Depends(current_user)) -> OrchestrationView:
    return get_orchestration()


@router.get('/prompts', response_model=list[KnowledgeSummaryPublic])
async def documents(user: UserPublic = Depends(current_user),
                    service: KnowledgeService = Depends(get_knowledge_service)) -> list[KnowledgeSummaryPublic]:
    view = get_orchestration()
    return [KnowledgeSummaryPublic(**item.model_dump(), stages=[
                KnowledgePromptRef(stage_id=stage.id, placement=file.placement, condition=file.condition)
                for stage in view.stages for file in stage.prompt_files if file.filename == item.filename])
            for item in await service.list_documents(user.id)]


@router.get('/prompt', response_model=KnowledgeDocument)
async def document(filename: str = Query(..., min_length=1, max_length=200),
                   user: UserPublic = Depends(current_user),
                   service: KnowledgeService = Depends(get_knowledge_service)) -> KnowledgeDocument:
    return await service.get_document(user.id, filename)


@router.put('/prompt', response_model=KnowledgeDocument)
async def save_document(payload: KnowledgeUpdate, filename: str = Query(..., min_length=1, max_length=200),
                        user: UserPublic = Depends(current_user),
                        service: KnowledgeService = Depends(get_knowledge_service)) -> KnowledgeDocument:
    return await service.save_document(user.id, filename, payload)


@router.get('/versions', response_model=KnowledgeVersionPage)
async def versions(filename: str = Query(..., min_length=1, max_length=200),
                   limit: int = Query(20, ge=1, le=50), before_revision: int | None = Query(None, ge=1),
                   user: UserPublic = Depends(current_user),
                   service: KnowledgeService = Depends(get_knowledge_service)) -> KnowledgeVersionPage:
    return await service.list_versions(user.id, filename, limit, before_revision)


@router.post('/versions/{version_id}/rollback', response_model=KnowledgeDocument)
async def rollback(version_id: uuid.UUID, payload: KnowledgeRollback,
                   filename: str = Query(..., min_length=1, max_length=200),
                   user: UserPublic = Depends(current_user),
                   service: KnowledgeService = Depends(get_knowledge_service)) -> KnowledgeDocument:
    return await service.rollback_version(user.id, filename, version_id, payload.expected_version)
