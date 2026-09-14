"""Bounded Telegram image downloads and atomic, decoded WebP assets."""

from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES = 20 * 1024 * 1024


class MediaError(RuntimeError):
    """An image could not be safely fetched or decoded."""


def validate_public_media_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        allowed = any(host == domain or host.endswith("." + domain)
                      for domain in ("cdn-telegram.org", "telegram-cdn.org", "telesco.pe"))
        if (parsed.scheme != "https" or not allowed or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            raise ValueError
    except (TypeError, ValueError):
        raise MediaError("untrusted Telegram media URL") from None
    return url


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MediaError("media redirect refused")


def download_public_media(url: str) -> bytes:
    validate_public_media_url(url)
    return download_image(url)


def download_bot_media(token: str, file_id: str) -> bytes:
    if not token:
        raise MediaError("Telegram bot token is required")
    try:
        url = f"https://api.telegram.org/bot{token}/getFile?{urlencode({'file_id': file_id})}"
        with build_opener(_NoRedirect()).open(Request(url), timeout=30) as response:
            payload = json.loads(response.read(1024 * 1024 + 1))
        if not payload.get("ok"):
            raise MediaError("Telegram file lookup failed")
        file_path = payload["result"]["file_path"]
        if (not isinstance(file_path, str) or not re.fullmatch(r"[A-Za-z0-9_/-]+\.[A-Za-z0-9]+", file_path)
                or file_path.startswith("/") or ".." in file_path):
            raise MediaError("unsafe Telegram file path")
        return download_image(
            f"https://api.telegram.org/file/bot{token}/{file_path}",
            allow_octet_stream=True,
        )
    except MediaError:
        raise
    except Exception:
        raise MediaError("Telegram file lookup failed") from None


def download_image(url: str, *, allow_octet_stream: bool = False) -> bytes:
    """Transport for a URL already validated or constructed by the caller."""
    try:
        with build_opener(_NoRedirect()).open(Request(url), timeout=30) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ALLOWED_TYPES and not (
                allow_octet_stream and content_type == "application/octet-stream"
            ):
                raise MediaError("unsupported media content type")
            content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise MediaError("media exceeds 20 MB")
        return content
    except MediaError:
        raise
    except Exception:
        # Request URLs may contain Bot API credentials; never include them in errors.
        raise MediaError("Telegram media download failed") from None


def save_webp(content: bytes, destination: Path) -> None:
    if len(content) > MAX_BYTES:
        raise MediaError("media exceeds 20 MB")
    temporary = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise MediaError("unsupported image format")
                source.verify()
            with Image.open(BytesIO(content)) as source:
                picture = ImageOps.exif_transpose(source).convert("RGB")
                picture.thumbnail((1800, 1800))
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    picture.save(handle, "WEBP", quality=84, method=6)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
    except MediaError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise MediaError("invalid image or media write failure") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

