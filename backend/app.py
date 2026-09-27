from datetime import timedelta
import secrets

from flask import Flask, request, jsonify, send_from_directory, session
import os
import random
import json
import re
import sqlite3
import time

import auth
import card_review
import covers
import insights
import sections
import chat
import limits
import llm
import rewaa_import
import testlog

from models import (
    init_db,
    seed_books,
    list_books,
    get_book,
    update_book,
    add_feedback,
    get_profile_cards,
    get_connection,
    FEEDBACK_RATINGS,
)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # uploads (Rewaa export) capped at 8 MB
PRODUCTION = os.environ.get("LIBRARY_KEEPER_PRODUCTION") == "1"
# Address the server listens on (127.0.0.1 = this computer only; 0.0.0.0 = phones on the same Wi-Fi, trial mode)
BIND_HOST = os.environ.get("LIBRARY_KEEPER_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("LIBRARY_KEEPER_PORT", "8802"))
app.secret_key = auth.secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                  SESSION_COOKIE_SECURE=PRODUCTION, SESSION_COOKIE_NAME="lk_admin",
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=auth.SESSION_HOURS))
init_db()  # tables for accounts/audit/usage exist before the first request
llm.call_hook = limits.record_call

# ---------------------------------------------------------------------------
# Access control (approved 2026-09-27). Default deny: every /api/admin/* route
# needs a signed-in account; routes not listed for staff are manager-only.
# ---------------------------------------------------------------------------
PUBLIC_ADMIN = {"/api/admin/session", "/api/admin/login", "/api/admin/logout", "/api/admin/setup"}
STAFF_PREFIXES = ("/api/admin/catalog", "/api/admin/books/", "/api/admin/me")


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    user = auth.get_user(uid)
    if not user or not user["active"] or session.get("pw") != (user.get("password_changed_at") or ""):
        session.clear()  # disabled, deleted, or password changed elsewhere
        return None
    return user


def _needs_auth(path, method):
    if path.startswith("/api/admin/"):
        return path not in PUBLIC_ADMIN
    return path.startswith("/api/books/") and method == "PATCH"


@app.before_request
def guard():
    path, method = request.path, request.method
    if not _needs_auth(path, method):
        return None
    user = current_user()
    if user is None:
        return jsonify({"error": "login required"}), 401
    staff_ok = path.startswith(STAFF_PREFIXES) or (path.startswith("/api/books/") and method == "PATCH")
    if user["role"] != "manager" and not staff_ok:
        return jsonify({"error": "هذي الصفحة للمدير فقط"}), 403
    if method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("X-CSRF-Token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            return jsonify({"error": "انتهت صلاحية الصفحة، حدّثها وحاول مرة ثانية"}), 403
    request.environ["lk.user"] = user
    return None


def acting_user():
    return request.environ.get("lk.user")


def _start_session(user):
    session.clear()
    session.permanent = True
    session["uid"] = user["user_id"]
    session["pw"] = user.get("password_changed_at") or ""
    session["csrf"] = secrets.token_urlsafe(24)


def _session_payload(user):
    return {"user": {"id": user["user_id"], "username": user["username"], "name": user["display_name"],
                     "role": user["role"], "role_label": auth.ROLE_LABELS[user["role"]]},
            "csrf": session.get("csrf")}

ROOT = os.path.dirname(__file__)
FRONTEND_DIR = os.path.join(ROOT, "..", "frontend")
ADMIN_DIR = os.path.join(ROOT, "..", "admin")

MAX_RESULTS = 4
MIN_RESULTS = 3
SHORT_PAGES = 250
LONG_PAGES = 450


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/static/<path:filename>")
def frontend_static(filename):
    return send_from_directory(FRONTEND_DIR, filename)


@app.route("/admin")
def admin_page():
    return send_from_directory(ADMIN_DIR, "index.html")


@app.route("/api/books")
def api_books():
    # Public: only books a visitor may see (active and in stock). Staff use /api/admin/catalog.
    rows = list_books(only_active=True)
    books = [with_origin(dict(row)) for row in rows]
    return jsonify(books)


@app.route("/covers/<path:filename>")
def cover_file(filename):
    if covers.cover_path(filename) is None:
        return jsonify({"error": "not found"}), 404
    resp = send_from_directory(covers.COVERS_DIR, filename)
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


# ---------------------------------------------------------------------------
# Phase 1 recommendation: transparent rule-based matching on existing catalog
# fields only (genre, page count, title, author). No reading-profile data
# exists yet, so reasons never claim mood, difficulty, or ending tone.
# ---------------------------------------------------------------------------

from keyword_prefs import normalize, strip_al  # noqa: E402  (shared with the keyword interpreter)
import keyword_prefs  # noqa: E402


STOPWORDS = {
    normalize(w)
    for w in (
        "ابغى ابغي ابي أبي ودي ودّي نفسي أبحث ابحث عن كتاب كتب كتابا شيء شي أي اي "
        "مع في من على إلى الى لي له لها لشخص شخص يحب تحب و او أو ما مو لا يكون تكون "
        "عندي عادة القراءة قراءة اقرأ أقرأ ابدأ أبدأ بداية سهلة سهل خفيف خفيفة قصير قصيرة "
        "طويل طويلة ممتع ممتعة القهوة قهوة هدية عربي عربية اليوم"
    ).split()
}

# ---------------------------------------------------------------------------
# Phase 2: reading-profile integration. Books with enriched cards
# (docs/book-profile-schema.md) add profile-driven matching on top of the
# Phase 1 rules. Only High-tier (>=0.85) or human-verified values may be
# quoted in reasons; Medium (0.65-0.84) boosts ranking only; unknown never
# blocks a book, it only removes that attribute from the reason.
# ---------------------------------------------------------------------------

HIGH_TIER = 0.85
MEDIUM_TIER = 0.65

# Age suitability (approved 2026-09-25). When the visitor shops for a child
# or a teen, only books whose card shows a suitable age (Medium tier or better)
# are eligible, and flagged content excludes a book. Unknown age never passes
# for children/teens: better fewer books than an unsuitable one.
AUDIENCE_KEYWORDS = {
    "children": ["طفل", "طفله", "اطفال", "ولدي الصغير", "بنتي الصغيره", "ابني الصغير", "صغير عمره",
                 "صغيره عمرها", "ابتدائي", "روضه", "قصص اطفال", "لطفلي", "لطفلتي", "child", "children", "kid", "kids", "my little", "little son", "little daughter", "toddler",
                 "kindergarten", "elementary", "primary school", "bedtime story", "picture book"],
    "young_adult": ["مراهق", "مراهقه", "مراهقين", "يافع", "يافعين", "ثانوي", "ثانويه", "المتوسطه",
                    "في المتوسط", "مراهقتي", "teen", "teens", "teenager", "young adult", "high school", "middle school"],
}
AGE_OK = {"children": ("children", "all_ages"), "young_adult": ("young_adult", "all_ages", "children")}
AGE_BLOCKING = {"children": ("violence", "sexual_content", "strong_language", "horror", "substance_use",
                             "self_harm", "religious_controversy", "political"),
                "young_adult": ("sexual_content", "strong_language", "substance_use", "self_harm",
                                "religious_controversy")}
AGE_LABELS = {"children": "للأطفال", "young_adult": "لليافعين", "adult": "للكبار", "all_ages": "لكل الأعمار"}
SENSITIVE_LABELS = {"violence": "عنف", "sexual_content": "مشاهد جريئة", "strong_language": "ألفاظ قاسية",
                    "horror": "رعب", "substance_use": "مخدرات أو كحول", "self_harm": "إيذاء النفس",
                    "religious_controversy": "جدل ديني", "political": "مضمون سياسي", "none": "لا تنبيهات"}
_AGE_NUM = re.compile(r"(عمره|عمرها|عمر|سنه|سنوات|سنين)\s*(\d{1,2})|(\d{1,2})\s*(سنه|سنوات|سنين)")
_AGE_NUM_EN = re.compile(r"(?:age|aged)\s*(\d{1,2})|(?:for|my)?\s*(\d{1,2})\s*(?:years?|yrs?)\s*old")


def detect_audience(normalized_query):
    for m in _AGE_NUM.finditer(normalized_query.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))):
        age = int(m.group(2) or m.group(3))
        if 2 <= age <= 12:
            return "children"
        if 13 <= age <= 17:
            return "young_adult"
    for m in _AGE_NUM_EN.finditer(normalized_query):
        age = int(next(group for group in m.groups() if group and group.isdigit()))
        if 2 <= age <= 12:
            return "children"
        if 13 <= age <= 17:
            return "young_adult"
    words = normalized_query.split()
    for aud in ("children", "young_adult"):
        if any(keyword_prefs.find_phrase(words, h) >= 0 for h in AUDIENCE_KEYWORDS[aud]):
            return aud
    return None


def _medium_or_better(card, attr, value):
    entry = card["confidence"].get((attr, value)) if card else None
    return bool(entry and (entry[1] or entry[0] >= MEDIUM_TIER))


def age_allowed(card, audience, avoid=()):
    """Eligibility of a book for the stated reader.

    audience children/young_adult: needs a suitable age value on the card and
    no blocking content flag. Anyone else: children-only books are left out
    (they belong to the children's shelf), everything else stays. Content the
    visitor asked to avoid excludes a book whenever the card flags it."""
    for c in avoid:
        if _medium_or_better(card, "sensitive_content", c):
            return False
    if audience in AGE_OK:
        if not any(_medium_or_better(card, "age_suitability", a) for a in AGE_OK[audience]):
            return False
        if audience == "young_adult" and _medium_or_better(card, "age_suitability", "adult"):
            return False
        return not any(_medium_or_better(card, "sensitive_content", c) for c in AGE_BLOCKING[audience])
    if card and _medium_or_better(card, "age_suitability", "children") \
            and not _medium_or_better(card, "age_suitability", "all_ages"):
        return False
    return True


