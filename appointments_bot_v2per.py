import os
import sqlite3
import logging
import calendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# =========================
# إعدادات عامة
# =========================
BOT_TOKEN = "8685835901:AAFosk0SiuSNykeyth-rzwQIYXnDexnVuPI"
TIMEZONE = os.getenv("BOT_TIMEZONE", "Asia/Riyadh")
DB_PATH = os.getenv("BOT_DB_PATH", "appointments.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# حالات المحادثة
TITLE, CAL_NAV, TIME_HOUR, TIME_MINUTE, REM_UNIT, REM_AMOUNT, CONFIRM_DELETE = range(7)

ARABIC_MONTHS = [
    "", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"
]

# =========================
# قاعدة البيانات
# =========================
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            event_time TEXT NOT NULL,
            remind_minutes INTEGER NOT NULL DEFAULT 0,
            sent_reminder INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    # دعم الترقية من الإصدار القديم إذا كان الجدول موجوداً بالأعمدة القديمة
    try:
        cur.execute("ALTER TABLE appointments ADD COLUMN remind_minutes INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE appointments ADD COLUMN sent_reminder INTEGER NOT NULL DEFAULT 0")
        # نقل البيانات القديمة: إذا كان هناك remind_1day → 1440 دقيقة، remind_1hour → 60 دقيقة
        cur.execute("""
            UPDATE appointments SET remind_minutes = 1440
            WHERE remind_1day = 1 AND remind_minutes = 0
        """)
        cur.execute("""
            UPDATE appointments SET remind_minutes = 60
            WHERE remind_1hour = 1 AND remind_minutes = 0
        """)
    except Exception:
        pass
    conn.commit()
    conn.close()

def get_conn():
    return sqlite3.connect(DB_PATH)

def add_appointment(chat_id, title, event_time, remind_minutes):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO appointments (chat_id, title, event_time, remind_minutes, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (chat_id, title, event_time.isoformat(), int(remind_minutes), now_local().isoformat()))
    conn.commit()
    conn.close()

def list_appointments(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, event_time, remind_minutes
        FROM appointments
        WHERE chat_id = ? AND event_time > ?
        ORDER BY event_time ASC
    """, (chat_id, now_local().isoformat()))
    rows = cur.fetchall()
    conn.close()
    return rows

def delete_appointment(chat_id, appointment_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM appointments WHERE chat_id = ? AND id = ?", (chat_id, appointment_id))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def get_appointment(chat_id, appointment_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, event_time, remind_minutes
        FROM appointments WHERE chat_id = ? AND id = ?
    """, (chat_id, appointment_id))
    row = cur.fetchone()
    conn.close()
    return row

def get_pending_reminders(current_time):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, chat_id, title, event_time, remind_minutes, sent_reminder
        FROM appointments
    """)
    rows = cur.fetchall()
    conn.close()

    due = []
    for row in rows:
        appointment_id, chat_id, title, event_time_str, remind_minutes, sent_reminder = row
        if remind_minutes == 0 or sent_reminder:
            continue
        event_dt = datetime.fromisoformat(event_time_str)
        remind_at = event_dt - timedelta(minutes=remind_minutes)
        if current_time >= remind_at:
            due.append((appointment_id, chat_id, title, event_dt, remind_minutes))
    return due

def mark_sent(appointment_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE appointments SET sent_reminder = 1 WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()

def cleanup_old_appointments(current_time):
    conn = get_conn()
    cur = conn.cursor()
    threshold = (current_time - timedelta(days=2)).isoformat()
    cur.execute("DELETE FROM appointments WHERE event_time < ?", (threshold,))
    conn.commit()
    conn.close()

# =========================
# أدوات مساعدة
# =========================
def now_local():
    return datetime.now(ZoneInfo(TIMEZONE))

def fmt_dt(dt):
    return dt.astimezone(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M")

def fmt_dt_ar(dt):
    """تنسيق التاريخ بشكل جميل بالعربي بنظام 12 ساعة"""
    d = dt.astimezone(ZoneInfo(TIMEZONE))
    hour_12 = d.strftime("%I").lstrip("0") or "12"
    minute = d.strftime("%M")
    period = "صباحاً" if d.hour < 12 else "مساءً"
    return f"{d.day} {ARABIC_MONTHS[d.month]} {d.year} الساعة {hour_12}:{minute} {period}"

def build_reminder_label(remind_minutes):
    if remind_minutes == 0:
        return "بدون تذكير"
    if remind_minutes % 1440 == 0:
        days = remind_minutes // 1440
        return f"قبل {days} يوم" if days == 1 else f"قبل {days} أيام"
    if remind_minutes % 60 == 0:
        hours = remind_minutes // 60
        return f"قبل {hours} ساعة" if hours == 1 else f"قبل {hours} ساعات"
    return f"قبل {remind_minutes} دقيقة"

def time_until(event_dt):
    diff = event_dt - now_local()
    if diff.total_seconds() < 0:
        return "انتهى"
    days = diff.days
    hours = diff.seconds // 3600
    minutes = (diff.seconds % 3600) // 60
    if days > 0:
        return f"بعد {days} يوم و{hours} ساعة"
    elif hours > 0:
        return f"بعد {hours} ساعة و{minutes} دقيقة"
    else:
        return f"بعد {minutes} دقيقة"

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة موعد", callback_data="menu_add")],
        [InlineKeyboardButton("📋 مواعيدي", callback_data="menu_list")],
        [InlineKeyboardButton("❓ المساعدة", callback_data="menu_help")],
    ])

def build_calendar(year, month):
    """بناء تقويم تفاعلي"""
    keyboard = []
    header = [
        InlineKeyboardButton("◀️", callback_data=f"cal_prev_{year}_{month}"),
        InlineKeyboardButton(f"📅 {ARABIC_MONTHS[month]} {year}", callback_data="cal_ignore"),
        InlineKeyboardButton("▶️", callback_data=f"cal_next_{year}_{month}"),
    ]
    keyboard.append(header)
    keyboard.append([
        InlineKeyboardButton(d, callback_data="cal_ignore")
        for d in ["أح", "اث", "ث", "أر", "خ", "ج", "س"]
    ])
    cal = calendar.monthcalendar(year, month)
    today = now_local()
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal_ignore"))
            else:
                day_dt = datetime(year, month, day, 23, 59, tzinfo=ZoneInfo(TIMEZONE))
                if day_dt < today:
                    row.append(InlineKeyboardButton(f"·{day}·", callback_data="cal_past"))
                else:
                    marker = "🔹" if (year == today.year and month == today.month and day == today.day) else ""
                    row.append(InlineKeyboardButton(f"{marker}{day}", callback_data=f"cal_day_{year}_{month}_{day}"))
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="conv_cancel")])
    return InlineKeyboardMarkup(keyboard)

def build_hour_keyboard():
    """اختيار الساعة بنظام 12 ساعة"""
    rows = []
    am_hours = [(12, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5),
                (6, 6), (7, 7), (8, 8), (9, 9), (10, 10), (11, 11)]
    pm_hours = [(12, 12), (1, 13), (2, 14), (3, 15), (4, 16), (5, 17),
                (6, 18), (7, 19), (8, 20), (9, 21), (10, 22), (11, 23)]
    rows.append([InlineKeyboardButton("🌅 صباحاً (AM)", callback_data="cal_ignore")])
    for i in range(0, len(am_hours), 6):
        row = [InlineKeyboardButton(f"{display:02d}", callback_data=f"hour_{h24}")
               for display, h24 in am_hours[i:i+6]]
        rows.append(row)
    rows.append([InlineKeyboardButton("🌆 مساءً (PM)", callback_data="cal_ignore")])
    for i in range(0, len(pm_hours), 6):
        row = [InlineKeyboardButton(f"{display:02d}", callback_data=f"hour_{h24}")
               for display, h24 in pm_hours[i:i+6]]
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="conv_cancel")])
    return InlineKeyboardMarkup(rows)

def build_minute_keyboard():
    """اختيار الدقائق"""
    minutes = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]
    rows = []
    for i in range(0, len(minutes), 4):
        row = [InlineKeyboardButton(f":{m:02d}", callback_data=f"min_{m}") for m in minutes[i:i+4]]
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="conv_cancel")])
    return InlineKeyboardMarkup(rows)

def build_reminder_unit_keyboard():
    """اختيار وحدة التذكير"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱ دقائق", callback_data="rem_unit_minutes"),
         InlineKeyboardButton("🕐 ساعات", callback_data="rem_unit_hours"),
         InlineKeyboardButton("📆 أيام",  callback_data="rem_unit_days")],
        [InlineKeyboardButton("🔕 بدون تذكير", callback_data="rem_unit_none")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="conv_cancel")],
    ])

