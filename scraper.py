#!/usr/bin/env python3
"""Daily scraper for St Peter's College meal timeslots. Sends ntfy push notification."""

import json
import logging
import re
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
TERM_DATES_PATH = SCRIPT_DIR / "term_dates.json"

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_INDEX = {name: i for i, name in enumerate(DAY_NAMES)}

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

MEAL_TYPES = {"breakfast", "lunch", "supper", "brunch", "informal hall", "formal hall"}

MENU_H2S = {"breakfast", "lunch", "informal hall"}

MEAL_TO_MENU_H2 = {
    "breakfast": "breakfast",
    "brunch":    "breakfast",
    "lunch":     "lunch",
    "supper":    "informal hall",
}


def load_config(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def load_term_dates(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)["terms"]


def is_in_term(today: date, terms: list[dict]) -> bool:
    for t in terms:
        week1 = date.fromisoformat(t["start"])
        if week1 - timedelta(days=7) <= today <= week1 + timedelta(days=62):
            return True
    return False


def setup_logging(log_file: str) -> None:
    fmt = "%(asctime)s %(levelname)s %(message)s"
    handlers = [
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def fetch_page(url: str, timeout: int) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 SPC-Meals-Notifier/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


class MealtimesParser(HTMLParser):
    """Walks the full page HTML and extracts meal time blocks and menu items.

    Each block: {"h2": "<section heading>", "h3": "<meal type>", "text": "<content line>"}
    """

    def __init__(self):
        super().__init__()
        self.blocks: list[dict] = []
        self._current_h2 = ""
        self._current_h3 = ""
        self._collecting_tag = None  # "h2", "h3", "p", or "li"
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("h2", "h3", "p", "li"):
            self._collecting_tag = tag
            self._buf = ""

    def handle_endtag(self, tag):
        if tag != self._collecting_tag:
            return
        text = self._buf.strip()
        self._collecting_tag = None
        self._buf = ""

        if not text:
            return

        if tag == "h2":
            self._current_h2 = text
            self._current_h3 = ""
        elif tag == "h3":
            lower = text.lower()
            if "informal hall" in lower or "supper" in lower:
                self._current_h3 = "supper"
            elif any(m in lower for m in MEAL_TYPES):
                self._current_h3 = lower.split()[0]  # "breakfast", "lunch", "brunch"
            else:
                self._current_h3 = lower
        elif tag in ("p", "li"):
            if self._current_h3 and self._current_h2:
                self.blocks.append({
                    "h2": self._current_h2,
                    "h3": self._current_h3,
                    "text": text,
                })

    def handle_data(self, data):
        if self._collecting_tag:
            self._buf += data

    def handle_entityref(self, name):
        if self._collecting_tag:
            entities = {"amp": "&", "lt": "<", "gt": ">", "nbsp": " ", "ndash": "-", "mdash": "-"}
            self._buf += entities.get(name, "")

    def handle_charref(self, name):
        if self._collecting_tag:
            try:
                char = chr(int(name[1:], 16) if name.startswith("x") else int(name))
                self._buf += char
            except (ValueError, OverflowError):
                pass


def parse_mealtimes(html: str) -> list[dict]:
    parser = MealtimesParser()
    parser.feed(html)
    return parser.blocks


def text_mentions_day(text: str, day_name: str) -> bool:
    """Return True if the time-line text covers the given day of week."""
    lower = text.lower()
    target_idx = DAY_INDEX.get(day_name)
    if target_idx is None:
        return False

    for m in re.finditer(r'(\w+day)-(\w+day)', lower):
        start = DAY_INDEX.get(m.group(1))
        end = DAY_INDEX.get(m.group(2))
        if start is not None and end is not None:
            if start <= target_idx <= end:
                return True

    mentioned = re.findall(r'\b(\w+day)\b', lower)
    return day_name in mentioned


def _parse_dates_from_text(text: str, year: int) -> list[date]:
    """Extract all DD Month dates from a text string."""
    pattern = re.findall(r'(\d{1,2})\s+(' + '|'.join(MONTH_NAMES) + r')', text.lower())
    dates = []
    for d, m in pattern:
        try:
            dates.append(date(year, MONTH_NAMES[m], int(d)))
        except ValueError:
            pass
    return dates


def _date_in_range(text: str, today: date) -> bool | None:
    """Check if today's date appears in a vacation-style date line.

    Returns True if today is mentioned, False if clearly out of range,
    None if the text doesn't look like a specific-date line.
    """
    lower = text.lower()
    if not any(m in lower for m in MONTH_NAMES):
        return None

    dates = _parse_dates_from_text(text, today.year)
    if not dates:
        return None

    if len(dates) == 1:
        return today.day == dates[0].day and today.month == dates[0].month

    start, end = dates[0], dates[-1]
    return start <= today <= end


def is_website_stale(blocks: list[dict], today: date) -> bool:
    """Return True if the menu sections only contain dates in the past."""
    menu_blocks = [b for b in blocks if b["h2"].lower() in MENU_H2S]
    if not menu_blocks:
        return False  # No dated menus posted yet; not stale

    latest: date | None = None
    for b in menu_blocks:
        for d in _parse_dates_from_text(b["h3"], today.year):
            if latest is None or d > latest:
                latest = d

    return latest is not None and latest < today


def find_times_for_today(blocks: list[dict], today: date) -> dict:
    """Extract meal times for today from parsed blocks."""
    day_name = DAY_NAMES[today.weekday()]
    is_weekend = today.weekday() >= 5

    result = {}

    vacation_blocks = [b for b in blocks if any(m in b["h2"].lower() for m in MONTH_NAMES)]
    term_blocks = [b for b in blocks if b not in vacation_blocks]

    for block in vacation_blocks:
        match = _date_in_range(block["text"], today)
        if match is True:
            lower = block["text"].lower()
            if "closed" in lower:
                return {"closed": True, "reason": block["text"]}
            meal = block["h3"]
            if meal in ("breakfast", "brunch") and not is_weekend and meal == "brunch":
                continue
            if meal in ("breakfast", "brunch") and is_weekend and meal == "breakfast":
                continue
            times = re.findall(r'\d+(?:\.\d+)?(?:am|pm)(?:-\d+(?:\.\d+)?(?:am|pm))?', lower)
            if times:
                result[meal] = times[0] if len(times) == 1 else f"{times[0]}-{times[-1]}"

    if result:
        return result

    for block in term_blocks:
        meal = block["h3"]
        if meal not in ("breakfast", "brunch", "supper", "lunch"):
            continue
        if is_weekend and meal == "breakfast":
            continue
        if not is_weekend and meal == "brunch":
            continue

        if text_mentions_day(block["text"], day_name):
            if "closed" in block["text"].lower():
                result[f"{meal}_closed"] = block["text"]
            else:
                time_str = extract_time_from_text(block["text"])
                if time_str and meal not in result:
                    result[meal] = time_str

    if not result:
        result["no_schedule"] = True

    return result


def extract_time_from_text(text: str) -> str:
    """Strip day-of-week prefix and return just the time portion."""
    m = re.search(r'\d', text)
    if m:
        return text[m.start():].strip()
    return text.strip()


def find_menu_for_meal(blocks: list[dict], meal_type: str, today: date) -> list[str]:
    """Extract food menu items for a specific meal today from the menu sections."""
    menu_h2 = MEAL_TO_MENU_H2.get(meal_type)
    if not menu_h2:
        return []
    day_name = DAY_NAMES[today.weekday()]
    for b in blocks:
        if b["h2"].lower() == menu_h2 and b["h3"].lower().startswith(day_name):
            return [b["text"]]
    return []


def determine_meal_from_time(now: datetime) -> str:
    """Determine which meal notification to send based on the current hour."""
    hour = now.hour
    if hour < 9:
        return "breakfast"
    if hour < 14:
        return "lunch"
    return "supper"


def build_meal_notification(meal: str, time_str: str, menu_items: list[str]) -> tuple[str, str]:
    """Build title and body for a single-meal notification."""
    title = f"{meal.capitalize()} {time_str}"
    if menu_items:
        parts = [p.strip() for p in menu_items[0].split(",")]
        body = ", ".join(parts[:2])
    else:
        body = time_str
    return title, body


def send_notification(base_url: str, topic: str, title: str, body: str) -> bool:
    url = f"{base_url.rstrip('/')}/{topic}"
    data = body.encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Title": title,
            "Content-Type": "text/plain; charset=utf-8",
            "Priority": "default",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            logging.info("ntfy response: HTTP %s", status)
            return status == 200
    except urllib.error.HTTPError as e:
        logging.error("ntfy HTTP error: %s", e)
        return False
    except urllib.error.URLError as e:
        logging.error("ntfy network error: %s", e)
        return False


def main() -> int:
    config = load_config(CONFIG_PATH)
    setup_logging(config["log_file"])

    now = datetime.now()
    today = now.date()
    logging.info("SPC meals notifier starting for %s", today.isoformat())

    terms = load_term_dates(TERM_DATES_PATH)
    if not is_in_term(today, terms):
        logging.info("Outside term dates — no notification sent")
        return 0

    prefix = config.get("ntfy_topic_prefix", "spc-meals")
    meal = determine_meal_from_time(now)
    topic = f"{prefix}-{meal}"
    logging.info("Meal: %s, topic: %s", meal, topic)

    try:
        html = fetch_page(config["meals_url"], config["request_timeout_seconds"])
    except Exception as e:
        logging.error("Failed to fetch meals page: %s", e)
        send_notification(
            config["ntfy_base_url"], topic,
            f"SPC Meals - fetch failed",
            "Could not load the meals page. Check manually.",
        )
        return 1

    blocks = parse_mealtimes(html)
    logging.info("Parsed %d meal blocks from page", len(blocks))

    if is_website_stale(blocks, today):
        logging.warning("SPC website appears stale (all date sections are in the past) — skipping notification")
        return 0

    times = find_times_for_today(blocks, today)
    logging.info("Times for today: %s", times)

    if times.get("closed"):
        send_notification(
            config["ntfy_base_url"], topic,
            "Dining closed today",
            times.get("reason", "Dining closed today."),
        )
        logging.info("Sent dining-closed notification")
        return 0

    if times.get("no_schedule"):
        logging.warning("No schedule found for today — skipping notification")
        return 0

    is_weekend = today.weekday() >= 5
    actual_meal = "brunch" if (meal == "breakfast" and is_weekend) else meal

    closed_reason = times.get(f"{actual_meal}_closed") or times.get(f"{meal}_closed")
    if closed_reason:
        send_notification(
            config["ntfy_base_url"], topic,
            f"{actual_meal.capitalize()} - Hall closed",
            str(closed_reason),
        )
        logging.info("Sent hall-closed notification for %s", actual_meal)
        return 0

    time_str = times.get(actual_meal) or times.get(meal)

    if not time_str:
        logging.warning("No time found for %s — skipping notification", meal)
        return 0

    menu_items = find_menu_for_meal(blocks, actual_meal, today)
    logging.info("Menu items for %s: %s", actual_meal, menu_items)

    title, body = build_meal_notification(actual_meal, time_str, menu_items)
    logging.info("Sending notification: %r / %r", title, body)

    ok = send_notification(config["ntfy_base_url"], topic, title, body)
    if ok:
        logging.info("Notification sent successfully")
        return 0
    else:
        logging.error("Failed to send notification")
        return 1


if __name__ == "__main__":
    sys.exit(main())
