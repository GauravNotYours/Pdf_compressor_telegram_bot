import asyncio
import io
import logging
import os
import re
import sys

import pymupdf
from dotenv import load_dotenv
from gridfs import GridFSBucket
from PIL import Image
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from telegram import Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 50 * 1024 * 1024
DPI = 150
JPEG_QUALITY = 70

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def parse_allowed_usernames(value: str) -> set[str]:
    usernames = {
        item.strip().removeprefix("@").casefold()
        for item in value.split(",")
        if item.strip()
    }
    if not usernames or any(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username) is None
        for username in usernames
    ):
        raise ValueError(
            "ALLOWED_USERNAMES must contain comma-separated Telegram usernames "
            "(without @, 5-32 letters, numbers, or underscores)."
        )
    return usernames


def compress_pdf(pdf_data: bytes) -> bytes:
    source = pymupdf.open(stream=pdf_data, filetype="pdf")
    compressed = pymupdf.open()

    try:
        if source.is_encrypted:
            raise ValueError("Password-protected PDFs are not supported.")
        if source.page_count == 0:
            raise ValueError("The PDF contains no pages.")

        for page_number, page in enumerate(source, start=1):
            pixmap = page.get_pixmap(dpi=DPI, alpha=False)
            image = Image.frombytes(
                "RGB",
                (pixmap.width, pixmap.height),
                pixmap.samples,
            )
            image_data = io.BytesIO()
            image.save(
                image_data,
                format="JPEG",
                quality=JPEG_QUALITY,
                optimize=True,
            )

            output_page = compressed.new_page(
                width=page.rect.width,
                height=page.rect.height,
            )
            output_page.insert_image(output_page.rect, stream=image_data.getvalue())
            logger.info("Compressed page %d of %d", page_number, source.page_count)

        return compressed.tobytes(garbage=4, deflate=True)
    finally:
        source.close()
        compressed.close()


def authorized_private_message(update: Update, allowed_usernames: set[str]) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    return (
        user is not None
        and user.username is not None
        and user.username.casefold() in allowed_usernames
        and chat is not None
        and chat.type == ChatType.PRIVATE
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    allowed_usernames: set[str],
) -> None:
    message = update.effective_message
    if not message:
        return
    if not authorized_private_message(update, allowed_usernames):
        await message.reply_text("This bot is private. Contact its owner for access.")
        return

    context.user_data["bot_stopped"] = False
    await message.reply_text(
        "Send me a PDF and I will compress it and send it back. "
        "Only PDF documents are accepted."
    )


async def stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    allowed_usernames: set[str],
) -> None:
    message = update.effective_message
    if not message:
        return
    if not authorized_private_message(update, allowed_usernames):
        await message.reply_text("This bot is private. Contact its owner for access.")
        return

    context.user_data["bot_stopped"] = True
    await message.reply_text("Stopped. Send /start whenever you want to use the bot again.")


async def handle_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    allowed_usernames: set[str],
    bucket: GridFSBucket,
) -> None:
    message = update.effective_message
    if not message:
        return
    if not authorized_private_message(update, allowed_usernames):
        await message.reply_text("This bot is private. Contact its owner for access.")
        return
    if context.user_data.get("bot_stopped", False):
        await message.reply_text("The bot is stopped for you. Send /start to use it again.")
        return

    document = message.document
    if not document or not document.file_name or not document.file_name.lower().endswith(".pdf"):
        await message.reply_text("Please send a file with a .pdf extension.")
        return
    output_filename = f"compressed_{document.file_name}"
    if document.file_size is not None and document.file_size > MAX_INPUT_BYTES:
        await message.reply_text("The PDF is too large. The maximum size is 20 MB.")
        return

    try:
        telegram_file = await document.get_file()
        pdf_data = bytes(await telegram_file.download_as_bytearray())
    except TelegramError:
        logger.exception("Could not download PDF from Telegram")
        await message.reply_text("I could not download that file. Please try again.")
        return

    if len(pdf_data) > MAX_INPUT_BYTES:
        await message.reply_text("The PDF is too large. The maximum size is 20 MB.")
        return

    status_message = await message.reply_text("Compressing your PDF...")
    try:
        compressed_data = await asyncio.to_thread(compress_pdf, pdf_data)
    except (pymupdf.FileDataError, OSError, RuntimeError, ValueError) as exc:
        logger.warning("PDF compression failed: %s", exc)
        await status_message.edit_text(
            "I could not process that file. Please send a valid, unencrypted PDF."
        )
        return

    if len(compressed_data) > MAX_OUTPUT_BYTES:
        await status_message.edit_text(
            "The compressed PDF is too large to send through Telegram (50 MB maximum)."
        )
        return

    try:
        await message.reply_document(
            document=io.BytesIO(compressed_data),
            filename=output_filename,
            caption="Your compressed PDF is ready.",
        )
    except TelegramError:
        logger.exception("Could not send compressed PDF through Telegram")
        await status_message.edit_text(
            "Telegram could not send the compressed PDF. Please try again."
        )
        return

    try:
        await asyncio.to_thread(
            bucket.upload_from_stream,
            output_filename,
            io.BytesIO(compressed_data),
        )
    except PyMongoError:
        logger.exception("Could not store compressed PDF in MongoDB")
        await status_message.edit_text(
            "Your compressed PDF was sent, but I could not save it to the database."
        )
        return

    try:
        await status_message.delete()
    except TelegramError:
        logger.warning("Could not remove PDF compression status message")


async def handle_other_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    allowed_usernames: set[str],
) -> None:
    message = update.effective_message
    if not message:
        return
    if not authorized_private_message(update, allowed_usernames):
        await message.reply_text("This bot is private. Contact its owner for access.")
        return
    if context.user_data.get("bot_stopped", False):
        await message.reply_text("The bot is stopped for you. Send /start to use it again.")
        return
    await message.reply_text("Please send a PDF document.")


def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    mongo_uri = os.getenv("MONGODB_URI", "").strip()
    database_name = os.getenv("MONGODB_DATABASE", "").strip()

    if not token or not mongo_uri or not database_name:
        logger.error("Set BOT_TOKEN, MONGODB_URI, and MONGODB_DATABASE before starting.")
        sys.exit(1)

    try:
        allowed_usernames = parse_allowed_usernames(
            os.getenv("ALLOWED_USERNAMES", "")
        )
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        try:
            mongo_client.admin.command("ping")
        except PyMongoError:
            logger.exception(
                "Could not connect to MongoDB at startup; PDF delivery will continue "
                "but compressed files may not be saved"
            )
        bucket = GridFSBucket(mongo_client[database_name])

        application = Application.builder().token(token).build()
        application.add_handler(
            CommandHandler(
                "start",
                lambda update, context: start(update, context, allowed_usernames),
            )
        )
        application.add_handler(
            CommandHandler(
                "stop",
                lambda update, context: stop(update, context, allowed_usernames),
            )
        )
        application.add_handler(
            MessageHandler(
                filters.Document.ALL,
                lambda update, context: handle_document(
                    update,
                    context,
                    allowed_usernames,
                    bucket,
                ),
            )
        )
        application.add_handler(
            MessageHandler(
                filters.ALL & ~filters.COMMAND,
                lambda update, context: handle_other_message(
                    update,
                    context,
                    allowed_usernames,
                ),
            )
        )

        logger.info("Bot started; authorized Telegram usernames: %d", len(allowed_usernames))
        application.run_polling()
    except PyMongoError:
        logger.exception("Could not connect to MongoDB")
        sys.exit(1)
    finally:
        mongo_client.close()


if __name__ == "__main__":
    main()
