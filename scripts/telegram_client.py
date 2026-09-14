import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TelegramError(RuntimeError):
    """Raised when the Telegram Bot API reports a failure."""


def _normalized_token(token: str) -> str:
    token = token.strip()
    if not token:
        raise TelegramError("Telegram bot token is required")
    if any(ord(character) < 33 or ord(character) == 127 for character in token):
        raise TelegramError("Telegram bot token contains invalid characters")
    return token


def _request(token: str, method: str, params: dict | None = None):
    token = _normalized_token(token)
    query = f"?{urlencode(params)}" if params else ""
    request = Request(f"https://api.telegram.org/bot{token}/{method}{query}")
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not payload.get("ok"):
        raise TelegramError(payload.get("description", "Telegram request failed"))
    return payload.get("result")


def fetch_bot_channel_status(token: str, channel_username: str) -> dict:
    bot = _request(token, "getMe")
    if not isinstance(bot, dict) or not isinstance(bot.get("id"), int):
        raise TelegramError("Telegram getMe response did not contain a bot identity")
    membership = _request(
        token,
        "getChatMember",
        {"chat_id": f"@{channel_username}", "user_id": bot["id"]},
    )
    if not isinstance(membership, dict) or not isinstance(membership.get("status"), str):
        raise TelegramError("Telegram getChatMember response did not contain a status")
    return {
        "bot_id": bot["id"],
        "bot_username": bot.get("username", ""),
        "channel_status": membership["status"],
    }


def fetch_updates(token: str, offset: int) -> list[dict]:
    token = _normalized_token(token)

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