def build_reminder_amount_keyboard(unit):
    """اختيار الكمية حسب الوحدة"""
    if unit == "minutes":
        amounts = [5, 10, 15, 20, 30, 45, 60, 90, 120]
        labels  = ["5د", "10د", "15د", "20د", "30د", "45د", "ساعة", "1.5س", "2س"]
        multiplier = 1
    elif unit == "hours":
        amounts = [1, 2, 3, 4, 6, 8, 12, 18, 24]
        labels  = ["1س", "2س", "3س", "4س", "6س", "8س", "12س", "18س", "يوم"]
        multiplier = 60
    else:  # days
        amounts = [1, 2, 3, 4, 5, 6, 7, 10, 14]
        labels  = ["1ي", "2ي", "3ي", "4ي", "5ي", "6ي", "أسبوع", "10ي", "أسبوعين"]
        multiplier = 1440

    rows = []
    pairs = list(zip(amounts, labels))
    for i in range(0, len(pairs), 3):
        row = [
            InlineKeyboardButton(lbl, callback_data=f"rem_val_{amt * multiplier}")
            for amt, lbl in pairs[i:i+3]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 رجوع للوحدة", callback_data="rem_back_unit")])
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="conv_cancel")])
    return InlineKeyboardMarkup(rows)

# =========================
# القائمة الرئيسية
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "أهلاً 👋 أنا بوت المواعيد. كيف أقدر أساعدك؟"
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu_keyboard())
    else:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu_keyboard())

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_add":
        await query.edit_message_text("✏️ أرسل اسم الموعد:")
        return

    elif data == "menu_list":
        await show_list(query, context)

    elif data == "menu_help":
        await query.edit_message_text(
            "📖 *دليل الاستخدام*\n\n"
            "• اضغط *إضافة موعد* وأدخل الاسم، ثم اختر التاريخ والوقت من التقويم\n"
            "• في *مواعيدي* تقدر تشوف وتحذف مواعيدك مباشرة\n"
            "• يمكنك تحديد وقت التذكير بدقة: دقائق، ساعات، أو أيام قبل الموعد\n\n"
            "الأوامر السريعة:\n"
            "/add · /list · /delete",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="menu_back")
            ]])
        )

    elif data == "menu_back":
        await start(update, context)

