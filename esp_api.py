# ============================================================
# esp_api.py
# ------------------------------------------------------------
# واجهة REST API تعمل بمكتبة Flask، وتربط بين:
#   - الـ ESP8266 (الجهاز الفيزيائي)
#   - الـ Scheduler (المنطق الخلفي في Python)
#
# Endpoints:
#   GET  /             → فحص حياة السيرفر
#   GET  /esp/poll     → ESP8266 يسأل: هل هناك أوامر تشغيل؟
#                        يرجع قائمة الـ slots التي يجب إضاءتها
#   POST /esp/sensor   → ESP8266 يُبلّغ: تم فتح خانة (slot)
#                        نُسجّل أن الدواء قد أُخذ
#   POST /esp/control  → داخلياً: Scheduler يطلب تشغيل/إيقاف
#                        LED+Buzzer لخانة معينة
#   GET  /esp/state    → عرض الحالة الحالية (للمراقبة)
#
# الأمان: مفتاح بسيط API_SECRET_KEY يجب أن يُرسَل في كل
# طلب — للحماية من العبث في الشبكة المحلية.
# ============================================================

import threading
from datetime import datetime
from typing import Dict, Set

from flask import Flask, request, jsonify

from config import API_HOST, API_PORT, API_SECRET_KEY, TOTAL_SLOTS
from database import db


# ============================================================
# كلاس واحد يدير حالة العلبة الذكية
# ------------------------------------------------------------
# الحالة (state) هي: أي الخانات يجب أن تكون مُضاءة الآن.
# الـ Scheduler يُحدّثها عبر /esp/control.
# الـ ESP يقرأها عبر /esp/poll.
# ============================================================
class BoxState:
    """يحفظ مجموعة الخانات النشطة + نوع تنبيه كل خانة + قفل Thread-safe.

    الجديد في النسخة 2:
      - slot_mode: لكل خانة نشطة نوع التنبيه:
            "due"  = حان موعد الدواء الآن  → صوت هادئ على العلبة
            "late" = تنبيه مكرر / تأخّر    → صوت أسرع وأحدّ
      - slot_seq: عدّاد يزيد مع كل تنبيه جديد لنفس الخانة، كي يعرف
        الـ ESP أن عليه إعادة تشغيل الصوت (لمدة محدودة) حتى لو كانت
        الخانة نشطة أصلاً.
    """

    def __init__(self) -> None:
        self.active_slots: Set[int] = set()
        self.slot_mode: Dict[int, str] = {}   # slot → "due" | "late"
        self.slot_seq: Dict[int, int] = {}    # slot → عدّاد التنبيهات
        self.last_seen: Dict[int, str] = {}   # آخر مرة أبلغ ESP عن كل slot
        self._lock = threading.Lock()

    def activate(self, slot: int, mode: str = "due") -> None:
        with self._lock:
            self.active_slots.add(slot)
            self.slot_mode[slot] = "late" if mode == "late" else "due"
            self.slot_seq[slot] = self.slot_seq.get(slot, 0) + 1

    def deactivate(self, slot: int) -> None:
        with self._lock:
            self.active_slots.discard(slot)
            self.slot_mode.pop(slot, None)

    def alerts(self):
        """قائمة التنبيهات الحالية بالشكل الذي يقرؤه الـ ESP."""
        with self._lock:
            return [
                {
                    "slot": s,
                    "mode": self.slot_mode.get(s, "due"),
                    "seq":  self.slot_seq.get(s, 1),
                }
                for s in sorted(self.active_slots)
            ]

    def snapshot(self) -> Dict:
        with self._lock:
            return {
                "active_slots": sorted(list(self.active_slots)),
                "modes":        dict(self.slot_mode),
                "last_seen":    dict(self.last_seen),
                "total_slots":  TOTAL_SLOTS,
            }