MOOD_LABELS = {
    "comforting": "مريح", "warm": "دافئ", "funny": "مرح", "suspenseful": "مشوق",
    "mysterious": "غامض", "inspiring": "ملهم", "contemplative": "تأملي",
    "romantic": "رومانسي", "tense": "متوتر", "dark": "مظلم", "sad": "حزين",
    "uplifting": "مبتهج", "unknown": "غير معروف",
}
# Feminine forms used after the plural word أجواؤه (أجواء حزينة، not حزين)
MOOD_LABELS_FEM = {
    "comforting": "مريحة", "warm": "دافئة", "funny": "مرحة", "suspenseful": "مشوقة",
    "mysterious": "غامضة", "inspiring": "ملهمة", "contemplative": "تأملية",
    "romantic": "رومانسية", "tense": "متوترة", "dark": "مظلمة", "sad": "حزينة",
    "uplifting": "مبهجة",
}
# Short forms after the word إيقاعه (avoids "إيقاعه سريع الإيقاع")
PACE_SHORT = {"fast": "سريع", "medium": "متوسط", "slow": "بطيء", "contemplative": "تأملي", "mixed": "متفاوت"}
PACE_LABELS = {
    "fast": "سريع الإيقاع", "medium": "متوسط الإيقاع", "slow": "بطيء الإيقاع",
    "contemplative": "تأملي", "mixed": "متفاوت الإيقاع", "unknown": "غير معروف",
}
WEIGHT_LABELS = {
    "light": "خفيف", "medium": "متوسط", "heavy": "ثقيل", "intense": "شديد",
    "unknown": "غير معروف",
}
# Feminine forms used when the label follows the feminine word قراءة
WEIGHT_LABELS_FEM = {
    "light": "خفيفة", "medium": "متوسطة", "heavy": "ثقيلة", "intense": "شديدة",
    "unknown": "غير معروفة",
}
USE_LABELS = {
    "habit_start": "بداية عادة القراءة", "light_reading": "قراءة خفيفة",
    "coffee": "مع القهوة", "travel": "أثناء السفر", "gift": "هدية",
    "book_club": "نادي قراءة", "discussion": "للنقاش",
    "practical_learning": "تعلّم عملي", "quick_read": "قراءة سريعة",
    "slump_recovery": "الخروج من فتور القراءة", "unknown": "غير معروف",
}
LEVEL_LABELS = {
    "beginner": "مناسب للمبتدئين", "regular": "للقارئ المعتاد",
    "advanced": "للقارئ المتقدم", "multiple": "مناسب لمستويات متعددة",
    "unknown": "غير معروف",
}

GENRE_LABELS_EN = {
    "رواية": "novel", "روايات": "novels", "أدب": "literature", "قصص قصيرة": "short stories",
    "شعر": "poetry", "سيرة": "biography", "تاريخ": "history", "فكر": "thought",
    "علم نفس": "psychology", "تطوير ذاتي": "self-development", "علوم": "science",
    "مسرحية": "play", "أدب رحلات": "travel writing", "ديني": "religion", "دين": "religion",
    "سيرة ذاتية": "biography", "سيرة نبوية": "the Prophet's biography",
    "أطفال": "children's books", "يافعين": "young adult", "أعمال": "business",
}
MOOD_LABELS_EN = {
    "comforting": "comforting", "warm": "warm", "funny": "funny", "suspenseful": "suspenseful",
    "mysterious": "mysterious", "inspiring": "inspiring", "contemplative": "contemplative",
    "romantic": "romantic", "tense": "tense", "dark": "dark", "sad": "sad", "uplifting": "uplifting",
}
PACE_LABELS_EN = {"fast": "fast", "medium": "steady", "slow": "slow", "contemplative": "contemplative", "mixed": "varied"}
WEIGHT_LABELS_EN = {"light": "emotionally light", "medium": "moderate in emotional weight", "heavy": "emotionally heavy", "intense": "emotionally intense"}
USE_LABELS_EN = {
    "habit_start": "starting a reading habit", "light_reading": "light reading", "coffee": "reading with coffee",
    "travel": "travel", "gift": "a gift", "book_club": "a book club", "discussion": "discussion",
    "practical_learning": "practical learning", "quick_read": "a quick read", "slump_recovery": "getting out of a reading slump",
}
LEVEL_LABELS_EN = {"beginner": "beginner-friendly", "regular": "suited to a regular reader", "advanced": "suited to an advanced reader", "multiple": "suited to different reading levels"}
AGE_LABELS_EN = {"children": "for children", "young_adult": "for young adults", "adult": "for adults", "all_ages": "for all ages"}
SENSITIVE_LABELS_EN = {
    "violence": "violence", "sexual_content": "sexual content", "strong_language": "strong language",
    "horror": "horror", "substance_use": "substance use", "self_harm": "self-harm",
    "religious_controversy": "religious controversy", "political": "political content",
}


def score_profile_card(book_id, card, pprefs):
    """Score a book against extracted profile preferences.

    High-tier (>=0.85) or verified matches add score AND a quotable reason;
    medium-tier matches add score only (schema rule: never a guarantee).
    """
    score = 0
    reasons = []

    def tier_of(attr, value):
        entry = card["confidence"].get((attr, value))
        if entry is None:
            return None
        conf, verified = entry
        if verified or conf >= HIGH_TIER:
            return "high"
        if conf >= MEDIUM_TIER:
            return "medium"
        return None

    for mood in pprefs["moods"]:
        tier = tier_of("moods", mood)
        if tier == "high":
            score += 2
            reasons.append(f"أجواؤه {MOOD_LABELS_FEM.get(mood, MOOD_LABELS.get(mood, mood))} كما طلبت")
        elif tier == "medium":
            score += 1
    for pace in pprefs["pace"]:
        tier = tier_of("pace", pace)
        if tier == "high":
            score += 2
            reasons.append(f"إيقاعه {PACE_SHORT.get(pace, PACE_LABELS.get(pace, pace))}")
        elif tier == "medium":
            score += 1
    for weight in pprefs["weight"]:
        tier = tier_of("emotional_weight", weight)
        if tier == "high":
            score += 2
            reasons.append(f"قراءة {WEIGHT_LABELS_FEM.get(weight, weight)} عاطفيًا")
        elif tier == "medium":
            score += 1
    for use in pprefs["use_cases"]:
        tier = tier_of("use_cases", use)
        if tier == "high":
            score += 2
            reasons.append(f"مناسب لـ{USE_LABELS.get(use, use)}")
        elif tier == "medium":
            score += 1
    aud = pprefs.get("audience")
    if aud in AGE_OK:
        tier = tier_of("age_suitability", aud)
        if tier == "high":
            score += 3
            reasons.append("مناسب " + AGE_LABELS[aud])
        elif tier or tier_of("age_suitability", "all_ages"):
            score += 1
    for level in pprefs["reader_level"]:
        tier = tier_of("reader_level", level)
        if tier == "high":
            score += 2
            reasons.append(LEVEL_LABELS.get(level, level))
        elif tier == "medium":
            score += 1
    return score, reasons


def token_in(token, words):
    """Match a query word, allowing one attached prefix letter (لشكسبير → شكسبير)."""
    if token in words:
        return True
    return len(token) > 3 and token[0] in "لبوكف" and strip_al(token[1:]) in words


def fmt_num(n):
    return str(n).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))


def score_book(book, prefs):
    score = 0
    reasons = []
    if prefs["genres"] and book.get("genre") in prefs["genres"]:
        score += 2
        reasons.append(f"من تصنيف «{book['genre']}» كما طلبت")
    pages = book.get("page_count")
    if pages and prefs["length"] == "short" and pages <= SHORT_PAGES:
        score += 2
        reasons.append(f"قصير نسبيًا، حوالي {fmt_num(pages)} صفحة")
    elif pages and prefs["length"] == "long" and pages >= LONG_PAGES:
        score += 2
        reasons.append(f"عمل طويل، حوالي {fmt_num(pages)} صفحة")
    author_words = {strip_al(w) for w in normalize(book.get("author")).split()}
    title_words = {strip_al(w) for w in normalize(book.get("title")).split()}
    author_hits = sum(1 for t in prefs["tokens"] if token_in(t, author_words))
    if author_hits:
        # each extra word of the author's name counts, so «نجيب محفوظ» outranks another «نجيب»
        score += 2 + author_hits
        reasons.append(f"من أعمال {book['author']}")
    if any(token_in(t, title_words) for t in prefs["tokens"]):
        score += 2
        reasons.append("عنوانه يطابق كلمات من وصفك")
    return score, reasons


def ai_tokens(ai):
    """Author/title words named by the visitor, normalized like catalog text."""
    words = []
    for phrase in ai["authors"] + ai["title_keywords"]:
        for w in normalize(phrase).split():
            if len(w) > 1 and w not in STOPWORDS:
                words.append(strip_al(w))
    return words


def excluded_by_card(card, exclusions):
    """True when the card shows (Medium tier or better, non-conflict) a value the
    visitor explicitly does not want. Unknown values never exclude a book."""
    if not card:
        return False
    for attr, values in (("moods", exclusions["moods"]), ("emotional_weight", exclusions["weight"])):
        for v in values:
            entry = card["confidence"].get((attr, v))
            if entry and (entry[1] or entry[0] >= MEDIUM_TIER):
                return True
    return False


def prefs_from_ai(ai, normalized):
    """Map cleaned AI preferences to the internal ranking structures."""
    prefs = {"genres": set(ai["genres"]), "length": ai["length"],
             "tokens": ai_tokens(ai), "normalized": normalized}
    pprefs = {"moods": set(ai["moods_include"]), "pace": set(ai["pace"]),
              "weight": set(ai["weight_include"]), "use_cases": set(ai["use_cases"]),
              "reader_level": set(ai["reader_level"]),
              "audience": ai.get("audience") or detect_audience(normalized),
              "avoid": set(ai.get("avoid_content") or [])}
    exclusions = {"moods": set(ai["moods_exclude"]), "weight": set(ai["weight_exclude"])}
    return prefs, pprefs, exclusions, list(ai["unsupported_requests"])


def score_books(books, prefs, pprefs, cards):
    """Phase 1 rules + profile matches; returns [(score, reasons, book)] best first."""
    has_profile_prefs = any((pprefs["moods"], pprefs["pace"], pprefs["weight"],
                             pprefs["use_cases"], pprefs["reader_level"], pprefs.get("audience")))
    books = list(books)
    random.shuffle(books)  # vary order among equally scored books
    scored = []
    for book in books:
        score, reasons = score_book(book, prefs)
        card = cards.get(book["book_id"])
        if card and has_profile_prefs:
            profile_score, profile_reasons = score_profile_card(book["book_id"], card, pprefs)
            score += profile_score
            reasons.extend(profile_reasons)
        scored.append((score, reasons, book))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def reason_text(reasons, language="ar"):
    reasons = list(dict.fromkeys(reasons))[:3]  # the strongest reasons only, no repeats
    if reasons:
        if language == "en":
            return "Matches what you asked for: " + "; ".join(localize_reason(r) for r in reasons) + "."
        return "اخترناه لأنه " + "، و".join(reasons) + "."
    # Matched only through Medium-tier profile values: ranking signal,
    # never quoted as a guarantee (schema §4/§7).
    return ("It may be close to what you described, based on early reader impressions that have not been reviewed yet."
            if language == "en" else "قد يكون قريبًا من وصفك حسب انطباعات قرّاء أولية لم تُراجع بعد.")