async def show_list(query_or_update, context, edit=True):
    """عرض قائمة المواعيد مع أزرار الحذف"""
    if hasattr(query_or_update, 'message'):
        chat_id = query_or_update.message.chat_id
    else:
        chat_id = query_or_update.effective_chat.id

    appointments = list_appointments(chat_id)

    if not appointments:
        text = "📭 ما عندك مواعيد قادمة."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("➕ إضافة موعد", callback_data="menu_add")]])
    else:
        text = "📋 *مواعيدك القادمة:*\n\n"
        buttons = []
        for aid, title, event_time_str, remind_mins in appointments:
            event_dt = datetime.fromisoformat(event_time_str)
            text += f"*{title}*\n🕐 {fmt_dt_ar(event_dt)}\n⏳ {time_until(event_dt)}\n🔔 {build_reminder_label(remind_mins)}\n\n"
            buttons.append([InlineKeyboardButton(f"🗑 حذف: {title[:25]}", callback_data=f"del_ask_{aid}")])
        buttons.append([InlineKeyboardButton("➕ إضافة موعد", callback_data="menu_add")])
        markup = InlineKeyboardMarkup(buttons)

    if hasattr(query_or_update, 'edit_message_text') and edit:
        await query_or_update.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    elif hasattr(query_or_update, 'message'):
        await query_or_update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)

# =========================
# إضافة موعد – المحادثة
# =========================
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("✏️ أرسل اسم الموعد:")
    else:
        await update.message.reply_text("✏️ أرسل اسم الموعد:")
    return TITLE

async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = update.message.text.strip()
    if len(title) > 100:
        await update.message.reply_text("⚠️ الاسم طويل جداً، اكتب أقل من 100 حرف.")
        return TITLE
    context.user_data["title"] = title
    today = now_local()
    await update.message.reply_text(
        "📅 اختر تاريخ الموعد:",
        reply_markup=build_calendar(today.year, today.month)
    )
    return CAL_NAV

async def calendar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data in ("cal_ignore", "cal_past"):
        return CAL_NAV

    if data == "conv_cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ تم الإلغاء.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    if data.startswith("cal_prev_") or data.startswith("cal_next_"):
        parts = data.split("_")
        direction = parts[1]
        year, month = int(parts[2]), int(parts[3])
        if direction == "prev":
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        else:
            month += 1
            if month == 13:
                month = 1
                year += 1
        await query.edit_message_reply_markup(build_calendar(year, month))
        return CAL_NAV

    if data.startswith("cal_day_"):
        _, _, year, month, day = data.split("_")
        year, month, day = int(year), int(month), int(day)
        context.user_data["year"] = year
        context.user_data["month"] = month
        context.user_data["day"] = day
        await query.edit_message_text(
            f"📅 التاريخ: {day} {ARABIC_MONTHS[month]} {year}\n\n🕐 اختر الساعة:",
            reply_markup=build_hour_keyboard()
        )
        return TIME_HOUR

    return CAL_NAV

