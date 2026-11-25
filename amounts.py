"""Currency-aware listing amount extraction.

Priority is intentionally simple and deterministic:
1. Topic title
2. Written listing caption/text
3. No automatic amount - the bot asks the admin group for the amount.

OCR is still indexed for search, but OCR text is not used as the authoritative
listing amount because OCR can turn game timers/statistics into false prices.
"""
import re

NUM = r"\d[\d,]*(?:\.\d+)?\s*[kK]?"
# Currency symbols used around the world. The parser also accepts a 3-letter
# uppercase currency code immediately next to the number (e.g. USD 500).
CURRENCY_SYMBOLS = r"[$₹€£¥₽₩₪₫₴₦₺₱₲₵₡₭₮₸₼₾៛﷼؋₢₣₤₥₧₯₠₰₻₷₶₳]"
CURRENCY_NAMES = (
    r"rupees?|dollars?|yen|euros?|pounds?|sterling|won|rubles?|roubles?|ruble|"
    r"francs?|pesos?|baht|ringgit|rand|naira|dirham|riyals?|rials?|dinars?|"
    r"lira|krona|kroner|krone|hryvnia|forints?|zloty|złoty|shekel|dong|"
    r"taka|kip|kyat|pataca|bolivars?|leu|lev|lari|manat|som|tenge|tugrik|"
    r"dalasi|birr|shilling|kwacha|ariary|metical|pula|cedi|kwanza|escudo|"
    r"quetzals?|col[oó]nes?|cordobas?|balboas?|gourde|lek|dram|afghani|"
    r"somoni|vatu|kina|tala|paanga|maloti|lilangeni|ouguiya|nakfa|francs?"
)
CURRENCY_WORD = rf"\b(?:{CURRENCY_NAMES})\b"
CURRENCY_CODE = r"\b[A-Z]{3}\b"
CURRENCY_TOKEN = rf"(?:{CURRENCY_SYMBOLS}|{CURRENCY_WORD}|{CURRENCY_CODE})"


def number(raw):
    raw = str(raw or "").lower().replace(",", "").replace(" ", "")
    mult = 1000 if raw.endswith("k") else 1
    raw = raw[:-1] if raw.endswith("k") else raw
    try:
        return float(raw) * mult
    except (ValueError, TypeError):
        return None


def _valid_range(a_raw, b_raw=None):
    a = number(a_raw)
    b = number(b_raw) if b_raw else a
    if a is None or b is None:
        return None
    return min(a, b), max(a, b)


def _accounts_topic_band(topic):
    topic = topic or ""
    # Keep the existing useful shorthand such as "Accounts 2k-5k".
    if re.search(r"\baccounts?\b", topic, re.I):
        m = re.search(rf"\baccounts?\b.*?({NUM})\s*(?:-|–|to)\s*({NUM})\b", topic, re.I)
        if m:
            result = _valid_range(m.group(1), m.group(2))
            if result:
                return result
        m = re.search(rf"\baccounts?\b.*?({NUM})\s*\+", topic, re.I)
        if m:
            low = number(m.group(1))
            if low is not None:
                return low, float("inf")
        # Do not treat a bare account/K number as a price here.
    return None


def _explicit_currency_amount(text):
    """Find any amount explicitly paired with a currency symbol/name/code."""
    text = text or ""
    # Labels are helpful but never required: USD 500, $500, 500 USD, 500 dollars,
    # ₹500-780 and 500-780 rupees are all accepted.
    patterns = [
        re.compile(
            rf"\b(?:price|amount|cost|asking\s+price)\s*(?:is\s*)?[:=]?\s*"
            rf"(?:{CURRENCY_TOKEN}\s*)?({NUM})"
            rf"(?:\s*(?:-|–|to)\s*(?:{CURRENCY_TOKEN}\s*)?({NUM}))?",
            re.I,
        ),
        re.compile(
            rf"(?<![\w:]){CURRENCY_TOKEN}\s*({NUM})"
            rf"(?:\s*(?:-|–|to)\s*(?:{CURRENCY_TOKEN}\s*)?({NUM}))?"
            rf"(?![\w:]|\.\d{{2}}:\d{{2}})",
            re.I,
        ),
        re.compile(
            rf"(?<![\w:])({NUM})(?:\s*(?:-|–|to)\s*({NUM}))?\s*"
            rf"(?:{CURRENCY_WORD}|{CURRENCY_CODE})\b"
            rf"(?![\w:]|\.\d{{2}}:\d{{2}})",
            re.I,
        ),
    ]
    for pat in patterns:
        m = pat.search(text)
        if m:
            # Guard against generic 'ABC 500' in labels if ABC is not uppercase.
            if len(m.groups()) >= 1:
                result = _valid_range(m.group(1), m.group(2) if len(m.groups()) > 1 else None)
                if result:
                    return result
    return None