_WESTERN = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _code(label, *tables):
    """Arabic label -> internal code, looked up in the label tables that produced it."""
    for table in tables:
        for code, text in table.items():
            if text == label:
                return code
    return None


def localize_reason(reason):
    """English wording for a matching reason / constraint label. Each Arabic label is mapped back
    to its code and then to the English label table, so no Arabic word is left in English text."""
    r = str(reason).translate(_WESTERN)
    if r in GENRE_LABELS_EN:
        return GENRE_LABELS_EN[r]
    if "، " in r and all(x in GENRE_LABELS_EN for x in r.split("، ")):   # several named genres
        return " or ".join(GENRE_LABELS_EN[x] for x in r.split("، "))
    if r == "قصير":
        return "short"
    if r.startswith("من تصنيف «"):
        genre = r.split("»", 1)[0].split("«", 1)[-1]
        return GENRE_LABELS_EN.get(genre, "the category you named")
    if r.startswith("قصير نسبيًا، حوالي "):
        return "short, about " + r.removeprefix("قصير نسبيًا، حوالي ").replace(" صفحة", " pages")
    if r.startswith("عمل طويل، حوالي "):
        return "a longer read, about " + r.removeprefix("عمل طويل، حوالي ").replace(" صفحة", " pages")
    if r.startswith("من أعمال "):
        return "by " + r.removeprefix("من أعمال ")
    if r == "عنوانه يطابق كلمات من وصفك":
        return "its title matches your words"
    if r.startswith("بدون أجواء "):
        codes = [_code(x, MOOD_LABELS_FEM, MOOD_LABELS) for x in r.removeprefix("بدون أجواء ").split("، ")]
        return "no " + " or ".join(MOOD_LABELS_EN.get(c, "unwanted") for c in codes) + " mood"
    if r.startswith("مو "):
        codes = [_code(x, WEIGHT_LABELS_FEM, WEIGHT_LABELS) for x in r.removeprefix("مو ").split("، ")]
        return "not " + " or ".join(WEIGHT_LABELS_EN.get(c, "heavy") for c in codes)
    if r.startswith("بدون "):
        codes = [_code(x, SENSITIVE_LABELS) for x in r.removeprefix("بدون ").split("، ")]
        return "without " + " or ".join(SENSITIVE_LABELS_EN.get(c, "that content") for c in codes)
    if r.startswith("أجواؤه "):
        code = _code(r.removeprefix("أجواؤه ").replace(" كما طلبت", ""), MOOD_LABELS_FEM, MOOD_LABELS)
        return f"{MOOD_LABELS_EN.get(code, 'the mood you wanted')} mood"
    if r.startswith("إيقاعه "):
        code = _code(r.removeprefix("إيقاعه "), PACE_SHORT, PACE_LABELS)
        return f"{PACE_LABELS_EN.get(code, 'the pace you wanted')} pace"
    if r.startswith("قراءة ") and r.endswith(" عاطفيًا"):
        code = _code(r.removeprefix("قراءة ").removesuffix(" عاطفيًا"), WEIGHT_LABELS_FEM, WEIGHT_LABELS)
        return WEIGHT_LABELS_EN.get(code, "the emotional weight you wanted")
    if r.startswith("مناسب لـ"):
        code = _code(r.removeprefix("مناسب لـ"), USE_LABELS)
        return "good for " + USE_LABELS_EN.get(code, "what you have in mind")
    if r.startswith("مناسب "):
        code = _code(r.removeprefix("مناسب "), AGE_LABELS)
        return "suitable " + AGE_LABELS_EN.get(code, "for the reader's age")
    code = _code(r, LEVEL_LABELS)
    if code:
        return LEVEL_LABELS_EN.get(code, "suited to your reading level")
    return r


# ---------------------------------------------------------------------------
# Constraints vs preferences (improvement plan item 14). Constraints are never broken
# to fill the list: in stock + active (done by callers), suitable age, content the visitor
# wants to avoid, moods/weights the visitor excluded, the genre they named, and "short"
# when they asked for it. Everything else (mood, pace, use case, level) only ranks.
# ---------------------------------------------------------------------------

GENRE_WITH_AUDIENCE = {"children": "أطفال", "young_adult": "يافعين"}


def is_long(book, card):
    pages = book.get("page_count")
    if pages:
        return pages > SHORT_PAGES
    return bool(card) and card.get("perceived_length") in ("long", "very_long")


def constrain(books, prefs, pprefs, exclusions, cards):
    """-> (books that satisfy every stated constraint, Arabic labels of those constraints)."""
    labels = []
    audience = pprefs.get("audience")
    avoid = pprefs.get("avoid") or ()
    genres = set(prefs.get("genres") or ())
    if genres and audience in GENRE_WITH_AUDIENCE:
        # "قصة لبنتي" = a children's story; "رواية لولدي المراهق" = a young-adult novel
        genres.add(GENRE_WITH_AUDIENCE[audience])
    out = []
    for b in books:
        card = cards.get(b["book_id"])
        if not age_allowed(card, audience, avoid):
            continue
        if (exclusions["moods"] or exclusions["weight"]) and excluded_by_card(card, exclusions):
            continue
        if genres and b.get("genre") not in genres:
            continue
        if prefs.get("length") == "short" and is_long(b, card):
            continue
        out.append(b)
    if prefs.get("genres"):
        labels.append("، ".join(prefs["genres"]))
    if prefs.get("length") == "short":
        labels.append("قصير")
    if audience in AGE_OK:
        labels.append("مناسب " + AGE_LABELS[audience])
    if exclusions["moods"]:
        labels.append("بدون أجواء " + "، ".join(MOOD_LABELS_FEM.get(m, m) for m in exclusions["moods"]))
    if exclusions["weight"]:
        labels.append("مو " + "، ".join(WEIGHT_LABELS_FEM.get(w, w) for w in exclusions["weight"]))
    if avoid:
        labels.append("بدون " + "، ".join(SENSITIVE_LABELS.get(a, a) for a in avoid))
    return out, labels


def filler_reason(labels, language="ar"):
    if labels:
        if language == "en":
            return "It fits part of your request (" + ", ".join(localize_reason(x) for x in labels) + "); we do not have enough reader data to confirm the rest."
        return "يلتزم بطلبك (" + "، ".join(labels) + ")، لكن ما عندنا بيانات كافية تؤكد باقي وصفك."
    return FILLER_REASON_EN if language == "en" else FILLER_REASON


def local_understand(messages):
    """Keyword interpretation in the same shape as the AI preferences."""
    return keyword_prefs.understand(messages, detect_audience)


@app.route("/api/recommend", methods=["POST"])
def recommend():
    data = request.get_json(silent=True) or {}
    if not limits.allow_request(request.remote_addr):
        return jsonify({"results": [], "message": RATE_LIMITED}), 429
    query = str(data.get("query", "")).strip()[:500]
    exclude = {int(i) for i in data.get("exclude", []) if str(i).isdigit()}

    books = [dict(row) for row in list_books(only_active=True)]
    books = [b for b in books if b["book_id"] not in exclude]
    if not books:
        message = (
            "عرضنا لك كل الخيارات المتوفرة حاليًا. جرّب وصفًا مختلفًا."
            if exclude
            else "لا توجد كتب متوفرة حاليًا."
        )
        return jsonify({"results": [], "message": message})

    ai = llm.understand(query) if limits.budget_left(1) else None  # None -> keyword fallback
    understood = ai if ai is not None else local_understand([query])
    prefs, pprefs, exclusions, unsupported = prefs_from_ai(understood, normalize(query))
    cards = get_profile_cards()
    books, labels = constrain(books, prefs, pprefs, exclusions, cards)
    if not books:
        return jsonify({
            "results": [],
            "message": ("ما عندنا الآن كتاب متوفر يطابق: " + "، ".join(labels) + ". جرّب تخفف أحد الشروط.")
            if labels else "لا توجد كتب متوفرة حاليًا.",
            "understanding": "ai" if ai is not None else "keywords",
        })
    scored = score_books(books, prefs, pprefs, cards)

    matched = [(r, b) for s, r, b in scored if s > 0]
    others = [b for s, r, b in scored if s == 0]
    notes = []

    results = []
    for reasons, book in matched[:MAX_RESULTS]:
        book["reason"] = reason_text(reasons)
        book["match"] = "direct"
        results.append(book)

    if not results:
        if exclude:
            return jsonify({
                "results": [],
                "message": "عرضنا لك كل الخيارات القريبة من وصفك. جرّب وصفًا مختلفًا أو ابدأ بحثًا جديدًا.",
            })
        if ai is None and not keyword_prefs.understood_anything(understood):
            notes.append("ما قدرت أفهم طلبك تمامًا، فهذي خيارات متنوعة متوفرة الآن. جرّب تذكر النوع أو المزاج اللي تبغاه.")
        elif labels:
            notes.append("ما لقينا تطابقًا أدق مع وصفك، فهذي كتب متوفرة تلتزم بطلبك (" + "، ".join(labels) + ").")
        else:
            notes.append("ما لقينا تطابقًا واضحًا مع وصفك في بياناتنا الحالية، فهذه خيارات متنوعة متوفرة الآن.")
    elif len(results) < MIN_RESULTS:
        notes.append("الخيارات المطابقة قليلة، فأضفنا خيارات أخرى متوفرة" + (" تلتزم بطلبك." if labels else " للتنويع."))

    while len(results) < MIN_RESULTS and others:
        book = others.pop(0)
        book["reason"] = filler_reason(labels)
        book["match"] = "filler"
        results.append(book)
    if not matched:
        while len(results) < MAX_RESULTS and others:
            book = others.pop(0)
            book["reason"] = filler_reason(labels)
            book["match"] = "filler"
            results.append(book)

    if pprefs.get("audience") in AGE_OK and len(matched) < MIN_RESULTS:
        notes.append("اخترنا فقط الكتب اللي بياناتها تقول إنها مناسبة " + AGE_LABELS[pprefs["audience"]] + ".")
    if unsupported:
        notes.append("ما عندنا بعد بيانات تساعدنا في: " + "، ".join(unsupported) + "، فالترشيح مبني على باقي وصفك.")
    elif ai is None and "عربي" in prefs["normalized"]:
        notes.append("تنبيه: بياناتنا الحالية لا تميّز بعد بين الأعمال العربية والمترجمة.")

    insights.record_shown(b["book_id"] for b in results)
    return jsonify({
        "results": [with_origin(b) for b in results],
        "message": " ".join(notes) or None,
        "understanding": "ai" if ai is not None else "keywords",
    })


