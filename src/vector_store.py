import uuid

from qdrant_client import AsyncQdrantClient, models

from src.config import CONFIG, QDRANT_API_KEY, QDRANT_URL

COLLECTION = CONFIG["retrieval"]["collection_name"]
DIMENSIONS = CONFIG["retrieval"]["embedding_dimensions"]
TOP_K = CONFIG["retrieval"]["top_k"]
MIN_SCORE = CONFIG["retrieval"].get("min_score", 0.0)


_client = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)


async def collection_exists() -> bool:
    """Check if the Qdrant collection already exists."""
    collections = await _client.get_collections()
    names = [c.name for c in collections.collections]
    return COLLECTION in names


async def ensure_collection() -> None:
    collections = await _client.get_collections()
    names = [c.name for c in collections.collections]
    if COLLECTION not in names:
        await _client.create_collection(
            collection_name=COLLECTION,
            vectors_config=models.VectorParams(
                size=DIMENSIONS, distance=models.Distance.COSINE
            ),
        )
        await _client.create_payload_index(
            collection_name=COLLECTION,
            field_name="filename",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


async def upsert_chunks(
    texts: list[str], vectors: list[list[float]], metadatas: list[dict]
) -> int:
    points = [
        models.PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload={"text": text, **meta},
        )
        for text, vec, meta in zip(texts, vectors, metadatas)
    ]
    batch_size = 100
    for i in range(0, len(points), batch_size):
        await _client.upsert(
            collection_name=COLLECTION, points=points[i : i + batch_size]
        )
    return len(points)


async def delete_stale_versions(filename: str, keep_version: str) -> None:
    """Delete all points for a filename EXCEPT the given upload version.

    Enables safe replace: upsert the new version first, then sweep the old
    points — a failed upload never destroys the existing document.
    """
    await _client.delete(
        collection_name=COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="filename",
                        match=models.MatchValue(value=filename),
                    )
                ],
                must_not=[
                    models.FieldCondition(
                        key="upload_version",
                        match=models.MatchValue(value=keep_version),
                    )
                ],
            )
        ),
    )


async def delete_upload_version(filename: str, upload_version: str) -> None:
    """Delete only the points of a specific upload version for a filename.

    Cleanup for a partially failed upsert: sweeps the incomplete new version
    so it doesn't linger as duplicate chunks eating quota, while the old
    version stays untouched.
    """
    await _client.delete(
        collection_name=COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="filename",
                        match=models.MatchValue(value=filename),
                    ),
                    models.FieldCondition(
                        key="upload_version",
                        match=models.MatchValue(value=upload_version),
                    ),
                ],
            )
        ),
    )


async def delete_by_filename(filename: str) -> None:
    await _client.delete(
        collection_name=COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="filename",
                        match=models.MatchValue(value=filename),
                    )
                ]
            )
        ),
    )


async def search(query_vector: list[float], top_k: int = TOP_K) -> list[dict]:
    results = await _client.query_points(
        collection_name=COLLECTION,
        query=query_vector,
        limit=top_k,
        with_payload=True,
    )
    return [
        {
            "text": hit.payload.get("text", ""),
            "filename": hit.payload.get("filename", ""),
            "section": hit.payload.get("section", ""),
            "score": hit.score,
        }
        for hit in results.points
        if hit.score >= MIN_SCORE
    ]


async def list_documents() -> dict[str, int]:
    docs: dict[str, int] = {}
    offset = None
    while True:
        points, offset = await _client.scroll(
            collection_name=COLLECTION,
            with_payload=models.PayloadSelectorInclude(include=["filename"]),
            with_vectors=False,
            limit=100,
            offset=offset,
        )
        for p in points:
            fn = p.payload.get("filename", "")
            if fn:
                docs[fn] = docs.get(fn, 0) + 1
        if offset is None:
            break
    return docs


async def recreate_collection() -> None:
    collections = await _client.get_collections()
    names = [c.name for c in collections.collections]
    if COLLECTION in names:
        await _client.delete_collection(collection_name=COLLECTION)
    await _client.create_collection(
        collection_name=COLLECTION,
        vectors_config=models.VectorParams(
            size=DIMENSIONS, distance=models.Distance.COSINE
        ),
    )
    await _client.create_payload_index(
        collection_name=COLLECTION,
        field_name="filename",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
