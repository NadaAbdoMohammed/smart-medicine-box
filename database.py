# ============================================================
# database.py
# ------------------------------------------------------------
# قاعدة بيانات SQLite للمشروع — الملف الوحيد المسؤول عن التخزين.
#
# الجداول:
#   patients     : المرضى (الاسم + معرف تيليجرام)
#   supervisors  : المشرفون، كل مشرف مرتبط بمريض
#   medicines    : الأدوية (الاسم، النوع، الجرعة، الخانة، الوقت،
#                  الأيام، الصورة، الكمية، إعدادات التنبيه)
#   history      : سجل كل جرعة (pending / taken / missed / late)
#   settings     : إعدادات قابلة للتغيير من البوت (مثل نافذة الانتظار)
#
# ملاحظة: الاتصال مشترك بين عدة Threads (البوت + المجدول + Flask)
# لذلك نستخدم قفلاً (Lock) حول كل عملية.
# ============================================================

import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from config import (
    DATABASE_PATH,
    STATUS_TAKEN, STATUS_MISSED, STATUS_PENDING, STATUS_LATE,
    DEFAULT_REMINDER_COUNT, DEFAULT_REMINDER_INTERVAL,
)


class Database:
    """كل التعامل مع SQLite يمر من هنا."""

    def __init__(self, path: str = DATABASE_PATH) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    # --------------------------------------------------------
    # إنشاء الجداول (تعمل مرة واحدة — IF NOT EXISTS)
    # --------------------------------------------------------
    def _create_tables(self) -> None:
        with self._lock:
            c = self._conn.cursor()
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS patients (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL,
                    telegram_id TEXT
                );

                CREATE TABLE IF NOT EXISTS supervisors (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id  INTEGER NOT NULL,
                    name        TEXT NOT NULL,
                    telegram_id TEXT,
                    FOREIGN KEY (patient_id) REFERENCES patients (id)
                );

                CREATE TABLE IF NOT EXISTS medicines (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id         INTEGER,
                    name               TEXT NOT NULL,
                    medicine_type      TEXT DEFAULT '',
                    dosage             TEXT DEFAULT '',
                    slot               INTEGER NOT NULL,
                    medicine_time      TEXT NOT NULL,      -- "HH:MM"
                    days               TEXT NOT NULL,      -- "Sat,Sun,Mon"
                    photo_id           TEXT,               -- Telegram file_id
                    remaining_quantity INTEGER DEFAULT 30,
                    reminder_count     INTEGER DEFAULT {rc},
                    reminder_interval  INTEGER DEFAULT {ri},
                    active             INTEGER DEFAULT 1,
                    FOREIGN KEY (patient_id) REFERENCES patients (id)
                );

                CREATE TABLE IF NOT EXISTS history (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    medicine_id    INTEGER NOT NULL,
                    scheduled_time TEXT NOT NULL,          -- "YYYY-MM-DD HH:MM"
                    taken_time     TEXT,
                    delay_minutes  INTEGER DEFAULT 0,
                    status         TEXT DEFAULT 'pending',
                    FOREIGN KEY (medicine_id) REFERENCES medicines (id)
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                """.format(rc=DEFAULT_REMINDER_COUNT,
                           ri=DEFAULT_REMINDER_INTERVAL)
            )
            self._conn.commit()

    # --------------------------------------------------------
    # أدوات داخلية
    # --------------------------------------------------------
    def _rows(self, query: str, params=()) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def _row(self, query: str, params=()) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(query, params)
            r = cur.fetchone()
            return dict(r) if r else None

    def _exec(self, query: str, params=()) -> int:
        with self._lock:
            cur = self._conn.execute(query, params)
            self._conn.commit()
            return cur.lastrowid

    # ========================================================
    # المرضى
    # ========================================================
    def add_patient(self, name: str, telegram_id: str = "") -> int:
        return self._exec(
            "INSERT INTO patients (name, telegram_id) VALUES (?, ?)",
            (name.strip(), str(telegram_id).strip()))

    def list_patients(self) -> List[Dict[str, Any]]:
        return self._rows("SELECT * FROM patients ORDER BY id")

    def get_patient(self, patient_id: int) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM patients WHERE id = ?",
                         (patient_id,))

    def delete_patient(self, patient_id: int) -> None:
        # حذف المريض يحذف مشرفيه وأدويته وسجلاتها (تنظيف كامل)
        med_ids = [m["id"] for m in self._rows(
            "SELECT id FROM medicines WHERE patient_id = ?", (patient_id,))]
        for mid in med_ids:
            self._exec("DELETE FROM history WHERE medicine_id = ?", (mid,))
        self._exec("DELETE FROM medicines WHERE patient_id = ?", (patient_id,))
        self._exec("DELETE FROM supervisors WHERE patient_id = ?", (patient_id,))
        self._exec("DELETE FROM patients WHERE id = ?", (patient_id,))

    # ========================================================
    # المشرفون
    # ========================================================
    def add_supervisor(self, patient_id: int, name: str,
                       telegram_id: str = "") -> int:
        return self._exec(
            "INSERT INTO supervisors (patient_id, name, telegram_id) "
            "VALUES (?, ?, ?)",
            (patient_id, name.strip(), str(telegram_id).strip()))

    def list_supervisors(self, patient_id: int) -> List[Dict[str, Any]]:
        return self._rows(
            "SELECT * FROM supervisors WHERE patient_id = ? ORDER BY id",
            (patient_id,))

    def list_all_supervisors(self) -> List[Dict[str, Any]]:
        return self._rows(
            "SELECT s.*, p.name AS patient_name FROM supervisors s "
            "LEFT JOIN patients p ON p.id = s.patient_id ORDER BY s.id")

    def delete_supervisor(self, supervisor_id: int) -> None:
        self._exec("DELETE FROM supervisors WHERE id = ?", (supervisor_id,))

    # ========================================================
    # الأدوية
    # ========================================================
    def add_medicine(self, patient_id: int, name: str, medicine_type: str,
                     dosage: str, slot: int, medicine_time: str, days: str,
                     photo_id: str = None, quantity: int = 30,
                     reminder_count: int = DEFAULT_REMINDER_COUNT,
                     reminder_interval: int = DEFAULT_REMINDER_INTERVAL,
                     ) -> int:
        return self._exec(
            "INSERT INTO medicines (patient_id, name, medicine_type, dosage,"
            " slot, medicine_time, days, photo_id, remaining_quantity,"
            " reminder_count, reminder_interval, active)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (patient_id, name.strip(), medicine_type, dosage, slot,
             medicine_time, days, photo_id, quantity,
             reminder_count, reminder_interval))

    def list_medicines(self, only_active: bool = False,
                       patient_id: int = None) -> List[Dict[str, Any]]:
        q = "SELECT * FROM medicines WHERE 1=1"
        params: list = []
        if only_active:
            q += " AND active = 1"
        if patient_id is not None:
            q += " AND patient_id = ?"
            params.append(patient_id)
        q += " ORDER BY medicine_time, slot"
        return self._rows(q, tuple(params))

    def get_medicine(self, medicine_id: int) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM medicines WHERE id = ?",
                         (medicine_id,))

    def get_medicine_by_slot(self, slot: int) -> Optional[Dict[str, Any]]:
        """آخر دواء نشِط معرَّف على هذه الخانة (يستخدمه المجدول عند الرفع)."""
        return self._row(
            "SELECT * FROM medicines WHERE slot = ? AND active = 1 "
            "ORDER BY id DESC LIMIT 1", (slot,))

    def delete_medicine(self, medicine_id: int) -> None:
        self._exec("DELETE FROM history WHERE medicine_id = ?", (medicine_id,))
        self._exec("DELETE FROM medicines WHERE id = ?", (medicine_id,))

    def toggle_medicine(self, medicine_id: int) -> bool:
        """يقلب حالة التفعيل ويعيد الحالة الجديدة."""
        med = self.get_medicine(medicine_id)
        if not med:
            return False
        new_val = 0 if med["active"] else 1
        self._exec("UPDATE medicines SET active = ? WHERE id = ?",
                   (new_val, medicine_id))
        return bool(new_val)

    def decrement_quantity(self, medicine_id: int) -> None:
        self._exec(
            "UPDATE medicines SET remaining_quantity = "
            "MAX(remaining_quantity - 1, 0) WHERE id = ?", (medicine_id,))

    # ========================================================
    # السجل History
    # ========================================================
    def add_history(self, medicine_id: int, scheduled_time: str,
                    status: str = STATUS_PENDING) -> int:
        return self._exec(
            "INSERT INTO history (medicine_id, scheduled_time, status) "
            "VALUES (?, ?, ?)", (medicine_id, scheduled_time, status))

    def get_history_at(self, medicine_id: int,
                       scheduled_time: str) -> Optional[Dict[str, Any]]:
        """هل يوجد سجل (بأي حالة) لهذا الدواء بهذا الموعد بالتحديد؟"""
        return self._row(
            "SELECT * FROM history WHERE medicine_id = ? "
            "AND scheduled_time = ?", (medicine_id, scheduled_time))

    def get_history_by_id(self, history_id: int) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM history WHERE id = ?", (history_id,))

    def mark_taken(self, history_id: int, taken_time: str,
                   delay_minutes: int = 0) -> None:
        """يعلّم السجل مأخوذاً — متأخراً (late) إن كان التأخير > 0."""
        status = STATUS_LATE if delay_minutes > 0 else STATUS_TAKEN
        self._exec(
            "UPDATE history SET status = ?, taken_time = ?, "
            "delay_minutes = ? WHERE id = ?",
            (status, taken_time, delay_minutes, history_id))

    def mark_missed(self, history_id: int) -> None:
        self._exec("UPDATE history SET status = ? WHERE id = ?",
                   (STATUS_MISSED, history_id))

    def get_latest_open_history(self, medicine_id: int,
                                grace_minutes: int
                                ) -> Optional[Dict[str, Any]]:
        """آخر جرعة «مفتوحة» (pending أو missed) لهذا الدواء وقع موعدها
        خلال نافذة الانتظار الأخيرة. تستخدمها confirm_taken في المجدول
        لقبول الأخذ المتأخر عبر الحساس."""
        cutoff = (datetime.now() - timedelta(minutes=grace_minutes)
                  ).strftime("%Y-%m-%d %H:%M")
        return self._row(
            "SELECT * FROM history WHERE medicine_id = ? "
            "AND status IN (?, ?) AND scheduled_time >= ? "
            "ORDER BY scheduled_time DESC LIMIT 1",
            (medicine_id, STATUS_PENDING, STATUS_MISSED, cutoff))

    def list_history(self, days: int = 7,
                     patient_id: int = None) -> List[Dict[str, Any]]:
        """السجل الأخير موصولاً بأسماء الدواء والمريض (للتقارير)."""
        cutoff = (datetime.now() - timedelta(days=days)
                  ).strftime("%Y-%m-%d %H:%M")
        q = ("SELECT h.*, m.name AS medicine_name, m.slot AS slot, "
             "m.patient_id AS patient_id, p.name AS patient_name "
             "FROM history h "
             "JOIN medicines m ON m.id = h.medicine_id "
             "LEFT JOIN patients p ON p.id = m.patient_id "
             "WHERE h.scheduled_time >= ?")
        params: list = [cutoff]
        if patient_id is not None:
            q += " AND m.patient_id = ?"
            params.append(patient_id)
        q += " ORDER BY h.scheduled_time DESC"
        return self._rows(q, tuple(params))

    # ========================================================
    # الإحصائيات
    # ========================================================
    def stats_summary(self, days: int = 30) -> Dict[str, Any]:
        cutoff = (datetime.now() - timedelta(days=days)
                  ).strftime("%Y-%m-%d %H:%M")
        rows = self._rows(
            "SELECT status, COUNT(*) AS n FROM history "
            "WHERE scheduled_time >= ? GROUP BY status", (cutoff,))
        counts = {r["status"]: r["n"] for r in rows}
        taken = counts.get(STATUS_TAKEN, 0)
        late = counts.get(STATUS_LATE, 0)
        missed = counts.get(STATUS_MISSED, 0)
        pending = counts.get(STATUS_PENDING, 0)
        total = taken + late + missed + pending
        closed = taken + late + missed  # الجرعات المحسومة فقط
        adherence = round(100 * (taken + late) / closed) if closed else 100
        return {
            "days": days, "total": total, "taken": taken, "late": late,
            "missed": missed, "pending": pending,
            "adherence_pct": adherence,
        }

    # ========================================================
    # الإعدادات (تُعدَّل من قائمة ⚙️ في البوت)
    # ========================================================
    def set_setting(self, key: str, value) -> None:
        self._exec(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)))

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._row("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def get_setting_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get_setting(key, str(default)))
        except (TypeError, ValueError):
            return default


# ============================================================
# النسخة الوحيدة المشتركة التي تستوردها بقية الملفات:
#   from database import db
# ============================================================
db = Database()
