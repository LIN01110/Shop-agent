"""
Purpose: 图片上传 API：接收用户上传图片，保存到本地并返回图片 URL。
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from server.config import Settings, get_settings
from server.inputs.upload_store import ImageUploadStore


router = APIRouter(prefix="/uploads", tags=["uploads"])


class ImageUploadResponse(BaseModel):
    image_id: str
    session_id: str
    filename: str
    mime_type: str
    size_bytes: int


@router.post("/images", response_model=ImageUploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    session_id: str = Form(default="default", min_length=1, max_length=128),
    settings: Settings = Depends(get_settings),
) -> ImageUploadResponse:
    content = await file.read(settings.upload_image_max_bytes + 1)
    store = ImageUploadStore(
        settings.upload_image_path,
        max_bytes=settings.upload_image_max_bytes,
        ttl_seconds=settings.upload_image_ttl_seconds,
    )
    try:
        stored = store.save(
            filename=file.filename or "upload",
            mime_type=file.content_type or "",
            content=content,
            session_id=session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ImageUploadResponse(
        image_id=stored.image_id,
        session_id=stored.session_id,
        filename=stored.filename,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
    )
