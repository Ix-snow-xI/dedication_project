"""Dedication bot: goals -> habits -> daily checklist + evening reminder."""
import asyncio
import json
import logging
import os
import shutil
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

import db

ROOT = Path(__file__).resolve().parent


def load_env(path):
    # Minimal .env reader: same setup on Windows and Linux, no extra dependency.
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


load_env(ROOT / ".env")
TOKEN = os.environ["BOT_TOKEN"]
USERS = [int(x) for x in (os.environ.get("USER_IDS") or os.environ["OWNER_ID"]).split(",") if x.strip()]
ADMIN_ID = USERS[0]
USER_IDS = set(USERS)
TZ = ZoneInfo(os.environ.get("BOT_TZ", "Asia/Baku"))
REMIND_AT = time.fromisoformat(os.environ.get("REMIND_AT", "21:00"))

DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
HELP = (
    "/today — отметить привычки на сегодня\n"
    "/goal <название> — новая цель\n"
    "/habit — новая привычка для цели\n"
    "/goals — цели, привычки и прогресс за 7 дней\n"
    "/archive — убрать цель или привычку в архив\n"
    f"Вечернее напоминание: {REMIND_AT:%H:%M}"
)

conn = db.connect(os.environ.get("DB_PATH") or ROOT / "dedication.db")
dp = Dispatcher()
# The bot is publicly reachable: ignore everyone except allowed users.
dp.message.filter(F.from_user.id.in_(USER_IDS))
dp.callback_query.filter(F.from_user.id.in_(USER_IDS))


class NewHabit(StatesGroup):
    title = State()


def today():
    return datetime.now(TZ).date()


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def today_view(user_id: int, day: date):
    items = db.today_habits(conn, user_id, day)
    if not items:
        return "На сегодня привычек нет. Добавь: /goal, затем /habit", None
    done = sum(d for _, _, d in items)
    rows = [
        # Date is in callback data so yesterday's message still marks yesterday after midnight.
        [Btn(text=f"{'✅' if d else '⬜'} {title}", callback_data=f"t:{hid}:{day.isoformat()}")]
        for hid, title, d in items
    ]
    tail = " 🔥 Всё сделано!" if done == len(items) else ""
    return f"{day:%d.%m} — {done}/{len(items)}{tail}", kb(rows)


def days_str(days):
    return "каждый день" if days == 127 else " ".join(d for i, d in enumerate(DAYS) if days >> i & 1)


def days_view(habit_id):
    h = db.get_habit(conn, habit_id)
    row = [
        Btn(text=("✓" if h["days"] >> i & 1 else "·") + d, callback_data=f"d:{habit_id}:{i}")
        for i, d in enumerate(DAYS)
    ]
    return f"Привычка «{h['title']}». В какие дни?", kb([row, [Btn(text="Готово", callback_data="ok")]])


@dp.message(Command("start", "help"))
async def start(m: Message):
    await m.answer(HELP)


@dp.message(Command("goal"))
async def goal(m: Message, command: CommandObject):
    title = (command.args or "").strip()
    if not title:
        return await m.answer("Формат: /goal <название>")
    db.add_goal(conn, m.from_user.id, title)
    await m.answer(f"Цель добавлена: {title}\nТеперь добавь привычку: /habit")


@dp.message(Command("habit"))
async def habit(m: Message):
    goals = db.goals(conn, m.from_user.id)
    if not goals:
        return await m.answer("Сначала создай цель: /goal <название>")
    await m.answer("Для какой цели?", reply_markup=kb([[Btn(text=g["title"], callback_data=f"g:{g['id']}")] for g in goals]))


@dp.callback_query(F.data.startswith("g:"))
async def habit_goal(cb: CallbackQuery, state: FSMContext):
    goal_id = int(cb.data[2:])
    if not any(g["id"] == goal_id for g in db.goals(conn, cb.from_user.id)):
        return await cb.answer("Это не твоя цель")
    await state.set_state(NewHabit.title)
    await state.update_data(goal_id=goal_id)
    await cb.message.edit_text("Название привычки?")
    await cb.answer()


