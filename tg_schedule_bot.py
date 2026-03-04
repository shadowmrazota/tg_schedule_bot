import asyncio
import datetime as dt
import requests
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from flask import Flask, request as flask_request
from bs4 import BeautifulSoup
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from zoneinfo import ZoneInfo

# --- configuration & state --------------------------------------------------
# С‚РѕРєРµРЅ Р±РѕС‚Р° С…СЂР°РЅРёС‚СЃСЏ РІ РїРµСЂРµРјРµРЅРЅРѕР№ РѕРєСЂСѓР¶РµРЅРёСЏ TG_TOKEN.
# РїСЂРё СЂР°Р·РІС‘СЂС‚С‹РІР°РЅРёРё (Render, Heroku Рё С‚.Рї.) СѓРєР°Р¶РёС‚Рµ СЌС‚Сѓ РїРµСЂРµРјРµРЅРЅСѓСЋ
# РІ РЅР°СЃС‚СЂРѕР№РєР°С… СЃРµСЂРІРёСЃР°, Рё РєРѕРґ РїСЂРѕС‡РёС‚Р°РµС‚ РµС‘ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё.
# Р•СЃР»Рё РїРµСЂРµРјРµРЅРЅР°СЏ РЅРµ Р·Р°РґР°РЅР°, СЃРєСЂРёРїС‚ Р·Р°РІРµСЂС€РёС‚СЃСЏ СЃ РѕС€РёР±РєРѕР№, С‡С‚РѕР±С‹
# РІС‹ СЃР»СѓС‡Р°Р№РЅРѕ РЅРµ Р·Р°РїСѓСЃС‚РёР»Рё Р±РѕС‚Р° Р±РµР· С‚РѕРєРµРЅР°.
TOKEN = os.getenv("TG_TOKEN")
if not TOKEN:
    raise RuntimeError("Telegram token not set. Export TG_TOKEN in environment.")

BASE_URL = "http://schedule.ckstr.ru/"

# С…СЂР°РЅРµРЅРёРµ РґР°РЅРЅС‹С… РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№ (chat_id -> group_code) Рё С…СЌС€РµР№ С‚РµРєСЃС‚Р°
users = {}
hashes = {}
started_users = set()
notify_users = set()
last_notified = {}
STATE_FILE = Path("state.json")

SCHEDULE_CACHE_TTL_SEC = 300
CHANGE_CHECK_INTERVAL_SEC = 600
schedule_cache = {}

MSK_TZ = ZoneInfo("Europe/Moscow")
MORNING_NOTIFY_HOUR = 5
MORNING_NOTIFY_MINUTE = 0

BTN_SHOW_SCHEDULE = "📅 Моё расписание"
BTN_REMAINING = "📊 Остаток"
BTN_MY_GROUP = "👥 Моя группа"
BTN_CHANGE_GROUP = "⚙️ Сменить группу"
BTN_NOTIFICATIONS = "🔔 Уведомления"
BTN_HELP = "❓ Помощь"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_SHOW_SCHEDULE, BTN_REMAINING],
        [BTN_MY_GROUP, BTN_CHANGE_GROUP],
        [BTN_NOTIFICATIONS, BTN_HELP],
    ],
    resize_keyboard=True,
)


def fix_mojibake(text: str) -> str:
    """Try to recover text that was decoded with a wrong codec."""
    if not isinstance(text, str):
        return text
    fixed = text

    def _mixed_bytes_recover(s: str) -> str:
        raw = bytearray()
        for ch in s:
            code = ord(ch)
            if code <= 255:
                raw.append(code)
                continue
            try:
                raw.extend(ch.encode("cp1251"))
            except Exception:
                return s
        try:
            return raw.decode("utf-8")
        except Exception:
            return s

    for _ in range(3):
        if not any(marker in fixed for marker in ("Р", "С", "вЂ", "Ѓ", "‚", "™", "\x98", "\x99")):
            break
        changed = False
        for enc in ("cp1251", "latin1"):
            try:
                candidate = fixed.encode(enc).decode("utf-8")
                if candidate != fixed:
                    fixed = candidate
                    changed = True
                    break
            except Exception:
                continue
        if changed:
            continue
        candidate = _mixed_bytes_recover(fixed)
        if candidate != fixed:
            fixed = candidate
            continue
        break
    return fixed

# logging setup
logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

