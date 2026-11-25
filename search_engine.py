"""Search parser and ranking for captions + OCR text from Telegram listings."""

import re
from datetime import datetime, timezone
import config
import database

CURRENCY = r"(?:[$₹€£¥₽₩₪₫₴₦₺₱]|\b(?:USD|INR|EUR|GBP|JPY|rupees?|dollars?|yen|euros?|pounds?|sterling|won|rubles?|roubles?|pesos?|baht|ringgit|rand|naira|dirham|riyal|rial|dinars?|lira|krona|hryvnia|forints?|zloty|złoty|shekel|dong|taka|kip|kyat|lari|manat|som|tenge|tugrik|birr|shilling|kwacha|cedi|kwanza|dram|afghani|somoni|vatu|kina|tala)\b|\b[A-Z]{3}\b)"
AMOUNT = r"\d[\d,]*(?:\.\d+)?[kK]?"


def amount_value(raw):
    value = str(raw).lower().replace(",", "").replace(" ", "")
    mult = 1000 if value.endswith("k") else 1
    if value.endswith("k"):
        value = value[:-1]
    try:
        return float(value) * mult
    except ValueError:
        return None


def normalize(text: str) -> str:
    text = (text or "").lower().replace("–", "-").replace("—", "-").replace("’", "'")
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    # Normalize compact shorthand common in account listings.
    text = re.sub(r"\b(\d+)\s*champs?\b", r"\1 champion", text)
    text = re.sub(r"\b(\d+)\s*champions?\b", r"\1 champion", text)
    text = re.sub(r"\b(\d+)\s*fams?\b", r"\1 familiar", text)
    text = re.sub(r"\b(\d+)\s*leader\s*skins?\b", r"\1 leader skin", text)
    # Treat OCR variants K 1700 and K1700 as the same kingdom token.
    text = re.sub(r"\bk\s*(\d{3,5})\b", r"k\1", text, flags=re.I)
    text = re.sub(r"[\u00a0\t\r]+", " ", text)
    text = re.sub(r"[^\w.%$€₹+-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for alias, canonical in sorted(config.ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(alias)}(?!\w)", canonical, text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_structured(query):
    """Return explicit numeric filters, free-text terms and a sort mode."""
    work = query or ""
    options = {"price_min": None, "price_max": None, "kingdom_min": None,
               "kingdom_max": None, "posted_within_hours": None,
               "sort": "relevance", "strict": False}

    if re.search(r"\bstrict\s*:", work, re.I):
        options["strict"] = True
        work = re.sub(r"\bstrict\s*:\s*", " ", work, flags=re.I)

    # Treat natural-language sort filters as controls, not listing keywords.
    for phrase in (r"filter\s+(?:the\s+)?(?:lowest|cheapest)(?:\s+price)?",
                   r"(?:lowest|cheapest)\s+price", r"sort\s+by\s+(?:price|amount)"):
        m = re.search(phrase, work, re.I)
        if m:
            options["sort"] = "cheapest"
            work = work[:m.start()] + " " + work[m.end():]
    sort_keys = sorted(config.SORT_KEYWORDS.keys(), key=len, reverse=True)
    for word in sort_keys:
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", work, re.I):
            options["sort"] = config.SORT_KEYWORDS[word]
            work = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", " ", work, flags=re.I)

    # Recency filters used by the reference bot: 7d, 24h/today, 2h, or natural wording.
    time_patterns = [
        (r"\b(?:today|24h|24hrs?|24hours?)\b", 24),
        (r"\b(?:last|within|posted\s+within)\s+(\d{1,3})\s*(?:hours?|hrs?|h)\b", None),
        (r"\b(?:last|within|posted\s+within)\s+(\d{1,3})\s*(?:days?|d)\b", None),
        (r"\b(\d{1,3})\s*(?:hours?|hrs?|h)\b", None),
        (r"\b(\d{1,3})\s*(?:days?|d)\b", None),
    ]
    for pattern, fixed_hours in time_patterns:
        m = re.search(pattern, work, re.I)
        if not m:
            continue
        if fixed_hours is not None:
            hours = fixed_hours
        else:
            amount = int(m.group(1))
            unit_text = m.group(0).lower()
            hours = amount * (24 if ("day" in unit_text or re.search(r"\d+d\b", unit_text)) else 1)
        if 1 <= hours <= 24 * 365:
            options["posted_within_hours"] = hours
            work = work[:m.start()] + " " + work[m.end():]
            break

    # Kingdom range forms: kingdom:1500-1700, K1500-K1700, K1500-1700, or K1500 K1700.
    m = re.search(r"\bkingdom\s*[:=]\s*(\d{3,5})\s*(?:-|to)\s*(\d{3,5})\b", work, re.I)
    if m:
        options["kingdom_min"], options["kingdom_max"] = sorted((int(m.group(1)), int(m.group(2))))
        work = work[:m.start()] + " " + work[m.end():]
    else:
        m = re.search(r"\bk(\d{3,5})\s*[-–]\s*k?(\d{3,5})\b", work, re.I)
        if not m:
            m = re.search(r"\bk(\d{3,5})\s+k(\d{3,5})\b", work, re.I)
        if m:
            options["kingdom_min"], options["kingdom_max"] = sorted((int(m.group(1)), int(m.group(2))))
            work = work[:m.start()] + " " + work[m.end():]

    m = re.search(r"\bkingdom\s*[:=]\s*k?(\d{3,5})\b", work, re.I)
    if m:
        options["kingdom_min"] = options["kingdom_max"] = int(m.group(1))
        work = work[:m.start()] + " " + work[m.end():]

    # Price range syntax: price:1000-5000, ₹500-780, ¥680, $500-$1500,
    # 500-780 rupees, and corresponding ISO currency codes.
    m = re.search(
        rf"\b(?:price|amount)\s*[:=]?\s*(?:{CURRENCY}\s*)?({AMOUNT})\s*(?:-|–|to)\s*"
        rf"(?:{CURRENCY}\s*)?({AMOUNT})(?:\s*{CURRENCY})?", work, re.I
    )
    if m:
        vals = [amount_value(m.group(1)), amount_value(m.group(2))]
        if all(v is not None for v in vals):
            options["price_min"], options["price_max"] = sorted(vals)
            work = work[:m.start()] + " " + work[m.end():]
    else:
        m = re.search(
            rf"(?<![\w:]){CURRENCY}\s*({AMOUNT})\s*(?:-|–|to)\s*"
            rf"(?:{CURRENCY}\s*)?({AMOUNT})(?:\s*{CURRENCY})?(?![\w:]|\.\d{{2}}:\d{{2}})", work, re.I
        )
        if m:
            vals = [amount_value(m.group(1)), amount_value(m.group(2))]
            if all(v is not None for v in vals):
                options["price_min"], options["price_max"] = sorted(vals)
                work = work[:m.start()] + " " + work[m.end():]
        else:
            m = re.search(
                rf"(?<![\w:])({AMOUNT})\s*(?:-|–|to)\s*({AMOUNT})\s*{CURRENCY}\b"
                rf"(?![\w:]|\.\d{{2}}:\d{{2}})", work, re.I
            )
            if m:
                vals = [amount_value(m.group(1)), amount_value(m.group(2))]
                if all(v is not None for v in vals):
                    options["price_min"], options["price_max"] = sorted(vals)
                    work = work[:m.start()] + " " + work[m.end():]

    # Under/below/over/above price filters: under:2000, price under 2000, below 2k.
    m = re.search(rf"\b(?:(?:price|amount)\s+)?(?:under|below|less\s+than|max(?:price|amount)?\s*[:=]?)\s*[:=]?\s*(?:{CURRENCY}\s*)?({AMOUNT})\b", work, re.I)
    if not m:
        m = re.search(rf"\bunder\s*[:=]\s*(?:{CURRENCY}\s*)?({AMOUNT})\b", work, re.I)
    if m:
        v = amount_value(m.group(1))
        if v is not None:
            options["price_max"] = v
            work = work[:m.start()] + " " + work[m.end():]
    m = re.search(rf"\b(?:(?:price|amount)\s+)?(?:over|above|more\s+than|min(?:price|amount)?\s*[:=]?)\s*[:=]?\s*(?:{CURRENCY}\s*)?({AMOUNT})\b", work, re.I)
    if m:
        v = amount_value(m.group(1))
        if v is not None:
            options["price_min"] = v
            work = work[:m.start()] + " " + work[m.end():]

    # Also accept explicit minimum/maximum syntax.
    for pattern, key in [
        (rf"\bminprice\s*[:=]\s*(?:{CURRENCY}\s*)?({AMOUNT})\b", "price_min"),
        (rf"\bmaxprice\s*[:=]\s*(?:{CURRENCY}\s*)?({AMOUNT})\b", "price_max"),
    ]:
        m = re.search(pattern, work, re.I)
        if m:
            options[key] = amount_value(m.group(1))
            work = work[:m.start()] + " " + work[m.end():]

    # Exact price: price:680, ¥680, £100, 500 rupees, 680 JPY.
    if options["price_min"] is None and options["price_max"] is None:
        m = re.search(rf"\b(?:price|amount)\s*[:=]?\s*(?:{CURRENCY}\s*)?({AMOUNT})\b", work, re.I)
        if m:
            value = amount_value(m.group(1))
            if value is not None:
                options["price_min"] = options["price_max"] = value
                work = work[:m.start()] + " " + work[m.end():]
        else:
            m = re.search(
                rf"(?<![\w:]){CURRENCY}\s*({AMOUNT})(?![\w:]|\.\d{{2}}:\d{{2}})", work, re.I
            )
            if m:
                value = amount_value(m.group(1))
                if value is not None:
                    options["price_min"] = options["price_max"] = value
                    work = work[:m.start()] + " " + work[m.end():]
            else:
                m = re.search(
                    rf"(?<![\w:])({AMOUNT})\s*{CURRENCY}\b(?![\w:]|\.\d{{2}}:\d{{2}})", work, re.I
                )
                if m:
                    value = amount_value(m.group(1))
                    if value is not None:
                        options["price_min"] = options["price_max"] = value
                        work = work[:m.start()] + " " + work[m.end():]


    normalized = normalize(work)
    words = [w for w in normalized.split() if w not in config.STOP_WORDS and w not in {"filter", "sort", "by"}]
    # Deduplicate repeated words without losing the relative term order.
    required = []
    for word in words:
        if word not in required:
            required.append(word)
    return options, required, normalized


def parse_query(query: str):
    options, terms, normalized = _extract_structured(query)
    return {"options": options, "terms": terms, "normalized": normalized}


def extract_price(text):
    """Parse only explicit currency amounts or price labels; never infer from K1700."""
    s = text or ""
    patterns = [
        rf"\bprice\s*[:=]\s*(?:{CURRENCY}\s*)?({AMOUNT})\b",
        rf"(?:[$₹€£¥])\s*({AMOUNT})\b",
        rf"\b({AMOUNT})\s*(?:USD|INR|EUR|GBP|JPY|rupees?|dollars?|yen|euros?|pounds?|sterling)\b",
    ]
    vals = []
    for pat in patterns:
        for m in re.finditer(pat, s, re.I):
            value = amount_value(m.group(1))
            if value is not None:
                vals.append(value)
    return min(vals) if vals else None


def extract_kingdoms(text):
    vals = [int(v) for v in re.findall(r"\bk\s*(\d{3,5})\b", text or "", re.I)]
    return vals


def _term_matches(term, normalized_body, body_tokens):
    if " " in term:
        return term in normalized_body
    if term in body_tokens:
        return True
    # Allow OCR/spacing variation for joined number-feature tokens like 5champ.
    if re.fullmatch(r"\d+", term):
        return term in body_tokens
    return False


def listing_price_band(item):
    """Return (low, high) using stored topic-first amount data, then explicit text."""
    low = item.get("amount_min")
    high = item.get("amount_max")
    if low is not None:
        return float(low), float(high) if high is not None else float("inf")
    value = extract_price(item.get("body", ""))
    return (value, value) if value is not None else (None, None)


def evaluate_listing(item, query, for_alert=False):
    parsed = parse_query(query)
    opts, terms = parsed["options"], parsed["terms"]
    body = normalize(f"{item.get('title', '')}\n{item.get('body', '')}")
    body_tokens = set(body.split())

    if opts.get("posted_within_hours") is not None:
        try:
            posted = datetime.fromisoformat(str(item.get("created_at", "")).replace("Z", "+00:00"))
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - posted.astimezone(timezone.utc)).total_seconds() / 3600
            if age_hours < 0 or age_hours > float(opts["posted_within_hours"]):
                return None
        except (ValueError, TypeError):
            return None

    if opts["price_min"] is not None or opts["price_max"] is not None:
        low, high = listing_price_band(item)
        if low is None:
            return None
        # A listing with a stated topic price band matches when the requested range
        # overlaps that band. The topic amount has already taken priority over the post.
        if opts["price_min"] is not None and high < opts["price_min"]:
            return None
        if opts["price_max"] is not None and low > opts["price_max"]:
            return None

    if opts["kingdom_min"] is not None or opts["kingdom_max"] is not None:
        kingdoms = extract_kingdoms(item.get("body", "") + " " + item.get("title", ""))
        if not kingdoms:
            return None
        lo = opts["kingdom_min"] if opts["kingdom_min"] is not None else -1
        hi = opts["kingdom_max"] if opts["kingdom_max"] is not None else 999999
        if not any(lo <= k <= hi for k in kingdoms):
            return None

    matched_terms = [t for t in terms if _term_matches(t, body, body_tokens)]
    ratio = len(matched_terms) / len(terms) if terms else 1.0
    if terms:
        strict_required = opts["strict"] or (for_alert and config.ALERTS_REQUIRE_ALL_KEYWORDS)
        if strict_required and len(matched_terms) != len(terms):
            return None
        if not strict_required and ratio < config.MIN_RANKED_MATCH_RATIO:
            return None
    # A small bonus for matching full multi-token query phrases.
    phrase_bonus = 0.10 if parsed["normalized"] and parsed["normalized"] in body else 0.0
    score = min(1.0, ratio + phrase_bonus)
    return {"score": score, "ratio": ratio, "matched_terms": matched_terms,
            "total_terms": len(terms), "options": opts}


