import logging
import time
import os
import threading
from io import BytesIO
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN
from database import init_database


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-type",
            "text/plain"
        )
        self.end_headers()

        self.wfile.write(
            b"Rose Guard is running!"
        )

    def log_message(self, format, *args):
        pass


def start_health_server():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Health server running on port {port}"
    )

    server.serve_forever()


# =========================================================
# OFFICIAL ROSE
# =========================================================

TRUSTED_ROSE_IDS = {
    609517172
}


# =========================================================
# ROSE NAMES
# =========================================================

ROSE_NAME_WORDS = {
    "rose",
    "rose bot",
    "rosebot",
    "miss rose",
    "missrose",
    "rose guard",
}

ROSE_USERNAME_WORDS = {
    "rose",
    "rosebot",
    "rose_bot",
    "missrose",
    "miss_rose",
}


# =========================================================
# PHOTO MATCHING
# =========================================================

PHOTO_MATCH_THRESHOLD = 0.10


# =========================================================
# ADMIN CACHE
# =========================================================

# Refresh admin information every 60 seconds
ADMIN_CACHE_TIME = 60

ADMIN_CACHE = {}


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# PHOTO HASH
# =========================================================

def make_photo_hash(image_bytes: bytes):

    image = Image.open(
        BytesIO(image_bytes)
    ).convert("RGB")

    image = image.resize((32, 32))
    image = image.convert("L")

    pixels = list(image.getdata())

    average = sum(pixels) / len(pixels)

    return tuple(
        1 if pixel >= average else 0
        for pixel in pixels
    )


def hash_difference(hash1, hash2):

    if not hash1 or not hash2:
        return 1.0

    if len(hash1) != len(hash2):
        return 1.0

    different = sum(
        a != b
        for a, b in zip(hash1, hash2)
    )

    return different / len(hash1)


# =========================================================
# GET USER PROFILE PHOTO
# =========================================================

async def get_profile_photo_hash(
    bot,
    user_id: int
):

    try:

        photos = await bot.get_user_profile_photos(
            user_id=user_id,
            offset=0,
            limit=1,
        )

        if not photos.photos:
            return None

        photo_sizes = photos.photos[0]

        largest_photo = photo_sizes[-1]

        telegram_file = await bot.get_file(
            largest_photo.file_id
        )

        photo_data = BytesIO()

        await telegram_file.download_to_memory(
            photo_data
        )

        return make_photo_hash(
            photo_data.getvalue()
        )

    except Exception as error:

        logger.warning(
            "PROFILE PHOTO ERROR | USER=%s | %s",
            user_id,
            error,
        )

        return None


# =========================================================
# LOAD OFFICIAL ROSE PHOTOS
# =========================================================

OFFICIAL_ROSE_HASHES = set()


async def load_rose_photos(
    application: Application
):

    global OFFICIAL_ROSE_HASHES

    try:

        photos = await application.bot.get_user_profile_photos(
            user_id=609517172,
            offset=0,
            limit=100,
        )

        if not photos.photos:

            logger.warning(
                "Could not load official Rose photos."
            )

            return

        for photo_sizes in photos.photos:

            largest_photo = photo_sizes[-1]

            telegram_file = await application.bot.get_file(
                largest_photo.file_id
            )

            photo_data = BytesIO()

            await telegram_file.download_to_memory(
                photo_data
            )

            photo_hash = make_photo_hash(
                photo_data.getvalue()
            )

            OFFICIAL_ROSE_HASHES.add(
                photo_hash
            )

        logger.info(
            "Loaded %s Rose photo fingerprints.",
            len(OFFICIAL_ROSE_HASHES),
        )

    except Exception as error:

        logger.error(
            "ROSE PHOTO LOAD ERROR: %s",
            error,
        )


# =========================================================
# CHECK PHOTO AGAINST REFERENCES
# =========================================================

def photo_matches(
    user_hash,
    reference_hashes
):

    if not user_hash:
        return False

    for reference_hash in reference_hashes:

        difference = hash_difference(
            user_hash,
            reference_hash
        )

        if difference <= PHOTO_MATCH_THRESHOLD:

            return True

    return False


# =========================================================
# ROSE NAME CHECK
# =========================================================

def is_rose_name(user):

    first_name = (
        user.first_name or ""
    ).strip().lower()

    last_name = (
        user.last_name or ""
    ).strip().lower()

    username = (
        user.username or ""
    ).strip().lower()

    full_name = (
        f"{first_name} {last_name}"
    ).strip()

    if full_name in ROSE_NAME_WORDS:
        return True

    if first_name in ROSE_NAME_WORDS:
        return True

    if "rose bot" in full_name:
        return True

    if "rosebot" in full_name:
        return True

    if "miss rose" in full_name:
        return True

    if username in ROSE_USERNAME_WORDS:
        return True

    if "rosebot" in username:
        return True

    if "rose_bot" in username:
        return True

    if "missrose" in username:
        return True

    return False


