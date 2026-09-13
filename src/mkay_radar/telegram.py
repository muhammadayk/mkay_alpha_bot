from __future__ import annotations

import html
import os
import re
import time

import requests


def format_review(opportunity: dict) -> str:
    lines = [
        "<b>🆕 REVIEW: Web3 opportunity</b>",
        f"<b>{html.escape(opportunity['title'])}</b>",
        f"Category: {html.escape(opportunity['category'])} · Source: {html.escape(opportunity['source'])}",
    ]
    for label, key in (("Status", "source_status"), ("Chain", "chain"), ("Reward", "reward"), ("What to do", "requirements"), ("Details", "description")):
        if opportunity.get(key):
            value = str(opportunity[key])[:700]
            lines.append(f"<b>{label}:</b> {html.escape(value)}")
    lines += [f"<a href=\"{html.escape(opportunity['url'], quote=True)}\">Open original guide</a>", "\n⚠️ Review manually before sharing. Never enter a seed phrase."]
    return "\n".join(lines)[:4000]


def format_approved_alpha(opportunity: dict) -> str:
    actions = _action_bullets(opportunity.get("requirements"))
    lines = [
        "<b>🚀 MKAY Alpha</b>",
        "",
        f"<b>{html.escape(opportunity['title'])}</b> is an active {html.escape(opportunity['category'].lower())} opportunity.",
        "",
        "<b>What to do:</b>",
    ]
    lines.extend(f"• {html.escape(action)}" for action in actions)
    for label, key in (("Chain", "chain"), ("Reward", "reward"), ("Status", "source_status")):
        if opportunity.get(key):
            lines.append(f"<b>{label}:</b> {html.escape(str(opportunity[key])[:160])}")
    lines += ["", f"🔗 <a href=\"{html.escape(opportunity['url'], quote=True)}\">Open the official guide</a>", "", "<i>DYOR. Never share your seed phrase.</i>"]
    return "\n".join(lines)[:4000]


def _action_bullets(requirements: str | None) -> list[str]:
    """Turn a source's compact action list into a maximum of three clear bullets."""
    if not requirements:
        return ["Review the official guide and confirm your eligibility."]
    chunks = re.split(r"\s*(?:,|;|\n)\s*", requirements)
    actions = [chunk.strip(" .") for chunk in chunks if chunk.strip(" .")]
    return actions[:3] or ["Review the official guide and confirm your eligibility."]


class TelegramReviewPublisher:
    def __init__(self) -> None:
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_REVIEW_CHAT_ID")

    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_review(self, opportunity: dict) -> None:
        if not self.configured():
            raise RuntimeError("Telegram is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_REVIEW_CHAT_ID.")
        response = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_review(opportunity),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": [[
                    {"text": "✅ Approve", "callback_data": f"approve:{opportunity['id']}"},
                    {"text": "❌ Reject", "callback_data": f"reject:{opportunity['id']}"},
                ]]},
            },
            timeout=30,
        )
        response.raise_for_status()

    def _api(self, method: str, **kwargs):
        response = requests.request(method, f"https://api.telegram.org/bot{self.token}/{kwargs.pop('endpoint')}", timeout=30, **kwargs)
        response.raise_for_status()
        return response.json()

    def _is_group_admin(self, chat_id: int, user_id: int) -> bool:
        result = self._api("GET", endpoint="getChatMember", params={"chat_id": chat_id, "user_id": user_id})
        return result.get("result", {}).get("status") in {"administrator", "creator", "owner"}

    def _is_bot_admin(self, chat_id: int) -> bool:
        me = self._api("GET", endpoint="getMe").get("result", {})
        return bool(me) and self._is_group_admin(chat_id, me["id"])

    def _send_alpha_to_groups(self, store, opportunity: dict) -> int:
        sent = 0
        try:
            chats = store.broadcast_chats()
        except requests.RequestException:
            # An approval must still complete even if broadcast setup is unfinished.
            return sent
        for chat in chats:
            try:
                self._api("POST", endpoint="sendMessage", json={"chat_id": chat["chat_id"], "text": format_approved_alpha(opportunity), "parse_mode": "HTML", "disable_web_page_preview": True})
                sent += 1
            except requests.RequestException:
                continue
        return sent

    def process_updates(self, store, timeout: int = 0) -> int:
        if not self.configured():
            raise RuntimeError("Telegram is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_REVIEW_CHAT_ID.")
        updates = self._api("GET", endpoint="getUpdates", params={"timeout": timeout}).get("result", [])
        processed = 0
        last_update_id = None
        for update in updates:
            last_update_id = update.get("update_id")
            callback = update.get("callback_query")
            if callback:
                try:
                    action, opportunity_id = callback.get("data", "").split(":", 1)
                    status = {"approve": "APPROVED", "reject": "REJECTED"}.get(action)
                    if not status:
                        continue
                    store.set_status(opportunity_id, status)
                    message = callback.get("message", {})
                    opportunity = store.get_opportunity(opportunity_id)
                    sent = self._send_alpha_to_groups(store, opportunity) if status == "APPROVED" and opportunity else 0
                    self._api("POST", endpoint="answerCallbackQuery", json={"callback_query_id": callback["id"], "text": f"{status.title()}. Sent to {sent} group(s)."})
                    self._api("POST", endpoint="editMessageReplyMarkup", json={"chat_id": message.get("chat", {}).get("id"), "message_id": message.get("message_id"), "reply_markup": {"inline_keyboard": []}})
                    processed += 1
                except (ValueError, StopIteration, requests.RequestException):
                    continue
                continue
            message = update.get("message") or update.get("channel_post")
            if message and message.get("text", "").startswith("/enable_broadcast"):
                chat = message["chat"]
                sender = message.get("from", {})
                try:
                    if chat.get("type") not in {"group", "supergroup", "channel"}:
                        self._api("POST", endpoint="sendMessage", json={"chat_id": chat["id"], "text": "Use this command inside a group or channel."})
                    elif not sender or not self._is_group_admin(chat["id"], sender["id"]):
                        self._api("POST", endpoint="sendMessage", json={"chat_id": chat["id"], "text": "Only a group administrator can enable broadcasts."})
                    elif not self._is_bot_admin(chat["id"]):
                        self._api("POST", endpoint="sendMessage", json={"chat_id": chat["id"], "text": "Make me a group administrator first, then run this command again."})
                    else:
                        store.register_broadcast_chat(chat["id"], chat.get("title"), chat["type"])
                        self._api("POST", endpoint="sendMessage", json={"chat_id": chat["id"], "text": "✅ This group is registered for approved MKAY Alpha posts."})
                        processed += 1
                except (KeyError, requests.RequestException) as error:
                    print(f"Could not register a Telegram group: {error}")
                    continue
        if last_update_id is not None:
            self._api("GET", endpoint="getUpdates", params={"offset": last_update_id + 1})
        return processed

    def listen(self, store) -> None:
        while True:
            self.process_updates(store, timeout=25)
            time.sleep(1)