GROUP_MAP = {
    "Р—РЈ11": "cg38",
    "Р—РЈ12Рџ": "cg117",
    "РРЎР’11": "cg118",
    "РРЎРџ11": "cg81",
    "РРЎРџ12Рџ": "cg102",
    "РџР”11Рџ": "cg120",
    "РџР”12Рџ": "cg121",
    "РЎР–11": "cg11",
    "РЎР–12": "cg12",
    "РЎР–13": "cg105",
    "РЎР–14": "cg110",
    "РЎР–15Рџ": "cg135",
    "РЎРњ11": "cg1",
    "РЎРњ12": "cg2",
    "Р—РЈ21": "cg39",
    "Р—РЈ22Рџ": "cg123",
    "РРЎР’21": "cg133",
    "РРЎР’22Рџ": "cg134",
    "РРЎРџ21": "cg106",
    "РРЎРџ22": "cg107",
    "РРЎРџ23Рџ": "cg108",
    "РџР”21Рџ": "cg122",
    "РџР”22Рџ": "cg131",
    "РЎР–21": "cg13",
    "РЎР–22": "cg14",
    "РЎР–23": "cg113",
    "РЎР–24": "cg132",
    "РЎР–25Рџ": "cg141",
    "РЎРњ21": "cg3",
    "РЎРњ22": "cg4",
    "Р—РЈ31": "cg40",
    "Р—РЈ32Рџ": "cg136",
    "РРЎР’31": "cg146",
    "РРЎР’32": "cg147",
    "РРЎР’33Рџ": "cg148",
    "РРЎРџ31": "cg114",
    "РРЎРџ32": "cg115",
    "РРЎРџ33Рџ": "cg116",
    "РџР”31Рџ": "cg139",
    "РџР”32Рџ": "cg142",
    "РЎР–31": "cg16",
    "РЎР–32": "cg70",
    "РЎР–33": "cg125",
    "РЎР–34Рџ": "cg143",
    "РЎРњ31": "cg5",
    "РЎРњ32": "cg10",
    "Р“Рљ41": "cg41",
    "Р“Рљ42Рџ": "cg144",
    "РРЎР’41": "cg149",
    "РРЎР’42": "cg150",
    "РРЎРџ41": "cg127",
    "РРЎРџ42": "cg128",
    "РРЎРџ43Рџ": "cg129",
    "РЎР–41": "cg18",
    "РЎР–42": "cg76",
    "РЎР–43": "cg138",
    "РЎРњ41": "cg65",
    "РЎРњ42": "cg66",
    "РЎ51Рє": "cg57"

}

BTN_SHOW_SCHEDULE = fix_mojibake(BTN_SHOW_SCHEDULE)
BTN_REMAINING = fix_mojibake(BTN_REMAINING)
BTN_MY_GROUP = fix_mojibake(BTN_MY_GROUP)
BTN_CHANGE_GROUP = fix_mojibake(BTN_CHANGE_GROUP)
BTN_NOTIFICATIONS = fix_mojibake(BTN_NOTIFICATIONS)
BTN_HELP = fix_mojibake(BTN_HELP)
GROUP_MAP = {fix_mojibake(k): v for k, v in GROUP_MAP.items()}
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_SHOW_SCHEDULE, BTN_REMAINING],
        [BTN_MY_GROUP, BTN_CHANGE_GROUP],
        [BTN_NOTIFICATIONS, BTN_HELP],
    ],
    resize_keyboard=True,
)


def get_group_name_by_code(group_code: str) -> str:
    for group_name, code in GROUP_MAP.items():
        if code == group_code:
            return fix_mojibake(group_name)
    return fix_mojibake(group_code)


def anonymize_chat_id(chat_id: int) -> str:
    return hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:12]


def log_action(action: str, chat_id: int, details: str = ""):
    suffix = f" | {details}" if details else ""
    logger.info("user_action=%s user=%s%s", action, anonymize_chat_id(chat_id), suffix)


def format_groups_list(groups, query: str = "") -> str:
    if not groups:
        if query:
            return (
                fix_mojibake(f"По запросу '{query}' ничего не найдено.\n")
                + fix_mojibake("Пример: /groups ИСП")
            )
        return fix_mojibake("Список групп пуст.")

    shown = groups[:20]
    lines = [fix_mojibake("Доступные группы:")]
    lines.extend([f"- {fix_mojibake(g)}" for g in shown])
    if len(groups) > len(shown):
        lines.append(fix_mojibake(f"... и ещё {len(groups) - len(shown)}."))
    lines.append(fix_mojibake("Для фильтра: /groups <часть названия>, например /groups ИСП"))
    return "\n".join(lines)