async def time_hour_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "conv_cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ تم الإلغاء.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    if data.startswith("hour_"):
        hour = int(data.split("_")[1])
        context.user_data["hour"] = hour
        y, m, d = context.user_data["year"], context.user_data["month"], context.user_data["day"]
        h12 = hour % 12 or 12
        period_disp = "صباحاً" if hour < 12 else "مساءً"
        await query.edit_message_text(
            f"📅 التاريخ: {d} {ARABIC_MONTHS[m]} {y}\n🕐 الساعة: {h12:02d} {period_disp}\n\n⏱ اختر الدقائق:",
            reply_markup=build_minute_keyboard()
        )
        return TIME_MINUTE

    return TIME_HOUR

async def time_minute_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "conv_cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ تم الإلغاء.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    if data.startswith("min_"):
        minute = int(data.split("_")[1])
        y = context.user_data["year"]
        m = context.user_data["month"]
        d = context.user_data["day"]
        h = context.user_data["hour"]

        dt = datetime(y, m, d, h, minute, tzinfo=ZoneInfo(TIMEZONE))

        if dt <= now_local():
            await query.edit_message_text(
                "⚠️ هذا الوقت في الماضي! اختر وقتاً آخر.",
                reply_markup=build_hour_keyboard()
            )
            return TIME_HOUR

        context.user_data["event_time"] = dt

        await query.edit_message_text(
            f"✅ *الموعد:* {context.user_data['title']}\n"
            f"📅 {fmt_dt_ar(dt)}\n"
            f"⏳ {time_until(dt)}\n\n"
            f"🔔 *اختر وحدة التذكير:*",
            parse_mode="Markdown",
            reply_markup=build_reminder_unit_keyboard()
        )
        return REM_UNIT

    return TIME_MINUTE

async def reminder_unit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """اختيار وحدة التذكير: دقائق / ساعات / أيام"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "conv_cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ تم الإلغاء.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    if data == "rem_unit_none":
        context.user_data["remind_minutes"] = 0
        return await _save_appointment(query, context)

    if data.startswith("rem_unit_"):
        unit = data.replace("rem_unit_", "")  # minutes / hours / days
        context.user_data["rem_unit"] = unit
        unit_ar = {"minutes": "الدقائق", "hours": "الساعات", "days": "الأيام"}[unit]
        dt = context.user_data["event_time"]
        await query.edit_message_text(
            f"✅ *الموعد:* {context.user_data['title']}\n"
            f"📅 {fmt_dt_ar(dt)}\n\n"
            f"🔔 كم {unit_ar} قبل الموعد تريد التذكير؟",
            parse_mode="Markdown",
            reply_markup=build_reminder_amount_keyboard(unit)
        )
        return REM_AMOUNT

    return REM_UNIT

async def reminder_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """اختيار كمية التذكير"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "conv_cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ تم الإلغاء.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    if data == "rem_back_unit":
        dt = context.user_data["event_time"]
        await query.edit_message_text(
            f"✅ *الموعد:* {context.user_data['title']}\n"
            f"📅 {fmt_dt_ar(dt)}\n\n"
            f"🔔 *اختر وحدة التذكير:*",
            parse_mode="Markdown",
            reply_markup=build_reminder_unit_keyboard()
        )
        return REM_UNIT

    if data.startswith("rem_val_"):
        remind_minutes = int(data.replace("rem_val_", ""))
        # التحقق أن وقت التذكير لم يمضِ
        dt = context.user_data["event_time"]
        remind_at = dt - timedelta(minutes=remind_minutes)
        if remind_at <= now_local():
            await query.answer("⚠️ وقت التذكير هذا قد مضى! اختر فترة أقصر.", show_alert=True)
            return REM_AMOUNT
        context.user_data["remind_minutes"] = remind_minutes
        return await _save_appointment(query, context)

    return REM_AMOUNT