@dp.message(NewHabit.title, F.text, ~F.text.startswith("/"))
async def habit_title(m: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    hid = db.add_habit(conn, data["goal_id"], m.text.strip(), today())
    text, markup = days_view(hid)
    await m.answer(text, reply_markup=markup)


@dp.callback_query(F.data.startswith("d:"))
async def habit_day(cb: CallbackQuery):
    _, hid, i = cb.data.split(":")
    h = db.get_habit(conn, int(hid))
    if not h or h["user_id"] != cb.from_user.id:
        return await cb.answer("Это не твоя привычка")
    if not db.toggle_day(conn, int(hid), int(i)):
        return await cb.answer("Нужен хотя бы один день")
    text, markup = days_view(int(hid))
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()


@dp.callback_query(F.data == "ok")
async def habit_done(cb: CallbackQuery):
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Сохранено")


@dp.message(Command("today"))
async def today_cmd(m: Message):
    text, markup = today_view(m.from_user.id, today())
    await m.answer(text, reply_markup=markup)


@dp.callback_query(F.data.startswith("t:"))
async def toggle(cb: CallbackQuery, bot: Bot):
    _, hid, d = cb.data.split(":")
    h = db.get_habit(conn, int(hid))
    if not h or h["user_id"] != cb.from_user.id:
        return await cb.answer("Это не твоя задача")
    day = date.fromisoformat(d)
    done = db.toggle_checkin(conn, int(hid), day)
    text, markup = today_view(cb.from_user.id, day)
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()
    if done:
        for uid in USER_IDS - {cb.from_user.id}:
            try:
                await bot.send_message(uid, f"✅ {cb.from_user.first_name} выполнил(а): {h['title']}")
            except Exception:
                logging.exception("Notify failed for %s", uid)


@dp.message(Command("goals"))
async def goals_cmd(m: Message):
    lines = []
    for g in db.goals(conn, m.from_user.id):
        lines.append(f"🎯 {g['title']}")
        for h in db.habits(conn, g["id"]):
            done, total = db.week_rate(conn, h["id"], today())
            pct = f"{done * 100 // total}%" if total else "—"
            lines.append(f"    • {h['title']} ({days_str(h['days'])}) — 7 дн: {done}/{total}, {pct}")
    await m.answer("\n".join(lines) or "Целей нет. Добавь: /goal <название>")


@dp.message(Command("archive"))
async def archive(m: Message):
    rows = []
    for g in db.goals(conn, m.from_user.id):
        rows.append([Btn(text=f"🎯 {g['title']}", callback_data=f"ag:{g['id']}")])
        rows += [[Btn(text=f"    • {h['title']}", callback_data=f"ah:{h['id']}")] for h in db.habits(conn, g["id"])]
    if not rows:
        return await m.answer("Архивировать нечего.")
    await m.answer("Что убрать в архив? (история сохранится)", reply_markup=kb(rows))


@dp.callback_query(F.data.startswith("ag:") | F.data.startswith("ah:"))
async def archive_pick(cb: CallbackQuery):
    kind, item_id = cb.data.split(":")
    ok = (db.archive_goal if kind == "ag" else db.archive_habit)(conn, cb.from_user.id, int(item_id))
    if not ok:
        return await cb.answer("Это не твоя задача")
    await cb.message.edit_text("Убрано в архив.")
    await cb.answer()


def next_remind(now: datetime):
    target = datetime.combine(now.date(), REMIND_AT, TZ)
    return target if target > now else target + timedelta(days=1)


async def reminder(bot: Bot):
    target = next_remind(datetime.now(TZ))
    while True:
        # Short sleeps instead of one long one: survives laptop sleep/suspend.
        await asyncio.sleep(min(60, max(0, (target - datetime.now(TZ)).total_seconds())))
        if datetime.now(TZ) < target:
            continue
        for uid in USER_IDS:
            text, markup = today_view(uid, target.date())
            if markup:
                try:
                    await bot.send_message(uid, f"Вечерняя проверка. {text}", reply_markup=markup)
                except Exception:
                    logging.exception("Reminder failed for %s", uid)
        target = next_remind(datetime.now(TZ))


def low_battery_alert(info: dict, alerted: bool):
    pct = info["percentage"]
    unplugged = info.get("plugged") == "UNPLUGGED"
    if unplugged and pct <= 15:
        return not alerted, True
    if not unplugged or pct >= 20:
        return False, False
    return False, alerted


async def battery_watch(bot: Bot):
    if not shutil.which("termux-battery-status"):
        return
    alerted = False
    while True:
        try:
            proc = await asyncio.create_subprocess_exec("termux-battery-status", stdout=asyncio.subprocess.PIPE)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise
            info = json.loads(out)
            should_alert, alerted = low_battery_alert(info, alerted)
            if should_alert:
                await bot.send_message(ADMIN_ID, f"🪫 Заряд сервера: {info['percentage']}%")
        except Exception:
            logging.exception("Battery check failed")
        await asyncio.sleep(300)


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(TOKEN)
    tasks = [asyncio.create_task(reminder(bot)), asyncio.create_task(battery_watch(bot))]
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