def _currency_label_near_amount(text):
    if not text:
        return ""
    # Prefer a symbol, then a written/coded marker near a number.
    for pat in (
        rf"{CURRENCY_SYMBOLS}\s*{NUM}",
        rf"{NUM}\s*{CURRENCY_SYMBOLS}",
        rf"{CURRENCY_WORD}\s*{NUM}",
        rf"{NUM}\s*{CURRENCY_WORD}",
        rf"\b[A-Z]{{3}}\s*{NUM}",
        rf"{NUM}\s*\b[A-Z]{{3}}\b",
    ):
        m = re.search(pat, text, re.I if "A-Z" not in pat else 0)
        if not m:
            continue
        raw = m.group(0)
        symbol = re.search(CURRENCY_SYMBOLS, raw)
        if symbol:
            return symbol.group(0)
        code = re.search(r"\b[A-Z]{3}\b", raw)
        if code:
            return code.group(0).upper()
        name_map = [
            (r"rupees?", "INR"), (r"dollars?", "USD"), (r"yen", "JPY"),
            (r"euros?", "EUR"), (r"pounds?|sterling", "GBP"),
            (r"won", "KRW"), (r"rubles?|roubles?", "RUB"), (r"francs?", "CHF"),
            (r"pesos?", "PHP"), (r"baht", "THB"), (r"ringgit", "MYR"),
            (r"rand", "ZAR"), (r"naira", "NGN"), (r"dirham", "AED"),
            (r"riyals?|rials?", "SAR"), (r"dinars?", "KWD"), (r"lira", "TRY"),
            (r"hryvnia", "UAH"), (r"forints?", "HUF"), (r"zloty|złoty", "PLN"),
            (r"shekel", "ILS"), (r"dong", "VND"), (r"taka", "BDT"),
            (r"kip", "LAK"), (r"kyat", "MMK"), (r"pataca", "MOP"),
            (r"bolivars?", "VES"), (r"leu", "RON"), (r"lev", "BGN"),
            (r"lari", "GEL"), (r"manat", "AZN"), (r"som", "KGS"),
            (r"tenge", "KZT"), (r"tugrik", "MNT"), (r"birr", "ETB"),
            (r"shilling", "KES"), (r"kwacha", "ZMW"), (r"cedi", "GHS"),
            (r"kwanza", "AOA"), (r"escudo", "CVE"), (r"quetzals?", "GTQ"),
            (r"cordobas?", "NIO"), (r"gourde", "HTG"), (r"lek", "ALL"),
            (r"dram", "AMD"), (r"afghani", "AFN"), (r"somoni", "TJS"),
            (r"vatu", "VUV"), (r"kina", "PGK"), (r"tala", "WST"),
        ]
        for name, code in name_map:
            if re.search(name, raw, re.I):
                return code
    return ""


def extract_listing_currency(topic_title, text, ocr_text="", source=None):
    if source == "topic":
        return _currency_label_near_amount(topic_title) or _currency_label_near_amount(text)
    if source == "message":
        return _currency_label_near_amount(text)
    if source == "ocr":
        return _currency_label_near_amount(ocr_text)
    return ""


def extract_listing_amount(topic_title, text, ocr_text=""):
    """Return (min, max, source) using topic -> caption/text only.

    OCR remains searchable but is deliberately excluded from price authority.
    """
    topic_band = _accounts_topic_band(topic_title)
    if topic_band:
        return topic_band[0], topic_band[1], "topic"
    topic_marked = _explicit_currency_amount(topic_title)
    if topic_marked:
        return topic_marked[0], topic_marked[1], "topic"
    post_amount = _explicit_currency_amount(text)
    if post_amount:
        return post_amount[0], post_amount[1], "message"
    return None, None, "missing"
