from __future__ import annotations

from typing import Any

from app.services import n2_document_export as _export


# python-docx cria um documento novo sem parágrafos. O gerador do N2 usa a
# primeira página como uma imagem de capa em um parágrafo de largura total; este
# factory garante que esse parágrafo exista sem alterar o restante do layout.
_OriginalDocument = _export.Document


def _document_with_cover_paragraph(*args: Any, **kwargs: Any):
    document = _OriginalDocument(*args, **kwargs)
    if not document.paragraphs:
        document.add_paragraph()
    return document


_export.Document = _document_with_cover_paragraph


export_docx = _export.export_docx
export_pdf = _export.export_pdf
export_n2_document = _export.export_n2_document
build_cover_png = _export.build_cover_png
