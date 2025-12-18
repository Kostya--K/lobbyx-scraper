import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from telegram import Bot

# -------------------------------------------------
# LOGGING
# -------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("hirefire-scraper")

# -------------------------------------------------
# TIME WINDOW (Kyiv)
# -------------------------------------------------
KYIV_TZ = ZoneInfo("Europe/Kyiv")
now = datetime.now(KYIV_TZ)

if not (8 <= now.hour < 20):
    logger.info("Outside Kyiv working hours")
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


def get_all_vacancy_links(session):
    """
    Collects all vacancy links using real pagination (rel="next")
    """
    vacancy_links = set()
    next_url = BASE_URL
    visited_pages = set()

    while next_url:
        if next_url in visited_pages:
            logger.warning("Pagination loop detected, stopping")
            break

        visited_pages.add(next_url)
        logger.debug("Loading vacancies page: %s", next_url)

        r = session.get(next_url)
        if r.status_code != 200:
            logger.warning("Failed to load %s", next_url)
            break

        soup = BeautifulSoup(r.text, "html.parser")

        # collect vacancy links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/vacancies/"):
                vacancy_links.add(href)

        # find next page
        next_link = soup.find("a", rel="next")
        if next_link and next_link.get("href"):
            next_url = BASE_URL + next_link["href"]
        else:
            next_url = None

    return vacancy_links


def parse_candidates(html, vacancy_name, account_label):
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for tr in soup.find_all("tr", {"data-controller": "candidate-line"}):
        cid = tr.get("data-candidate")
        if not cid or cid in seen_ids:
            logger.debug("Skipping already seen candidate %s or missing cid", cid)
            continue

        name = tr.select_one(".form-name")
        info_divs = tr.select_one(".form-info").find_all("div")
        phone_el = info_divs[1] if len(info_divs) > 1 else None
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
            "phone": phone_el.get_text(strip=True) if phone_el else None,
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

    logger.info("Processing account: %s", acc["label"])

    session = requests.Session()
    login(session, acc["email"], acc["password"])

    vacancy_links = get_all_vacancy_links(session)

    logger.info(
        "[%s] Total vacancies found: %d",
        acc["label"],
        len(vacancy_links),
    )

    for link in vacancy_links:
        r = session.get(BASE_URL + link)
        if r.status_code != 200:
            logger.warning("Vacancy %s returned %s", link, r.status_code)
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
        lines = []

        if c.get("account"):
            lines.append(f"👤 {c['account']}")
        if c.get("vacancy_name"):
            lines.append(f"📌 {c['vacancy_name']}")
        if c.get("name"):
            lines.append(f"👨 {c['name']}")
        if c.get("phone"):
            lines.append(f"📞 {c['phone']}")
        if c.get("age"):
            lines.append(f"🎂 {c['age']}")
        if c.get("rank"):
            lines.append(f"🎖 {c['rank']}")
        if c.get("combat_experience"):
            lines.append(f"⚔️ Бойовий досвід: {c['combat_experience']}")
        if c.get("awol"):
            lines.append(f"🚫 СЗЧ: {c['awol']}")
        if c.get("military_training"):
            lines.append(f"🎓 Підготовка: {c['military_training']}")
        if c.get("created_at"):
            lines.append(f"🕒 {c['created_at']}")
        if c.get("source"):
            lines.append(f"📎 Джерело: {c['source']}")

        msg = "\n".join(lines)

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
logger.debug("Stored candidates")
logger.debug("\n".join(seen_ids))

logger.info("Sent %d new candidates.", len(new_candidates))
