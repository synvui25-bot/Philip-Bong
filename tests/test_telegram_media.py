from io import BytesIO

from PIL import Image
import pytest

from scripts import telegram_media as media


def image_bytes(mode="RGBA", size=(2000, 1000), format="PNG"):
    output = BytesIO()
    Image.new(mode, size, "red").save(output, format)
    return output.getvalue()


def test_image_is_decoded_resized_and_atomically_saved_as_rgb_webp(tmp_path):
    destination = tmp_path / "20-0.webp"
    media.save_webp(image_bytes(), destination)
    with Image.open(destination) as result:
        assert result.format == "WEBP"
        assert result.mode == "RGB"
        assert result.size == (1800, 900)
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("content", [b"x" * (20 * 1024 * 1024 + 1), b"not an image", image_bytes(format="GIF")], ids=["oversized", "invalid", "gif"])
def test_invalid_oversized_or_unsupported_image_preserves_existing_file(tmp_path, content):
    destination = tmp_path / "20-0.webp"
    destination.write_bytes(b"old asset")
    with pytest.raises(media.MediaError):
        media.save_webp(content, destination)
    assert destination.read_bytes() == b"old asset"
    assert list(tmp_path.iterdir()) == [destination]


def test_failed_atomic_rename_preserves_existing_asset_and_cleans_temp(monkeypatch, tmp_path):
    destination = tmp_path / "20-0.webp"
    destination.write_bytes(b"old asset")
    def fail(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(media.os, "replace", fail)
    with pytest.raises(media.MediaError):
        media.save_webp(image_bytes(), destination)
    assert destination.read_bytes() == b"old asset"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("url", ["http://cdn1.cdn-telegram.org/x", "https://127.0.0.1/x", "https://cdn-telegram.org.evil.com/x", "https://user:secret@cdn1.cdn-telegram.org/x"])
def test_untrusted_media_url_rejected_before_fetch(url):
    with pytest.raises(media.MediaError):
        media.download_public_media(url)


class Response:
    def __init__(self, content, content_type="image/png"):
        from email.message import Message
        self.content = content
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.content[:limit] if limit >= 0 else self.content


def test_download_bounds_response_and_checks_content_type(monkeypatch):
    from types import SimpleNamespace
    for response in [Response(b"x", "text/html"), Response(b"x" * (media.MAX_BYTES + 1))]:
        monkeypatch.setattr(media, "build_opener", lambda *args: SimpleNamespace(open=lambda *a, **kw: response))
        with pytest.raises(media.MediaError):
            media.download_public_media("https://cdn1.cdn-telegram.org/a.jpg")


def test_bot_file_ids_resolve_to_safe_file_download(monkeypatch):
    from types import SimpleNamespace
    responses = {
        "https://api.telegram.org/botsecret/getFile?file_id=photo+one": Response(b'{"ok": true, "result": {"file_path": "photos/file_20.jpg"}}', "application/json"),
        "https://api.telegram.org/file/botsecret/photos/file_20.jpg": Response(image_bytes()),
    }
    monkeypatch.setattr(media, "build_opener", lambda *args: SimpleNamespace(open=lambda req, **kw: responses[req.full_url]))
    assert media.download_bot_media("secret", "photo one") == image_bytes()


@pytest.mark.parametrize("file_path", ["../private", "https://evil.example/a.jpg", "photos/../../x", "photos/x?token=secret"])
def test_bot_file_path_cannot_escape_constructed_api_url(monkeypatch, file_path):
    import json
    from types import SimpleNamespace
    response = Response(json.dumps({"ok": True, "result": {"file_path": file_path}}).encode(), "application/json")
    monkeypatch.setattr(media, "build_opener", lambda *args: SimpleNamespace(open=lambda *a, **kw: response))
    with pytest.raises(media.MediaError) as error:
        media.download_bot_media("secret", "p1")
    assert "secret" not in str(error.value)


def test_decompression_bomb_is_rejected_before_writing(monkeypatch, tmp_path):
    content = image_bytes()
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(media.MediaError):
        media.save_webp(content, tmp_path / "20-0.webp")
    assert list(tmp_path.iterdir()) == []
