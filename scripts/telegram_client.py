import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TelegramError(RuntimeError):
    """Raised when the Telegram Bot API reports a failure."""


def fetch_updates(token: str, offset: int) -> list[dict]:
    token = token.strip()
    if not token:
        raise TelegramError("Telegram bot token is required")
    if any(ord(character) < 33 or ord(character) == 127 for character in token):
        raise TelegramError("Telegram bot token contains invalid characters")

    query = urlencode(
        {
            "offset": offset,
            "timeout": 0,
            "allowed_updates": json.dumps(["channel_post", "edited_channel_post"]),
        }
    )
    request = Request(f"https://api.telegram.org/bot{token}/getUpdates?{query}")
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)

    if not payload.get("ok"):
        raise TelegramError(payload.get("description", "Telegram request failed"))

    result = payload.get("result")
    if not isinstance(result, list):
        raise TelegramError("Telegram response did not contain updates")
    return result

