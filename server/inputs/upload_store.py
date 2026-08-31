"""
Purpose: 图片上传存储：接收上传图片，保存到本地磁盘，生成访问 URL。
"""

import json
import logging
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError


ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredImage:
    image_id: str
    session_id: str
    filename: str
    mime_type: str
    size_bytes: int
    path: Path
    created_at: float


class ImageUploadStore:
    def __init__(self, root_dir: Path, *, max_bytes: int, ttl_seconds: int) -> None:
        self.root_dir = root_dir
        self.max_bytes = max(max_bytes, 1)
        self.ttl_seconds = max(ttl_seconds, 1)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save(self, *, filename: str, mime_type: str, content: bytes, session_id: str = "default") -> StoredImage:
        self.cleanup_expired()
        normalized_mime = normalize_mime_type(mime_type)
        validate_image_upload(content, normalized_mime, self.max_bytes)

        image_id = uuid4().hex
        owner_session_id = normalize_session_id(session_id)
        safe_filename = sanitize_filename(filename) or f"upload{IMAGE_EXTENSIONS[normalized_mime]}"
        image_path = self.root_dir / f"{image_id}{IMAGE_EXTENSIONS[normalized_mime]}"
        meta_path = self.metadata_path(image_id)
        created_at = time.time()

        image_path.write_bytes(content)
        metadata = {
            "image_id": image_id,
            "session_id": owner_session_id,
            "filename": safe_filename,
            "mime_type": normalized_mime,
            "size_bytes": len(content),
            "path": image_path.name,
            "created_at": created_at,
        }
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return StoredImage(
            image_id=image_id,
            session_id=owner_session_id,
            filename=safe_filename,
            mime_type=normalized_mime,
            size_bytes=len(content),
            path=image_path,
            created_at=created_at,
        )

    def get(self, image_id: str, *, session_id: str | None = None) -> StoredImage | None:
        if not is_safe_image_id(image_id):
            return None
        meta_path = self.metadata_path(image_id)
        if not meta_path.exists():
            return None
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        owner_session_id = normalize_session_id(str(metadata.get("session_id", "default")))
        if session_id is not None and owner_session_id != normalize_session_id(session_id):
            return None
        created_at = float(metadata.get("created_at", 0))
        if is_expired(created_at, self.ttl_seconds):
            self.delete(image_id)
            return None

        image_path = self.root_dir / str(metadata.get("path", ""))
        if not image_path.exists() or not image_path.is_file():
            return None
        return StoredImage(
            image_id=image_id,
            session_id=owner_session_id,
            filename=str(metadata.get("filename", "")),
            mime_type=normalize_mime_type(str(metadata.get("mime_type", ""))),
            size_bytes=int(metadata.get("size_bytes", image_path.stat().st_size)),
            path=image_path,
            created_at=created_at,
        )

    def read_bytes(self, image_id: str, *, session_id: str | None = None) -> tuple[bytes, StoredImage] | None:
        stored = self.get(image_id, session_id=session_id)
        if stored is None:
            return None
        return stored.path.read_bytes(), stored

    def delete(self, image_id: str) -> None:
        if not is_safe_image_id(image_id):
            return
        meta_path = self.metadata_path(image_id)
        if meta_path.exists():
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                image_name = str(metadata.get("path", ""))
                image_path = self.root_dir / image_name
                if image_path.exists():
                    image_path.unlink()
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to delete uploaded image bytes for %s: %s", image_id, exc)
            meta_path.unlink(missing_ok=True)

    def cleanup_expired(self) -> int:
        removed = 0
        now = time.time()
        for meta_path in self.root_dir.glob("*.json"):
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                created_at = float(metadata.get("created_at", 0))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if now - created_at > self.ttl_seconds:
                self.delete(meta_path.stem)
                removed += 1
        return removed

    def metadata_path(self, image_id: str) -> Path:
        return self.root_dir / f"{image_id}.json"


def validate_image_upload(content: bytes, mime_type: str, max_bytes: int) -> None:
    if not content:
        raise ValueError("Uploaded image is empty.")
    if len(content) > max_bytes:
        raise ValueError(f"Uploaded image exceeds {max_bytes} bytes.")
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValueError("Unsupported image MIME type.")
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("Uploaded file is not a valid image.") from exc


def normalize_mime_type(mime_type: str) -> str:
    value = mime_type.split(";", 1)[0].strip().lower()
    if value == "image/jpg":
        return "image/jpeg"
    return value


def sanitize_filename(filename: str) -> str:
    path_name = Path(filename or "").name
    return "".join(char for char in path_name if char.isalnum() or char in {".", "_", "-"}).strip(".")


def normalize_session_id(session_id: str) -> str:
    value = (session_id or "default").strip()
    if not value:
        return "default"
    safe_chars = []
    for char in value[:128]:
        if char.isalnum() or char in {"-", "_", ".", ":"}:
            safe_chars.append(char)
    return "".join(safe_chars) or "default"


def is_safe_image_id(image_id: str) -> bool:
    return bool(image_id) and len(image_id) == 32 and all(char in "0123456789abcdef" for char in image_id)


def is_expired(created_at: float, ttl_seconds: int) -> bool:
    return time.time() - created_at > ttl_seconds
