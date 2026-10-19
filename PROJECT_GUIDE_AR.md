# تشغيل مشروع علبة الدواء الذكية — v3

## أول مرة فقط
1. تثبيت المكتبات:
   pip install flask pyTelegramBotAPI requests
2. تأكدي أن التوكن صحيح في config.py
3. ارفعي كود esp8266/MedicineBox_ESP32/MedicineBox_ESP32.ino على اللوحة
   (عدّلي فيه الواي فاي و SERVER_HOST = IP حاسوبك من ipconfig)

## التشغيل اليومي — أمر واحد بنافذة واحدة:
   python main.py

هذا يشغّل كل شيء معاً: السيرفر (منفذ 5000) + المجدول + البوت.
(لم يعد هناك حاجة لتشغيل server.py و bot.py منفصلين —
 النظام الجديد عملية واحدة لأن الأجزاء تحتاج بعضها)

## الملفات
- main.py               نقطة التشغيل الوحيدة
- bot.py                إنشاء البوت
- telegram_handlers.py  قوائم وأزرار البوت (الواجهة الاحترافية)
- database.py           قاعدة البيانات SQLite
- config.py             الإعدادات (التوكن، المنفذ، المفتاح...)
- scheduler.py          محرك التذكير
- esp_api.py            واجهة الاتصال مع العلبة
- medicine_box.db       تُنشأ تلقائياً أول تشغيل
