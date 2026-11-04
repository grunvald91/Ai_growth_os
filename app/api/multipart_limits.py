"""Stream-counted multipart parser; limits apply without Content-Length."""
from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.datastructures import FormData


async def limited_multipart_form(request: Request, *,
                                 max_body: int, max_files: int,
                                 max_fields: int) -> FormData:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise HTTPException(400, "Некорректная длина запроса") from None
        if length < 0:
            raise HTTPException(400, "Некорректная длина запроса")
        if length > max_body:
            raise HTTPException(413, "Загружаемый файл слишком большой")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_body:
            raise HTTPException(413, "Загружаемый файл слишком большой")
        chunks.append(chunk)
    data = b"".join(chunks)
    if declared is not None and len(data) != length:
        raise HTTPException(400, "Размер запроса не совпадает с Content-Length")

    async def receive():
        return {"type": "http.request", "body": data, "more_body": False}

    bounded = Request(request.scope, receive=receive)
    return await bounded.form(max_files=max_files, max_fields=max_fields)