@app.route("/api/choice", methods=["POST"])
def choose_book():
    """A visitor tapped «أبغى هذا الكتاب». Recorded anonymously for the statistics page.
    Stock is NOT changed: quantities are managed only by staff or the Rewaa file."""
    data = request.get_json(silent=True) or {}
    if not limits.allow_request(request.remote_addr):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    book_id = data.get("book_id")
    if not str(book_id).isdigit():
        return jsonify({"ok": False, "error": "unknown book"}), 400
    book = get_book(int(book_id))
    if book is None or not is_available(dict(book)):
        return jsonify({"ok": False, "error": "unknown book"}), 400
    choice_id = insights.record_choice(int(book_id), data.get("language"), data.get("match"))
    testlog.record("choice", {"conversation_id": testlog.clean_id(data.get("conversation_id")),
                              "book_id": int(book_id), "choice_id": choice_id})
    return jsonify({"ok": True, "choice_id": choice_id}), 201


def _known_genres():
    conn = get_connection()
    genres = {r[0] for r in conn.execute("SELECT DISTINCT genre FROM books WHERE genre IS NOT NULL AND genre <> ''")}
    conn.close()
    for s_ in sections.all_sections():
        genres.update(s_["genres"])
    return genres


def _sections_payload():
    conn = get_connection()
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT genre, COUNT(*) FROM books WHERE active = 1 AND quantity > 0 GROUP BY genre")}
    rows = [dict(r) for r in conn.execute(
        "SELECT genre, shelf_location FROM books WHERE active = 1 AND quantity > 0")]
    conn.close()
    no_location = sum(1 for b in rows if sections.location(b)[0] is None)
    typed = sum(1 for b in rows if (b["shelf_location"] or "").strip())
    genres = sorted(_known_genres(), key=lambda g: (-counts.get(g, 0), g))
    return {"sections": sections.all_sections(), "genres": [{"genre": g, "books": counts.get(g, 0)} for g in genres],
            "available_books": len(rows), "without_location": no_location, "typed_locations": typed}


@app.route("/api/admin/sections", methods=["GET"])
def admin_sections():
    return jsonify(_sections_payload())


@app.route("/api/admin/sections", methods=["POST"])
def admin_sections_save():
    data = request.get_json(silent=True) or {}
    try:
        saved = sections.save_sections(data.get("sections"), _known_genres())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    auth.audit(acting_user(), "sections.set", None,
               {"sections": [{"name": x["name"], "active": x["active"], "genres": x["genres"]} for x in saved]})
    return jsonify(_sections_payload())