# =========================================================
# GET ALL GROUP ADMINS
# =========================================================

async def get_group_admins(
    bot,
    chat_id: int
):

    now = time.time()

    cached = ADMIN_CACHE.get(chat_id)

    # Use cache for 60 seconds
    if cached:

        if now - cached["time"] < ADMIN_CACHE_TIME:

            return cached["admins"]

    try:

        administrators = await bot.get_chat_administrators(
            chat_id=chat_id
        )

        admins = {}

        for admin in administrators:

            user = admin.user

            first_name = (
                user.first_name or ""
            ).strip().lower()

            last_name = (
                user.last_name or ""
            ).strip().lower()

            full_name = (
                f"{first_name} {last_name}"
            ).strip()

            username = (
                user.username or ""
            ).strip().lower()

            photo_hash = await get_profile_photo_hash(
                bot,
                user.id
            )

            admins[user.id] = {
                "user_id": user.id,
                "first_name": first_name,
                "last_name": last_name,
                "full_name": full_name,
                "username": username,
                "photo_hash": photo_hash,
                "status": admin.status,
            }

        ADMIN_CACHE[chat_id] = {
            "time": now,
            "admins": admins,
        }

        logger.info(
            "ADMIN LIST UPDATED | CHAT=%s | ADMINS=%s",
            chat_id,
            len(admins),
        )

        return admins

    except Exception as error:

        logger.error(
            "ADMIN LIST ERROR | CHAT=%s | %s",
            chat_id,
            error,
        )

        return {}


# =========================================================
# ADMIN NAME IMPERSONATION
# =========================================================

def find_admin_name_impersonation(
    user,
    admins
):

    user_id = user.id

    user_first = (
        user.first_name or ""
    ).strip().lower()

    user_last = (
        user.last_name or ""
    ).strip().lower()

    user_full = (
        f"{user_first} {user_last}"
    ).strip()

    for admin_id, admin in admins.items():

        # REAL ADMIN
        if user_id == admin_id:
            continue

        # Ignore empty names
        if not admin["full_name"]:
            continue

        # -------------------------------------------------
        # EXACT FULL NAME
        # -------------------------------------------------

        if user_full == admin["full_name"]:

            return (
                True,
                f"Copied admin name: {admin['full_name']}"
            )

        # -------------------------------------------------
        # SAME FIRST NAME
        # -------------------------------------------------

        if (
            user_first
            and user_first == admin["first_name"]
        ):

            return (
                True,
                f"Copied admin first name: {admin['first_name']}"
            )

    return False, ""


# =========================================================
# ADMIN PHOTO IMPERSONATION
# =========================================================

async def find_admin_photo_impersonation(
    bot,
    user_id: int,
    admins
):

    user_hash = await get_profile_photo_hash(
        bot,
        user_id
    )

    if not user_hash:
        return False, ""

    for admin_id, admin in admins.items():

        # Never compare a real admin against himself
        if user_id == admin_id:
            continue

        admin_hash = admin.get(
            "photo_hash"
        )

        if not admin_hash:
            continue

        difference = hash_difference(
            user_hash,
            admin_hash
        )

        logger.info(
            "ADMIN PHOTO CHECK | USER=%s | ADMIN=%s | DIFF=%.3f",
            user_id,
            admin_id,
            difference,
        )

        if difference <= PHOTO_MATCH_THRESHOLD:

            return (
                True,
                f"Copied admin profile photo: {admin['full_name']}"
            )

    return False, ""


# =========================================================
# BAN
# =========================================================

async def ban_impersonator(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    reason: str
):

    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user or not chat:
        return

    user_id = user.id

    full_name = (
        f"{user.first_name or ''} "
        f"{user.last_name or ''}"
    ).strip()

    username = user.username or "none"

    logger.warning(
        "IMPERSONATOR FOUND | ID=%s | NAME=%s | REASON=%s",
        user_id,
        full_name,
        reason,
    )

    # =====================================================
    # DELETE MESSAGE
    # =====================================================

    try:

        await message.delete()

        logger.info(
            "MESSAGE DELETED | USER=%s",
            user_id,
        )

    except Exception as error:

        logger.error(
            "DELETE FAILED | USER=%s | %s",
            user_id,
            error,
        )

    # =====================================================
    # BAN USER
    # =====================================================

    try:

        await context.bot.ban_chat_member(
            chat_id=chat.id,
            user_id=user_id,
        )

        logger.warning(
            "USER BANNED | USER=%s",
            user_id,
        )

    except Exception as error:

        logger.error(
            "BAN FAILED | USER=%s | %s",
            user_id,
            error,
        )

    # =====================================================
    # ALERT
    # =====================================================

    try:

        await context.bot.send_message(
            chat_id=chat.id,

            text=(
                "🚨 IMPERSONATION DETECTED\n\n"

                f"👤 Name: {full_name}\n"
                f"🔗 Username: @{username}\n"
                f"🆔 User ID: {user_id}\n\n"

                f"🔎 {reason}\n\n"

                "🗑️ Message deleted\n"
                "🔨 User banned"
            ),
        )

    except Exception as error:

        logger.error(
            "ALERT FAILED | %s",
            error,
        )


