import asyncio
import requests
import hashlib
import json
import logging
import os
from pathlib import Path
from flask import Flask, request as flask_request
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- configuration & state --------------------------------------------------
# токен бота хранится в переменной окружения TG_TOKEN.
# при развёртывании (Render, Heroku и т.п.) укажите эту переменную
# в настройках сервиса, и код прочитает её автоматически.
# Если переменная не задана, скрипт завершится с ошибкой, чтобы
# вы случайно не запустили бота без токена.
TOKEN = os.getenv("TG_TOKEN")
if not TOKEN:
    raise RuntimeError("Telegram token not set. Export TG_TOKEN in environment.")

BASE_URL = "http://schedule.ckstr.ru/"

# хранение данных пользователей (chat_id -> group_code) и хэшей текста
users = {}
hashes = {}
STATE_FILE = Path("state.json")

# logging setup
logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

GROUP_MAP = {
    "ЗУ11": "cg38",
    "ЗУ12П": "cg117",
    "ИСВ11": "cg118",
    "ИСП11": "cg81",
    "ИСП12П": "cg102",
    "ПД11П": "cg120",
    "ПД12П": "cg121",
    "СЖ11": "cg11",
    "СЖ12": "cg12",
    "СЖ13": "cg105",
    "СЖ14": "cg110",
    "СЖ15П": "cg135",
    "СМ11": "cg1",
    "СМ12": "cg2",
    "ЗУ21": "cg39",
    "ЗУ22П": "cg123",
    "ИСВ21": "cg133",
    "ИСВ22П": "cg134",
    "ИСП21": "cg106",
    "ИСП22": "cg107",
    "ИСП23П": "cg108",
    "ПД21П": "cg122",
    "ПД22П": "cg131",
    "СЖ21": "cg13",
    "СЖ22": "cg14",
    "СЖ23": "cg113",
    "СЖ24": "cg132",
    "СЖ25П": "cg141",
    "СМ21": "cg3",
    "СМ22": "cg4",
    "ЗУ31": "cg40",
    "ЗУ32П": "cg136",
    "ИСВ31": "cg146",
    "ИСВ32": "cg147",
    "ИСВ33П": "cg148",
    "ИСП31": "cg114",
    "ИСП32": "cg115",
    "ИСП33П": "cg116",
    "ПД31П": "cg139",
    "ПД32П": "cg142",
    "СЖ31": "cg16",
    "СЖ32": "cg70",
    "СЖ33": "cg125",
    "СЖ34П": "cg143",
    "СМ31": "cg5",
    "СМ32": "cg10",
    "ГК41": "cg41",
    "ГК42П": "cg144",
    "ИСВ41": "cg149",
    "ИСВ42": "cg150",
    "ИСП41": "cg127",
    "ИСП42": "cg128",
    "ИСП43П": "cg129",
    "СЖ41": "cg18",
    "СЖ42": "cg76",
    "СЖ43": "cg138",
    "СМ41": "cg65",
    "СМ42": "cg66",
    "С51к": "cg57"

}