def lan_address():
    """This computer's address on the local network (no packet is sent: UDP connect only picks a route)."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        ip = probe.getsockname()[0]
    except OSError:
        ip = None
    finally:
        probe.close()
    return None if not ip or ip.startswith("127.") else ip


@app.route("/api/admin/trial")
def admin_trial():
    """Where staff phones should open the visitor page (the QR code in الإعدادات)."""
    public = os.environ.get("LIBRARY_KEEPER_PUBLIC_URL", "").strip()
    if public:
        return jsonify({"url": public.rstrip("/") + "/", "mode": "public", "reachable": True})
    ip = lan_address()
    open_to_network = BIND_HOST not in ("127.0.0.1", "localhost", "::1")
    return jsonify({"url": f"http://{ip}:{BIND_PORT}/" if ip else None, "mode": "network" if open_to_network else "local",
                    "reachable": bool(ip and open_to_network), "test_mode": testlog.enabled()})


@app.route("/api/admin/stats")
def admin_stats():
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    if days not in (7, 30, 90, 365, 0):
        days = 30
    data = insights.summary(days)
    for key in ("top_books", "shown_not_chosen", "low_rated"):
        for b in data[key]:
            if b.get("cover_url") and not covers.has_local_cover(b):
                b["cover_url"] = None
    return jsonify(data)


@app.route("/api/feedback", methods=["POST"])
def feedback():
    data = request.get_json(silent=True) or {}
    rating = data.get("rating")
    book_id = data.get("book_id")
    if rating not in FEEDBACK_RATINGS:
        return jsonify({"ok": False, "error": "invalid rating"}), 400
    if data.get("choice_id") is not None:
        # rating of a choice made with «أبغى هذا الكتاب»: one rating per choice
        if not str(data["choice_id"]).isdigit():
            return jsonify({"ok": False, "error": "unknown choice"}), 400
        status = insights.choice_status(int(data["choice_id"]))
        if status is None:
            return jsonify({"ok": False, "error": "unknown choice"}), 400
        if status[1]:
            return jsonify({"ok": False, "error": "already rated"}), 409
        insights.add_rating(int(data["choice_id"]), status[0], rating)
        testlog.record("feedback", {"conversation_id": testlog.clean_id(data.get("conversation_id")),
                                    "rating": rating, "book_id": status[0], "choice_id": int(data["choice_id"])})
        return jsonify({"ok": True}), 201
    if book_id is not None:
        if not str(book_id).isdigit() or get_book(int(book_id)) is None:
            return jsonify({"ok": False, "error": "unknown book"}), 400
        book_id = int(book_id)
    add_feedback(book_id, rating)
    testlog.record("feedback", {"conversation_id": testlog.clean_id(data.get("conversation_id")),
                                "rating": rating, "book_id": book_id})
    return jsonify({"ok": True}), 201


LANG_NAMES = {"ar": "العربية", "en": "الإنجليزية", "fr": "الفرنسية", "es": "الإسبانية", "de": "الألمانية",
              "ru": "الروسية", "it": "الإيطالية", "pt": "البرتغالية", "ja": "اليابانية", "zh": "الصينية",
              "ko": "الكورية", "tr": "التركية", "fa": "الفارسية", "ur": "الأردية", "hi": "الهندية",
              "sv": "السويدية", "no": "النرويجية", "da": "الدنماركية", "nl": "الهولندية", "pl": "البولندية",
              "cs": "التشيكية", "hu": "المجرية", "el": "اليونانية", "grc": "اليونانية القديمة", "he": "العبرية",
              "la": "اللاتينية", "bs": "البوسنية",
              "other": "لغة أخرى"}


LANG_NAMES_EN = {"ar": "Arabic", "en": "English", "fr": "French", "es": "Spanish", "de": "German",
                 "ru": "Russian", "it": "Italian", "pt": "Portuguese", "ja": "Japanese", "zh": "Chinese",
                 "ko": "Korean", "tr": "Turkish", "fa": "Persian", "ur": "Urdu", "hi": "Hindi",
                 "sv": "Swedish", "no": "Norwegian", "da": "Danish", "nl": "Dutch", "pl": "Polish",
                 "cs": "Czech", "hu": "Hungarian", "el": "Greek", "grc": "Ancient Greek", "he": "Hebrew",
                 "la": "Latin", "bs": "Bosnian", "other": "another language"}


def origin_label(book, language="ar"):
    """Original / translated / other-language label, without guessing unknown origins."""
    edition = (book.get("language") or "ar").strip() or "ar"
    orig = (book.get("original_language") or "").strip()
    if language == "en":
        if edition != "ar":
            return f"A {LANG_NAMES_EN.get(edition, edition)}-language book (not translated into Arabic)"
        if not orig:
            return None
        if orig == "ar":
            return "Originally written in Arabic"
        label = f"Translated from {LANG_NAMES_EN.get(orig, orig)}"
        translator = (book.get("translator") or "").strip()
        return label + (f", translated by {translator}" if translator else "")
    if edition != "ar":
        return f"كتاب باللغة {LANG_NAMES.get(edition, edition)} (غير مترجم للعربية)"
    if not orig:
        return None
    if orig == "ar":
        return "عمل عربي أصلي"
    label = f"مترجم عن {LANG_NAMES.get(orig, orig)}"
    translator = (book.get("translator") or "").strip()
    return label + (f"، ترجمة {translator}" if translator else "")


def with_origin(book, language="ar"):
    book["origin"] = origin_label(book, language)
    if book.get("cover_url") and not covers.has_local_cover(book):
        book["cover_url"] = None  # never send a link to a missing file
    book["location"], book["location_kind"] = sections.location(book)  # never invented
    return book


def _validate_book_fields(data):
    clean = {}
    try:
        for key in ("language", "original_language"):
            if key in data:
                v = str(data[key] or "").strip().lower()
                if v and v not in LANG_NAMES:
                    raise ValueError
                clean[key] = v or (None if key == "original_language" else "ar")
        if "translator" in data:
            clean["translator"] = str(data["translator"] or "").strip()[:120] or None
        if "price" in data:
            clean["price"] = float(data["price"])
            if clean["price"] < 0:
                raise ValueError
        if "quantity" in data:
            q = float(data["quantity"])
            if q < 0 or q != int(q):
                raise ValueError
            clean["quantity"] = int(q)
        if "active" in data:
            if int(data["active"]) not in (0, 1):
                raise ValueError
            clean["active"] = int(data["active"])
        if "shelf_location" in data:
            clean["shelf_location"] = str(data["shelf_location"]).strip()[:300]
        if "summary" in data:
            text = re.sub(r"\s+", " ", str(data["summary"] or "")).strip()
            if len(text) > 700:
                raise ValueError
            clean["summary"] = text or None
            clean["summary_source"] = "staff" if text else None
            clean["summary_updated_at"] = auth._now()
    except (TypeError, ValueError):
        return None
    return clean


@app.route("/api/books/<int:book_id>", methods=["PATCH"])
def patch_book(book_id):
    data = request.get_json(silent=True) or {}
    if get_book(book_id) is None:
        return jsonify({"ok": False, "error": "book not found"}), 404
    fields = _validate_book_fields(data)
    if fields is None:
        return jsonify({"ok": False, "error": "invalid value"}), 400
    before = dict(get_book(book_id))
    update_book(book_id, fields)
    auth.audit(acting_user(), "book.update", book_id,
               {k: [before.get(k), v] for k, v in fields.items() if before.get(k) != v})
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Conversation (/api/chat). The model interprets and phrases; the database
# selects books; every shown id is validated against the candidates.
# ---------------------------------------------------------------------------

CHAT_MAX_MESSAGES = 12
CHAT_MAX_FACTS = 10
LANG_LABELS = {"very_easy": "سهلة جدًا", "easy": "سهلة", "medium": "متوسطة", "advanced": "متقدمة"}
FILLER_REASON = "خيار متوفر للتنويع، ولا يطابق وصفك مباشرة."
FILLER_REASON_EN = "Available for variety, though it is not a direct match for your description."


def card_tier(card, attr, value):
    entry = card["confidence"].get((attr, value)) if card else None
    if not entry:
        return None
    conf, verified = entry
    if verified or conf >= HIGH_TIER:
        return "confirmed"
    if conf >= MEDIUM_TIER:
        return "impression"
    return None


def is_available(book):
    return bool(book.get("active")) and (book.get("quantity") or 0) > 0


def book_fact(book, card, why, language="ar"):
    if language == "en":
        def mood_label(v): return MOOD_LABELS_EN.get(v, v)
        def use_label(v): return USE_LABELS_EN.get(v, v)
        def pace_label(v): return PACE_LABELS_EN.get(v, v)
        def weight_label(v): return WEIGHT_LABELS_EN.get(v, v)
        def lang_label(v): return {"very_easy": "very easy", "easy": "easy", "medium": "medium", "advanced": "advanced"}.get(v, v)
        def level_label(v): return LEVEL_LABELS_EN.get(v, v)
        def age_label(v): return AGE_LABELS_EN.get(v, v)
        def sensitive_label(v): return SENSITIVE_LABELS_EN.get(v, v)
    else:
        mood_label = lambda v: MOOD_LABELS_FEM.get(v, v)
        use_label = lambda v: USE_LABELS.get(v, v)
        pace_label = lambda v: PACE_SHORT.get(v, v)
        weight_label = lambda v: WEIGHT_LABELS_FEM.get(v, v)
        lang_label = lambda v: LANG_LABELS.get(v, v)
        level_label = lambda v: LEVEL_LABELS.get(v, v)
        age_label = lambda v: AGE_LABELS.get(v, v)
        sensitive_label = lambda v: SENSITIVE_LABELS.get(v, v)
    fact = {
        "id": book["book_id"], "title": book["title"], "author": book["author"],
        "genre": book.get("genre"), "pages_approx": book.get("page_count"),
        "available": is_available(book), "copies": book.get("quantity") or 0,
        "shelf": sections.location(book)[0], "price_sar": book.get("price"),
        "why_it_matches": why, "confirmed": [], "impression": [],
        "origin": origin_label(book, language),
    }
    if book.get("summary"):
        fact["summary"] = book["summary"]
    if not card:
        return fact
    if language == "en":
        items = [("moods", m, f"{mood_label(m)} atmosphere") for m in card.get("moods") or []]
        items += [("use_cases", u, f"suited to {use_label(u)}") for u in card.get("use_cases") or []]
        singles = [("pace", card.get("pace"), lambda v: f"{pace_label(v)} pace"),
                   ("emotional_weight", card.get("emotional_weight"), lambda v: weight_label(v)),
                   ("language_difficulty", card.get("language_difficulty"), lambda v: f"{lang_label(v)} language"),
                   ("reader_level", card.get("reader_level"), lambda v: level_label(v))]
    else:
        items = [("moods", m, f"أجواء {mood_label(m)}") for m in card.get("moods") or []]
        items += [("use_cases", u, f"مناسب لـ{use_label(u)}") for u in card.get("use_cases") or []]
        singles = [("pace", card.get("pace"), lambda v: f"إيقاع {pace_label(v)}"),
                   ("emotional_weight", card.get("emotional_weight"), lambda v: f"قراءة {weight_label(v)} عاطفيًا"),
                   ("language_difficulty", card.get("language_difficulty"), lambda v: f"لغة {lang_label(v)}"),
                   ("reader_level", card.get("reader_level"), lambda v: level_label(v))]
    items += [(a, v, fmt(v)) for a, v, fmt in singles if v and v != "unknown"]
    if card.get("age_suitability") and card["age_suitability"] != "unknown":
        items.append(("age_suitability", card["age_suitability"], ("Age range: " if language == "en" else "الفئة العمرية: ") + age_label(card["age_suitability"])))
    items += [("sensitive_content", c, ("Content note: " if language == "en" else "تنبيه محتوى: ") + sensitive_label(c))
              for c in card.get("sensitive_content") or [] if c != "none"]
    for attr, value, label in items:
        tier = card_tier(card, attr, value)
        if tier:
            fact[tier].append(label)
    return fact


def match_titles(mentions, all_books):
    found = []
    for m in mentions:
        nm = normalize(m).strip()
        if len(nm) < 3:
            continue
        for b in all_books.values():
            nt = normalize(b["title"]).strip()
            if nt and (nt in nm or nm in nt) and b["book_id"] not in [x["book_id"] for x in found]:
                found.append(b)
    return found[:3]


def chat_keyword_fallback(user_messages, all_books, shown, cards, language="ar"):
    """Safe answer without a model: keyword understanding of the visitor's messages, read in
    order (later messages override earlier ones), with the same constraints as the AI path."""
    understood = local_understand(user_messages)
    if user_messages and keyword_prefs.is_greeting(user_messages[-1]) and not keyword_prefs.understood_anything(understood):
        en = language == "en"
        return {"reply": ("Hello! What are you in the mood to read today: something light, a gripping story, or a particular kind of book?"
                          if en else "هلا والله! وش مزاجك اليوم؟ تبغى شي خفيف، قصة تشد، أو نوع معيّن من الكتب؟"),
                "books": [], "suggestions": (["Something light", "A gripping novel", "A gift"] if en else ["شي خفيف", "رواية تشد", "هدية"]),
                "understanding": "keywords", "_debug": {"mode": "keywords", "why": "greeting"}}
    prefs, pprefs, exclusions, _ = prefs_from_ai(understood, normalize(" ".join(user_messages)))
    pool = [b for b in all_books.values() if is_available(b) and b["book_id"] not in shown]
    pool, labels = constrain(pool, prefs, pprefs, exclusions, cards)
    scored = score_books(pool, prefs, pprefs, cards)
    direct = [(r, b) for s, r, b in scored if s > 0][:4]
    books = [dict(b, reason=reason_text(r, language), match="direct") for r, b in direct]
    if direct:
        reply = ("These books are available now and close to what you described. Tap a book to see its details."
                 if language == "en" else "هذي كتب متوفرة الآن وقريبة من كلامك. اضغط على أي كتاب تشوف تفاصيله.")
    elif not pool:
        books = []
        reply = (("We do not have an available book matching: " + ", ".join(localize_reason(x) for x in labels) + ". Would you like to relax one condition?") if labels
                 else "I have shown the available options closest to your request. Would you like to start with a different description?") if language == "en" else \
            (("ما عندنا الآن كتاب متوفر يطابق: " + "، ".join(labels) + ". تحب نخفف أحد الشروط؟") if labels
             else "عرضت لك كل الخيارات المتوفرة القريبة من طلبك. تحب نبدأ بوصف مختلف؟")
    elif labels:
        books = [dict(b, reason=filler_reason(labels, language), match="filler") for s, r, b in scored[:3]]
        reply = (("These available books follow your request (" + ", ".join(localize_reason(x) for x in labels) + "). Tell me a little more about your mood so I can narrow it down.") if language == "en" else
                 "هذي كتب متوفرة تلتزم بطلبك (" + "، ".join(labels) + "). قل لي أكثر عن مزاجك عشان أقرّب الاختيار.")
    else:
        books = [dict(b, reason=filler_reason(labels, language), match="filler") for s, r, b in scored[:3]]
        reply = (("I could not pin down your request, so here are a few varied books available now. Tell me more about your mood or the kind of book you enjoy.") if language == "en" else
                 "ما قدرت أحدد طلبك بدقة، فهذي خيارات متنوعة متوفرة الآن. قل لي أكثر عن مزاجك أو نوع الكتاب اللي تحبه.")
    return {"reply": reply, "books": [with_origin(b, language) for b in books],
            "suggestions": (["Something lighter", "A novel", "Show me others"] if language == "en" else ["شي أخف", "رواية", "ورّني غيرها"]), "understanding": "keywords",
            "_debug": {"mode": "keywords",
                       "why": ("model failed: " + str(llm.last_error.get("message"))) if llm.get_active() else "no model selected"}}


def run_chat(messages, active=None, language="ar"):
    """Core conversation turn. Raises ValueError for malformed input."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages required")
    all_books = {b["book_id"]: dict(b) for b in list_books(only_active=False)}
    conv, shown, streak = [], [], 0
    for m in messages[-CHAT_MAX_MESSAGES:]:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), str(m.get("content", "")).strip()[:600]
        if role not in ("user", "assistant") or not content:
            continue
        if role == "assistant":
            ids = [int(i) for i in (m.get("book_ids") or []) if str(i).isdigit() and int(i) in all_books]
            shown.extend(i for i in ids if i not in shown)
            if ids:
                content += "\n(الكتب التي عرضتُها: " + "، ".join(all_books[i]["title"] for i in ids) + ")"
                streak = 0
            else:
                streak += 1
        conv.append({"role": role, "content": content})
    if not conv or conv[-1]["role"] != "user":
        raise ValueError("last message must be from the visitor")
    user_messages = [c["content"] for c in conv if c["role"] == "user"]
    user_text = " ".join(user_messages)
    cards = get_profile_cards()

    active = active if active is not None else llm.get_active()
    it = (chat.interpret(conv, active, language) if language == "en" else chat.interpret(conv, active)) if active else None
    if it is None:
        out = chat_keyword_fallback(user_messages, all_books, set(shown), cards, language)
        if active:
            out["_debug"].update(failed_stage="interpret", call=dict(chat.last_failure.get("call") or {}))
        return out

    interpret_call = dict(llm.last_call)
    prefs, pprefs, exclusions, unsupported = prefs_from_ai(it["prefs"], normalize(user_text))
    pool = [b for b in all_books.values() if is_available(b)]
    if it["wants_new_options"]:
        pool = [b for b in pool if b["book_id"] not in shown]
    pool, labels = constrain(pool, prefs, pprefs, exclusions, cards)
    scored = score_books(pool, prefs, pprefs, cards)
    candidates = [(b, reason_text(r, language), "direct") for s, r, b in scored if s > 0][:6]
    if len(candidates) < 3:  # fillers come from the constrained pool, so they never break a stated wish
        used = {b["book_id"] for b, _, _ in candidates}
        candidates += [(b, filler_reason(labels, language), "filler") for s, r, b in scored
                       if b["book_id"] not in used][:4 - len(candidates)]

    facts, reason_by_id, seen = [], {}, set()

    def add(book, why, match):
        if book["book_id"] in seen or len(facts) >= CHAT_MAX_FACTS:
            return
        seen.add(book["book_id"])
        facts.append(book_fact(book, cards.get(book["book_id"]), why, language))
        reason_by_id[book["book_id"]] = (why, match)

    for b in match_titles(it["mentioned_titles"], all_books):
        add(b, "سأل عنه الزائر", "asked")
    # a title the visitor asked about is discussed even if unsuitable, but only
    # age-suitable books may be shown as cards (see the id validation below)
    suitable = {b["book_id"] for b in all_books.values()
                if age_allowed(cards.get(b["book_id"]), pprefs.get("audience"), pprefs.get("avoid") or ())}
    for b, why, match in candidates:
        add(b, why, match)
    for i in shown[-4:]:
        if age_allowed(cards.get(i), pprefs.get("audience"), pprefs.get("avoid") or ()):
            add(all_books[i], "سبق عرضه في المحادثة", "shown")

    guidance = ({
        "recommend": "Recommend 2 to 4 available books from facts, with a brief reason for each from its facts, then ask one question to narrow the choice.",
        "clarify": "The request is still broad: ask one friendly question about mood, time, or a recent favourite. You may offer one especially suitable first book.",
        "about_book": "Answer about the requested book using only its facts. If unavailable, say so and suggest an available alternative. Do not show new books unless asked.",
        "compare": "Compare the named books using only their facts and help the visitor choose.",
        "smalltalk": "Reply warmly in one sentence, then ask about the mood or type of reading they want.",
        "off_topic": "The request is outside reading: decline briefly and return to books. Do not show books.",
    } if language == "en" else {
        "recommend": "اقترح من 2 إلى 4 كتب متوفرة (available=true) من facts، مع سبب قصير لكل كتاب من صفاته في facts، ثم سؤال واحد يساعد يضيّق الاختيار.",
        "clarify": "الطلب ما زال عامًا: اسأل سؤالًا واحدًا لطيفًا يساعدك تختار (المزاج، الوقت، آخر كتاب عجبه). يمكنك اقتراح كتاب واحد مبدئي فقط إذا كان مناسبًا جدًا.",
        "about_book": "أجب عن الكتاب المسؤول عنه من معلوماته في facts فقط. إذا كان غير متوفر قل ذلك واقترح بديلًا متوفرًا. لا تعرض كتبًا جديدة إلا إذا طلب.",
        "compare": "قارن بين الكتب المذكورة من معلوماتها في facts فقط، وساعده يختار.",
        "smalltalk": "رد بلطف في جملة، ثم اسأله عن مزاجه أو نوع القراءة اللي يبغاها.",
        "off_topic": "الكلام خارج القراءة: اعتذر بلطف في جملة وارجع لسؤاله عن الكتب. لا تعرض كتبًا.",
    })[it["intent"]]
    if streak >= 3 and it["intent"] in ("clarify", "smalltalk"):
        guidance = ("You have asked enough questions: recommend 2 to 4 available books from facts with a brief reason for each."
                    if language == "en" else "سألت أسئلة كافية: اقترح الآن من 2 إلى 4 كتب متوفرة من facts مع سبب قصير لكل كتاب.")
    if pprefs.get("audience") in AGE_OK:
        guidance += ((" The reader is " + ("a child" if pprefs["audience"] == "children" else "a teenager")
                      + ". Recommend only from facts; do not mention any other title from your knowledge.") if language == "en" else
                     (" القارئ " + ("طفل" if pprefs["audience"] == "children" else "مراهق")
                      + ": اقترح فقط من facts (كلها مفحوصة لعمره). لا تذكر كتابًا آخر من معرفتك."))
    if unsupported:
        guidance += ((" We do not have data for these wishes: " + ", ".join(unsupported) + ". Say that plainly in half a sentence.") if language == "en" else
                     " لاحظ أن هذه الرغبات لا نملك بيانات عنها: " + "، ".join(unsupported) + ". قل ذلك بصراحة في نصف جملة.")
    if not any(f["available"] for f in facts):
        guidance += (" No matching books are available; say so plainly and ask about an alternative."
                     if language == "en" else " لا توجد كتب متوفرة مطابقة؛ قل ذلك بصراحة واسأله عن بديل.")

    debug = {"mode": "ai", "intent": it["intent"], "prefs": it["prefs"],
             "mentioned_titles": it["mentioned_titles"], "wants_new_options": it["wants_new_options"],
             "candidates": [{"id": f["id"], "title": f["title"], "available": f["available"],
                             "why": f["why_it_matches"]} for f in facts],
             "guidance": guidance}
    resp = (chat.respond(conv, json.dumps(facts, ensure_ascii=False), guidance, active, language)
            if language == "en" else chat.respond(conv, json.dumps(facts, ensure_ascii=False), guidance, active))
    if resp is None:
        out = chat_keyword_fallback(user_messages, all_books, set(shown), cards, language)
        out["understanding"] = "keywords"
        out["_debug"].update(interpreted=debug, failed_stage="respond",
                             call=dict(chat.last_failure.get("call") or {}))
        return out
    valid = {f["id"] for f in facts if f["available"] and f["id"] in suitable}
    ids = []
    for i in resp["show_book_ids"]:
        if i in valid and i not in ids:
            ids.append(i)
    books = []
    for i in ids[:4]:
        why, match = reason_by_id[i]
        reason = why if match in ("direct", "filler") else "سألت عنه، وهو متوفر الآن."
        books.append(dict(all_books[i], reason=reason, match=match))
    debug["model_book_ids"] = resp["show_book_ids"]
    debug["calls"] = {"interpret": interpret_call, "respond": dict(llm.last_call)}
    debug["dropped_ids"] = [i for i in resp["show_book_ids"] if i not in valid]
    return {"reply": resp["reply"], "books": [with_origin(b, language) for b in books], "suggestions": resp["suggestions"],
            "understanding": "ai", "provider": active[0], "model": active[1], "_debug": debug}


