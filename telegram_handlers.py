# ============================================================
# telegram_handlers.py
# ------------------------------------------------------------
# واجهة بوت Telegram — النسخة الاحترافية التفاعلية.
#
# المبدأ: كل شيء أزرار. المستخدم لا يكتب إلا عندما لا بديل عن
# الكتابة (اسم، جرعة، وقت) — وكل إدخال نصي له تحقق وأمثلة.
#
# البنية:
#   - قائمة رئيسية أنيقة (Inline Keyboard)
#   - معالج إضافة دواء خطوة بخطوة (Wizard) بشريط تقدم
#   - إدارة مرضى ومشرفين وأدوية (عرض/إيقاف/حذف بأزرار)
#   - تقارير وإحصائيات بأشرطة رسومية
#   - إعدادات (نافذة انتظار الرفع) بأزرار جاهزة
#
# التسجيل: register_handlers(bot) تُستدعى من bot.py
# ============================================================

from datetime import datetime
from telebot import TeleBot, types

from config import (
    BOT_NAME, MSG_WELCOME, MEDICINE_TYPES, WEEK_DAYS,
    DAY_EN_TO_AR, TOTAL_SLOTS,
    DEFAULT_REMINDER_COUNT, DEFAULT_REMINDER_INTERVAL,
    SETTING_PICKUP_GRACE, DEFAULT_PICKUP_GRACE_MINUTES,
    PICKUP_GRACE_CHOICES,
    STATUS_TAKEN, STATUS_MISSED, STATUS_PENDING, STATUS_LATE,
)
from database import db

# ------------------------------------------------------------
# حالة المحادثة لكل مستخدم (للخطوات النصية والمعالجات)
#   STATES[chat_id] = {"step": "...", "data": {...}}
# ------------------------------------------------------------
STATES: dict = {}

STATUS_ICON = {
    STATUS_TAKEN:   "✅",
    STATUS_LATE:    "🟡",
    STATUS_MISSED:  "❌",
    STATUS_PENDING: "⏳",
}
STATUS_AR = {
    STATUS_TAKEN:   "أُخذ",
    STATUS_LATE:    "أُخذ متأخراً",
    STATUS_MISSED:  "فات",
    STATUS_PENDING: "قيد الانتظار",
}


# ============================================================
# لوحات الأزرار الجاهزة
# ============================================================
def kb_main() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💊 إضافة دواء", callback_data="med:add"),
        types.InlineKeyboardButton("📋 الأدوية", callback_data="med:list"),
    )
    kb.add(
        types.InlineKeyboardButton("👤 المرضى", callback_data="pat:menu"),
        types.InlineKeyboardButton("👨‍👩‍👧 المشرفون", callback_data="sup:menu"),
    )
    kb.add(
        types.InlineKeyboardButton("📊 التقارير", callback_data="rep:menu"),
        types.InlineKeyboardButton("⚙️ الإعدادات", callback_data="set:menu"),
    )
    kb.add(types.InlineKeyboardButton("❓ مساعدة", callback_data="help"))
    return kb


def kb_back(target: str = "home") -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data=target))
    return kb


def btn_home() -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton("🏠 القائمة الرئيسية",
                                      callback_data="home")


def progress(step: int, total: int = 8) -> str:
    """شريط تقدم للمعالج: ▰▰▰▱▱▱▱▱"""
    return "▰" * step + "▱" * (total - step) + f"  ({step}/{total})"