def normalize_group_name(text: str) -> str:
    return "".join(ch for ch in fix_mojibake(text) if ch.isalnum()).upper()


def resolve_group_code(raw_text: str):
    wanted = normalize_group_name(raw_text)
    if not wanted:
        return None, None
    for key, code in GROUP_MAP.items():
        if normalize_group_name(key) == wanted:
            return fix_mojibake(key), code
    return None, None

def get_schedule(group):
    schedule, _error_text = get_schedule_with_error(group)
    return schedule


def get_schedule_with_error(group, use_cache=True):
    now = time.time()
    if use_cache:
        cached = schedule_cache.get(group)
        if cached and now - cached["ts"] <= SCHEDULE_CACHE_TTL_SEC:
            return cached["text"], None

    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"{BASE_URL}{group}.htm"

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.Timeout:
        logger.warning("schedule timeout: %s", url)
        return None, "Сайт расписания не ответил вовремя. Попробуй ещё раз через минуту."
    except requests.RequestException as exc:
        logger.warning("schedule request failed: %s: %s", url, exc)
        return None, "Не удалось подключиться к сайту расписания. Попробуй позже."

    response.encoding = "windows-1251"
    soup = BeautifulSoup(response.text, "html.parser")

    tables = soup.find_all("table")
    if not tables:
        return "Расписание не найдено.", None

    schedule_table = max(tables, key=lambda t: len(t.get_text()))

    days = []
    first_day_found = False
    current_pair = None

    for row in schedule_table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]

        if not any(cell.strip() for cell in cells):
            continue

        if cells[0].count(".") == 2:
            if first_day_found:
                break
            first_day_found = True
            current_day = cells[0]
            days.append((current_day, []))

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
            info = " ".join(cells)
            days[-1][1].append((current_pair, [info]))

    if not days or not days[0][1]:
        return "Расписание не найдено.", None

    import re

    def split_info(text: str):
        text = text.strip()
        if not text:
            return "", "", ""
        m = re.match(r"(.+?)\s+(\d+)\s+(.+)", text)
        if m:
            return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        parts = text.split()
        if len(parts) >= 3 and parts[-2].isdigit():
            subject = " ".join(parts[:-2])
            room = parts[-2]
            teacher = parts[-1]
            return subject, room, teacher
        return text, "", ""

    formatted_text = ""
    for date, lessons in days:
        formatted_text += f"📅 <b>{date}</b>\n"
        for pair, info_list in lessons:
            for idx, info in enumerate(info_list):
                subj, room, teacher = split_info(info)
                if idx == 0:
                    formatted_text += f"🔹 <b>{pair}</b> - <i>{subj or 'нет пары'}</i>\n"
                else:
                    formatted_text += f"    ↳ <i>{subj or 'нет пары'}</i>\n"
                if room:
                    formatted_text += f"    🏢 {room}\n"
                if teacher:
                    formatted_text += f"    👩‍🏫 {teacher}\n"
            formatted_text += "────────\n"
        formatted_text += "\n"

    schedule_text = formatted_text.strip()
    if use_cache:
        schedule_cache[group] = {"text": schedule_text, "ts": now}
    return schedule_text, None

def get_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_state():
    global users, hashes, started_users, notify_users, last_notified
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        users = {int(k): v for k, v in data.get("users", {}).items()}
        hashes = {int(k): v for k, v in data.get("hashes", {}).items()}
        started_users = {int(x) for x in data.get("started_users", [])}
        notify_users = {int(x) for x in data.get("notify_users", [])}
        last_notified = {str(k): str(v) for k, v in data.get("last_notified", {}).items()}
        logger.info("state loaded: %d users", len(users))
    except Exception as exc:
        logger.warning("РЅРµ СѓРґР°Р»РѕСЃСЊ Р·Р°РіСЂСѓР·РёС‚СЊ СЃРѕСЃС‚РѕСЏРЅРёРµ: %s", exc)


