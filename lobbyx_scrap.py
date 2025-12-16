import requests
import json
import os
import sys
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from telegram import Bot

# -------------------------------------------------
# TIME WINDOW (Kyiv)
# -------------------------------------------------
KYIV_TZ = ZoneInfo("Europe/Kyiv")
now = datetime.now(KYIV_TZ)

if not (8 <= now.hour < 20):
    print("Outside Kyiv working hours")
    sys.exit(0)

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
LOGIN_URL = "https://hirefire.thelobbyx.com/login"
BASE_URL = "https://hirefire.thelobbyx.com"
SEEN_FILE = "seen_candidates.json"

ACCOUNTS = [
    {
        "email": os.getenv("EMAIL_1"),
        "password": os.getenv("PASSWORD_1"),
        "label": 'ББС "Сапсан"',
    },
    {
        "email": os.getenv("EMAIL_2"),
        "password": os.getenv("PASSWORD_2"),
        "label": "14 ОМБр",
    },
]

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_IDS = json.loads(os.getenv("TELEGRAM_CHAT_IDS"))

bot = Bot(token=TELEGRAM_TOKEN)

# -------------------------------------------------
# LOAD SEEN IDS
# -------------------------------------------------
if os.path.exists(SEEN_FILE):
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        seen_ids = set(json.load(f))
else:
    seen_ids = set()

new_candidates = []

# -------------------------------------------------
# HELPERS
# -------------------------------------------------
def login(session, email, password):
    r = session.get(LOGIN_URL)
    soup = BeautifulSoup(r.text, "html.parser")
    token = soup.find("input", {"name": "authenticity_token"})["value"]

    payload = {
        "utf8": "✓",
        "authenticity_token": token,
        "user[email]": email,
        "user[password]": password,
        "commit": "Увійти",
    }

    session.post(LOGIN_URL, data=payload)


def extract_phone(info_div):
    """Return ONLY phone number"""
    for div in info_div.find_all("div"):
        text = div.get_text(strip=True)
        if text.isdigit():
            return text
    return None


def parse_candidates(html, vacancy_name, account_label):
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for tr in soup.find_all("tr", {"data-controller": "candidate-line"}):
        cid = tr.get("data-candidate")
        if not cid or cid in seen_ids:
            continue

        name = tr.select_one(".form-name")
        info = tr.select_one(".form-info")
        time_el = tr.select_one(".divTableCellTime")
        age_el = tr.select_one(".divTableCellAge")
        rank_el = tr.select_one(".divTableCellRank")
        combat_el = tr.select_one(".divTableCellCombatExperience")
        awol_el = tr.select_one(".divTableCellAbsentWithoutPermission")
        training_el = tr.select_one(".divTableCellMilitaryTraining")
        source_el = tr.select_one(".divTableCellSourse")

        candidate = {
            "id": cid,
            "account": account_label,
            "vacancy_name": vacancy_name,
            "name": name.get_text(strip=True) if name else None,
            "phone": extract_phone(info) if info else None,
            "created_at": time_el.get_text(strip=True) if time_el else None,
            "age": age_el.get_text(strip=True) if age_el else None,
            "rank": rank_el.get_text(strip=True) if rank_el else None,
            "combat_experience": combat_el.get_text(strip=True) if combat_el else None,
            "awol": awol_el.get_text(strip=True) if awol_el else None,
            "military_training": training_el.get_text(strip=True) if training_el else None,
            "source": source_el.get_text(strip=True) if source_el else None,
        }

        seen_ids.add(cid)
        results.append(candidate)

    return results


# -------------------------------------------------
# SCRAPE ALL ACCOUNTS
# -------------------------------------------------
for acc in ACCOUNTS:
    if not acc["email"]:
        continue

    session = requests.Session()
    login(session, acc["email"], acc["password"])

    main = session.get(BASE_URL)
    soup = BeautifulSoup(main.text, "html.parser")

    vacancy_links = {
        a["href"] for a in soup.find_all("a", href=True)
        if a["href"].startswith("/vacancies/")
    }

    for link in vacancy_links:
        r = session.get(BASE_URL + link)
        if r.status_code != 200:
            continue

        soup_v = BeautifulSoup(r.text, "html.parser")
        h1 = soup_v.find("h1")
        vacancy_name = h1.get_text(strip=True) if h1 else link

        parsed = parse_candidates(r.text, vacancy_name, acc["label"])
        new_candidates.extend(parsed)

# -------------------------------------------------
# TELEGRAM SEND
# -------------------------------------------------
async def send_to_telegram(candidates):
    for c in candidates:
        msg = (
            f"👤 {c['account']}\n"
            f"📌 {c['vacancy_name']}\n"
            f"👨 {c['name']}\n"
            f"📞 {c['phone']}\n"
            f"🎂 {c['age']}\n"
            f"🎖 {c['rank']}\n"
            f"⚔️ Бойовий досвід: {c['combat_experience']}\n"
            f"🚫 СЗЧ: {c['awol']}\n"
            f"🎓 Підготовка: {c['military_training']}\n"
            f"🕒 {c['created_at']}\n"
            f"📎 Джерело: {c['source']}"
        )

        for chat_id in TELEGRAM_CHAT_IDS:
            await bot.send_message(chat_id=chat_id, text=msg)

        await asyncio.sleep(0.15)


if new_candidates:
    asyncio.run(send_to_telegram(new_candidates))

# -------------------------------------------------
# SAVE STATE
# -------------------------------------------------
with open(SEEN_FILE, "w", encoding="utf-8") as f:
    json.dump(list(seen_ids), f, ensure_ascii=False, indent=2)

print(f"Sent {len(new_candidates)} new candidates.")
