import asyncio
import json
import os
from datetime import datetime
import uuid
from dateutil.parser import parse as parse_date
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.types import BotCommand

TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE = "bot_data.json"

# ---------- Хранилище данных ----------
def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {
            "chat_id": None,
            "participants": [],  # список дежурных: [{"user_id": int, "number": int}]
            "vacations": {},    # отпуска: {"user_id": [{"start": str, "end": str, "announced_start": False, "announced_end": False}]}
            "last_run": None
        }

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

data = load_data()

# ---------- Функции очереди ----------
def get_next_participant():
    """Выбирает следующего дежурного и обновляет номера по логике rotator"""
    if not data["participants"]:
        return None

    now = datetime.now().date()

    # Фильтруем участников, которые не в отпуске
    available = []
    for p in data["participants"]:
        user_id_str = str(p["user_id"])
        in_vac = False
        for vac in data["vacations"].get(user_id_str, []):
            start = parse_date(vac["start"]).date()
            end = parse_date(vac["end"]).date()
            if start <= now <= end:
                in_vac = True
                break
        if not in_vac:
            available.append(p)

    if not available:
        return None

    # Выбираем активного: номер == 2
    active = None
    for p in available:
        if p["number"] == 2:
            active = p
            break
    if not active:
        active = min(available, key=lambda x: x["number"])

    # Обновляем номера
    max_number = max(p["number"] for p in data["participants"]) if data["participants"] else 1
    for p in data["participants"]:
        if p["user_id"] == active["user_id"]:
            continue
        if p["number"] == 1:
            p["number"] = max_number
        else:
            p["number"] -= 1
    active["number"] = 1

    save_data(data)
    return active["user_id"]

def get_fullname_by_user_id(user_id: int) -> str | None:
    participants = data.get("participants", [])

    for participant in participants:
        if participant.get("user_id") == user_id:
            return participant.get("fullname")

    return None

# ---------- Команды ----------
async def cmd_start(message: Message):
    data["chat_id"] = message.chat.id
    save_data(data)
    await message.answer("Бот активирован. Chat ID сохранён.")

async def add_duty(message: Message):
    if not message.reply_to_message:
        await message.answer("Ответь на сообщение пользователя, чтобы добавить его.")
        return

    user_id = message.reply_to_message.from_user.id
    full_name = message.reply_to_message.from_user.full_name
    if not any(p["user_id"] == user_id for p in data["participants"]):
        number = max([p["number"] for p in data["participants"]], default=0) + 1
        data["participants"].append({"fullname": full_name, "user_id": user_id, "number": number})
        save_data(data)
        await message.answer(f"{full_name} добавлен в список дежурных.")
    else:
        await message.answer(f"{full_name} уже в списке дежурных.")


async def remove_duty(message: Message):
    if not message.reply_to_message:
        await message.answer("Ответь на сообщение пользователя, чтобы удалить его.")
        return

    user_id = message.reply_to_message.from_user.id
    full_name = message.reply_to_message.from_user.full_name
    if any(p["user_id"] == user_id for p in data["participants"]):
        data["participants"] = [p for p in data["participants"] if p["user_id"] != user_id]
        save_data(data)
        await message.answer(f"{full_name} удалён из списка дежурных.")
    else:
        await message.answer(f"{full_name} нет в списке дежурных.")

async def show_current(message: Message):
    active_id = get_next_participant()
    fullname = get_fullname_by_user_id(active_id)
    if not active_id:
        await message.answer("Нет доступных дежурных.")
        return
    await message.answer(f"Дежурный на этой неделе: <a href='tg://user?id={active_id}'>{fullname}</a>", parse_mode="HTML")

async def show_my_number(message: Message):
    user_id = message.from_user.id
    found = next((p for p in data["participants"] if p["user_id"] == user_id), None)
    if not found:
        await message.answer("Вы не в списке дежурных.")
    else:
        await message.answer(f"Ваш текущий номер в списке: {found['number']}")

async def add_vacation(message: Message):
    try:
        parts = message.text.split()
        if len(parts) != 3:
            raise ValueError("Wrong format")

        _, start_str, end_str = parts

        start = parse_date(start_str).date()
        end = parse_date(end_str).date()

        if start > end:
            await message.answer("Дата начала позже даты окончания.")
            return

        user_id = str(message.from_user.id)

        # гарантируем, что структура существует
        if "vacations" not in data:
            data["vacations"] = {}

        vac_list = data["vacations"].get(user_id, [])

        # Проверка на пересечение отпусков
        for v in vac_list:
            existing_start = datetime.fromisoformat(v["start"]).date()
            existing_end = datetime.fromisoformat(v["end"]).date()

            if not (end < existing_start or start > existing_end):
                await message.answer("Этот отпуск пересекается с уже существующим.")
                return

        # Создаём отпуск с уникальным id
        new_vacation = {
            "id": str(uuid.uuid4()),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "announced_start": False,
            "announced_end": False
        }

        vac_list.append(new_vacation)
        data["vacations"][user_id] = vac_list

        save_data(data)

        await message.answer(
            f"Отпуск установлен с {start.isoformat()} по {end.isoformat()}"
        )

    except ValueError:
        await message.answer(
            "Используй формат: /добавить_отпуск YYYY-MM-DD YYYY-MM-DD"
        )

