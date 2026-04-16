from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from qdrant_client import models


@pytest.fixture
def mock_client():
    with patch("src.vector_store._client") as client:
        client.delete = AsyncMock()
        client.upsert = AsyncMock()
        client.scroll = AsyncMock()
        yield client


@pytest.mark.asyncio
async def test_delete_by_filename_builds_correct_filter(mock_client):
    from src.vector_store import delete_by_filename

    await delete_by_filename("test.pdf")

    mock_client.delete.assert_awaited_once()
    call_kwargs = mock_client.delete.call_args.kwargs
    selector = call_kwargs["points_selector"]
    assert isinstance(selector, models.FilterSelector)
    condition = selector.filter.must[0]
    assert condition.key == "filename"
    assert condition.match.value == "test.pdf"


@pytest.mark.asyncio
async def test_delete_by_filename_no_match_is_silent(mock_client):
    """Qdrant no-ops when no points match — verify we don't raise."""
    from src.vector_store import delete_by_filename

    mock_client.delete.return_value = None
    await delete_by_filename("nonexistent.pdf")
    mock_client.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_after_delete_replaces_chunks(mock_client):
    """Simulate the replacement flow: delete old, upsert new."""
    from src.vector_store import delete_by_filename, upsert_chunks

    await delete_by_filename("update.pdf")
    count = await upsert_chunks(
        texts=["chunk1", "chunk2"],
        vectors=[[0.1] * 1024, [0.2] * 1024],
        metadatas=[{"filename": "update.pdf"}, {"filename": "update.pdf"}],
    )

    assert count == 2
    mock_client.delete.assert_awaited_once()
    mock_client.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_documents_aggregates_chunks(mock_client):
    from src.vector_store import list_documents

    points = [
        SimpleNamespace(payload={"filename": "zakon_o_pdv.pdf"}),
        SimpleNamespace(payload={"filename": "zakon_o_pdv.pdf"}),
        SimpleNamespace(payload={"filename": "pravilnik.pdf"}),
    ]
    mock_client.scroll.return_value = (points, None)

    result = await list_documents()

    assert result == {"zakon_o_pdv.pdf": 2, "pravilnik.pdf": 1}
    mock_client.scroll.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_documents_empty_collection(mock_client):
    from src.vector_store import list_documents

    mock_client.scroll.return_value = ([], None)

    result = await list_documents()

    assert result == {}


@pytest.mark.asyncio
async def test_list_documents_paginates(mock_client):
    from src.vector_store import list_documents

    page1 = [SimpleNamespace(payload={"filename": "a.pdf"})]
    page2 = [SimpleNamespace(payload={"filename": "b.pdf"})]
    mock_client.scroll.side_effect = [(page1, "next"), (page2, None)]

    result = await list_documents()

    assert result == {"a.pdf": 1, "b.pdf": 1}
    assert mock_client.scroll.await_count == 2
