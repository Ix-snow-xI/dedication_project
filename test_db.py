import os
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN")
os.environ.setdefault("USER_IDS", "42,77")
os.environ.setdefault("DB_PATH", ":memory:")

import bot
import db
from aiogram.types import CallbackQuery, Chat, Message, Update, User

MON = date(2026, 11, 23)  # a Monday
TUE = date(2026, 11, 24)
OWNER = User(id=42, is_bot=False, first_name="Owner")
FRIEND = User(id=77, is_bot=False, first_name="Friend")
STRANGER = User(id=99, is_bot=False, first_name="Stranger")
CHAT = Chat(id=42, type="private")


class DbTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.goal = db.add_goal(self.conn, OWNER.id, "Health")
        self.habit = db.add_habit(self.conn, self.goal, "Run", MON)

    def test_toggle_checkin(self):
        self.assertTrue(db.toggle_checkin(self.conn, self.habit, MON))
        self.assertEqual(db.today_habits(self.conn, OWNER.id, MON), [(self.habit, "Run", True)])
        self.assertFalse(db.toggle_checkin(self.conn, self.habit, MON))
        self.assertEqual(db.today_habits(self.conn, OWNER.id, MON), [(self.habit, "Run", False)])

    def test_schedule_filters_today(self):
        db.toggle_day(self.conn, self.habit, TUE.weekday())  # remove Tuesday
        self.assertEqual(len(db.today_habits(self.conn, OWNER.id, MON)), 1)
        self.assertEqual(db.today_habits(self.conn, OWNER.id, TUE), [])

    def test_toggle_day_keeps_at_least_one(self):
        for i in range(1, 7):
            self.assertTrue(db.toggle_day(self.conn, self.habit, i))
        self.assertFalse(db.toggle_day(self.conn, self.habit, 0))
        self.assertEqual(db.get_habit(self.conn, self.habit)["days"], 1)

    def test_archived_goal_hides_habits(self):
        db.archive_goal(self.conn, OWNER.id, self.goal)
        self.assertEqual(db.today_habits(self.conn, OWNER.id, MON), [])

    def test_week_rate_ignores_days_before_creation(self):
        db.toggle_checkin(self.conn, self.habit, MON)
        self.assertEqual(db.week_rate(self.conn, self.habit, TUE), (1, 2))

    def test_habit_not_shown_before_creation(self):
        self.assertEqual(db.today_habits(self.conn, OWNER.id, date(2026, 11, 22)), [])

    def test_users_are_isolated(self):
        self.assertEqual(db.goals(self.conn, FRIEND.id), [])
        self.assertEqual(db.today_habits(self.conn, FRIEND.id, MON), [])
        self.assertFalse(db.archive_goal(self.conn, FRIEND.id, self.goal))


class BotTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot.conn = db.connect(":memory:")
        self.tg = AsyncMock()
        self.seq = 0

    def tearDown(self):
        bot.conn.close()

    async def send(self, text, user=OWNER):
        self.seq += 1
        msg = Message(message_id=self.seq, date=datetime.now(), chat=CHAT, from_user=user, text=text)
        await bot.dp.feed_update(self.tg, Update(update_id=self.seq, message=msg))

    async def click(self, data, user=OWNER):
        self.seq += 1
        msg = Message(message_id=self.seq, date=datetime.now(), chat=CHAT, from_user=user, text="...")
        cb = CallbackQuery(id=str(self.seq), from_user=user, chat_instance="1", message=msg, data=data)
        await bot.dp.feed_update(self.tg, Update(update_id=self.seq, callback_query=cb))

    async def test_full_flow_and_owner_filter(self):
        # Stranger is ignored
        await self.send("/goal Secret", user=STRANGER)
        self.assertEqual(db.goals(bot.conn, STRANGER.id), [])

        # Owner creates goal -> habit via FSM -> schedule -> checkin -> goals -> archive
        await self.send("/goal Спорт")
        self.assertEqual(len(db.goals(bot.conn, OWNER.id)), 1)
        gid = db.goals(bot.conn, OWNER.id)[0]["id"]

        await self.send("/habit")
        await self.click(f"g:{gid}")
        await self.send("Бег")
        habits = db.habits(bot.conn, gid)
        self.assertEqual(len(habits), 1)
        hid = habits[0]["id"]

        await self.click(f"d:{hid}:0")
        self.assertEqual(db.get_habit(bot.conn, hid)["days"], 126)
        await self.click("ok")

        d = bot.today().isoformat()
        await self.send("/today")
        await self.click(f"t:{hid}:{d}")
        self.assertEqual(db.week_rate(bot.conn, hid, bot.today())[0], 1)

        await self.send("/goals")
        await self.send("/archive")
        await self.click(f"ah:{hid}")
        self.assertEqual(db.habits(bot.conn, gid), [])

    async def test_friend_notified_on_completion_only(self):
        gid = db.add_goal(bot.conn, OWNER.id, "Спорт")
        hid = db.add_habit(bot.conn, gid, "Бег", bot.today())
        d = bot.today().isoformat()

        # Friend cannot toggle owner's habit
        await self.click(f"t:{hid}:{d}", user=FRIEND)
        self.assertEqual(db.week_rate(bot.conn, hid, bot.today())[0], 0)
        self.tg.send_message.assert_not_awaited()

        # Owner completes habit -> friend gets notified
        await self.click(f"t:{hid}:{d}", user=OWNER)
        self.tg.send_message.assert_awaited_once_with(FRIEND.id, "✅ Owner выполнил(а): Бег")

        # Owner unchecks habit -> no second notification
        self.tg.send_message.reset_mock()
        await self.click(f"t:{hid}:{d}", user=OWNER)
        self.tg.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