async def my_vacations(message: Message):
    user_id = str(message.from_user.id)

    vacations_dict = data.get("vacations", {})
    user_vacations = vacations_dict.get(user_id, [])

    if not user_vacations:
        await message.answer("У вас нет запланированных отпусков.")
        return

    text = "Ваши отпуска:\n\n"

    for i, v in enumerate(user_vacations, start=1):
        text += f"{i}. {v['start']} — {v['end']}\n"

    await message.answer(text)

async def delete_vacation(message: Message):
    try:
        _, idx = message.text.split()
        idx = int(idx) - 1
        user_id = str(message.from_user.id)
        vac_list = data["vacations"].get(user_id, [])
        if 0 <= idx < len(vac_list):
            vac_list.pop(idx)
            if vac_list:
                data["vacations"][user_id] = vac_list
            else:
                data["vacations"].pop(user_id)
            save_data(data)
            await message.answer("Отпуск удалён.")
        else:
            await message.answer("Неверный индекс отпуска.")
    except:
        await message.answer("Используй: /удалить_отпуск INDEX")

async def shift_queue(message: Message):
    next_id = get_next_participant()
    fullname = get_fullname_by_user_id(next_id)
    if next_id:
        await message.answer(f"Очередь сдвинута. Новый дежурный: <a href='tg://user?id={next_id}'>{fullname}</a>", parse_mode="HTML")
    else:
        await message.answer("Нет доступных дежурных для назначения.")

# ---------- Планировщик ----------
async def scheduler(bot: Bot):
    while True:
        await asyncio.sleep(60)
        now = datetime.now()

        # Проверка отпусков
        for user_id_str, vac_list in data["vacations"].items():
            for vac in vac_list:
                fullname = get_fullname_by_user_id(user_id_str)
                start = parse_date(vac["start"]).date()
                end = parse_date(vac["end"]).date()
                if not vac.get("announced_start") and now.date() == start and now.hour == 10:
                    await bot.send_message(data["chat_id"],
                        f"<a href='tg://user?id={user_id_str}'>{fullname}</a> ушёл в отпуск!",
                        parse_mode="HTML")
                    vac["announced_start"] = True
                    save_data(data)
                if not vac.get("announced_end") and now.date() == end and now.hour == 10:
                    await bot.send_message(data["chat_id"],
                        f"<a href='tg://user?id={user_id_str}'>{fullname}</a> вернулся из отпуска!",
                        parse_mode="HTML")
                    vac["announced_end"] = True
                    save_data(data)

        # Уведомление дежурного в понедельник 12:00
        last_run = data.get("last_run")
        last_run_date = datetime.fromisoformat(last_run).date() if last_run else None
        if now.weekday() == 0 and now.hour == 12 and (last_run_date != now.date()):
            user_id = get_next_participant()
            fullname = get_fullname_by_user_id(user_id)
            if user_id:
                await bot.send_message(data["chat_id"],
                    f"<a href='tg://user?id={user_id}'>{fullname}</a>, твоя очередь на дежурство!</a>",
                    parse_mode="HTML")
            data["last_run"] = now.isoformat()
            save_data(data)

            
async def set_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Запустить бота и сохранить чат"),
        BotCommand(command="add_user", description="Добавить пользователя в список дежурных"),
        BotCommand(command="del_user", description="Удалить пользователя из списка дежурных"),
        BotCommand(command="cur_active", description="Показать текущего дежурного"),
        BotCommand(command="when_my_turn", description="Показать свой номер в очереди дежурных"),
        BotCommand(command="add_vacation", description="Добавить отпуск"),
        BotCommand(command="remove_vacation", description="Удалить отпуск"),
        BotCommand(command="queue_move", description="Принудительно назначить нового дежурного"),
        BotCommand(command="my_vacations", description="Показать мои отпуска")
    ]
    await bot.set_my_commands(commands)

# ---------- Запуск бота ----------
async def main():
    bot = Bot(TOKEN)
    dp = Dispatcher()

    dp.message.register(cmd_start, Command(commands=["start"]))
    dp.message.register(add_duty, Command(commands=["add_user"]))
    dp.message.register(remove_duty, Command(commands=["del_user"]))
    dp.message.register(show_current, Command(commands=["cur_active"]))
    dp.message.register(show_my_number, Command(commands=["when_my_turn"]))
    dp.message.register(add_vacation, Command(commands=["add_vacation"]))
    dp.message.register(delete_vacation, Command(commands=["remove_vacation"]))
    dp.message.register(shift_queue, Command(commands=["queue_move"]))
    dp.message.register(my_vacations, Command(commands=["my_vacations"]))

    await set_commands(bot)

    asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