def search_listings(query: str, limit=5):
    all_items = database.get_listings()
    results = []
    for item in all_items:
        evaluation = evaluate_listing(item, query)
        if evaluation is None:
            continue
        item.update(evaluation)
        results.append(item)

    sort = parse_query(query)["options"]["sort"]
    if sort == "cheapest":
        results.sort(key=lambda x: (listing_price_band(x)[0] is None,
                                    listing_price_band(x)[0] if listing_price_band(x)[0] is not None else float("inf"), x["created_at"]))
    elif sort == "expensive":
        results.sort(key=lambda x: (listing_price_band(x)[0] is None,
                                    -(listing_price_band(x)[0] or 0), x["created_at"]), reverse=False)
    elif sort == "newest":
        results.sort(key=lambda x: x["created_at"], reverse=True)
    elif sort == "highest":
        results.sort(key=lambda x: (extract_stat_score(x["body"]), x["created_at"]), reverse=True)
    else:
        results.sort(key=lambda x: (x["score"], x["created_at"]), reverse=True)
    return results[:limit]


def extract_stat_score(text):
    # A lightweight fallback for the generic “highest” sort; price and K-numbers are excluded.
    cleaned = re.sub(r"\bk\s*\d{3,5}\b", " ", text or "", flags=re.I)
    cleaned = re.sub(r"(?:[$₹€£¥])\s*\d[\d,]*(?:\.\d+)?", " ", cleaned)
    nums = [int(n.replace(",", "")) for n in re.findall(r"\b\d[\d,]*\b", cleaned)]
    return sum(nums) if nums else 0


def listing_matches_alert(item, query):
    return evaluate_listing(item, query, for_alert=True) is not None
