# ============================================================
# bot.py
# ------------------------------------------------------------
# إنشاء بوت Telegram وربط كل المعالجات به.
#
# ملاحظة تشغيل: لا تُشغّلي هذا الملف وحده — نقطة التشغيل الوحيدة
# للنظام كله هي:  python main.py
# (تشغّل البوت + السيرفر + المجدول معاً في نافذة واحدة)
# ولتسهيل الأمر: تشغيل هذا الملف مباشرة يستدعي main.py تلقائياً.
# ============================================================

from telebot import TeleBot

from config import TELEGRAM_TOKEN
from telegram_handlers import register_handlers


def create_bot() -> TeleBot:
    """ينشئ البوت ويسجّل عليه كل الأوامر والقوائم."""
    bot = TeleBot(TELEGRAM_TOKEN)
    register_handlers(bot)
    return bot


# تشغيل مباشر؟ حوّلي إلى نقطة التشغيل الموحدة
if __name__ == "__main__":
    print("ℹ️  نقطة التشغيل الموحدة هي main.py — جارٍ تشغيلها الآن...")
    import main
    main.main()