# ============================================================
# نقطة التسجيل الرئيسية
# ============================================================
def register_handlers(bot: TeleBot) -> None:

    # --------------------------------------------------------
    # أدوات إرسال مختصرة
    # --------------------------------------------------------
    def send(chat_id, text, kb=None):
        return bot.send_message(chat_id, text, parse_mode="Markdown",
                                reply_markup=kb)

    def edit(call, text, kb=None):
        """تعديل نفس الرسالة بدل إغراق المحادثة برسائل جديدة."""
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb)
        except Exception:
            send(call.message.chat.id, text, kb)

    # ========================================================
    # /start والقائمة الرئيسية
    # ========================================================
    @bot.message_handler(commands=["start", "menu"])
    def cmd_start(message):
        STATES.pop(message.chat.id, None)
        send(message.chat.id, MSG_WELCOME, kb_main())

    @bot.callback_query_handler(func=lambda c: c.data == "home")
    def cb_home(call):
        STATES.pop(call.message.chat.id, None)
        edit(call, MSG_WELCOME, kb_main())
        bot.answer_callback_query(call.id)

    # ========================================================
    # المساعدة
    # ========================================================
    @bot.callback_query_handler(func=lambda c: c.data == "help")
    def cb_help(call):
        text = (
            f"❓ *دليل الاستخدام السريع — {BOT_NAME}*\n\n"
            "1️⃣ أضِف *مريضاً* أولاً (👤 المرضى ← ➕)\n"
            "2️⃣ أضِف *مشرفاً* له — يصله إنذار عند فوات أي جرعة\n"
            "3️⃣ أضِف *الأدوية* بمواعيدها وخاناتها (💊)\n\n"
            "🔔 عند الموعد: يضيء ضوء الخانة، يصدر صوت، وتصل رسالة "
            "تذكير للمريض.\n"
            "📦 *رفع العلبة من مكانها* هو تأكيد الأخذ — يُسجَّل "
            "تلقائياً ويتوقف التنبيه فوراً.\n"
            "❌ إن لم تُرفع بعد كل التنبيهات: تُعلَّم الجرعة فائتة "
            "ويُنذَر المشرف.\n"
            "🟡 الرفع المتأخر (ضمن نافذة الانتظار) يُسجَّل متأخراً "
            "ويُخطَر المشرف.\n\n"
            "⚙️ من الإعدادات تضبط مدة نافذة الانتظار."
        )
        edit(call, text, kb_back())
        bot.answer_callback_query(call.id)

    # ========================================================
    # 👤 المرضى
    # ========================================================
    @bot.callback_query_handler(func=lambda c: c.data == "pat:menu")
    def cb_pat_menu(call):
        pats = db.list_patients()
        kb = types.InlineKeyboardMarkup(row_width=1)
        for p in pats:
            kb.add(types.InlineKeyboardButton(
                f"👤 {p['name']}", callback_data=f"pat:view:{p['id']}"))
        kb.add(types.InlineKeyboardButton("➕ إضافة مريض",
                                          callback_data="pat:new"))
        kb.add(btn_home())
        text = ("👤 *المرضى*\n\n" +
                (f"عدد المرضى: *{len(pats)}* — اختر مريضاً لعرض تفاصيله:"
                 if pats else
                 "لا يوجد مرضى بعد.\nأضف المريض أولاً لتستطيع إضافة "
                 "أدويته ومشرفيه. 👇"))
        edit(call, text, kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "pat:new")
    def cb_pat_new(call):
        STATES[call.message.chat.id] = {"step": "pat_name", "data": {}}
        edit(call,
             "👤 *إضافة مريض جديد*\n\n"
             "✍️ أرسل *اسم المريض*:\n"
             "_مثال: أبو أحمد_",
             kb_back("pat:menu"))
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("pat:view:"))
    def cb_pat_view(call):
        pid = int(call.data.split(":")[2])
        p = db.get_patient(pid)
        if not p:
            bot.answer_callback_query(call.id, "غير موجود")
            return
        meds = db.list_medicines(patient_id=pid)
        sups = db.list_supervisors(pid)
        text = (
            f"👤 *{p['name']}*\n"
            f"🆔 معرف تيليجرام: `{p['telegram_id'] or '—'}`\n"
            f"💊 عدد الأدوية: {len(meds)}\n"
            f"👨‍👩‍👧 عدد المشرفين: {len(sups)}"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton(
            "🗑 حذف المريض", callback_data=f"pat:del:{pid}"))
        kb.add(types.InlineKeyboardButton("🔙 رجوع",
                                          callback_data="pat:menu"),
               btn_home())
        edit(call, text, kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("pat:del:"))
    def cb_pat_del(call):
        pid = int(call.data.split(":")[2])
        p = db.get_patient(pid)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton(
                "⚠️ نعم، احذف", callback_data=f"pat:delok:{pid}"),
            types.InlineKeyboardButton("إلغاء", callback_data="pat:menu"),
        )
        edit(call,
             f"هل أنت متأكد من حذف المريض *{p['name']}*؟\n"
             "سيُحذف مع كل أدويته ومشرفيه وسجلاته!", kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("pat:delok:"))
    def cb_pat_delok(call):
        db.delete_patient(int(call.data.split(":")[2]))
        bot.answer_callback_query(call.id, "تم الحذف 🗑")
        cb_pat_menu(call)

    # ========================================================
    # 👨‍👩‍👧 المشرفون
    # ========================================================
    @bot.callback_query_handler(func=lambda c: c.data == "sup:menu")
    def cb_sup_menu(call):
        sups = db.list_all_supervisors()
        kb = types.InlineKeyboardMarkup(row_width=1)
        for s in sups:
            kb.add(types.InlineKeyboardButton(
                f"🗑 {s['name']} (مشرف على: {s['patient_name'] or '—'})",
                callback_data=f"sup:del:{s['id']}"))
        kb.add(types.InlineKeyboardButton("➕ إضافة مشرف",
                                          callback_data="sup:new"))
        kb.add(btn_home())
        text = ("👨‍👩‍👧 *المشرفون*\n\n"
                "المشرف يستقبل إنذاراً فورياً عند فوات أي جرعة، "
                "وتحديثاً عند الأخذ المتأخر.\n\n" +
                (f"عدد المشرفين: *{len(sups)}* — اضغط على مشرف لحذفه:"
                 if sups else "لا يوجد مشرفون بعد. 👇"))
        edit(call, text, kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "sup:new")
    def cb_sup_new(call):
        pats = db.list_patients()
        if not pats:
            edit(call, "⚠️ أضف *مريضاً* أولاً قبل إضافة مشرف.",
                 kb_back("sup:menu"))
            bot.answer_callback_query(call.id)
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for p in pats:
            kb.add(types.InlineKeyboardButton(
                f"👤 {p['name']}", callback_data=f"sup:pat:{p['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="sup:menu"))
        edit(call, "👨‍👩‍👧 *إضافة مشرف*\n\nعلى أي مريض سيشرف؟", kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("sup:pat:"))
    def cb_sup_pat(call):
        pid = int(call.data.split(":")[2])
        STATES[call.message.chat.id] = {
            "step": "sup_name", "data": {"patient_id": pid}}
        edit(call, "✍️ أرسل *اسم المشرف*:\n_مثال: أحمد (الابن)_",
             kb_back("sup:menu"))
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("sup:del:"))
    def cb_sup_del(call):
        db.delete_supervisor(int(call.data.split(":")[2]))
        bot.answer_callback_query(call.id, "تم الحذف 🗑")
        cb_sup_menu(call)

    # ========================================================
    # 💊 الأدوية — العرض والإدارة
    # ========================================================
    @bot.callback_query_handler(func=lambda c: c.data == "med:list")
    def cb_med_list(call):
        meds = db.list_medicines()
        if not meds:
            edit(call, "📋 لا توجد أدوية بعد.\nأضف أول دواء من الزر 👇",
                 _kb_medlist_footer())
            bot.answer_callback_query(call.id)
            return
        edit(call, f"📋 *الأدوية المسجلة: {len(meds)}*", None)
        for m in meds:
            send(call.message.chat.id, _med_card(m), _kb_med_card(m))
        send(call.message.chat.id, "⬇️", _kb_medlist_footer())
        bot.answer_callback_query(call.id)

    def _kb_medlist_footer():
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("➕ إضافة دواء",
                                          callback_data="med:add"),
               btn_home())
        return kb

    def _med_card(m) -> str:
        p = db.get_patient(m["patient_id"]) if m["patient_id"] else None
        days_ar = "، ".join(DAY_EN_TO_AR.get(d, d)
                            for d in (m["days"] or "").split(",") if d)
        state = "🟢 مفعّل" if m["active"] else "⏸ موقوف"
        return (
            f"💊 *{m['name']}*   {state}\n"
            f"👤 المريض: {p['name'] if p else '—'}\n"
            f"🔖 النوع: {m['medicine_type']}   |   🥄 الجرعة: {m['dosage']}\n"
            f"🗄 الخانة: {m['slot']}   |   ⏰ الوقت: {m['medicine_time']}\n"
            f"📅 الأيام: {days_ar}\n"
            f"🔔 التنبيهات: {m['reminder_count']} × كل "
            f"{m['reminder_interval']} د\n"
            f"📦 المتبقي: {m['remaining_quantity']}"
        )

    def _kb_med_card(m):
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton(
                "⏸ إيقاف" if m["active"] else "▶️ تفعيل",
                callback_data=f"med:tog:{m['id']}"),
            types.InlineKeyboardButton(
                "🗑 حذف", callback_data=f"med:del:{m['id']}"),
        )
        return kb

    @bot.callback_query_handler(func=lambda c: c.data.startswith("med:tog:"))
    def cb_med_tog(call):
        mid = int(call.data.split(":")[2])
        now_active = db.toggle_medicine(mid)
        m = db.get_medicine(mid)
        try:
            bot.edit_message_text(_med_card(m), call.message.chat.id,
                                  call.message.message_id,
                                  parse_mode="Markdown",
                                  reply_markup=_kb_med_card(m))
        except Exception:
            pass
        bot.answer_callback_query(
            call.id, "تم التفعيل ▶️" if now_active else "تم الإيقاف ⏸")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("med:del:"))
    def cb_med_del(call):
        mid = int(call.data.split(":")[2])
        m = db.get_medicine(mid)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("⚠️ نعم، احذف",
                                       callback_data=f"med:delok:{mid}"),
            types.InlineKeyboardButton("إلغاء",
                                       callback_data=f"med:keep:{mid}"),
        )
        edit(call, f"حذف الدواء *{m['name']}* مع كل سجلاته؟", kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("med:delok:"))
    def cb_med_delok(call):
        db.delete_medicine(int(call.data.split(":")[2]))
        edit(call, "🗑 تم حذف الدواء.", kb_back("med:list"))
        bot.answer_callback_query(call.id, "تم الحذف")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("med:keep:"))
    def cb_med_keep(call):
        m = db.get_medicine(int(call.data.split(":")[2]))
        if m:
            edit(call, _med_card(m), _kb_med_card(m))
        bot.answer_callback_query(call.id)

    # ========================================================
    # 💊 معالج إضافة دواء (Wizard) — خطوة بخطوة بأزرار
    # ========================================================
    @bot.callback_query_handler(func=lambda c: c.data == "med:add")
    def cb_med_add(call):
        pats = db.list_patients()
        if not pats:
            edit(call,
                 "⚠️ لا يوجد مرضى بعد.\n"
                 "أضف المريض أولاً ثم عُد لإضافة دوائه.",
                 kb_back())
            bot.answer_callback_query(call.id)
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for p in pats:
            kb.add(types.InlineKeyboardButton(
                f"👤 {p['name']}", callback_data=f"addmed:pat:{p['id']}"))
        kb.add(btn_home())
        edit(call,
             f"💊 *إضافة دواء جديد*\n{progress(1)}\n\n"
             "لمن هذا الدواء؟", kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("addmed:pat:"))
    def cb_addmed_pat(call):
        pid = int(call.data.split(":")[2])
        STATES[call.message.chat.id] = {
            "step": "med_name",
            "data": {"patient_id": pid,
                     "reminder_count": DEFAULT_REMINDER_COUNT,
                     "reminder_interval": DEFAULT_REMINDER_INTERVAL},
        }
        edit(call,
             f"💊 *إضافة دواء*\n{progress(2)}\n\n"
             "✍️ أرسل *اسم الدواء*:\n_مثال: أملور 5_",
             kb_back("home"))
        bot.answer_callback_query(call.id)

    # --- النوع (أزرار) ---
    def ask_type(chat_id):
        kb = types.InlineKeyboardMarkup(row_width=2)
        for i, t in enumerate(MEDICINE_TYPES):
            kb.add(types.InlineKeyboardButton(
                t, callback_data=f"addmed:type:{i}"))
        send(chat_id,
             f"💊 *إضافة دواء*\n{progress(3)}\n\nاختر *نوع الدواء*:", kb)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("addmed:type:"))
    def cb_addmed_type(call):
        st = STATES.get(call.message.chat.id)
        if not st:
            return bot.answer_callback_query(call.id)
        idx = int(call.data.split(":")[2])
        st["data"]["medicine_type"] = MEDICINE_TYPES[idx]
        st["step"] = "med_dosage"
        edit(call,
             f"💊 *إضافة دواء*\n{progress(4)}\n\n"
             "✍️ أرسل *الجرعة*:\n_مثال: حبة واحدة  •  5 مل  •  بختان_",
             None)
        bot.answer_callback_query(call.id)

    # --- الخانة (أزرار مع إظهار المشغولة) ---
    def ask_slot(chat_id):
        used = {m["slot"]: m["name"]
                for m in db.list_medicines(only_active=True)}
        kb = types.InlineKeyboardMarkup(row_width=2)
        for s in range(1, TOTAL_SLOTS + 1):
            label = (f"🗄 خانة {s} (فيها: {used[s]})" if s in used
                     else f"🗄 خانة {s} — فارغة")
            kb.add(types.InlineKeyboardButton(
                label, callback_data=f"addmed:slot:{s}"))
        send(chat_id,
             f"💊 *إضافة دواء*\n{progress(5)}\n\nاختر *الخانة*:", kb)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("addmed:slot:"))
    def cb_addmed_slot(call):
        st = STATES.get(call.message.chat.id)
        if not st:
            return bot.answer_callback_query(call.id)
        st["data"]["slot"] = int(call.data.split(":")[2])
        st["step"] = "med_time"
        edit(call,
             f"💊 *إضافة دواء*\n{progress(6)}\n\n"
             "⏰ أرسل *وقت الدواء* بصيغة 24 ساعة `HH:MM`:\n"
             "_مثال: 08:00 صباحاً  •  20:30 مساءً_",
             None)
        bot.answer_callback_query(call.id)

    # --- الأيام (اختيار متعدد بأزرار تتبدل ✅) ---
    def kb_days(selected: set) -> types.InlineKeyboardMarkup:
        kb = types.InlineKeyboardMarkup(row_width=2)
        row = []
        for ar, en in WEEK_DAYS:
            mark = "✅ " if en in selected else ""
            row.append(types.InlineKeyboardButton(
                f"{mark}{ar}", callback_data=f"addmed:day:{en}"))
            if len(row) == 2:
                kb.add(*row); row = []
        if row:
            kb.add(*row)
        kb.add(types.InlineKeyboardButton("📅 كل الأيام",
                                          callback_data="addmed:day:ALL"))
        kb.add(types.InlineKeyboardButton("✔️ تم — التالي",
                                          callback_data="addmed:day:DONE"))
        return kb

    def ask_days(chat_id):
        st = STATES[chat_id]
        st["data"].setdefault("days_set", set())
        send(chat_id,
             f"💊 *إضافة دواء*\n{progress(7)}\n\n"
             "📅 اختر *أيام الدواء* (اضغط لتحديد/إلغاء) ثم «تم»:",
             kb_days(st["data"]["days_set"]))

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("addmed:day:"))
    def cb_addmed_day(call):
        st = STATES.get(call.message.chat.id)
        if not st:
            return bot.answer_callback_query(call.id)
        token = call.data.split(":")[2]
        sel: set = st["data"].setdefault("days_set", set())
        if token == "ALL":
            sel.clear()
            sel.update(en for _, en in WEEK_DAYS)
        elif token == "DONE":
            if not sel:
                return bot.answer_callback_query(
                    call.id, "اختر يوماً واحداً على الأقل!", show_alert=True)
            # التالي: عدد التنبيهات
            kb = types.InlineKeyboardMarkup(row_width=5)
            kb.add(*[types.InlineKeyboardButton(
                str(n), callback_data=f"addmed:rc:{n}")
                for n in (1, 2, 3, 4, 5)])
            edit(call,
                 f"💊 *إضافة دواء*\n{progress(8)}\n\n"
                 "🔔 كم *تنبيهاً* يُرسل إن لم يأخذ الدواء؟\n"
                 "_(الافتراضي 3)_", kb)
            return bot.answer_callback_query(call.id)
        else:
            sel.symmetric_difference_update({token})
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id, call.message.message_id,
                reply_markup=kb_days(sel))
        except Exception:
            pass
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("addmed:rc:"))
    def cb_addmed_rc(call):
        st = STATES.get(call.message.chat.id)
        if not st:
            return bot.answer_callback_query(call.id)
        st["data"]["reminder_count"] = int(call.data.split(":")[2])
        kb = types.InlineKeyboardMarkup(row_width=4)
        kb.add(*[types.InlineKeyboardButton(
            f"{n} د", callback_data=f"addmed:ri:{n}")
            for n in (2, 5, 10, 15)])
        edit(call, "⏱ *الفاصل بين التنبيهات* بالدقائق؟\n_(الافتراضي 5)_",
             kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("addmed:ri:"))
    def cb_addmed_ri(call):
        st = STATES.get(call.message.chat.id)
        if not st:
            return bot.answer_callback_query(call.id)
        st["data"]["reminder_interval"] = int(call.data.split(":")[2])
        st["step"] = "med_photo"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⏭ تخطي الصورة",
                                          callback_data="addmed:nophoto"))
        edit(call,
             "📷 أرسل *صورة الدواء* (تُرفق مع كل تذكير ليعرف المريض "
             "شكله)\nأو اضغط تخطي:", kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "addmed:nophoto")
    def cb_addmed_nophoto(call):
        st = STATES.get(call.message.chat.id)
        if not st:
            return bot.answer_callback_query(call.id)
        st["data"]["photo_id"] = None
        show_summary(call.message.chat.id)
        bot.answer_callback_query(call.id)

    # --- الملخص والحفظ ---
    def show_summary(chat_id):
        st = STATES.get(chat_id)
        d = st["data"]
        st["step"] = "med_confirm"
        p = db.get_patient(d["patient_id"])
        days_ar = "، ".join(DAY_EN_TO_AR[e] for _, e in WEEK_DAYS
                            if e in d["days_set"])
        text = (
            "🧾 *مراجعة أخيرة قبل الحفظ:*\n\n"
            f"👤 المريض: {p['name']}\n"
            f"💊 الدواء: *{d['name']}*\n"
            f"🔖 النوع: {d['medicine_type']}\n"
            f"🥄 الجرعة: {d['dosage']}\n"
            f"🗄 الخانة: {d['slot']}\n"
            f"⏰ الوقت: {d['medicine_time']}\n"
            f"📅 الأيام: {days_ar}\n"
            f"🔔 التنبيهات: {d['reminder_count']} × كل "
            f"{d['reminder_interval']} د\n"
            f"📷 صورة: {'نعم ✅' if d.get('photo_id') else 'لا'}"
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("✅ حفظ", callback_data="addmed:save"),
            types.InlineKeyboardButton("❌ إلغاء", callback_data="home"),
        )
        send(chat_id, text, kb)

    @bot.callback_query_handler(func=lambda c: c.data == "addmed:save")
    def cb_addmed_save(call):
        st = STATES.pop(call.message.chat.id, None)
        if not st:
            return bot.answer_callback_query(call.id)
        d = st["data"]
        days = ",".join(en for _, en in WEEK_DAYS if en in d["days_set"])
        db.add_medicine(
            patient_id=d["patient_id"], name=d["name"],
            medicine_type=d["medicine_type"], dosage=d["dosage"],
            slot=d["slot"], medicine_time=d["medicine_time"], days=days,
            photo_id=d.get("photo_id"),
            reminder_count=d["reminder_count"],
            reminder_interval=d["reminder_interval"])
        edit(call,
             f"🎉 *تمت إضافة الدواء بنجاح!*\n\n"
             f"سيبدأ التذكير تلقائياً عند الموعد ⏰\n"
             f"({d['medicine_time']} — خانة {d['slot']})",
             kb_main())
        bot.answer_callback_query(call.id, "تم الحفظ ✅")

    # ========================================================
    # 📊 التقارير
    # ========================================================
    @bot.callback_query_handler(func=lambda c: c.data == "rep:menu")
    def cb_rep_menu(call):
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton("📅 سجل اليوم",
                                       callback_data="rep:days:1"),
            types.InlineKeyboardButton("🗓 آخر 7 أيام",
                                       callback_data="rep:days:7"),
            types.InlineKeyboardButton("📈 نسبة الالتزام (30 يوماً)",
                                       callback_data="rep:stats"),
        )
        kb.add(btn_home())
        edit(call, "📊 *التقارير والإحصائيات*\n\nاختر تقريراً:", kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("rep:days:"))
    def cb_rep_days(call):
        days = int(call.data.split(":")[2])
        rows = db.list_history(days=days)
        title = "📅 *سجل اليوم*" if days == 1 else f"🗓 *سجل آخر {days} أيام*"
        if not rows:
            edit(call, f"{title}\n\nلا توجد جرعات مسجلة في هذه الفترة.",
                 kb_back("rep:menu"))
            return bot.answer_callback_query(call.id)
        lines = [title, ""]
        for r in rows[:25]:
            icon = STATUS_ICON.get(r["status"], "•")
            when = r["scheduled_time"][5:]  # MM-DD HH:MM
            extra = (f" (+{r['delay_minutes']}د)"
                     if r["status"] == STATUS_LATE else "")
            lines.append(
                f"{icon} `{when}` — *{r['medicine_name']}* "
                f"({STATUS_AR.get(r['status'], r['status'])}{extra})")
        if len(rows) > 25:
            lines.append(f"\n… و{len(rows) - 25} سجلاً أقدم")
        edit(call, "\n".join(lines), kb_back("rep:menu"))
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "rep:stats")
    def cb_rep_stats(call):
        s = db.stats_summary(days=30)
        pct = s["adherence_pct"]
        filled = round(pct / 10)
        bar = "🟩" * filled + "⬜" * (10 - filled)
        text = (
            "📈 *نسبة الالتزام — آخر 30 يوماً*\n\n"
            f"{bar}  *{pct}%*\n\n"
            f"✅ أُخذت بوقتها: *{s['taken']}*\n"
            f"🟡 أُخذت متأخرة: *{s['late']}*\n"
            f"❌ فاتت: *{s['missed']}*\n"
            f"⏳ قيد الانتظار: *{s['pending']}*\n"
            f"━━━━━━━━━━━━\n"
            f"📦 الإجمالي: *{s['total']}* جرعة"
        )
        edit(call, text, kb_back("rep:menu"))
        bot.answer_callback_query(call.id)

    # ========================================================
    # ⚙️ الإعدادات
    # ========================================================
    @bot.callback_query_handler(func=lambda c: c.data == "set:menu")
    def cb_set_menu(call):
        cur = db.get_setting_int(SETTING_PICKUP_GRACE,
                                 DEFAULT_PICKUP_GRACE_MINUTES)
        kb = types.InlineKeyboardMarkup(row_width=3)
        row = []
        for m in PICKUP_GRACE_CHOICES:
            label = f"{'✅ ' if m == cur else ''}{m} د"
            row.append(types.InlineKeyboardButton(
                label, callback_data=f"set:grace:{m}"))
            if len(row) == 3:
                kb.add(*row); row = []
        if row:
            kb.add(*row)
        kb.add(btn_home())
        edit(call,
             "⚙️ *الإعدادات*\n\n"
             "⏳ *مدة انتظار رفع العلبة*\n"
             "بعد فوات الموعد، يبقى رفع العلبة يُسجَّل كأخذٍ متأخر "
             "طوال هذه المدة.\n\n"
             f"الحالية: *{cur} دقيقة* — اختر قيمة جديدة:", kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("set:grace:"))
    def cb_set_grace(call):
        val = int(call.data.split(":")[2])
        db.set_setting(SETTING_PICKUP_GRACE, val)
        bot.answer_callback_query(call.id, f"تم الضبط على {val} دقيقة ✅")
        cb_set_menu(call)

    # ========================================================
    # استقبال الصور (خطوة صورة الدواء)
    # ========================================================
    @bot.message_handler(content_types=["photo"])
    def on_photo(message):
        st = STATES.get(message.chat.id)
        if st and st.get("step") == "med_photo":
            st["data"]["photo_id"] = message.photo[-1].file_id
            send(message.chat.id, "📷 تم حفظ الصورة ✅")
            show_summary(message.chat.id)
        else:
            send(message.chat.id,
                 "📷 وصلتني الصورة، لكن لا توجد عملية إضافة دواء جارية.\n"
                 "أرسل /start للقائمة.")

    # ========================================================
    # استقبال النصوص (خطوات الكتابة في المعالجات)
    # ========================================================
    @bot.message_handler(func=lambda m: True, content_types=["text"])
    def on_text(message):
        chat_id = message.chat.id
        text = (message.text or "").strip()
        st = STATES.get(chat_id)

        if not st:
            send(chat_id,
                 f"👋 أهلاً! أنا *{BOT_NAME}*\n"
                 "استخدم الأزرار بالأسفل 👇", kb_main())
            return

        step = st.get("step")
        d = st["data"]

        # ---- إضافة مريض ----
        if step == "pat_name":
            d["name"] = text
            st["step"] = "pat_tid"
            send(chat_id,
                 "🆔 أرسل *معرّف تيليجرام* للمريض (رقم):\n"
                 "_يحصل عليه من_ @userinfobot\n"
                 "أو أرسل `0` إن لم يكن لديه تيليجرام "
                 "(سيعمل التنبيه الضوئي والصوتي فقط).")
        elif step == "pat_tid":
            tid = text if text.isdigit() and text != "0" else ""
            db.add_patient(d["name"], tid)
            STATES.pop(chat_id, None)
            send(chat_id,
                 f"✅ تمت إضافة المريض *{d['name']}*\n"
                 "الخطوة التالية المقترحة: أضف له مشرفاً ثم أدويته 👇",
                 kb_main())

        # ---- إضافة مشرف ----
        elif step == "sup_name":
            d["name"] = text
            st["step"] = "sup_tid"
            send(chat_id,
                 "🆔 أرسل *معرّف تيليجرام* للمشرف (رقم):\n"
                 "_يحصل عليه من_ @userinfobot")
        elif step == "sup_tid":
            if not text.isdigit():
                send(chat_id,
                     "⚠️ المعرّف يجب أن يكون *رقماً*.\n"
                     "جرّب مرة أخرى، أو احصل عليه من @userinfobot")
                return
            db.add_supervisor(d["patient_id"], d["name"], text)
            STATES.pop(chat_id, None)
            send(chat_id,
                 f"✅ تمت إضافة المشرف *{d['name']}*\n"
                 "سيصله إنذار فوري عند فوات أي جرعة 🔔", kb_main())

        # ---- معالج إضافة دواء (الخطوات النصية) ----
        elif step == "med_name":
            d["name"] = text
            st["step"] = "med_type"
            ask_type(chat_id)
        elif step == "med_dosage":
            d["dosage"] = text
            st["step"] = "med_slot"
            ask_slot(chat_id)
        elif step == "med_time":
            try:
                t = datetime.strptime(text.replace("：", ":"), "%H:%M")
                d["medicine_time"] = t.strftime("%H:%M")
            except ValueError:
                send(chat_id,
                     "⚠️ الصيغة غير صحيحة.\n"
                     "أرسل الوقت هكذا: `08:00` أو `20:30`")
                return
            st["step"] = "med_days"
            ask_days(chat_id)
        else:
            send(chat_id, "استخدم الأزرار 👇", kb_main())