def get_schedule(group):
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"{BASE_URL}{group}.htm"

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("не удалось загрузить %s: %s", url, exc)
        return None

    response.encoding = "windows-1251"
    soup = BeautifulSoup(response.text, "html.parser")

    tables = soup.find_all("table")
    if not tables:
        return "Расписание не найдено."

    schedule_table = max(tables, key=lambda t: len(t.get_text()))

    # собираем данные в структуру [(date, [(pair, [info1, info2...]), ...])]
    days = []
    first_day_found = False
    current_pair = None

    for row in schedule_table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]

        if not any(cell.strip() for cell in cells):
            continue

        # дата
        if cells[0].count(".") == 2:
            if first_day_found:
                break  # нам нужно только первая дата
            first_day_found = True
            current_day = cells[0]
            days.append((current_day, []))

            # первая пара может идти в той же строке
            if len(cells) > 1 and cells[1].isdigit():
                current_pair = cells[1]
                lesson_info = " ".join(cells[2:])
                days[-1][1].append((current_pair, [lesson_info]))
            continue

        if not first_day_found:
            continue

        if cells[0].isdigit():
            current_pair = cells[0]
            info = " ".join(cells[1:])
            days[-1][1].append((current_pair, [info]))
        elif current_pair:
            # продолжение предыдущей пары, добавляем как новый info
            info = " ".join(cells)
            days[-1][1].append((current_pair, [info]))

    if not days or not days[0][1]:
        return "Расписание не найдено."

    # helper to split lesson string into subject/room/teacher
    import re
    def split_info(text: str):
        text = text.strip()
        if not text:
            return "", "", ""
        # попытка распарсить «предмет <номер> <имя>»
        m = re.match(r"(.+?)\s+(\d+)\s+(.+)", text)
        if m:
            return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        parts = text.split()
        if len(parts) >= 3 and parts[-2].isdigit():
            subject = " ".join(parts[:-2])
            room = parts[-2]
            teacher = parts[-1]
            return subject, room, teacher
        # если не удалось, возвращаем всё как предмет
        return text, "", ""

    # форматируем вывод красиво с разделителями между парами
    formatted_text = ""
    for date, lessons in days:
        formatted_text += f"📅 <b>{date}</b>\n"
        for pair, info_list in lessons:
            # info_list может содержать несколько вариантов (подгруппы)
            for idx, info in enumerate(info_list):
                subj, room, teacher = split_info(info)
                # номер пары и предмет выводим только один раз для первой подгруппы
                if idx == 0:
                    formatted_text += f"🔹 <b>{pair}</b> — <i>{subj or 'нет пары'}</i>\n"
                else:
                    formatted_text += f"    ↳ <i>{subj or 'нет пары'}</i>\n"
                if room:
                    formatted_text += f"    🏢 {room}\n"
                if teacher:
                    formatted_text += f"    👩‍🏫 {teacher}\n"
            # разделитель между парами
            formatted_text += "────────\n"
        # разделитель между днями ( на будущее )
        formatted_text += "\n"

    return formatted_text.strip()


def get_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_state():
    global users, hashes
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        users = {int(k): v for k, v in data.get("users", {}).items()}
        hashes = data.get("hashes", {})
        logger.info("state loaded: %d users", len(users))
    except Exception as exc:
        logger.warning("не удалось загрузить состояние: %s", exc)


def save_state():
    try:
        STATE_FILE.write_text(
            json.dumps({"users": users, "hashes": hashes}, ensure_ascii=False),
            encoding="utf-8",
        )
        # set restrictive permissions if possible
        try:
            STATE_FILE.chmod(0o600)
        except Exception:
            pass
    except Exception as exc:
        logger.warning("не удалось сохранить состояние: %s", exc)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет 👋\nВведите номер группы (например ИСП22).\n"
        "После сохранения группы вы можете запросить:\n"
        "• `остаток <предмет>` – сколько пар осталось по дисциплине\n"
        "• `остаток всех пар` – общее количество оставшихся пар\n",
        parse_mode="HTML"
    )


async def save_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # entry point for every text message; can be group name or "остаток" query
    text = update.message.text.strip()
    chat_id = update.message.chat_id

    # handle remaining‑hours request
    if text.lower().startswith("остаток"):
        await handle_remaining(update, context, text)
        return

    # otherwise treat as group selection
    user_input = (''
        .join(ch for ch in text if ch.isalnum())
        .upper()
    )
    logger.info("ввод пользователя: %s", user_input)

    if user_input not in GROUP_MAP:
        groups_list = "\n".join(GROUP_MAP.keys())
        await update.message.reply_text(
            f"❌ Такой группы нет.\n\nДоступные группы:\n{groups_list}"
        )
        return

    group_code = GROUP_MAP[user_input]
    schedule = get_schedule(group_code)

    if schedule is None:
        await update.message.reply_text("Ошибка загрузки расписания.")
        return

    users[chat_id] = group_code
    hashes[chat_id] = get_hash(schedule)
    save_state()

    await update.message.reply_text(
        f"✅ Группа {user_input} сохранена.\n\n{schedule}",
        parse_mode="HTML"
    )



