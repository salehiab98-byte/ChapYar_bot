import os
import io
import math
import logging
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")

# قیمت‌ها را می‌توانی بعداً فقط از همین بخش تغییر بدهی.
# مبلغ‌ها به ریال هستند.
PRICES = {
    "black": {
        "single": {"A4": 5000, "A5": 4000},
        "double": {"A4": 8000, "A5": 6500},
    },
    "color": {
        "single": {"A4": 20000, "A5": 15000},
        "double": {"A4": 35000, "A5": 27000},
    },
}

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

def money(n: int) -> str:
    return f"{n:,} ریال"

def keyboard(rows):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for text, data in row]
         for row in rows]
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["order"] = {"files": [], "total_units": 0}
    await update.message.reply_text(
        "سلام 👋\nبه چاپ‌یار خوش آمدید.\n\n"
        "اول نوع چاپ را انتخاب کنید:"
    )
    await update.message.reply_text(
        "سیاه‌وسفید یا رنگی؟",
        reply_markup=keyboard([
            [("⚫ سیاه‌وسفید", "color:black"), ("🌈 رنگی", "color:color")]
        ])
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("سفارش لغو شد. برای شروع دوباره /start را بزنید.")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    order = context.user_data.setdefault("order", {"files": [], "total_units": 0})

    if data.startswith("color:"):
        order["color"] = data.split(":")[1]
        await q.edit_message_text(
            "چاپ یک‌رو می‌خواهید یا پشت‌ورو؟",
            reply_markup=keyboard([
                [("📄 یک‌رو", "side:single"), ("📑 پشت‌ورو", "side:double")]
            ])
        )
    elif data.startswith("side:"):
        order["side"] = data.split(":")[1]
        await q.edit_message_text(
            "سایز کاغذ را انتخاب کنید:",
            reply_markup=keyboard([
                [("A4", "size:A4"), ("A5", "size:A5")]
            ])
        )
    elif data.startswith("size:"):
        order["size"] = data.split(":")[1]
        await q.edit_message_text(
            f"انتخاب شما: {order['size']} / "
            f"{'رنگی' if order['color']=='color' else 'سیاه‌وسفید'} / "
            f"{'پشت‌ورو' if order['side']=='double' else 'یک‌رو'}\n\n"
            "حالا فایل‌های PDF یا عکس‌های موردنظر برای چاپ را ارسال کنید.\n"
            "می‌توانید چند فایل پشت‌سرهم بفرستید.\n\n"
            "وقتی تمام شد، دکمه «محاسبه قیمت» را بزنید.",
            reply_markup=keyboard([[("🧮 محاسبه قیمت", "calc")],
                                   [("❌ لغو سفارش", "cancel")]])
        )
    elif data == "calc":
        if not order.get("files"):
            await q.answer("هنوز فایلی دریافت نشده است.", show_alert=True)
            return

        units = order["total_units"]
        price_each = PRICES[order["color"]][order["side"]][order["size"]]
        total = units * price_each
        order["total"] = total

        details = (
            f"🧾 خلاصه سفارش\n\n"
            f"نوع چاپ: {'رنگی' if order['color']=='color' else 'سیاه‌وسفید'}\n"
            f"نوع چاپ: {'پشت‌ورو' if order['side']=='double' else 'یک‌رو'}\n"
            f"سایز: {order['size']}\n"
            f"تعداد فایل: {len(order['files'])}\n"
            f"تعداد صفحات/برگه‌های محاسبه‌شده: {units}\n"
            f"قیمت واحد: {money(price_each)}\n\n"
            f"💰 مبلغ قابل پرداخت: {money(total)}"
        )
        await q.edit_message_text(
            details,
            reply_markup=keyboard([
                [("📎 ارسال فایل بیشتر", "more")],
                [("🔄 سفارش جدید", "new")]
            ])
        )
    elif data == "more":
        await q.edit_message_text(
            "فایل بعدی را ارسال کنید. پس از اتمام، «محاسبه قیمت» را بزنید.",
            reply_markup=keyboard([[("🧮 محاسبه قیمت", "calc")]])
        )
    elif data in ("new", "cancel"):
        context.user_data.clear()
        await q.edit_message_text("برای شروع سفارش جدید، /start را بزنید.")

async def receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order = context.user_data.get("order")
    if not order or "size" not in order:
        await update.message.reply_text("ابتدا /start را بزنید و گزینه‌های چاپ را انتخاب کنید.")
        return

    doc = update.message.document
    filename = doc.file_name or "file"
    ext = Path(filename).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        await update.message.reply_text(
            "فعلاً فقط PDF و تصاویر JPG/JPEG/PNG قابل محاسبه هستند."
        )
        return

    units = 1
    if ext == ".pdf":
        # فایل را فقط برای شمارش صفحات دانلود می‌کنیم.
        tg_file = await doc.get_file()
        data = await tg_file.download_as_bytearray()
        if PdfReader:
            try:
                units = len(PdfReader(io.BytesIO(bytes(data))).pages)
            except Exception:
                units = 1
                logger.exception("Could not count PDF pages")
        else:
            units = 1

    order["files"].append({"name": filename, "units": units})
    order["total_units"] += units

    await update.message.reply_text(
        f"✅ {filename} دریافت شد.\n"
        f"تعداد محاسبه‌شده: {units}\n"
        f"مجموع فعلی: {order['total_units']}",
        reply_markup=keyboard([[("🧮 محاسبه قیمت", "calc")]])
    )

async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order = context.user_data.get("order")
    if not order or "size" not in order:
        await update.message.reply_text("ابتدا /start را بزنید و گزینه‌های چاپ را انتخاب کنید.")
        return

    # هر عکس = یک صفحه/برگه
    order["files"].append({"name": "photo", "units": 1})
    order["total_units"] += 1

    await update.message.reply_text(
        "✅ عکس دریافت شد.\n"
        f"مجموع فعلی: {order['total_units']}",
        reply_markup=keyboard([[("🧮 محاسبه قیمت", "calc")]])
    )

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "برای شروع سفارش /start را بزنید یا فایل PDF/عکس ارسال کنید."
    )

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.Document.ALL, receive_document))
    app.add_handler(MessageHandler(filters.PHOTO, receive_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    logger.info("ChapYar is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