RATE_LIMITED = "وصلنا حد الرسائل المسموح لهذا الجهاز مؤقتًا. انتظر دقائق بسيطة ثم كمّل، أو اسأل موظف المكتبة."
RATE_LIMITED_EN = "This device has reached its message limit for now. Please wait a few minutes, then continue or ask a bookseller."


def active_within_budget():
    """The chosen model, or None when today's model-call budget is used up (keyword fallback)."""
    active = llm.get_active()
    if active and not limits.budget_left():
        limits.bump("capped_turns")
        return None
    return active


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    language = data.get("language") if data.get("language") in ("ar", "en") else "ar"
    if not limits.allow_request(request.remote_addr, testlog.clean_id(data.get("conversation_id"))):
        return jsonify({"error": "rate_limited", "reply": RATE_LIMITED_EN if language == "en" else RATE_LIMITED, "books": [], "suggestions": []}), 429
    t0 = time.time()
    active = active_within_budget()
    try:
        result = run_chat(data.get("messages"), active=active if active else False, language=language)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if llm.get_active() and result.get("understanding") != "ai":
        limits.bump("fallback_turns")
    debug = result.pop("_debug", {})
    if llm.get_active() and not active:
        debug["why"] = "daily model budget reached"
    if testlog.enabled():
        msgs = data.get("messages") or []
        last = msgs[-1] if msgs and isinstance(msgs[-1], dict) else {}
        active = llm.get_active()
        testlog.record("turn", {
            "conversation_id": testlog.clean_id(data.get("conversation_id")),
            "turn": sum(1 for m in msgs if isinstance(m, dict) and m.get("role") == "user"),
            "provider": active[0] if active else None, "model": active[1] if active else None,
            "understanding": result.get("understanding"), "seconds": round(time.time() - t0, 2),
            "visitor": str(last.get("content", ""))[:600], "reply": result.get("reply"),
            "books_shown": [{"id": b["book_id"], "title": b["title"], "reason": b.get("reason")} for b in result["books"]],
            "suggestions": result.get("suggestions"), "debug": debug,
            "language": language,
        })
    insights.record_shown(b["book_id"] for b in result["books"])
    result["test_mode"] = testlog.enabled()
    return jsonify(result)


@app.route("/api/test-mode")
def api_test_mode():
    return jsonify({"enabled": testlog.enabled()})


# ---------------------------------------------------------------------------
# Admin: model selection (no secrets are ever returned or stored)
# ---------------------------------------------------------------------------

@app.route("/api/admin/llm", methods=["GET"])
def admin_llm_status():
    return jsonify(llm.status())


@app.route("/api/admin/llm", methods=["POST"])
def admin_llm_set():
    data = request.get_json(silent=True) or {}
    try:
        llm.set_active(str(data.get("provider", "")), str(data.get("model", "")))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    auth.audit(acting_user(), "model.set", data.get("provider"), {"model": str(data.get("model", ""))[:100]})
    return jsonify({"ok": True, "status": llm.status()})


def _is_local_request():
    """Key and address changes are accepted only from this computer, even if the
    server is exposed on the Wi-Fi for phone testing (LIBRARY_KEEPER_HOST=0.0.0.0)."""
    return request.remote_addr in ("127.0.0.1", "::1", "localhost")


