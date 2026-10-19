# ============================================================
# main.py — نقطة تشغيل النظام كاملاً (أمر واحد، نافذة واحدة)
# ------------------------------------------------------------
#   python main.py
#
# يشغّل بالترتيب:
#   1) قاعدة البيانات (تُنشأ تلقائياً أول مرة)
#   2) بوت Telegram
#   3) المجدول Scheduler (في Thread خلفي)
#   4) سيرفر Flask للـ ESP (في Thread خلفي، منفذ 5000)
#   5) استماع البوت للرسائل (في المقدمة)
#
# لماذا عملية واحدة؟ لأن سيرفر Flask يحتاج المجدول (ليبلغه برفع
# العلبة)، والمجدول يحتاج البوت (ليرسل الرسائل) — فلا يمكن فصلهم
# في نوافذ مستقلة.
# ============================================================

import time

from config import API_PORT, BOT_NAME
from database import db          # استيراده يُنشئ القاعدة والجداول
from bot import create_bot
from scheduler import MedicineScheduler
from esp_api import run_api_in_thread


def main() -> None:
    print("=" * 52)
    print(f"   💊 {BOT_NAME} — تشغيل النظام")
    print("=" * 52)

    # 1) البوت
    bot = create_bot()
    print("🤖 بوت Telegram جاهز")

    # 2) المجدول (يحتاج البوت ليرسل الرسائل)
    scheduler = MedicineScheduler(bot)
    scheduler.start()

    # 3) سيرفر Flask للـ ESP (يحتاج المجدول لتأكيد الأخذ)
    run_api_in_thread(scheduler=scheduler)
    print(f"🌐 واجهة ESP جاهزة على المنفذ {API_PORT}")

    print("-" * 52)
    print("✅ النظام يعمل الآن بالكامل — اتركي هذه النافذة مفتوحة")
    print("   (للإيقاف: Ctrl + C)")
    print("-" * 52)

    # 4) استماع البوت (مع إعادة محاولة تلقائية عند انقطاع الإنترنت)
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=25)
        except KeyboardInterrupt:
            print("\n👋 تم إيقاف النظام")
            scheduler.stop()
            break
        except Exception as e:
            print(f"⚠️ انقطاع في اتصال البوت: {e} — إعادة محاولة بعد 5 ثوانٍ")
            time.sleep(5)


if __name__ == "__main__":
    main()
