from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_update(chat_id, file_name, file_size=1024, file_id="abc123"):
    """Build a minimal mock Update with a document attachment."""
    doc = SimpleNamespace(
        file_name=file_name,
        file_size=file_size,
        file_id=file_id,
    )
    message = AsyncMock()
    message.document = doc
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message = message
    return update


@pytest.fixture
def admin_id():
    return 111


@pytest.fixture(autouse=True)
def _patch_acl(admin_id):
    with patch("src.telegram_handler._is_admin", side_effect=lambda cid: cid == admin_id):
        yield


def _fake_pymupdf_open(page_count):
    """Build a context-manager mock mimicking pymupdf.open(path)."""
    fake_doc = MagicMock()
    fake_doc.page_count = page_count
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=fake_doc)
    cm.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=cm)


@pytest.fixture
def _patch_pipeline():
    """Patch the full PDF processing pipeline so document_handler runs to completion."""
    chunks = [
        {
            "text": "chunk",
            "embedding_text": "chunk",
            "metadata": {"filename": "safe.pdf", "section": ""},
        }
    ]
    fake_pymupdf = MagicMock()
    fake_pymupdf.open = _fake_pymupdf_open(page_count=10)
    with (
        patch("src.telegram_handler.pymupdf", fake_pymupdf),
        patch("src.telegram_handler.process_single_pdf_with_doc", return_value=("doc", chunks)),
        patch("src.telegram_handler.embed_documents", new_callable=AsyncMock, return_value=[[0.1] * 1024]),
        patch("src.telegram_handler.delete_by_filename", new_callable=AsyncMock),
        patch("src.telegram_handler.upsert_chunks", new_callable=AsyncMock, return_value=1),
    ):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malicious_name,expected_safe",
    [
        ("../../etc/evil.pdf", "evil.pdf"),
        ("../../../config.yaml.pdf", "config.yaml.pdf"),
        ("subdir/nested/file.pdf", "file.pdf"),
        ("normal.pdf", "normal.pdf"),
    ],
)
async def test_path_traversal_stripped(
    admin_id, malicious_name, expected_safe, _patch_pipeline, tmp_path
):
    from src.telegram_handler import document_handler

    update = _make_update(admin_id, malicious_name)
    ctx = MagicMock()
    mock_file = AsyncMock()
    ctx.bot.get_file = AsyncMock(return_value=mock_file)

    with patch("src.telegram_handler.PDF_DIR", tmp_path):
        await document_handler(update, ctx)

    # File should be downloaded to PDF_DIR / basename only
    downloaded_path = mock_file.download_to_drive.call_args[0][0]
    assert Path(downloaded_path) == tmp_path / expected_safe
    assert ".." not in downloaded_path


@pytest.mark.asyncio
async def test_delete_uses_safe_name(admin_id, _patch_pipeline, tmp_path):
    from src.telegram_handler import document_handler

    update = _make_update(admin_id, "../../traversal.pdf")
    ctx = MagicMock()
    ctx.bot.get_file = AsyncMock(return_value=AsyncMock())

    with (
        patch("src.telegram_handler.PDF_DIR", tmp_path),
        patch("src.telegram_handler.delete_by_filename", new_callable=AsyncMock) as mock_delete,
    ):
        await document_handler(update, ctx)
        mock_delete.assert_awaited_once_with("traversal.pdf")


@pytest.mark.asyncio
async def test_large_page_count_warns_in_success_message(admin_id, tmp_path):
    """PDFs with page_count > _PAGE_WARN_THRESHOLD succeed but append a warning."""
    from src import telegram_handler
    from src.telegram_handler import document_handler

    oversized_pages = telegram_handler._PAGE_WARN_THRESHOLD + 50
    chunks = [
        {"text": "c", "embedding_text": "c", "metadata": {"filename": "big.pdf", "section": ""}}
    ]
    fake_pymupdf = MagicMock()
    fake_pymupdf.open = _fake_pymupdf_open(page_count=oversized_pages)

    update = _make_update(admin_id, "big.pdf")
    ctx = MagicMock()
    ctx.bot.get_file = AsyncMock(return_value=AsyncMock())

    with (
        patch("src.telegram_handler.PDF_DIR", tmp_path),
        patch("src.telegram_handler.pymupdf", fake_pymupdf),
        patch("src.telegram_handler.process_single_pdf_with_doc", return_value=("doc", chunks)),
        patch("src.telegram_handler.embed_documents", new_callable=AsyncMock, return_value=[[0.1] * 1024]),
        patch("src.telegram_handler.delete_by_filename", new_callable=AsyncMock),
        patch("src.telegram_handler.upsert_chunks", new_callable=AsyncMock, return_value=1),
    ):
        await document_handler(update, ctx)

    calls = [c.args[0] for c in update.message.reply_text.await_args_list]
    success = [m for m in calls if m.startswith("✅")]
    assert success, f"expected a success reply, got: {calls}"
    final = success[-1]
    assert f"{oversized_pages} stranica" in final
    assert str(telegram_handler._PAGE_WARN_THRESHOLD) in final
    assert "⚠️" in final


@pytest.mark.asyncio
async def test_small_page_count_no_warning(admin_id, _patch_pipeline, tmp_path):
    """Normal PDFs (page_count <= threshold) show the plain success message."""
    from src.telegram_handler import document_handler

    update = _make_update(admin_id, "normal.pdf")
    ctx = MagicMock()
    ctx.bot.get_file = AsyncMock(return_value=AsyncMock())

    with patch("src.telegram_handler.PDF_DIR", tmp_path):
        await document_handler(update, ctx)

    calls = [c.args[0] for c in update.message.reply_text.await_args_list]
    success = [m for m in calls if m.startswith("✅")]
    assert success
    assert "⚠️" not in success[-1]
    assert "stranica" not in success[-1]


