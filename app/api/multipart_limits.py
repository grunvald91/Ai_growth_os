"""Stream-counted multipart parser; limits apply without Content-Length."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request
from starlette.datastructures import FormData

# Bound in-process image decoding and multipart spooling; each worker enforces this.
IMAGE_UPLOAD_SLOTS = asyncio.Semaphore(2)


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
    source = request.stream().__aiter__()
    size = 0
    finished = False

    async def receive():
        nonlocal size, finished
        if finished:
            return {"type": "http.request", "body": b"", "more_body": False}
        try:
            chunk = await anext(source)
        except StopAsyncIteration:
            finished = True
            if declared is not None and size != length:
                raise HTTPException(400, "Размер запроса не совпадает с Content-Length")
            return {"type": "http.request", "body": b"", "more_body": False}
        size += len(chunk)
        if size > max_body:
            raise HTTPException(413, "Загружаемый файл слишком большой")
        # Starlette's multipart parser writes uploaded files into bounded SpooledTemporaryFile.
        # No in-memory collection or concatenation of the whole request.
        return {"type": "http.request", "body": chunk, "more_body": True}

    bounded = Request(request.scope, receive=receive)
    return await bounded.form(max_files=max_files, max_fields=max_fields,
                              max_part_size=max_body)