async def handle_remaining(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Process a user request like "остаток ЧМ" or "остаток всех пар"."""
    chat_id = update.message.chat_id
    group_code = users.get(chat_id)
    if not group_code:
        await update.message.reply_text("Сначала укажите группу (например ИСП22)")
        return

    # determine type of query
    rest = text.lower().replace("остаток", "", 1).strip()
    all_query = False
    subject_query = None
    if rest.startswith("всех"):
        all_query = True
    else:
        subject_query = rest

    result = get_remaining(group_code, subject_query, all_query)
    if result is None:
        await update.message.reply_text("Не удалось получить данные об остатках.")
    else:
        await update.message.reply_text(result)


def get_remaining(group, subject=None, all_subjects=False):
    """Fetches the "Итоги" page for a group and computes remaining pairs.

    If subject is provided (non‑empty string), returns text about that
    discipline.  If all_subjects=True, returns total by summing all
    disciplines (excluding ПП и УП)."""
    # summary page uses 'v' instead of 'c' prefix (cg38 -> vg38)
    if group.startswith("cg"):
        summ_group = "v" + group[1:]
    else:
        summ_group = group
    url = f"{BASE_URL}{summ_group}.htm"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    resp.encoding = "windows-1251"
    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None
    # summary page is the largest table by text length (contains the output rows)
    summary_table = max(tables, key=lambda t: len(t.get_text()))

    rows = []
    for row in summary_table.find_all("tr")[1:]:
        cols = [c.get_text(" ", strip=True) for c in row.find_all(["td","th"])]
        if len(cols) < 10:
            continue
        discipline = cols[4]
        rem_text = cols[9]
        try:
            hours = float(rem_text.replace(",", "."))
        except ValueError:
            hours = 0.0
        rows.append((discipline, hours))

    def matches(disc, q):
        return q.lower() in disc.lower()

    if all_subjects:
        total_hours = sum(h for d, h in rows if not any(x in d.upper() for x in ("ПП","УП")))
        pairs = total_hours / 2
        return f"Осталось всего {pairs:.1f} пар ({total_hours:.1f} часов)" if total_hours else "Нет данных"

    if subject:
        # look for discipline containing subject
        for disc, h in rows:
            if matches(disc, subject):
                pairs = h / 2
                return f"{disc}: осталось {pairs:.1f} пар ({h:.1f} часов)"
        return "Предмет не найден или остаток не указан."

    return "Неправильный запрос."



if __name__ == "__main__":
    load_state()
    
    # Create telegram bot application and initialize it immediately so
    # process_update() can be called from the Flask handler.
    tg_app = ApplicationBuilder().token(TOKEN).build()
    # `initialize()` is a coroutine; run it now so the object is ready.
    asyncio.run(tg_app.initialize())

    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_group))
    
    # Create Flask web server for webhook
    flask_app = Flask(__name__)
    
    # Webhook endpoint to receive updates from Telegram
    @flask_app.route("/webhook", methods=["POST"])
    def webhook():
        data = flask_request.get_json()
        try:
            update = Update.de_json(data, tg_app.bot)
            # schedule processing on the running event loop instead of creating
            # a new one each time; the application is already initialized.
            asyncio.create_task(tg_app.process_update(update))
        except Exception as e:
            logger.exception("Error in webhook: %s", e)
        return "OK", 200
    
    @flask_app.route("/health", methods=["GET"])
    def health():
        return "OK", 200
    
    # Get PORT from environment, default to 8080 for Render
    port = int(os.getenv("PORT", 8080))
    
    logger.info(f"Starting webhook bot on port {port}")
    logger.info("Webhook endpoint: /webhook")
    flask_app.run(host="0.0.0.0", port=port)