# =========================================================
# MAIN MESSAGE CHECK
# =========================================================

async def check_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.effective_message

    if not message:
        return

    user = update.effective_user

    if not user:
        return

    chat = update.effective_chat

    if not chat:
        return

    user_id = user.id

    # =====================================================
    # OFFICIAL ROSE
    # =====================================================

    if user_id in TRUSTED_ROSE_IDS:

        logger.info(
            "OFFICIAL ROSE | %s",
            user_id,
        )

        return

    # =====================================================
    # GET GROUP ADMINS
    # =====================================================

    admins = await get_group_admins(
        context.bot,
        chat.id
    )

    # =====================================================
    # REAL ADMIN = ALWAYS ALLOWED
    # =====================================================

    if user_id in admins:

        logger.info(
            "REAL ADMIN | ID=%s | NAME=%s",
            user_id,
            admins[user_id]["full_name"],
        )

        return

    # =====================================================
    # ROSE NAME
    # =====================================================

    rose_name = is_rose_name(user)

    # =====================================================
    # ROSE PHOTO
    # =====================================================

    rose_photo = False

    if not rose_name:

        current_hash = await get_profile_photo_hash(
            context.bot,
            user_id
        )

        rose_photo = photo_matches(
            current_hash,
            OFFICIAL_ROSE_HASHES
        )

    # =====================================================
    # FAKE ROSE
    # =====================================================

    if rose_name or rose_photo:

        if rose_name and rose_photo:

            reason = (
                "Fake Rose: Rose name + Rose profile photo"
            )

        elif rose_name:

            reason = (
                "Fake Rose: Rose name/username"
            )

        else:

            reason = (
                "Fake Rose: Rose profile photo"
            )

        await ban_impersonator(
            update,
            context,
            reason
        )

        return

    # =====================================================
    # FAKE ADMIN / OWNER NAME
    # =====================================================

    name_match, name_reason = (
        find_admin_name_impersonation(
            user,
            admins
        )
    )

    # =====================================================
    # FAKE ADMIN / OWNER PHOTO
    # =====================================================

    photo_match = False
    photo_reason = ""

    if not name_match:

        photo_match, photo_reason = (
            await find_admin_photo_impersonation(
                context.bot,
                user_id,
                admins
            )
        )

    # =====================================================
    # FAKE ADMIN FOUND
    # =====================================================

    if name_match or photo_match:

        if name_match and photo_match:

            reason = (
                f"{name_reason} + copied admin profile photo"
            )

        elif name_match:

            reason = name_reason

        else:

            reason = photo_reason

        await ban_impersonator(
            update,
            context,
            reason
        )

        return

    # =====================================================
    # NORMAL USER
    # =====================================================

    logger.info(
        "NORMAL USER | ID=%s | NAME=%s",
        user_id,
        (
            f"{user.first_name or ''} "
            f"{user.last_name or ''}"
        ).strip(),
    )


# =========================================================
# COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.message:

        await update.message.reply_text(
            "🛡️ Rose Guard is online."
        )


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.message:

        await update.message.reply_text(
            "✅ Rose Guard is running."
        )


# =========================================================
# ERROR
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "BOT ERROR:",
        exc_info=context.error
    )


# =========================================================
# STARTUP
# =========================================================

async def post_init(
    application: Application
):

    print("Loading official Rose...")

    await load_rose_photos(
        application
    )


# =========================================================
# MAIN
# =========================================================

def main():

    init_database()

    # =====================================================
    # START RENDER HEALTH SERVER
    # =====================================================

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    # =====================================================
    # TELEGRAM BOT
    # =====================================================

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "status",
            status
        )
    )

    # ALL messages
    app.add_handler(
        MessageHandler(
            filters.ALL,
            check_message
        )
    )

    app.add_error_handler(
        error_handler
    )

    print("================================")
    print("       ROSE GUARD STARTED")
    print("================================")
    print("Official Rose ID: 609517172")
    print("Admin/Owner protection: ON")
    print("Rose protection: ON")
    print("Render health server: ON")
    print("Bot is running...")
    print("Press CTRL+C to stop.")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()