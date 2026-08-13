from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.services.n2_document_store import delete_n2_document, get_n2_document, list_n2_documents, save_n2_document
from app.services.n2_documentation import build_n2_collection_plan, build_n2_review, sanitize_n2_review
from app.services.n2_workspace import build_n2_documentation_draft, list_n2_sites, n2_site_context
from app.web import _require_access, _require_mutation


router = APIRouter(tags=["n2-workspace"])


class N2DraftPayload(BaseModel):
    site_id: str = Field(min_length=1, max_length=64)
    responsibles: dict[str, str] = Field(default_factory=dict)


class N2CollectionPlanPayload(BaseModel):
    site_id: str = Field(min_length=1, max_length=64)
    host_names: list[str] = Field(min_length=1, max_length=100)


class N2ReviewPayload(BaseModel):
    site_id: str = Field(min_length=1, max_length=64)
    host_names: list[str] = Field(min_length=1, max_length=100)
    responsibles: dict[str, str] = Field(default_factory=dict)
    execution_ids: list[str] = Field(default_factory=list, max_length=100)


class N2DocumentSavePayload(BaseModel):
    document_id: str | None = Field(default=None, max_length=64)
    review: dict[str, Any]
    status: str = Field(default="reviewed", max_length=30)
    export_format: str | None = Field(default=None, max_length=12)


class N2ExportPayload(BaseModel):
    review: dict[str, Any]
    document_id: str | None = Field(default=None, max_length=64)


@router.get("/ui/api/n2/sites")
def n2_sites(
    request: Request,
    query: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=500, ge=1, le=1000),
) -> dict:
    _require_access(request)
    return list_n2_sites(query=query, limit=limit)


@router.get("/ui/api/n2/sites/{site_id}")
def n2_site(site_id: str, request: Request) -> dict:
    _require_access(request)
    result = n2_site_context(site_id)
    if result is None:
        raise HTTPException(status_code=404, detail="cliente/site não encontrado")
    return result


@router.get("/ui/api/n2/documents")
def n2_documents(
    request: Request,
    site_id: str | None = Query(default=None, max_length=64),
    query: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    _require_access(request)
    try:
        return list_n2_documents(site_id=site_id, query=query, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"não foi possível listar documentos N2: {type(exc).__name__}: {exc}") from exc


@router.get("/ui/api/n2/documents/{document_id}")
def n2_document(document_id: str, request: Request) -> dict:
    _require_access(request)
    try:
        result = get_n2_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"não foi possível abrir o documento N2: {type(exc).__name__}: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="documento N2 não encontrado")
    return result


@router.post("/ui/api/n2/documents")
def n2_save_document(payload: N2DocumentSavePayload, request: Request) -> dict:
    _require_mutation(request)
    try:
        return save_n2_document(
            payload.review,
            document_id=payload.document_id,
            status=payload.status,
            export_format=payload.export_format,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"não foi possível salvar o documento N2: {type(exc).__name__}: {exc}") from exc


@router.delete("/ui/api/n2/documents/{document_id}")
def n2_delete_document(document_id: str, request: Request) -> dict:
    _require_mutation(request)
    try:
        deleted = delete_n2_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"não foi possível remover o documento N2: {type(exc).__name__}: {exc}") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="documento N2 não encontrado")
    return {"deleted": True, "document_id": document_id}


@router.post("/ui/api/n2/draft")
def n2_draft(payload: N2DraftPayload, request: Request) -> dict:
    """Endpoint legado mantido para compatibilidade com clientes anteriores."""
    _require_mutation(request)
    try:
        return build_n2_documentation_draft(payload.site_id, responsibles=payload.responsibles)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"workspace N2 indisponível: {type(exc).__name__}: {exc}") from exc


@router.post("/ui/api/n2/plan")
def n2_collection_plan(payload: N2CollectionPlanPayload, request: Request) -> dict:
    """Monta lotes somente leitura para os hosts escolhidos pelo analista."""
    _require_mutation(request)
    try:
        return build_n2_collection_plan(payload.site_id, payload.host_names)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"não foi possível montar a coleta N2: {type(exc).__name__}: {exc}") from exc


@router.post("/ui/api/n2/review")
def n2_review(payload: N2ReviewPayload, request: Request) -> dict:
    """Consolida as execuções de IA, salva no PostgreSQL e devolve formulário editável."""
    _require_mutation(request)
    try:
        review = build_n2_review(
            payload.site_id,
            payload.host_names,
            responsibles=payload.responsibles,
            execution_ids=payload.execution_ids,
        )
        saved = save_n2_document(review, status="collected")
        review["document_id"] = saved["id"]
        return review
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"não foi possível montar a revisão N2: {type(exc).__name__}: {exc}") from exc


@router.post("/ui/api/n2/export/{document_format}")
def n2_export(document_format: str, payload: N2ExportPayload, request: Request) -> Response:
    """Exporta a revisão editada em Word ou PDF no padrão documental N2."""
    _require_mutation(request)
    fmt = str(document_format or "").strip().lower()
    if fmt not in {"docx", "pdf"}:
        raise HTTPException(status_code=422, detail="formato inválido; use docx ou pdf")
    try:
        from app.services.n2_document_export_runtime import export_n2_document

        safe_review = sanitize_n2_review(payload.review)
        if payload.document_id:
            save_n2_document(
                safe_review,
                document_id=payload.document_id,
                status="exported",
                export_format=fmt,
            )
        content, filename, media_type = export_n2_document(safe_review, fmt)
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"não foi possível exportar a documentação N2: {type(exc).__name__}: {exc}") from exc