@pytest.mark.asyncio
async def test_chunk_count_over_limit_rejected_before_embedding(admin_id, tmp_path):
    """Documents producing > _MAX_CHUNKS are rejected before any embedding spend."""
    from src import telegram_handler
    from src.telegram_handler import document_handler

    oversized_count = telegram_handler._MAX_CHUNKS + 10
    oversized_chunks = [
        {"text": f"c{i}", "embedding_text": f"c{i}", "metadata": {"filename": "huge.pdf", "section": ""}}
        for i in range(oversized_count)
    ]
    fake_pymupdf = MagicMock()
    fake_pymupdf.open = _fake_pymupdf_open(page_count=10)

    update = _make_update(admin_id, "huge.pdf")
    ctx = MagicMock()
    ctx.bot.get_file = AsyncMock(return_value=AsyncMock())

    with (
        patch("src.telegram_handler.PDF_DIR", tmp_path),
        patch("src.telegram_handler.pymupdf", fake_pymupdf),
        patch("src.telegram_handler.process_single_pdf_with_doc", return_value=("doc", oversized_chunks)),
        patch("src.telegram_handler.embed_documents", new_callable=AsyncMock) as mock_embed,
        patch("src.telegram_handler.delete_by_filename", new_callable=AsyncMock) as mock_delete,
        patch("src.telegram_handler.upsert_chunks", new_callable=AsyncMock) as mock_upsert,
    ):
        await document_handler(update, ctx)

    mock_embed.assert_not_awaited()
    mock_delete.assert_not_awaited()
    mock_upsert.assert_not_awaited()

    calls = [c.args[0] for c in update.message.reply_text.await_args_list]
    rejections = [m for m in calls if m.startswith("❌")]
    assert rejections
    final = rejections[-1]
    assert str(oversized_count) in final
    assert str(telegram_handler._MAX_CHUNKS) in final


@pytest.mark.asyncio
async def test_qdrant_quota_error_shows_distinct_message(admin_id, tmp_path):
    """Qdrant UnexpectedResponse with quota-like status surfaces a dedicated Serbian message."""
    from qdrant_client.http.exceptions import UnexpectedResponse

    from src.telegram_handler import document_handler

    quota_error = UnexpectedResponse(
        status_code=403,
        reason_phrase="Forbidden",
        content=b"quota exceeded",
        headers=None,
    )
    chunks = [
        {"text": "c", "embedding_text": "c", "metadata": {"filename": "ok.pdf", "section": ""}}
    ]
    fake_pymupdf = MagicMock()
    fake_pymupdf.open = _fake_pymupdf_open(page_count=10)

    update = _make_update(admin_id, "ok.pdf")
    ctx = MagicMock()
    ctx.bot.get_file = AsyncMock(return_value=AsyncMock())

    with (
        patch("src.telegram_handler.PDF_DIR", tmp_path),
        patch("src.telegram_handler.pymupdf", fake_pymupdf),
        patch("src.telegram_handler.process_single_pdf_with_doc", return_value=("doc", chunks)),
        patch("src.telegram_handler.embed_documents", new_callable=AsyncMock, return_value=[[0.1] * 1024]),
        patch("src.telegram_handler.delete_by_filename", new_callable=AsyncMock),
        patch("src.telegram_handler.upsert_chunks", new_callable=AsyncMock, side_effect=quota_error),
    ):
        await document_handler(update, ctx)

    calls = [c.args[0] for c in update.message.reply_text.await_args_list]
    assert any("Qdrant skladište je puno" in m for m in calls), calls
    assert not any("pokušajte ponovo" in m for m in calls), calls


@pytest.mark.asyncio
async def test_qdrant_non_quota_error_falls_through_to_generic(admin_id, tmp_path):
    """Non-quota Qdrant errors still surface the generic 'try again' message."""
    from qdrant_client.http.exceptions import UnexpectedResponse

    from src.telegram_handler import document_handler

    server_error = UnexpectedResponse(
        status_code=500,
        reason_phrase="Internal Server Error",
        content=b"boom",
        headers=None,
    )
    chunks = [
        {"text": "c", "embedding_text": "c", "metadata": {"filename": "ok.pdf", "section": ""}}
    ]
    fake_pymupdf = MagicMock()
    fake_pymupdf.open = _fake_pymupdf_open(page_count=10)

    update = _make_update(admin_id, "ok.pdf")
    ctx = MagicMock()
    ctx.bot.get_file = AsyncMock(return_value=AsyncMock())

    with (
        patch("src.telegram_handler.PDF_DIR", tmp_path),
        patch("src.telegram_handler.pymupdf", fake_pymupdf),
        patch("src.telegram_handler.process_single_pdf_with_doc", return_value=("doc", chunks)),
        patch("src.telegram_handler.embed_documents", new_callable=AsyncMock, return_value=[[0.1] * 1024]),
        patch("src.telegram_handler.delete_by_filename", new_callable=AsyncMock),
        patch("src.telegram_handler.upsert_chunks", new_callable=AsyncMock, side_effect=server_error),
    ):
        await document_handler(update, ctx)

    calls = [c.args[0] for c in update.message.reply_text.await_args_list]
    assert any("pokušajte ponovo" in m for m in calls), calls
    assert not any("Qdrant skladište je puno" in m for m in calls), calls