def save_state():
    try:
        STATE_FILE.write_text(
            json.dumps(
                {
                    "users": users,
                    "hashes": hashes,
                    "started_users": sorted(started_users),
                    "notify_users": sorted(notify_users),
                    "last_notified": last_notified,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # set restrictive permissions if possible
        try:
            STATE_FILE.chmod(0o600)
        except Exception:
            pass
    except Exception as exc:
        logger.warning("РЅРµ СѓРґР°Р»РѕСЃСЊ СЃРѕС…СЂР°РЅРёС‚СЊ СЃРѕСЃС‚РѕСЏРЅРёРµ: %s", exc)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)


async def set_group_by_text(update: Update, raw_text: str):
    if not update.message:
        return

    chat_id = update.message.chat_id
    user_input = normalize_group_name(raw_text)
    log_action("group_input", chat_id, f"value={user_input or '-'}")

    matched_group_name, group_code = resolve_group_code(raw_text)
    if not group_code:
        await update.message.reply_text(
            fix_mojibake(
                "Такой группы нет.\n"
                "Поставь группу командой: /setgroup ЗУ11\n"
                "Или найди варианты: /groups ЗУ"
            )
        )
        return

    schedule, error_text = get_schedule_with_error(group_code)
    if schedule is None:
        await update.message.reply_text(fix_mojibake(error_text or "Ошибка загрузки расписания."))
        return

    users[chat_id] = group_code
    hashes[chat_id] = get_hash(schedule)
    save_state()
    log_action("group_saved", chat_id, f"group={user_input}")

    await update.message.reply_text(
        fix_mojibake(f"✅ Группа {matched_group_name or user_input} сохранена.\n\n{schedule}"),
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
    )


async def set_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.message.chat_id
    raw_text = " ".join(context.args).strip() if context.args else ""
    if not raw_text:
        await update.message.reply_text(
            fix_mojibake(
                "Использование:\n"
                "/setgroup ЗУ11\n\n"
                "Для поиска групп: /groups ЗУ"
            )
        )
        return
    log_action("set_group_command", chat_id, f"value={raw_text}")
    await set_group_by_text(update, raw_text)


async def save_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # entry point for every text message; can be group name, button click or remaining query
    if not update.message:
        return

    text = update.message.text.strip()
    chat_id = update.message.chat_id
    lowered = text.lower()

    if text == BTN_SHOW_SCHEDULE:
        await show_saved_schedule(update, context)
        return
    if text == BTN_REMAINING:
        await update.message.reply_text(
            "Напиши запрос в формате:\n"
            "• остаток математика\n"
            "• остаток всех пар"
        )
        return
    if text == BTN_MY_GROUP:
        await show_group(update, context)
        return
    if text == BTN_CHANGE_GROUP:
        await change_group(update, context)
        return
    if text == BTN_NOTIFICATIONS:
        await toggle_notifications(update, context)
        return
    if text == BTN_HELP:
        await help_command(update, context)
        return

    if lowered.startswith("остаток"):
        await handle_remaining(update, context, text)
        return

    await set_group_by_text(update, text)



async def handle_remaining(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Process a user request like 'остаток математика' or 'остаток всех пар'."""
    if not update.message:
        return

    chat_id = update.message.chat_id
    log_action("remaining_request", chat_id, f"text={text[:40]}")
    group_code = users.get(chat_id)
    if not group_code:
        await update.message.reply_text("Сначала укажи группу (например ЗУ11)")
        return

    rest = text.lower().replace("остаток", "", 1).strip()
    all_query = False
    subject_query = None
    if rest.startswith("всех"):
        all_query = True
    else:
        subject_query = rest

    result = get_remaining(group_code, subject_query, all_query)
    if result is None:
        await update.message.reply_text("Не удалось получить данные об остатке. Попробуй позже.")
    else:
        await update.message.reply_text(result)


def get_remaining(group, subject=None, all_subjects=False):
    """Fetches the "РС‚РѕРіРё" page for a group and computes remaining pairs.

    If subject is provided (nonвЂ‘empty string), returns text about that
    discipline.  If all_subjects=True, returns total by summing all
    disciplines (excluding РџРџ Рё РЈРџ)."""
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
        total_hours = sum(h for d, h in rows if not any(x in d.upper() for x in ("РџРџ","РЈРџ")))
        pairs = total_hours / 2
        return f"Осталось всего {pairs:.1f} пар ({total_hours:.1f} часов)" if total_hours else "Нет данных"

    if subject:
        # look for discipline containing subject
        for disc, h in rows:
            if matches(disc, subject):
                pairs = h / 2
                return f"{fix_mojibake(disc)}: осталось {pairs:.1f} пар ({h:.1f} часов)"
        return "Предмет не найден или остаток не указан."

    return "Неправильный запрос."



async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.message.chat_id
    log_action("start", chat_id)
    is_first_start = chat_id not in started_users
    started_users.add(chat_id)
    save_state()

    if is_first_start:
        text = (
            "Привет! Я помогу с расписанием и остатком пар.\n\n"
            "Что сделать сначала:\n"
            "1) Установи группу: /setgroup ЗУ11\n"
            "   (или просто отправь: ЗУ11)\n"
            "2) Дальше используй /r для быстрого просмотра расписания\n"
            "3) Для остатков: остаток <предмет> или остаток всех пар"
        )
    else:
        text = (
            "С возвращением. Используй кнопки ниже или команду /help."
        )

    await update.message.reply_text(fix_mojibake(text), reply_markup=MAIN_KEYBOARD)


async def show_saved_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.message.chat_id
    log_action("show_saved_schedule", chat_id)
    group_code = users.get(chat_id)
    if not group_code:
        await update.message.reply_text(
            fix_mojibake("Группа ещё не сохранена. Установи её командой: /setgroup ЗУ11")
        )
        return

    schedule, error_text = get_schedule_with_error(group_code)
    if schedule is None:
        await update.message.reply_text(fix_mojibake(error_text or "Не удалось загрузить расписание. Попробуй чуть позже."))
        return

    group_name = get_group_name_by_code(group_code)
    await update.message.reply_text(
        fix_mojibake(f"📌 Твоя группа: {group_name}\n\n{schedule}"),
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.message.chat_id
    log_action("help", chat_id)
    await update.message.reply_text(
        fix_mojibake(
            "Команды:\n"
        "/start - старт и меню\n"
        "/setgroup ЗУ11 - установить/сменить группу\n"
        "/r - расписание сохранённой группы\n"
        "/group - показать текущую группу\n"
        "/change_group - сменить группу\n"
        "/groups [текст] - поиск группы (например: /groups ИСП)\n"
        "/notify_on - включить утренние уведомления\n"
        "/notify_off - выключить утренние уведомления\n"
        "/help - эта справка"
        ),
        reply_markup=MAIN_KEYBOARD,
    )


async def show_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.message.chat_id
    log_action("show_group", chat_id)
    group_code = users.get(chat_id)
    if not group_code:
        await update.message.reply_text(fix_mojibake("Группа не сохранена. Установи: /setgroup ЗУ11"))
        return
    await update.message.reply_text(fix_mojibake(f"Текущая группа: {get_group_name_by_code(group_code)}"))


async def change_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.message.chat_id
    log_action("change_group_prompt", chat_id)
    await update.message.reply_text(fix_mojibake("Отправь новый номер группы (например ЗУ11) или используй /setgroup ЗУ11"))


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.message.chat_id
    query = " ".join(context.args).strip() if context.args else ""
    log_action("groups", chat_id, f"query={query or '-'}")

    all_groups = sorted({fix_mojibake(k) for k in GROUP_MAP.keys()})
    if not query:
        text = format_groups_list(all_groups)
    else:
        q = normalize_group_name(query)
        matched = [g for g in all_groups if q in normalize_group_name(g)]
        text = format_groups_list(matched, query=query)
    await update.message.reply_text(fix_mojibake(text))


async def notify_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.message.chat_id
    log_action("notify_on", chat_id)
    notify_users.add(chat_id)
    save_state()
    await update.message.reply_text(fix_mojibake("Утренние уведомления включены. Каждый день в 05:00 (МСК)."))


async def notify_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.message.chat_id
    log_action("notify_off", chat_id)
    notify_users.discard(chat_id)
    save_state()
    await update.message.reply_text(fix_mojibake("Утренние уведомления выключены."))


async def toggle_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.message.chat_id
    if chat_id in notify_users:
        await notify_off(update, context)
    else:
        await notify_on(update, context)


async def morning_notifications_loop(tg_app):
    while True:
        now = dt.datetime.now(MSK_TZ)
        if now.hour == MORNING_NOTIFY_HOUR and now.minute == MORNING_NOTIFY_MINUTE:
            today = now.date().isoformat()
            for chat_id in list(notify_users):
                try:
                    group_code = users.get(chat_id)
                    if not group_code:
                        continue
                    if last_notified.get(str(chat_id)) == today:
                        continue
                    schedule, _error_text = await asyncio.to_thread(get_schedule_with_error, group_code)
                    if schedule:
                        group_name = get_group_name_by_code(group_code)
                        await tg_app.bot.send_message(
                            chat_id=chat_id,
                            text=f"Доброе утро. Расписание для {group_name}:\n\n{schedule}",
                            parse_mode="HTML",
                        )
                        last_notified[str(chat_id)] = today
                        log_action("notify_sent", chat_id, f"date={today}")
                except Exception:
                    logger.exception("Failed to send morning notification")
            save_state()
            await asyncio.sleep(65)
            continue
        await asyncio.sleep(20)


async def schedule_change_notifications_loop(tg_app):
    while True:
        state_changed = False
        for chat_id, group_code in list(users.items()):
            try:
                schedule, _error_text = await asyncio.to_thread(
                    get_schedule_with_error,
                    group_code,
                    False,
                )
                if schedule is None:
                    continue

                new_hash = get_hash(schedule)
                old_hash = hashes.get(chat_id)
                if not old_hash:
                    hashes[chat_id] = new_hash
                    state_changed = True
                    continue

                if new_hash != old_hash:
                    hashes[chat_id] = new_hash
                    state_changed = True
                    group_name = get_group_name_by_code(group_code)
                    await tg_app.bot.send_message(
                        chat_id=chat_id,
                        text=fix_mojibake(
                            f"🔄 Расписание обновилось для группы {group_name}.\n\n"
                            f"Новое расписание:\n\n{schedule}"
                        ),
                        parse_mode="HTML",
                    )
                    log_action("schedule_changed_notify", chat_id, f"group={group_name}")
            except Exception:
                logger.exception("Failed to check/send schedule change notification")

        if state_changed:
            save_state()
        await asyncio.sleep(CHANGE_CHECK_INTERVAL_SEC)


def start_event_loop(loop: asyncio.AbstractEventLoop):
    """Run dedicated asyncio loop in a background thread."""
    asyncio.set_event_loop(loop)
    loop.run_forever()


def log_future_exception(future):
    """Log errors from background coroutine execution."""
    try:
        future.result()
    except Exception:
        logger.exception("Error while processing Telegram update")


if __name__ == "__main__":
    load_state()

    # PTB runs on a dedicated loop; Flask handlers are synchronous.
    tg_loop = asyncio.new_event_loop()
    tg_loop_thread = threading.Thread(
        target=start_event_loop,
        args=(tg_loop,),
        daemon=True,
    )
    tg_loop_thread.start()

    tg_app = ApplicationBuilder().token(TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start_command))
    tg_app.add_handler(CommandHandler("setgroup", set_group_command))
    tg_app.add_handler(CommandHandler("r", show_saved_schedule))
    tg_app.add_handler(CommandHandler("help", help_command))
    tg_app.add_handler(CommandHandler("group", show_group))
    tg_app.add_handler(CommandHandler("change_group", change_group))
    tg_app.add_handler(CommandHandler("groups", groups_command))
    tg_app.add_handler(CommandHandler("notify_on", notify_on))
    tg_app.add_handler(CommandHandler("notify_off", notify_off))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_group))

    init_future = asyncio.run_coroutine_threadsafe(tg_app.initialize(), tg_loop)
    init_future.result(timeout=30)
    notify_loop_future = asyncio.run_coroutine_threadsafe(
        morning_notifications_loop(tg_app),
        tg_loop,
    )
    notify_loop_future.add_done_callback(log_future_exception)
    change_notify_loop_future = asyncio.run_coroutine_threadsafe(
        schedule_change_notifications_loop(tg_app),
        tg_loop,
    )
    change_notify_loop_future.add_done_callback(log_future_exception)

    # Create Flask web server for webhook
    flask_app = Flask(__name__)

    # Webhook endpoint to receive updates from Telegram
    @flask_app.route("/webhook", methods=["POST"])
    def webhook():
        data = flask_request.get_json()
        try:
            update = Update.de_json(data, tg_app.bot)
            process_future = asyncio.run_coroutine_threadsafe(
                tg_app.process_update(update),
                tg_loop,
            )
            process_future.add_done_callback(log_future_exception)
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

