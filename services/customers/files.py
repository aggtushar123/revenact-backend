"""What may be uploaded, and how it is handed back (SOC2:API-10).

A closed list of types by extension *and* declared content type, a size
cap, a sanitised name, and magic-byte checks for the binary formats — a
PDF that starts with "<html" is refused. Downloads always go out as an
attachment with sniffing disabled, so the browser never renders a file
someone uploaded as if it were part of this site.
"""

import re
import unicodedata

from django.conf import settings
from django.http import FileResponse

# extension → the content types a browser may declare for it. HTML, SVG,
# scripts and archives are deliberately absent: nothing here should ever
# execute or be rendered inline.
ALLOWED_TYPES = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".xls": {"application/vnd.ms-excel"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".ppt": {"application/vnd.ms-powerpoint"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    ".csv": {"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".vtt": {"text/vtt", "text/plain"},
    ".srt": {"application/x-subrip", "text/plain", "application/octet-stream"},
    ".json": {"application/json", "text/plain"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".gif": {"image/gif"},
    ".webp": {"image/webp"},
    ".mp3": {"audio/mpeg"},
    ".m4a": {"audio/mp4", "audio/x-m4a"},
    ".wav": {"audio/wav", "audio/x-wav"},
}

#: Text formats a transcript may be pasted or uploaded as.
TRANSCRIPT_EXTENSIONS = {".txt", ".vtt", ".srt", ".md"}

# Leading bytes that identify the binary formats above.
MAGIC = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".docx": (b"PK\x03\x04",),
    ".xlsx": (b"PK\x03\x04",),
    ".pptx": (b"PK\x03\x04",),
    ".doc": (b"\xd0\xcf\x11\xe0",),
    ".xls": (b"\xd0\xcf\x11\xe0",),
    ".ppt": (b"\xd0\xcf\x11\xe0",),
    ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    ".wav": (b"RIFF",),
}


class InvalidUpload(ValueError):
    pass


def extension_of(filename: str) -> str:
    name = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def safe_name(filename: str) -> str:
    """The name as it will be shown and downloaded: no paths, no control
    characters, printable and at most 255 characters."""
    name = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = unicodedata.normalize("NFKC", name)
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:255] or "file"


def validate_upload(uploaded, *, allowed=None):
    """Check one UploadedFile. Returns (safe name, extension, content type).
    Raises InvalidUpload with a message fit for the API."""
    allowed = allowed or ALLOWED_TYPES
    name = safe_name(uploaded.name)
    ext = extension_of(name)
    if ext not in allowed:
        raise InvalidUpload(f"{ext or 'that type'} is not an accepted file type.")
    if uploaded.size == 0:
        raise InvalidUpload("The file is empty.")
    if uploaded.size > settings.ATTACHMENT_MAX_BYTES:
        limit = settings.ATTACHMENT_MAX_BYTES // (1024 * 1024)
        raise InvalidUpload(f"Files are limited to {limit} MB.")
    declared = (uploaded.content_type or "").split(";")[0].strip().lower()
    expected = ALLOWED_TYPES[ext]
    if declared and declared != "application/octet-stream" and declared not in expected:
        raise InvalidUpload(f"A {ext} file was sent as {declared}.")
    head = uploaded.read(8)
    uploaded.seek(0)
    if ext in MAGIC and not any(head.startswith(m) for m in MAGIC[ext]):
        raise InvalidUpload(f"That does not look like a {ext} file.")
    content_type = declared if declared in expected else sorted(expected)[0]
    return name, ext, content_type


def attachment_response(attachment):
    """Hand the bytes back as a download, never rendered inline."""
    response = FileResponse(
        attachment.file.open("rb"),
        as_attachment=True,
        filename=attachment.name,
        content_type=attachment.content_type,
    )
    # SOC2:API-10 user content is a download, not a page
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "private, no-store"
    return response


def read_transcript_text(uploaded, limit=400_000) -> str:
    """A transcript file's text, for the summariser. Capped so a stray
    multi-hour recording doesn't become a prompt."""
    raw = uploaded.read(limit)
    uploaded.seek(0)
    return raw.decode("utf-8", errors="replace")