@app.route("/api/admin/llm/key", methods=["POST"])
def admin_llm_key():
    if not _is_local_request():
        return jsonify({"ok": False, "error": "حفظ المفاتيح مسموح من نفس الجهاز فقط"}), 403
    data = request.get_json(silent=True) or {}
    try:
        llm.set_key(str(data.get("provider", "")), str(data.get("key", "")))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    auth.audit(acting_user(), "key.saved" if str(data.get("key", "")).strip() else "key.deleted", data.get("provider"))
    return jsonify({"ok": True, "status": llm.status()})


@app.route("/api/admin/llm/endpoint", methods=["POST"])
def admin_llm_endpoint():
    if not _is_local_request():
        return jsonify({"ok": False, "error": "تغيير العناوين مسموح من نفس الجهاز فقط"}), 403
    data = request.get_json(silent=True) or {}
    try:
        llm.set_endpoint(str(data.get("provider", "")), str(data.get("url", "")))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    auth.audit(acting_user(), "endpoint.set", data.get("provider"))
    return jsonify({"ok": True, "status": llm.status()})


@app.route("/api/admin/llm/ping", methods=["POST"])
def admin_llm_ping():
    """Tiny round-trip to check a provider/model works (no visitor data)."""
    data = request.get_json(silent=True) or {}
    pid = str(data.get("provider", ""))
    if pid not in llm.PROVIDERS:
        return jsonify({"ok": False, "error": "unknown provider"}), 400
    model = str(data.get("model") or llm.PROVIDERS[pid]["default_model"]).strip()
    if not model:
        return jsonify({"ok": False, "error": "اكتب اسم النموذج"}), 400
    t0 = time.time()
    try:
        out = llm.complete_json("Reply with the JSON object {\"ok\": true}.",
                                [{"role": "user", "content": "ping"}],
                                {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                                max_tokens=50, active=(pid, model))
        return jsonify({"ok": bool(out.get("ok", True)), "seconds": round(time.time() - t0, 2)})
    except llm.LLMError as e:
        return jsonify({"ok": False, "error": str(e)[:300], "seconds": round(time.time() - t0, 2)})


@app.route("/api/admin/llm/test", methods=["POST"])
def admin_llm_test():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()[:500]
    if not message:
        return jsonify({"error": "message required"}), 400
    t0 = time.time()
    result = run_chat([{"role": "user", "content": message}])
    result["debug"] = result.pop("_debug", {})
    result["seconds"] = round(time.time() - t0, 2)
    result["books"] = [{"title": b["title"], "reason": b["reason"]} for b in result["books"]]
    result["last_error"] = llm.last_error if result["understanding"] != "ai" else None
    return jsonify(result)


# ---------------------------------------------------------------------------
# Admin catalog cards: everything the enrichment wrote, made readable.
# Read-only views over books + book_profiles + attribute_evidence.
# ---------------------------------------------------------------------------

ATTR_ORDER = ["format", "genres", "perceived_length", "language_difficulty", "pace",
              "moods", "emotional_weight", "reader_level", "use_cases", "age_suitability", "sensitive_content"]
ATTR_NAMES_AR = {
    "age_suitability": "الفئة العمرية", "sensitive_content": "تنبيهات المحتوى",
    "format": "الشكل", "genres": "التصنيفات", "perceived_length": "الطول المُدرك",
    "language_difficulty": "سهولة اللغة", "pace": "الإيقاع", "moods": "المزاج",
    "emotional_weight": "الثقل العاطفي", "reader_level": "مستوى القارئ", "use_cases": "يناسب",
}
FORMAT_LABELS = {"novel": "رواية", "short_stories": "قصص قصيرة", "biography": "سيرة", "history": "تاريخ",
                 "thought": "فكر", "psychology": "علم نفس", "self_dev": "تطوير ذاتي", "science": "علوم",
                 "poetry": "شعر", "play": "مسرحية", "travel": "أدب رحلات", "religious": "ديني",
                 "children": "أطفال", "young_adult": "يافعين", "business": "أعمال", "other": "آخر"}
VALUE_LABELS = {
    "format": FORMAT_LABELS, "genres": FORMAT_LABELS,
    "perceived_length": {"very_short": "قصير جدًا", "short": "قصير", "medium": "متوسط",
                         "long": "طويل", "very_long": "طويل جدًا"},
    "language_difficulty": LANG_LABELS,
    "pace": PACE_SHORT, "moods": MOOD_LABELS, "emotional_weight": WEIGHT_LABELS,
    "reader_level": LEVEL_LABELS, "use_cases": USE_LABELS,
    "age_suitability": AGE_LABELS, "sensitive_content": SENSITIVE_LABELS,
}
SITE_LABELS = {"goodreads": "Goodreads", "abjjad": "أبجد", "web": "مقال ويب", "staff": "موظف", "derived": "محسوب"}
SCOPE_LABELS = {"arabic_edition": "الطبعة العربية", "original_or_any": "العمل الأصلي/أي طبعة"}
STATUS_LABELS = {"pending": "بانتظار الإثراء", "enriched": "مُثرى", "needs_review": "يحتاج مراجعة", "approved": "معتمد"}


def value_label(attr, value):
    if value in (None, "", "unknown"):
        return "غير معروف"
    return VALUE_LABELS.get(attr, {}).get(value, value)


def evidence_tier(row):
    """confirmed / impression / weak / conflict / derived / rejected — mirrors recommender rules."""
    if row.get("rejected"):
        return "rejected"
    if row["source_site"] == "derived":
        return "derived"
    if row["conflict_seen"] and not row["human_verified"]:
        return "conflict"
    conf = row["confidence"] or 0
    if row["human_verified"] or conf >= HIGH_TIER:
        return "confirmed"
    if conf >= MEDIUM_TIER:
        return "impression"
    return "weak"


def _json_list(text):
    try:
        v = json.loads(text or "[]")
        return v if isinstance(v, list) else []
    except ValueError:
        return []


def _catalog_rows():
    conn = get_connection()
    try:
        books = [dict(r) for r in conn.execute("SELECT * FROM books ORDER BY book_id")]
        try:
            profiles = {r["book_id"]: dict(r) for r in conn.execute("SELECT * FROM book_profiles")}
            evidence = [dict(r) for r in conn.execute("SELECT * FROM attribute_evidence ORDER BY evidence_id")]
        except sqlite3.OperationalError:
            profiles, evidence = {}, []
    finally:
        conn.close()
    by_book = {}
    for ev in evidence:
        by_book.setdefault(ev["book_id"], []).append(ev)
    return books, profiles, by_book


@app.route("/api/admin/catalog")
def admin_catalog():
    books, profiles, by_book = _catalog_rows()
    out = []
    for b in books:
        evs = by_book.get(b["book_id"], [])
        counts = {"confirmed": 0, "impression": 0, "weak": 0, "conflict": 0, "derived": 0, "rejected": 0}
        highlights = []
        for ev in evs:
            tier = evidence_tier(ev)
            counts[tier] += 1
            if tier in ("confirmed", "impression") and ev["attribute_name"] in ("moods", "pace", "emotional_weight", "language_difficulty") \
                    and ev["attribute_value"] != "unknown":
                highlights.append({"attr": ev["attribute_name"], "label": value_label(ev["attribute_name"], ev["attribute_value"]),
                                   "tier": tier, "confidence": ev["confidence"]})
        highlights.sort(key=lambda h: (h["tier"] != "confirmed", -(h["confidence"] or 0)))
        p = profiles.get(b["book_id"]) or {}
        reviewed = any(ev["source_site"] in ("goodreads", "abjjad", "web", "staff") for ev in evs)
        out.append({
            "book_id": b["book_id"], "title": b["title"], "author": b["author"], "genre": b["genre"],
            "page_count": b["page_count"], "price": b["price"], "quantity": b["quantity"],
            "shelf_location": b["shelf_location"], "active": b["active"],
            "status": p.get("overall_status") or "pending",
            "status_label": STATUS_LABELS.get(p.get("overall_status") or "pending", p.get("overall_status")),
            "has_reviews": reviewed, "counts": counts, "highlights": highlights[:4],
            "source": b.get("source") or "seed", "barcode": b.get("barcode"), "last_synced_at": b.get("last_synced_at"),
            "cover_url": b.get("cover_url") if covers.has_local_cover(b) else None, "cover_source": b.get("cover_source"),
            "has_summary": bool(b.get("summary")),
        })
    return jsonify(out)


@app.route("/api/admin/books/<int:book_id>")
def admin_book_card(book_id):
    books, profiles, by_book = _catalog_rows()
    book = next((b for b in books if b["book_id"] == book_id), None)
    if book is None:
        return jsonify({"error": "book not found"}), 404
    p = profiles.get(book_id) or {}
    card_values = {
        "format": [p.get("format")] if p.get("format") else [],
        "genres": _json_list(p.get("genres")),
        "perceived_length": [p.get("perceived_length")] if p.get("perceived_length") else [],
        "language_difficulty": [p.get("language_difficulty")] if p.get("language_difficulty") else [],
        "pace": [p.get("pace")] if p.get("pace") else [],
        "moods": _json_list(p.get("moods")),
        "emotional_weight": [p.get("emotional_weight")] if p.get("emotional_weight") else [],
        "reader_level": [p.get("reader_level")] if p.get("reader_level") else [],
        "use_cases": _json_list(p.get("use_cases")),
        "age_suitability": [p.get("age_suitability")] if p.get("age_suitability") else [],
        "sensitive_content": _json_list(p.get("sensitive_content")),
    }
    evs = by_book.get(book_id, [])
    attributes = []
    for attr in ATTR_ORDER:
        rows = []
        for ev in (e for e in evs if e["attribute_name"] == attr):
            rows.append({
                "evidence_id": ev["evidence_id"], "rejected": bool(ev.get("rejected")),
                "verified_by": ev.get("verified_by"), "verified_at": ev.get("verified_at"),
                "rejected_by": ev.get("rejected_by"), "rejected_at": ev.get("rejected_at"),
                "value": ev["attribute_value"], "label": value_label(attr, ev["attribute_value"]),
                "confidence": ev["confidence"], "tier": evidence_tier(ev),
                "site": ev["source_site"], "site_label": SITE_LABELS.get(ev["source_site"], ev["source_site"]),
                "scope": ev["edition_scope"], "scope_label": SCOPE_LABELS.get(ev["edition_scope"], ""),
                "review_count": ev["review_count"], "conflict": bool(ev["conflict_seen"]),
                "summary": ev["evidence_summary"], "urls": [u for u in _json_list(ev["source_urls"]) if isinstance(u, str) and u.startswith(("http://", "https://"))],
                "analyzed_at": ev["analyzed_at"], "verified": bool(ev["human_verified"]),
            })
        rows.sort(key=lambda r: (r["rejected"], -(r["confidence"] or 0)))
        attributes.append({
            "key": attr, "name": ATTR_NAMES_AR[attr],
            "kind": "multi" if attr in card_review.MULTI else "single",
            "options": [{"value": v, "label": value_label(attr, v)} for v in card_review.ALLOWED[attr]],
            "card": [{"value": v, "label": value_label(attr, v)} for v in card_values[attr] if v],
            "evidence": rows,
        })
    sources = sorted({u for a in attributes for r in a["evidence"] for u in r["urls"]})
    active = [r for a in attributes for r in a["evidence"] if not r["rejected"] and r["site"] != "derived" and r["value"] != "unknown"]
    review = {"verified": sum(1 for r in active if r["verified"]), "total": len(active)}
    return jsonify({
        "book": with_origin(book),
        "languages": [{"value": k, "label": v} for k, v in LANG_NAMES.items()],
        "profile": {"status": p.get("overall_status") or "pending",
                    "status_label": STATUS_LABELS.get(p.get("overall_status") or "pending", ""),
                    "last_enriched_at": p.get("last_enriched_at")},
        "attributes": attributes, "sources": sources, "review": review,
    })


@app.route("/api/admin/books/<int:book_id>/attributes/<attr>", methods=["POST"])
def admin_review_attribute(book_id, attr):
    """Optional staff review: confirm / reject / restore / set / add."""
    data = request.get_json(silent=True) or {}
    evidence_id = data.get("evidence_id")
    try:
        evidence_id = int(evidence_id) if evidence_id is not None else None
        user = acting_user()
        card_review.apply_action(book_id, attr, data.get("action"), evidence_id=evidence_id,
                                 value=data.get("value"), reviewer=user["display_name"])
        auth.audit(user, "card." + str(data.get("action")), book_id,
                   {"attribute": attr, "evidence_id": evidence_id, "value": data.get("value")})
    except LookupError:
        return jsonify({"error": "book not found"}), 404
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    return admin_book_card(book_id)


# ---------------------------------------------------------------------------
# Rewaa inventory import (admin). Dry-run first; apply writes one transaction.
# ---------------------------------------------------------------------------

COVER_UPLOAD_MAX = 5 * 1024 * 1024


@app.route("/api/admin/books/<int:book_id>/cover", methods=["POST"])
def admin_cover_upload(book_id):
    if get_book(book_id) is None:
        return jsonify({"error": "book not found"}), 404
    f = request.files.get("file")
    if f is None:
        return jsonify({"error": "no file"}), 400
    data = f.read(COVER_UPLOAD_MAX + 1)
    try:
        result = covers.save_cover(book_id, data, "staff")
    except covers.CoverError as e:
        return jsonify({"error": str(e)}), 400
    auth.audit(acting_user(), "cover.upload", book_id, {"bytes": len(data)})
    return jsonify(dict(result, ok=True))


@app.route("/api/admin/books/<int:book_id>/cover", methods=["DELETE"])
def admin_cover_delete(book_id):
    if get_book(book_id) is None:
        return jsonify({"error": "book not found"}), 404
    covers.remove_cover(book_id)
    auth.audit(acting_user(), "cover.delete", book_id, None)
    return jsonify({"ok": True})


@app.route("/api/admin/books/<int:book_id>/cover/fetch", methods=["POST"])
def admin_cover_fetch(book_id):
    book = get_book(book_id)
    if book is None:
        return jsonify({"error": "book not found"}), 404
    try:
        found = covers.fetch_cover(dict(book))
    except covers.CoverError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not found:
        return jsonify({"ok": False, "error": "no cover found"}), 404
    auth.audit(acting_user(), "cover.fetch", book_id, found)
    book = dict(get_book(book_id))
    return jsonify({"ok": True, "cover_url": book["cover_url"], "cover_source": book["cover_source"]})


@app.route("/api/admin/import/upload", methods=["POST"])
def admin_import_upload():
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "اختر ملف"}), 400
    try:
        return jsonify(rewaa_import.store_upload(f.filename, f.read()))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/admin/import/plan", methods=["POST"])
