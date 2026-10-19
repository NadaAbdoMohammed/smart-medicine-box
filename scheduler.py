# ============================================================
# scheduler.py
# ------------------------------------------------------------
# محرّك التذكير (الجدولة + التنبيهات + تنبيه المشرف).
#
# آلية العمل:
#   - يعمل في Thread منفصل (لا يُعطّل البوت).
#   - يفحص قاعدة البيانات كل SCHEDULER_CHECK_INTERVAL_SECONDS.
#   - عند تطابق (وقت + يوم) مع دواء يُنشئ سجل history.
#   - يرسل تنبيه إلى المريض + يأمر ESP8266 بتشغيل LED + Buzzer.
#   - إن لم يُؤخذ الدواء، يُعيد التنبيه reminder_count مرة كل
#     reminder_interval دقيقة.
#   - بعد انتهاء التنبيهات، يُعلّم السجل missed ويُرسل للمشرف.
# ============================================================

import time
import threading
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from telebot import TeleBot

from config import (
    SCHEDULER_CHECK_INTERVAL_SECONDS, SCHEDULER_CATCHUP_SECONDS,
    MSG_SUPERVISOR_ALERT,
    DAY_EN_TO_AR, API_HOST, API_PORT, API_SECRET_KEY,
    STATUS_PENDING, STATUS_MISSED,
    SETTING_PICKUP_GRACE, DEFAULT_PICKUP_GRACE_MINUTES,
)
from database import db


