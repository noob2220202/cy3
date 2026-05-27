import json
import logging
import os
import re

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

CONFIG_FILE = "config.json"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {"source_group": None, "target_group": None, "topic_id": None}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception:
        return False


# ── Photo forward ─────────────────────────────────────────────────────────────

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = load_config()
    source = config.get("source_group")
    target = config.get("target_group")
    topic = config.get("topic_id")

    if not source or not target:
        return

    chat_id = update.effective_chat.id
    if chat_id != source:
        return

    msg = update.effective_message
    try:
        kwargs = dict(
            chat_id=target,
            from_chat_id=chat_id,
            message_id=msg.message_id,
        )
        if topic:
            kwargs["message_thread_id"] = topic
        await context.bot.forward_message(**kwargs)
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception as e:
        logger.error("Photo forward/delete error: %s", e)


# ── Ban command ────────────────────────────────────────────────────────────────

async def ban_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat_id = update.effective_chat.id

    if not await is_admin(update, context):
        return

    text = msg.text or ""
    # strip ".ban" prefix
    after_cmd = text[len(".ban"):].strip()

    target_id = None
    target_name = None
    reason = None

    # Case 1: reply to a message
    if msg.reply_to_message:
        target_user = msg.reply_to_message.from_user
        if target_user:
            target_id = target_user.id
            target_name = target_user.full_name
        reason = after_cmd if after_cmd else None

    # Case 2: mention via entity (text_mention or @username)
    else:
        entities = msg.entities or []
        for entity in entities:
            if entity.type == "text_mention" and entity.user:
                target_id = entity.user.id
                target_name = entity.user.full_name
                end = entity.offset + entity.length
                reason = text[end:].strip() or None
                break
            elif entity.type == "mention":
                mention_text = text[entity.offset : entity.offset + entity.length]
                username = mention_text.lstrip("@")
                try:
                    chat_member = await context.bot.get_chat(f"@{username}")
                    target_id = chat_member.id
                    target_name = chat_member.full_name
                except Exception:
                    target_name = mention_text
                end = entity.offset + entity.length
                reason = text[end:].strip() or None
                break

    if target_id is None:
        await msg.reply_text(
            "사용법:\n"
            "  `.ban @유저 [사유]` — 유저 멘션\n"
            "  `.ban [사유]` — 메시지 답장",
            parse_mode="Markdown",
        )
        return

    # Check target is not admin
    try:
        target_member = await context.bot.get_chat_member(chat_id, target_id)
        if target_member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            await msg.reply_text("관리자는 밴할 수 없습니다.")
            return
    except Exception:
        pass

    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=target_id)
    except Exception as e:
        await msg.reply_text(f"밴 실패: {e}")
        return

    display_name = target_name or str(target_id)
    reason_text = reason if reason else "사유 없음"
    await msg.reply_text(f"🔨 {display_name} 님이 밴되었습니다.\n사유: {reason_text}")

    try:
        await msg.delete()
    except Exception:
        pass


# ── Config commands ────────────────────────────────────────────────────────────

async def set_source_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return
    config = load_config()
    config["source_group"] = update.effective_chat.id
    save_config(config)
    await update.effective_message.reply_text(
        f"✅ 이 그룹을 사진 수신 그룹(A)으로 설정했습니다.\nID: `{update.effective_chat.id}`",
        parse_mode="Markdown",
    )


async def set_target_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return
    config = load_config()
    config["target_group"] = update.effective_chat.id
    save_config(config)
    await update.effective_message.reply_text(
        f"✅ 이 그룹을 사진 전달 그룹(B)으로 설정했습니다.\nID: `{update.effective_chat.id}`",
        parse_mode="Markdown",
    )


async def set_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return
    msg = update.effective_message
    text = msg.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await msg.reply_text("사용법: `.settopic <토픽ID>`", parse_mode="Markdown")
        return
    topic_id = int(parts[1])
    config = load_config()
    config["topic_id"] = topic_id
    save_config(config)
    await msg.reply_text(
        f"✅ 전달 토픽 ID를 `{topic_id}`로 설정했습니다.",
        parse_mode="Markdown",
    )


async def show_config_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return
    config = load_config()
    source = config.get("source_group") or "미설정"
    target = config.get("target_group") or "미설정"
    topic = config.get("topic_id") or "미설정"
    await update.effective_message.reply_text(
        f"⚙️ 현재 설정\n"
        f"• 수신 그룹(A): `{source}`\n"
        f"• 전달 그룹(B): `{target}`\n"
        f"• 전달 토픽 ID: `{topic}`",
        parse_mode="Markdown",
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")

    app = Application.builder().token(BOT_TOKEN).build()

    # Photo forwarding
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.GROUPS, photo_handler))

    # Config commands (dot-prefixed, groups only)
    app.add_handler(
        MessageHandler(
            filters.Regex(re.compile(r"^\.setsource(\s|$)", re.I)) & filters.ChatType.GROUPS,
            set_source_handler,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.Regex(re.compile(r"^\.settarget(\s|$)", re.I)) & filters.ChatType.GROUPS,
            set_target_handler,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.Regex(re.compile(r"^\.settopic(\s|$)", re.I)) & filters.ChatType.GROUPS,
            set_topic_handler,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.Regex(re.compile(r"^\.showconfig(\s|$)", re.I)) & filters.ChatType.GROUPS,
            show_config_handler,
        )
    )

    # Ban command
    app.add_handler(
        MessageHandler(
            filters.Regex(re.compile(r"^\.ban(\s|$)", re.I)) & filters.ChatType.GROUPS,
            ban_handler,
        )
    )

    logger.info("봇 시작")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