async def _save_appointment(query, context):
    """حفظ الموعد وإظهار التأكيد"""
    title = context.user_data["title"]
    event_time = context.user_data["event_time"]
    remind_minutes = context.user_data.get("remind_minutes", 0)
    chat_id = query.message.chat_id

    add_appointment(chat_id, title, event_time, remind_minutes)

    await query.edit_message_text(
        f"✅ *تم حفظ الموعد!*\n\n"
        f"📌 {title}\n"
        f"📅 {fmt_dt_ar(event_time)}\n"
        f"⏳ {time_until(event_time)}\n"
        f"🔔 {build_reminder_label(remind_minutes)}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 عرض كل المواعيد", callback_data="menu_list")],
            [InlineKeyboardButton("➕ إضافة موعد آخر", callback_data="menu_add")],
        ])
    )
    context.user_data.clear()
    return ConversationHandler.END

# =========================
# حذف موعد
# =========================
async def delete_ask_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    appointment_id = int(query.data.split("_")[2])
    chat_id = query.message.chat_id

    row = get_appointment(chat_id, appointment_id)
    if not row:
        await query.answer("⚠️ الموعد غير موجود!", show_alert=True)
        return

    aid, title, event_time_str, remind_mins = row
    event_dt = datetime.fromisoformat(event_time_str)

    await query.edit_message_text(
        f"🗑 *هل تريد حذف هذا الموعد؟*\n\n"
        f"📌 {title}\n"
        f"📅 {fmt_dt_ar(event_dt)}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، احذف", callback_data=f"del_confirm_{aid}"),
             InlineKeyboardButton("❌ لا، رجوع", callback_data="menu_list")],
        ])
    )

async def delete_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    appointment_id = int(query.data.split("_")[2])
    chat_id = query.message.chat_id

    deleted = delete_appointment(chat_id, appointment_id)
    if deleted:
        await query.answer("✅ تم الحذف!", show_alert=False)
    else:
        await query.answer("⚠️ حدث خطأ.", show_alert=True)
    await show_list(query, context)

# =========================
# أوامر نصية (اختصارات)
# =========================
async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_list(update, context, edit=False)

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        try:
            aid = int(context.args[0])
            deleted = delete_appointment(update.effective_chat.id, aid)
            if deleted:
                await update.message.reply_text("✅ تم حذف الموعد.")
            else:
                await update.message.reply_text("⚠️ ما لقيت موعد بهذا الرقم.")
        except ValueError:
            await update.message.reply_text("اكتب رقم الموعد. مثال: /delete 3")
    else:
        await show_list(update, context, edit=False)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ تم الإلغاء.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# =========================
# مهمة التذكير التلقائي
# =========================
async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    current_time = now_local()
    due_reminders = get_pending_reminders(current_time)

    for appointment_id, chat_id, title, event_dt, remind_minutes in due_reminders:
        remaining = time_until(event_dt)
        label = build_reminder_label(remind_minutes)

        message = (
            f"⏰ *تذكير بموعدك*\n\n"
            f"📌 {title}\n"
            f"📅 {fmt_dt_ar(event_dt)}\n"
            f"⏳ {remaining}\n"
            f"🔔 {label}"
        )
        try:
            await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
            mark_sent(appointment_id)
        except Exception as exc:
            logger.exception("Failed to send reminder: %s", exc)

    cleanup_old_appointments(current_time)

# =========================
# تشغيل التطبيق
# =========================
def main() -> None:
    init_db()

    if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise ValueError("ضع توكن البوت في متغير البيئة TELEGRAM_BOT_TOKEN")

    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            CallbackQueryHandler(add_start, pattern="^menu_add$"),
        ],
        states={
            TITLE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            CAL_NAV:    [CallbackQueryHandler(calendar_handler, pattern="^cal_")],
            TIME_HOUR:  [CallbackQueryHandler(time_hour_handler, pattern="^(hour_|conv_cancel)")],
            TIME_MINUTE:[CallbackQueryHandler(time_minute_handler, pattern="^(min_|conv_cancel)")],
            REM_UNIT:   [CallbackQueryHandler(reminder_unit_handler,   pattern="^(rem_unit_|conv_cancel)")],
            REM_AMOUNT: [CallbackQueryHandler(reminder_amount_handler, pattern="^(rem_val_|rem_back_unit|conv_cancel)")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern="^conv_cancel$"),
        ],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(menu_handler, pattern="^menu_"))
    application.add_handler(CallbackQueryHandler(delete_ask_handler, pattern="^del_ask_"))
    application.add_handler(CallbackQueryHandler(delete_confirm_handler, pattern="^del_confirm_"))

    application.job_queue.run_repeating(reminder_job, interval=30, first=10)

    print("✅ البوت يعمل...")
    application.run_polling()

if __name__ == "__main__":
    main()