# ============================================================
# تطبيق Flask
# ============================================================
def create_app(scheduler=None) -> Flask:
    """يبني تطبيق Flask ويمرّر له مرجعاً لكائن Scheduler."""

    app = Flask(__name__)
    state = BoxState()
    # نُتيح للسكيدولر تحديث الحالة عبر create_app(...).state
    app.state = state

    # ------------------------------------------------------
    # دالة مساعدة للتحقق من المفتاح السري
    # ------------------------------------------------------
    def _check_key(data) -> bool:
        if not data:
            return False
        return data.get("key") == API_SECRET_KEY

    # ------------------------------------------------------
    # GET /  — فحص حياة السيرفر
    # ------------------------------------------------------
    @app.route("/", methods=["GET"])
    def home():
        return jsonify({
            "service": "MedicineBox API",
            "status":  "ok",
            "time":    datetime.now().isoformat(timespec="seconds"),
            "slots":   TOTAL_SLOTS,
        })

    # ------------------------------------------------------
    # GET /esp/poll
    # ------------------------------------------------------
    # ESP8266 يستدعيه دورياً (كل ~2 ثانية) ليعرف الخانات النشطة.
    # المفتاح يُمرَّر كـ query param: /esp/poll?key=XXX
    # ------------------------------------------------------
    @app.route("/esp/poll", methods=["GET"])
    def esp_poll():
        if request.args.get("key") != API_SECRET_KEY:
            return jsonify({"error": "unauthorized"}), 401
        snap = state.snapshot()
        return jsonify({
            "active_slots": snap["active_slots"],
            # الشكل الجديد: نوع التنبيه (due/late) وعدّاد لكل خانة
            "alerts":       state.alerts(),
            "time":         datetime.now().strftime("%H:%M:%S"),
        })

    # ------------------------------------------------------
    # POST /esp/sensor
    # ------------------------------------------------------
    # ESP يرسل: { "key":"...", "slot": 2, "event": "opened" }
    # عند فتح خانة، نُسجّل أن الدواء قد أُخذ ونوقف التنبيه.
    # ------------------------------------------------------
    @app.route("/esp/sensor", methods=["POST"])
    def esp_sensor():
        data = request.get_json(silent=True) or {}
        # تسجيل: نطبع كل طلب يصل من الـ ESP لتسهيل تتبّع المشكلة
        print(f"📥 /esp/sensor وصل طلب: {data}")
        if not _check_key(data):
            print("⛔ /esp/sensor مفتاح غير صحيح — رُفض الطلب")
            return jsonify({"error": "unauthorized"}), 401

        try:
            slot = int(data.get("slot"))
        except (TypeError, ValueError):
            print("⛔ /esp/sensor قيمة slot غير صالحة")
            return jsonify({"error": "invalid slot"}), 400

        if not 1 <= slot <= TOTAL_SLOTS:
            print(f"⛔ /esp/sensor الخانة {slot} خارج النطاق")
            return jsonify({"error": "slot out of range"}), 400

        event = data.get("event", "opened")
        state.last_seen[slot] = datetime.now().isoformat(timespec="seconds")

        if event == "opened":
            # أبلغ الـ Scheduler بأن الدواء قد أُخذ من هذه الخانة
            history_id = None
            if scheduler is not None:
                history_id = scheduler.confirm_taken(slot)
            if history_id is None:
                print(f"⚠️ /esp/sensor الخانة {slot}: لا يوجد تذكير نشط "
                      "لهذه الخانة → لم تُرسل رسالة تأكيد")
            else:
                print(f"✅ /esp/sensor الخانة {slot}: سُجّل الأخذ "
                      f"(history_id={history_id})")
            # أوقف الخانة في حالة العلبة
            state.deactivate(slot)
            return jsonify({
                "result":     "ok",
                "slot":       slot,
                "history_id": history_id,
                "message":    "تم تسجيل أخذ الدواء",
            })

        # أحداث أخرى (مثلاً heartbeat) — نقبلها فقط
        return jsonify({"result": "ok", "event": event})

    # ------------------------------------------------------
    # POST /esp/control  (داخلي)
    # ------------------------------------------------------
    # يستدعيه الـ Scheduler ليطلب تشغيل/إيقاف LED لخانة.
    # ------------------------------------------------------
    @app.route("/esp/control", methods=["POST"])
    def esp_control():
        data = request.get_json(silent=True) or {}
        if not _check_key(data):
            return jsonify({"error": "unauthorized"}), 401

        try:
            slot = int(data.get("slot"))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid slot"}), 400

        action = (data.get("action") or "").lower()
        # mode: "due" (حان الموعد) أو "late" (تنبيه مكرر/تأخر)
        mode = (data.get("mode") or "due").lower()
        if action == "on":
            state.activate(slot, mode=mode)
        elif action == "off":
            state.deactivate(slot)
        else:
            return jsonify({"error": "action must be on|off"}), 400

        return jsonify({
            "result":       "ok",
            "slot":         slot,
            "action":       action,
            "active_slots": sorted(list(state.active_slots)),
        })

    # ------------------------------------------------------
    # GET /esp/state  — لعرض الحالة (مراقبة / debugging)
    # ------------------------------------------------------
    @app.route("/esp/state", methods=["GET"])
    def esp_state():
        if request.args.get("key") != API_SECRET_KEY:
            return jsonify({"error": "unauthorized"}), 401
        snap = state.snapshot()
        snap["medicines"] = [
            {
                "id":   m["id"],
                "name": m["name"],
                "slot": m["slot"],
                "time": m["medicine_time"],
                "remaining": m["remaining_quantity"],
            }
            for m in db.list_medicines(only_active=True)
        ]
        snap["stats"] = db.stats_summary()
        return jsonify(snap)

    return app


# ============================================================
# تشغيل الـ API في Thread مستقل
# ============================================================
def run_api_in_thread(scheduler=None) -> threading.Thread:
    """يُشغّل Flask في Thread خلفي ليعمل مع البوت بالتوازي."""
    app = create_app(scheduler=scheduler)

    def _run():
        # use_reloader=False ضروري عند التشغيل داخل Thread
        app.run(host=API_HOST, port=API_PORT,
                debug=False, use_reloader=False, threaded=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print(f"🌐 Flask API يعمل على http://{API_HOST}:{API_PORT}")
    return t


# ============================================================
# تشغيل مستقل (لاختبار الـ API فقط)
# ============================================================
if __name__ == "__main__":
    app = create_app(scheduler=None)
    print(f"🌐 تشغيل API على http://{API_HOST}:{API_PORT}")
    app.run(host=API_HOST, port=API_PORT, debug=True)