def admin_import_plan():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(rewaa_import.build_plan(data.get("upload_id"), data.get("mapping"),
                                               data.get("missing_mode", "zero")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/admin/import/apply", methods=["POST"])
def admin_import_apply():
    data = request.get_json(silent=True) or {}
    try:
        result = rewaa_import.apply_plan(data.get("upload_id"), data.get("mapping"),
                                         data.get("missing_mode", "zero"))
        auth.audit(acting_user(), "import.apply", None, {k: result.get(k) for k in ("added", "updated", "missing")
                                                           if isinstance(result, dict)})
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/admin/import/history")
def admin_import_history():
    return jsonify(rewaa_import.history())


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "الملف أكبر من ٨ ميجابايت"}), 413


@app.route("/api/admin/testlog", methods=["GET"])
def admin_testlog_status():
    return jsonify(testlog.summary())


@app.route("/api/admin/testlog", methods=["POST"])
def admin_testlog_set():
    data = request.get_json(silent=True) or {}
    try:
        testlog.set_enabled(bool(data.get("enabled")))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    auth.audit(acting_user(), "testmode.set", None, {"enabled": bool(data.get("enabled"))})
    return jsonify({"ok": True, "status": testlog.summary()})


# ---------------------------------------------------------------------------
# Accounts: sign-in, first-run setup, users (manager), own password, audit, usage
# ---------------------------------------------------------------------------

@app.route("/api/admin/session")
def admin_session():
    user = current_user()
    if user is None:
        return jsonify({"user": None, "needs_setup": auth.count_users() == 0,
                        "setup_allowed": _is_local_request()}), 401
    return jsonify(_session_payload(user))


@app.route("/api/admin/setup", methods=["POST"])
def admin_setup():
    """First manager account: only when no account exists, and only from this computer."""
    if auth.count_users() > 0:
        return jsonify({"error": "الإعداد تم من قبل"}), 409
    if not _is_local_request():
        return jsonify({"error": "إنشاء أول حساب مسموح من نفس الجهاز فقط"}), 403
    if request.mimetype != "application/json":
        return jsonify({"error": "bad request"}), 400
    data = request.get_json(silent=True) or {}
    try:
        uid = auth.create_user(data.get("username"), data.get("name"), "manager", data.get("password"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    user = auth.get_user(uid)
    _start_session(user)
    auth.audit(user, "user.setup", user["username"])
    return jsonify(_session_payload(user))


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    if request.mimetype != "application/json":  # blocks cross-site form posts
        return jsonify({"error": "bad request"}), 400
    data = request.get_json(silent=True) or {}
    user, error = auth.authenticate(data.get("username"), data.get("password"), request.remote_addr)
    if user is None:
        return jsonify({"error": error}), 401
    _start_session(user)
    auth.audit(user, "login", None)
    return jsonify(_session_payload(user))


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/admin/me/password", methods=["POST"])
def admin_me_password():
    user = acting_user()
    data = request.get_json(silent=True) or {}
    full = auth.get_user(user["user_id"])
    ok, _ = auth.authenticate(full["username"], data.get("current"), request.remote_addr)
    if not ok:
        return jsonify({"error": "كلمة المرور الحالية غير صحيحة"}), 400
    try:
        auth.update_user(user["user_id"], password=data.get("new"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _start_session(auth.get_user(user["user_id"]))
    auth.audit(user, "user.password_changed", user["username"])
    return jsonify(_session_payload(auth.get_user(user["user_id"])))


@app.route("/api/admin/users", methods=["GET"])
def admin_users():
    return jsonify({"users": auth.list_users(), "roles": [{"value": r, "label": auth.ROLE_LABELS[r]} for r in auth.ROLES]})


@app.route("/api/admin/users", methods=["POST"])
def admin_users_create():
    data = request.get_json(silent=True) or {}
    try:
        uid = auth.create_user(data.get("username"), data.get("name"), data.get("role"), data.get("password"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    auth.audit(acting_user(), "user.created", data.get("username"), {"role": data.get("role")})
    return jsonify({"ok": True, "user": auth.get_user(uid)})


@app.route("/api/admin/users/<int:user_id>", methods=["POST"])
def admin_users_update(user_id):
    data = request.get_json(silent=True) or {}
    changes = {k: data[k] for k in ("role", "active", "password", "display_name") if k in data}
    if "active" in changes:
        changes["active"] = bool(changes["active"])
    try:
        auth.update_user(user_id, **changes)
    except LookupError:
        return jsonify({"error": "user not found"}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    auth.audit(acting_user(), "user.updated", user_id,
               {k: ("***" if k == "password" else v) for k, v in changes.items()})
    return jsonify({"ok": True, "user": auth.get_user(user_id)})


@app.route("/api/admin/audit")
def admin_audit():
    return jsonify(auth.recent_audit(200))


@app.route("/api/admin/usage", methods=["GET"])
def admin_usage():
    return jsonify(limits.usage(7))


@app.route("/api/admin/usage", methods=["POST"])
def admin_usage_set():
    data = request.get_json(silent=True) or {}
    try:
        limits.set_daily_cap(data.get("daily_model_calls"))
    except (TypeError, ValueError):
        return jsonify({"error": "قيمة غير صحيحة"}), 400
    auth.audit(acting_user(), "budget.set", None, {"daily_model_calls": data.get("daily_model_calls")})
    return jsonify(limits.usage(7))


if __name__ == "__main__":
    init_db()
    seed_books()
    # Safe defaults: local-only, no debugger. For phone testing on the same
    # Wi-Fi, run with LIBRARY_KEEPER_HOST=0.0.0.0. For development reloads,
    # set LIBRARY_KEEPER_DEBUG=1 (never on a shared network).
    if BIND_HOST not in ("127.0.0.1", "localhost", "::1"):
        _ip = lan_address()
        print(f"Trial mode - phones on the same Wi-Fi: http://{_ip}:{BIND_PORT}/" if _ip
              else "Trial mode - no network address found: connect this computer to the Wi-Fi.", flush=True)
    app.run(
        host=BIND_HOST,
        port=BIND_PORT,
        debug=os.environ.get("LIBRARY_KEEPER_DEBUG") == "1",
    )