# ============================================================
# الكلاس الرئيسي
# ============================================================
class MedicineScheduler:
    """يدير كل عمليات الجدولة وإرسال التنبيهات."""

    def __init__(self, bot: TeleBot) -> None:
        self.bot = bot
        # نُتتبّع كل تنبيه نشِط حتى لا نرسل نفس التنبيه عدة مرات
        # المفتاح: history_id ، القيمة: dict فيه
        # (sent_count, last_sent_at, scheduled_at, medicine)
        self.active_reminders: Dict[int, Dict[str, Any]] = {}

        # علم لإيقاف الـ thread عند الإغلاق
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ----------------------------------------------------------
    # نقطة التشغيل
    # ----------------------------------------------------------
    def start(self) -> None:
        """يُشغّل الـ scheduler في Thread خلفي."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("⏰ Scheduler يعمل الآن (كل "
              f"{SCHEDULER_CHECK_INTERVAL_SECONDS} ثانية)")

    # ----------------------------------------------------------
    # مدة انتظار رفع العلبة (تُقرأ من إعدادات البوت في كل مرة)
    # ----------------------------------------------------------
    # نقرأها من قاعدة البيانات لا من ثابت، كي يأخذ أي تعديل من قائمة
    # ⚙️ الإعدادات مفعوله فوراً دون إعادة تشغيل النظام.
    def _pickup_grace_minutes(self) -> int:
        return db.get_setting_int(
            SETTING_PICKUP_GRACE, DEFAULT_PICKUP_GRACE_MINUTES)

    def stop(self) -> None:
        self._running = False

    # ----------------------------------------------------------
    # حلقة الفحص الرئيسية
    # ----------------------------------------------------------
    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as e:
                print("❌ خطأ في Scheduler:", e)
            time.sleep(SCHEDULER_CHECK_INTERVAL_SECONDS)

    # ----------------------------------------------------------
    # كل دورة (Tick) تفعل 3 أشياء:
    #   1) تفحص الأدوية للوقت الحالي وتُنشئ تنبيهاً جديداً
    #   2) تُعيد إرسال التنبيهات القديمة التي حان موعد التكرار
    #   3) تُغلق التنبيهات المنتهية وتُنبّه المشرف
    # ----------------------------------------------------------
    def _tick(self) -> None:
        now = datetime.now()
        # day_en: Mon/Tue/... يطابق ما يخزّنه البوت
        day_map_py_to_en = {
            0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu",
            4: "Fri", 5: "Sat", 6: "Sun"
        }
        today_en = day_map_py_to_en[now.weekday()]

        # ----- (1) ابحث عن أدوية حان وقتها الآن -----
        for med in db.list_medicines(only_active=True):
            # نُطبّع الوقت المخزّن إلى "HH:MM" (بصفر بادئ) كي يطابق
            # حتى لو حُفظ قديماً كـ "5:59" بدل "05:59".
            med_time = (med["medicine_time"] or "").strip()
            try:
                med_time = datetime.strptime(med_time, "%H:%M").strftime("%H:%M")
            except ValueError:
                continue
            days = (med["days"] or "").split(",")
            if today_en not in days:
                continue

            # وقت الدواء اليوم كـ datetime، لمقارنته بالوقت الحالي.
            sched_dt = now.replace(
                hour=int(med_time[:2]), minute=int(med_time[3:]),
                second=0, microsecond=0,
            )
            delay_sec = (now - sched_dt).total_seconds()
            # نُطلق التنبيه إذا حان الوقت الآن أو تأخّر الفحص قليلاً
            # (ضمن نافذة التعويض) — هكذا لا تضيع الجرعة لو بدأ البوت
            # متأخراً بدقيقة أو اثنتين بدل اشتراط مطابقة الدقيقة تماماً.
            if delay_sec < 0 or delay_sec > SCHEDULER_CATCHUP_SECONDS:
                continue

            # تحقق أن السجل لم يُنشَأ مسبقاً لهذا الموعد (بأي حالة)
            scheduled_str = sched_dt.strftime("%Y-%m-%d %H:%M")
            if db.get_history_at(med["id"], scheduled_str):
                continue  # أُنشئ بالفعل (قيد الانتظار أو أُخذ/فات)

            history_id = db.add_history(med["id"], scheduled_str,
                                        status=STATUS_PENDING)
            print(f"🔔 تنبيه جديد: {med['name']} ({scheduled_str})")
            self._send_reminder(history_id, med, scheduled_str,
                                attempt=1)

        # ----- (2) أعد إرسال التنبيهات التي حان موعد تكرارها -----
        for history_id, info in list(self.active_reminders.items()):
            med = info["medicine"]
            interval = med["reminder_interval"]
            count = med["reminder_count"]
            sent = info["sent_count"]
            last_sent = info["last_sent_at"]

            # ننتظر دائماً انقضاء الفاصل الزمني منذ آخر تنبيه قبل
            # اتخاذ أي إجراء (إعادة تنبيه أو إعلان فوات الدواء).
            if (now - last_sent).total_seconds() < interval * 60:
                continue

            # هل وصلنا للحد الأقصى من التنبيهات؟ → فات الدواء
            if sent >= count:
                self._handle_missed(history_id, med, info["scheduled_at"])
                continue

            self._send_reminder(history_id, med, info["scheduled_at"],
                                attempt=sent + 1)

    # ----------------------------------------------------------
    # إرسال تنبيه واحد (Telegram + ESP)
    # ----------------------------------------------------------
    def _send_reminder(self, history_id: int, med, scheduled_str: str,
                       attempt: int) -> None:
        # 1) أعلِم ESP8266 بتشغيل LED + Buzzer
        #    التنبيه الأول = "due" (صوت هادئ عند حلول الموعد)
        #    أي تكرار بعده = "late" (صوت أسرع وأحدّ لأن المريض تأخر)
        mode = "due" if attempt <= 1 else "late"
        self._notify_esp(med["slot"], action="on", mode=mode)

        # 2) أرسل رسالة Telegram للمريض
        patient = (db.get_patient(med["patient_id"])
                   if med["patient_id"] else None)
        text = (
            f"🔔 *تذكير بموعد الدواء* (التنبيه {attempt})\n\n"
            f"🩺 الدواء: *{med['name']}*\n"
            f"💊 النوع: {med['medicine_type']}\n"
            f"🥄 الجرعة: {med['dosage']}\n"
            f"🗄 الخانة: {med['slot']}\n"
            f"⏰ الوقت المجدول: {med['medicine_time']}\n\n"
            "📦 *ارفع العلبة من مكانها لأخذ الدواء* — عندها يُسجَّل الأخذ "
            "تلقائياً ويتوقف التنبيه."
        )

        # ملاحظة: لا يوجد زر تأكيد يدوي بعد الآن. التأكيد يتم حصراً عبر
        # الحساس (رفع العلبة فعلياً) كي لا يُسجَّل أخذٌ لم يحدث.

        # أرسل للمريض إن كان لديه Telegram ID
        if patient and patient["telegram_id"]:
            try:
                self.bot.send_message(patient["telegram_id"], text,
                                      parse_mode="Markdown")
                # نُرفق صورة الدواء إن وُجدت
                if med["photo_id"]:
                    try:
                        self.bot.send_photo(patient["telegram_id"],
                                            med["photo_id"])
                    except Exception:
                        pass
            except Exception as e:
                print(f"⚠️ تعذّر إرسال التنبيه للمريض: {e}")

        # 3) حدّث/أنشئ سجل التنبيه النشِط
        self.active_reminders[history_id] = {
            "sent_count":   attempt,
            "last_sent_at": datetime.now(),
            "scheduled_at": scheduled_str,
            "medicine":     med,
        }

    # ----------------------------------------------------------
    # المنطق المشترك لتسجيل أخذ الدواء (المصدر الوحيد: حساس IR / رفع العلبة)
    # ----------------------------------------------------------
    def _finalize_taken(self, history_id: int, med,
                        scheduled_at: str) -> None:
        """يعلّم السجل مأخوذاً، يُنقص الكمية، يشكر المريض، ويُطفئ الإنذار.

        قاعدة الإشعار للمشرف: *كلما* رُفعت العلبة بعد موعد الجرعة (تأخير > 0)
        نُخطر المشرف بأن المريض أخذ الدواء متأخراً — سواء كانت الجرعة قد
        فاتت رسمياً (missed) أو كانت لا تزال ضمن نافذة التذكير (pending).
        هكذا يصل الإشعار في كل مرة بدل أن يصل أحياناً فقط.
        """
        now = datetime.now()
        try:
            sched_dt = datetime.strptime(scheduled_at, "%Y-%m-%d %H:%M")
            delay_min = max(0, int((now - sched_dt).total_seconds() // 60))
        except (ValueError, TypeError):
            delay_min = 0

        # نقرأ الحالة السابقة قبل التعديل لنعرف هل كانت "فائتة"
        prev = db.get_history_by_id(history_id)
        was_missed = bool(prev and prev["status"] == STATUS_MISSED)

        db.mark_taken(history_id, now.strftime("%Y-%m-%d %H:%M:%S"),
                      delay_minutes=delay_min)
        db.decrement_quantity(med["id"])

        patient = (db.get_patient(med["patient_id"])
                   if med["patient_id"] else None)

        # أبلغ المريض بالشكر
        if patient and patient["telegram_id"]:
            try:
                msg = f"✅ تم تأكيد أخذ *{med['name']}* — شكراً لك! 🌿"
                if delay_min > 0:
                    msg += f"\n(تأخير: {delay_min} دقيقة)"
                self.bot.send_message(patient["telegram_id"], msg,
                                      parse_mode="Markdown")
            except Exception:
                pass

        # إشعار المشرف: في كل مرة تُرفع فيها العلبة بعد الموعد (تأخير > 0).
        # نُمرّر was_missed فقط لنغيّر صياغة الرسالة (فات ثم أُخذ / تأخّر).
        if delay_min > 0 and patient:
            self._notify_supervisors_late_taken(patient, med, delay_min,
                                                was_missed)

        # أوقف LED + Buzzer على ESP، واحذف من التنبيهات النشطة (إن وُجد)
        self._notify_esp(med["slot"], action="off")
        self.active_reminders.pop(history_id, None)

    # ----------------------------------------------------------
    # إخطار المشرفين بأن جرعةً فائتة قد أُخذت متأخراً (بعد رفع العلبة)
    # ----------------------------------------------------------
    def _notify_supervisors_late_taken(self, patient, med,
                                       delay_min: int,
                                       was_missed: bool = False) -> None:
        # صياغة مختلفة بحسب ما إذا كانت الجرعة قد فاتت رسمياً قبل الأخذ
        if was_missed:
            lead = (f"المريض *{patient['name']}* تناول الآن الدواء "
                    f"*{med['name']}* الذي *فات موعده*.")
        else:
            lead = (f"المريض *{patient['name']}* رفع العلبة وأخذ الدواء "
                    f"*{med['name']}* *متأخراً عن موعده*.")
        text = (
            "🟢 *تحديث للمشرف*\n\n"
            f"{lead}\n"
            f"🗄 الخانة: {med['slot']}\n"
            f"⏱ التأخير: {delay_min} دقيقة"
        )
        for s in db.list_supervisors(patient["id"]):
            try:
                self.bot.send_message(s["telegram_id"], text,
                                      parse_mode="Markdown")
            except Exception as e:
                print(f"⚠️ تعذّر إخطار المشرف {s['name']} بالأخذ المتأخر: {e}")

    # ----------------------------------------------------------
    # عند تأكيد الأخذ من الحساس (يُستدعى من esp_api.py)
    # ----------------------------------------------------------
    def confirm_taken(self, slot: int) -> Optional[int]:
        """يُسجّل أن الدواء قد أُخذ من الخانة slot. يُعيد history_id."""
        # تسجيل: نطبع الخانات التي لها تذكير نشط حالياً لتسهيل التتبّع
        active_slots = [info["medicine"]["slot"]
                        for info in self.active_reminders.values()]
        print(f"🔎 confirm_taken(slot={slot}) — "
              f"خانات لها تذكير نشط الآن: {active_slots}")

        # (1) المسار السريع: تذكير نشط في الذاكرة بهذه الخانة
        for hid, info in list(self.active_reminders.items()):
            if info["medicine"]["slot"] == slot:
                self._finalize_taken(hid, info["medicine"],
                                     info["scheduled_at"])
                return hid

        # (2) نافذة الانتظار: لا تذكير نشط في الذاكرة، لكن قد يكون المريض
        # رفع العلبة بعد انتهاء التنبيهات أو حتى بعد إخطار المشرف (فوات)،
        # أو بعد إعادة تشغيل النظام. نقبل الأخذ مهما تأخّر ضمن سقف 24 ساعة
        # (أو نافذة الانتظار من الإعدادات إن كانت أطول) — فيُسجَّل الأخذ
        # المتأخر دائماً مع مقدار التأخير، ويُخطَر المشرف.
        grace = max(self._pickup_grace_minutes(), 24 * 60)
        med = db.get_medicine_by_slot(slot)
        if med:
            h = db.get_latest_open_history(med["id"], grace_minutes=grace)
            if h:
                print(f"↩️ confirm_taken: ضمن نافذة الانتظار ({grace}د) "
                      f"(history_id={h['id']}, الحالة السابقة={h['status']})")
                self._finalize_taken(h["id"], med, h["scheduled_time"])
                return h["id"]

        # (3) لا جرعة مستحقّة لهذه الخانة (رفعٌ خارج وقت الدواء، أو انتهت
        # نافذة الانتظار) → لا نُسجّل شيئاً ولا نُرسل أي إشعار. نكتفي
        # بإطفاء الخانة احترازياً كي لا يستمر أي صفير.
        print(f"⚠️ confirm_taken(slot={slot}): لا توجد جرعة مستحقّة "
              "ضمن نافذة الانتظار — تم تجاهل رفع العلبة")
        self._notify_esp(slot, action="off")
        return None

    # ----------------------------------------------------------
    # معالجة دواء فات وقته بالكامل
    # ----------------------------------------------------------
    def _handle_missed(self, history_id: int, med,
                       scheduled_str: str) -> None:
        db.mark_missed(history_id)
        # لا نُطفئ الضوء عند الفوات — يبقى ليد الخانة مضاءً (بنمط late)
        # حتى يرفع المريض العلبة فعلاً، فيعرف أن هناك دواءً بانتظاره.
        # (الصوت لا يستمر: جلسة الصوت على ESP محدودة المدة أصلاً)
        self._notify_esp(med["slot"], action="on", mode="late")

        # أرسل تنبيه عاجل للمشرف
        patient = (db.get_patient(med["patient_id"])
                   if med["patient_id"] else None)
        if not patient:
            del self.active_reminders[history_id]
            return

        sups = db.list_supervisors(patient["id"])
        # اسم اليوم بالعربي للرسالة
        day_en_today = scheduled_str  # غير مهم، نعرض الوقت فقط
        try:
            sched_dt = datetime.strptime(scheduled_str, "%Y-%m-%d %H:%M")
            day_map = {
                0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
                3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
            }
            day_ar = day_map[sched_dt.weekday()]
            time_ar = sched_dt.strftime("%H:%M")
        except Exception:
            day_ar, time_ar = "—", med["medicine_time"]

        alert_text = MSG_SUPERVISOR_ALERT.format(
            patient=patient["name"],
            medicine=med["name"],
            day=day_ar,
            time=time_ar,
            slot=med["slot"],
            dosage=med["dosage"],
        )

        for s in sups:
            try:
                self.bot.send_message(s["telegram_id"], alert_text,
                                      parse_mode="Markdown")
            except Exception as e:
                print(f"⚠️ تعذّر إرسال إنذار للمشرف {s['name']}: {e}")

        # أبلغ المريض أيضاً — بلا أي زر. لا يزال بإمكانه تسجيل الأخذ
        # المتأخر بمجرد رفع العلبة من مكانها ضمن نافذة الانتظار، وعندها
        # نُخطر المشرف تلقائياً بأنه تناوله.
        grace = self._pickup_grace_minutes()
        if patient["telegram_id"]:
            try:
                self.bot.send_message(
                    patient["telegram_id"],
                    f"❌ لم تتناول *{med['name']}* في موعده. تم تنبيه المشرف.\n"
                    f"إن أخذته الآن، *ارفع العلبة من مكانها* خلال {grace} دقيقة "
                    "وسيُسجَّل تلقائياً. 👍",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        print(f"❌ دواء فاته الوقت: {med['name']} → تم إخطار "
              f"{len(sups)} مشرف")
        del self.active_reminders[history_id]

    # ----------------------------------------------------------
    # إرسال أمر لـ ESP8266 (تشغيل/إيقاف LED + Buzzer)
    # ----------------------------------------------------------
    def _notify_esp(self, slot: int, action: str,
                    mode: str = "due") -> None:
        """
        نرسل HTTP request إلى /esp/control كي يُحدِّث الـ ESP حالته.
        mode = "due"  → حان الموعد (صوت هادئ)
        mode = "late" → تنبيه مكرر/تأخر (صوت أسرع وأحدّ)
        إن لم يكن الـ ESP متصلاً (تطوير/اختبار)، نتجاهل الخطأ.
        """
        url = f"http://127.0.0.1:{API_PORT}/esp/control"
        try:
            requests.post(url, json={
                "key":    API_SECRET_KEY,
                "slot":   slot,
                "action": action,  # on / off
                "mode":   mode,    # due / late
            }, timeout=2)
        except Exception:
            # عادي في وضع التطوير
            pass
