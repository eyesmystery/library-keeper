from datetime import timedelta
import secrets
import hmac
import hashlib

from flask import Flask, request, jsonify, send_from_directory, session
import os
import random
import itertools
import json
import re
import sqlite3
import time

import auth
import author_flow
import authors
import card_review
import confirmations
import relations
import covers
import deleted_books
import insights
import sections
import chat
import limits
import llm
import rewaa_import
import testlog
import topics
import visitor_notes
import honors
import inbox_export
import no_data
import open_questions
import staff_notes
import events
import embeddings

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
STAFF_PREFIXES = ("/api/admin/catalog", "/api/admin/books/", "/api/admin/me", "/api/admin/authors",
                  "/api/admin/inbox", "/api/admin/events")


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


def read_version():
    """The app version: the one line of the VERSION file in the project folder (docs/development.md, Versioning)."""
    try:
        with open(os.path.join(ROOT, "..", "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


APP_VERSION = read_version()  # read once at startup, so it is the version of the running server

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


@app.route("/event-images/<path:filename>")
def event_image_file(filename):
    if events.image_path(filename) is None:
        return jsonify({"error": "not found"}), 404
    resp = send_from_directory(events.IMAGES_DIR, filename)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


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
# Age rule (Ibrahim, 2026-10-04): no age categories. A child or teenager is offered books from the
# children's / young-adult shelves (the card's format), minus the content warnings below; the only
# age statement shown is «لا يناسب الأطفال» on a book whose card lists sexual content.
# `age_suitability` rows stay in the database but are no longer read here.
AGE_OK = {"children": ("children",), "young_adult": ("young_adult", "children")}
AGE_BLOCKING = {"children": ("violence", "sexual_content", "strong_language", "horror", "substance_use",
                             "self_harm", "religious_controversy", "political"),
                "young_adult": ("sexual_content", "strong_language", "substance_use", "self_harm",
                                "religious_controversy")}
AGE_LABELS = {"children": "للأطفال", "young_adult": "لليافعين", "adult": "للكبار", "all_ages": "لكل الأعمار"}
SENSITIVE_LABELS = {"violence": "عنف", "sexual_content": "مشاهد جريئة", "strong_language": "ألفاظ قاسية",
                    "horror": "رعب", "substance_use": "مخدرات أو كحول", "self_harm": "إيذاء النفس",
                    "religious_controversy": "جدل ديني", "political": "مضمون سياسي", "grief": "فقد وحزن",
                    "none": "لا تنبيهات"}
_AGE_NUM = re.compile(r"(عمره|عمرها|عمري|عمر|سنه|سنوات|سنين)\s*(\d{1,2})|(\d{1,2})\s*(سنه|سنوات|سنين)")
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
        for h in AUDIENCE_KEYWORDS[aud]:
            i = keyword_prefs.find_phrase(words, h)
            # «مو رعب أطفال» / «ليس للأطفال» / "not for kids": the reader is NOT a child (2026-10-06)
            if i >= 0 and not _audience_negated(words, i):
                return aud
    return None


_AUDIENCE_NEGATORS = keyword_prefs.NEGATORS | {keyword_prefs.normalize(w) for w in "ليس ليست لست مهو ماهو موب مهوب".split()}


def _audience_negated(words, i):
    """A negator within the three words before the audience word, whatever stands between
    («مو رعب أطفال», «ما أبغاه للأطفال», "not a kids book")."""
    return any(w in _AUDIENCE_NEGATORS for w in words[max(0, i - 3):i])


def _medium_or_better(card, attr, value):
    entry = card["confidence"].get((attr, value)) if card else None
    return bool(entry and (entry[1] or entry[0] >= MEDIUM_TIER))


# Severity rules (card v2 §12.4, approved 2026-09-28).
#  children: every flag in AGE_BLOCKING blocks at any severity; grief only when strong (3)
#  young adults: their AGE_BLOCKING flags block from «clear» (2) up, a mild one (1) does not;
#                violence / horror / grief block only when strong (3)
# An unknown severity stays unknown (Ibrahim, 2026-09-28): it is not assumed strong or mild; the
# flag then decides exactly as before severities existed (a flag on the reader's blocking list
# blocks; violence / horror / grief alone do not).
SEVERE_ONLY = {"violence", "horror", "grief"}


def _warning_blocks(card, audience, flag):
    if not _medium_or_better(card, "sensitive_content", flag):
        return False
    sev = (card.get("severity") or {}).get(flag)
    if flag in AGE_BLOCKING[audience]:
        return True if audience == "children" or sev is None else sev >= 2
    return flag in SEVERE_ONLY and sev is not None and sev >= 3


def _age_range_ok(book, audience, age=None):
    """A book outside the children's / young readers' shelves (a comic, a manga) whose researched or
    staff-given age range fits this reader (QA2 2026-10-09: no comics for a 13-year-old). Unknown
    range -> False: such a book is never offered to a child unchecked."""
    if not book or (book.get("age_min") is None and book.get("age_max") is None):
        return False
    if age is not None:
        return age_fit(book, age) > 0
    lo = book.get("age_min") or 0
    return lo <= (10 if audience == "children" else 15)


def age_allowed(card, audience, avoid=(), book=None, age=None):
    """Eligibility of a book for the stated reader.

    audience children/young_adult: the book must be on that shelf (card format
    children / young_adult) and carry no blocking content flag. Anyone else:
    everything stays (meets_constraints keeps children's books out of a request
    that names no genre). Content the visitor asked to avoid excludes a book
    whenever the card flags it."""
    for c in avoid:
        if _medium_or_better(card, "sensitive_content", c):
            return False
    if audience in AGE_OK:
        if (not card or card.get("format") not in AGE_OK[audience]) and not _age_range_ok(book, audience, age):
            return False
        return not any(_warning_blocks(card, audience, c) for c in set(AGE_BLOCKING[audience]) | SEVERE_ONLY)
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
    "قصص مصورة": "comics and manga", "مقالات ونصوص": "essays", "نقد أدبي": "literary criticism", "علم اجتماع": "sociology",
    "فنون وسينما": "arts and cinema",
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
    "religious_controversy": "religious controversy", "political": "political content", "grief": "grief and loss",
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
        if card.get("format") == aud:
            score += 3
            reasons.append("مناسب " + AGE_LABELS[aud])
        elif card.get("format") in AGE_OK[aud]:
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
    if prefs.get("awards"):
        won = honors.matches_award(book["book_id"], prefs["awards"], prefs.get("award_year"), prefs.get("award_min_year"), prefs.get("award_max_year"))
        if won:
            # the book's own prize first, then its author's, then a list / nomination (2026-10-08)
            score += 5 if won[0].startswith("فاز") else 3 if won[0].startswith("مؤلفه") else 2
            # a newer prize ranks higher: 2024 before 1984 (library trial, 2026-10-08)
            newest = honors.newest_award_year(book["book_id"], prefs["awards"], prefs.get("award_min_year"), prefs.get("award_max_year"))
            score += max(0, newest - 1950) / 25
            reasons.append(won[0])
    if prefs.get("adaptation"):
        made = honors.matches_adaptation(book["book_id"], prefs["adaptation"])
        if made:
            score += 4
            reasons.append("تحوّل إلى " + made[0])
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
    author_words = set(normalize(book.get("author")).split())
    title_words = {strip_al(w) for w in normalize(book.get("title")).split()}
    author_hits = (sum(author_matches(a, book.get("author")) for a in prefs["authors"])
                   if prefs.get("authors") else sum(1 for t in prefs["tokens"] if token_in(t, author_words)))
    if author_hits:
        # each extra word of the author's name counts, so «نجيب محفوظ» outranks another «نجيب»
        score += 2 + author_hits
        reasons.append(f"من أعمال {book['author']}")
    if any(token_in(t, title_words) for t in prefs["tokens"]):
        score += 2
        reasons.append("عنوانه يطابق كلمات من وصفك")
    for subgenre in matching_subgenres(book, prefs) + matching_subgenres(book, prefs, "soft_subgenres"):
        score += 6
        reasons.append(f"تصنيفه الفرعي «{subgenre}» يوافق طلبك")
    return score, reasons


def author_matches(requested, stored):
    # Preserve the article in personal names: ماركيز is not الماركيز دو ساد.
    wanted = set(normalize(requested).split())
    actual = set(normalize(stored or "").split())
    return bool(wanted) and wanted <= actual


# A subgenre word that is really a mood (Ibrahim, 2026-10-04): models put «رومانسية» sometimes in
# moods and sometimes in subgenres. Both now mean the same: the mood ranks, a book whose subgenre
# names it ranks higher, and neither excludes a book.
MOOD_SUBGENRES = {"romantic": ["رومانسية", "رومانسي"], "mysterious": ["غموض"], "suspenseful": ["تشويق", "إثارة"],
                  "funny": ["كوميديا", "كوميدي"]}


def split_mood_subgenres(subgenres, moods):
    """-> (hard subgenres, soft subgenre words, moods incl. those named as a subgenre)."""
    table = {normalize(w): mood for mood, forms in MOOD_SUBGENRES.items() for w in forms}
    hard, moods = [], set(moods)
    for s in subgenres or []:
        hit = {table[w] for w in normalize(s).split() if w in table}
        if hit and all(w in table or w in ("روايه", "قصه", "قصص", "كتاب", "ادب") for w in normalize(s).split()):
            moods |= hit
        else:
            hard.append(s)
    soft = [w for mood in MOOD_SUBGENRES if mood in moods for w in MOOD_SUBGENRES[mood]]
    return hard, soft, moods


def matching_subgenres(book, prefs, key="subgenres"):
    def words(value):
        return {strip_al(w) for w in normalize(value).split()}
    wanted = [words(v) for v in prefs.get(key, [])]
    return [v for v in _json_list(book.get("subgenres"))
            if isinstance(v, str) and any(w and w <= words(v) for w in wanted)]


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
             "tokens": ai_tokens(ai), "normalized": normalized,
             "authors": ai.get("authors", []), "subgenres": ai.get("subgenres", []),
             "original_languages": set(ai.get("original_languages") or ()), "translated": ai.get("translated"),
             # prizes and screen adaptations (2026-10-08, backend/honors.py)
             "awards": list(ai.get("awards") or []), "award_year": ai.get("award_year"), "adaptation": ai.get("adaptation")}
    if prefs["original_languages"]:
        # «أدب ياباني» = Japanese literature of any kind, not the catalog genre «أدب» (2026-10-03)
        prefs["genres"].discard("أدب")
    prefs["subgenres"], prefs["soft_subgenres"], moods = split_mood_subgenres(prefs["subgenres"], ai["moods_include"])
    moods -= set(ai["moods_exclude"])
    pprefs = {"moods": moods, "pace": set(ai["pace"]),
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
    if r.startswith(("حائز على ", "له صلة بـ", "تحوّل إلى ", "المؤلف: ", "فاز ب", "مؤلفه ", "وصل إلى القائمة", "رُشّح ل")) or any(n in r for n in honors.AWARDS.values()):
        return honors.to_english(r)
    if r == "مترجم":
        return "translated"
    if r == "عربي غير مترجم":
        return "originally Arabic"
    for prefix, en in (("مترجم عن ", "translated from "), ("لغته الأصلية ", "originally written in ")):
        if r.startswith(prefix):
            return en + " or ".join(LANG_NAMES_EN.get(_code(x, LANG_NAMES), "that language") for x in r.removeprefix(prefix).split(" أو "))
    if r.startswith("من تصنيف «"):
        genre = r.split("»", 1)[0].split("«", 1)[-1]
        return GENRE_LABELS_EN.get(genre, "the category you named")
    if r.startswith("قصير نسبيًا، حوالي "):
        return "short, about " + r.removeprefix("قصير نسبيًا، حوالي ").replace(" صفحة", " pages")
    if r.startswith("عمل طويل، حوالي "):
        return "a longer read, about " + r.removeprefix("عمل طويل، حوالي ").replace(" صفحة", " pages")
    if r.startswith("من أعمال "):
        return "by " + r.removeprefix("من أعمال ")
    if r.startswith("يتناول «") or r.startswith("عنوانه عن «"):
        return "about " + r.split("«", 1)[1].rsplit("»", 1)[0]
    if r.startswith("نبذته تذكر «"):
        return "its summary mentions " + r.split("«", 1)[1].rsplit("»", 1)[0]
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


def origin_allowed(book, prefs):
    """The original language / translated wish of the visitor (2026-10-03). A book whose original
    language is unknown never satisfies a language wish: origins are not guessed."""
    langs = prefs.get("original_languages") or ()
    translated = prefs.get("translated")
    if not langs and not translated:
        return True
    edition = (book.get("language") or "ar").strip() or "ar"
    orig = (book.get("original_language") or "").strip()
    translator = bool((book.get("translator") or "").strip())
    # A copy printed in the wanted language counts only when its known origin does not say otherwise:
    # an English edition of a French novel is not "originally English" (audit 2026-10-05).
    in_language = edition != "ar" and edition in langs and orig in ("", edition)
    if langs and orig not in langs and not in_language:
        return False
    if translated == "yes":
        return edition == "ar" and (orig not in ("", "ar") or (not orig and translator))
    if translated == "no":
        if langs:  # "Italian, not translated": the copy itself is in that language
            return edition in langs and orig in ("", edition) and not translator
        return edition == "ar" and orig == "ar"
    return True


def origin_labels(prefs):
    langs = [l for l in llm.ORIGIN_LANGS if l in (prefs.get("original_languages") or ())]
    translated = prefs.get("translated")
    foreign = [LANG_NAMES.get(l, l) for l in langs if l != "ar"]
    out = []
    if foreign:
        out.append(("مترجم عن " if translated == "yes" else "لغته الأصلية ") + " أو ".join(foreign))
    elif translated == "yes":
        out.append("مترجم")
    if "ar" in langs or (translated == "no" and not foreign):
        out.append("عربي غير مترجم")
    return out


def wanted_genres(prefs, pprefs):
    genres = set(prefs.get("genres") or ())
    if genres and pprefs.get("audience") in GENRE_WITH_AUDIENCE:
        # "قصة لبنتي" = a children's story; "رواية لولدي المراهق" = a young-adult novel
        genres.add(GENRE_WITH_AUDIENCE[pprefs["audience"]])
    return genres


def meets_constraints(b, card, prefs, pprefs, exclusions, genres):
    """One book against every stated constraint of the visitor. The single test used by constrain()
    and by every other path that can put a book in front of the visitor (related books, books shown
    earlier, reference matches), so no path can break a stated wish (audit 2026-10-02/03)."""
    if not age_allowed(card, pprefs.get("audience"), pprefs.get("avoid") or (), b, pprefs.get("reader_age")):
        return False
    if not genres and pprefs.get("audience") not in AGE_OK and card and card.get("format") == "children":
        return False  # a request that names neither a genre nor a child gets no children's books
    if (exclusions["moods"] or exclusions["weight"]) and excluded_by_card(card, exclusions):
        return False
    if genres and b.get("genre") not in genres:
        return False
    if prefs.get("subgenres") and not matching_subgenres(b, prefs):
        return False
    if prefs.get("length") == "short" and is_long(b, card):
        return False
    if prefs.get("awards") and not honors.matches_award(b["book_id"], prefs["awards"], prefs.get("award_year"),
                                                       prefs.get("award_min_year"), prefs.get("award_max_year")):
        return False
    if prefs.get("adaptation") and not honors.matches_adaptation(b["book_id"], prefs["adaptation"]):
        return False
    return origin_allowed(b, prefs)


LENGTH_LABELS = {"short": "قصير", "medium": "متوسط الطول", "long": "طويل"}
LENGTH_LABELS_EN = {"short": "short", "medium": "medium length", "long": "long"}


def chat_context(prefs, pprefs, user_messages, publisher_name=None, country=None, topic_phrases=(), language="ar", edition=None, pub_years=None):
    """What the search is applying now, as removable badges above the suggestions (Ibrahim,
    2026-10-08: «ابغاه في كل المحادثات»): [{"key": "genre:رواية", "label": "رواية"}, ...].
    The visitor's ✕ sends the key back in `drop_context`."""
    en = language == "en"
    out = []
    def add(key, label):
        if label and key not in [x["key"] for x in out]:
            out.append({"key": key, "label": label})
    aud = pprefs.get("audience")
    if aud in ("children", "young_adult"):
        age, g = audience_age(user_messages), audience_gender(user_messages)
        if en:
            add("audience", ("a child" if aud == "children" else "a teen") + (f", age {age}" if age else ""))
        else:
            who = {"children": "طفلة" if g == "f" else "طفل", "young_adult": "يافعة" if g == "f" else "يافع"}.get(aud)
            add("audience", who and who + ((" عمرها " if g == "f" else " عمره ") + fmt_num(age) if age else ""))
    for g in sorted(prefs.get("genres") or ()):
        if not (aud and (g in CHILD_GENRES or g == "رواية")):   # «قصة لبنتي» is read as «رواية»: not shown
            add("genre:" + g, g)
    for s in prefs.get("subgenres") or ():
        add("subgenre:" + s, s)
    if prefs.get("length"):
        add("length", (LENGTH_LABELS_EN if en else LENGTH_LABELS).get(prefs["length"], prefs["length"]))
    langs = origin_labels(prefs)
    if langs:
        add("lang", "، ".join(langs))
    for m in sorted(pprefs.get("moods") or ()):
        add("mood:" + m, (MOOD_LABELS_EN if en else MOOD_LABELS_FEM).get(m, m))
    if prefs.get("awards"):
        add("awards", next((x for x in honor_labels(dict(prefs, adaptation=None))), "جائزة"))
        if prefs.get("award_min_year"):
            add("recent", f"since {prefs['award_min_year']}" if en else
                (f"من {fmt_num(prefs['award_min_year'])}" if prefs.get("award_years_said") else f"حديثة (من {fmt_num(prefs['award_min_year'])})"))
        if prefs.get("award_max_year"):
            add("before", f"until {prefs['award_max_year']}" if en else f"حتى {fmt_num(prefs['award_max_year'])}")
    if prefs.get("adaptation"):
        add("adaptation", next((x for x in honor_labels(dict(prefs, awards=[]))), ""))
    if edition:
        add("edition", {"en": ("in English", "بالإنجليزية"), "ar": ("in Arabic", "بالعربية")}[edition][0 if en else 1])
    if pub_years:
        lo, hi = pub_years
        if en:
            add("pubyear", "published " + (f"{lo}–{hi}" if lo and hi else f"from {lo}" if lo else f"until {hi}"))
        else:
            add("pubyear", "نُشر " + (f"من {fmt_num(lo)} إلى {fmt_num(hi)}" if lo and hi else f"من {fmt_num(lo)}" if lo else f"حتى {fmt_num(hi)}"))
    if publisher_name:
        add("publisher", publisher_name)
    if country:
        _names = []
        for n in country[1][:2]:   # «عُمان» and «عمان» are one place on the badge
            if normalize(n) not in [normalize(x) for x in _names]:
                _names.append(n)
        place = "الخليج" if country[0] == "خليجي" else " أو ".join(_names)
        add("country", ("author from " if en else "كاتب من ") + place)
    for p in topic_phrases or ():
        add("topic:" + p, (f"about «{p}»" if en else f"عن «{p}»"))
    return out[:10]


def honor_labels(prefs):
    """Arabic labels of the prize / adaptation wish («حائز على جائزة نوبل للآداب»، «تحوّل إلى فيلم»)."""
    out = []
    keys = [k for k in prefs.get("awards") or [] if k != "any"]
    if prefs.get("awards"):
        name = " أو ".join(honors.AWARDS.get(k) or ("جائزة " + k[5:] if k.startswith("name:") else k) for k in keys) if keys else "جائزة"
        out.append(("حائز على " if not keys else "له صلة بـ") + name + (f" {prefs['award_year']}" if prefs.get("award_year") else ""))
    if prefs.get("adaptation"):
        out.append({"film": "تحوّل إلى فيلم", "series": "تحوّل إلى مسلسل"}.get(prefs["adaptation"], "تحوّل إلى فيلم أو مسلسل"))
    return out


def constrain(books, prefs, pprefs, exclusions, cards):
    """-> (books that satisfy every stated constraint, Arabic labels of those constraints)."""
    labels = []
    audience = pprefs.get("audience")
    avoid = pprefs.get("avoid") or ()
    genres = wanted_genres(prefs, pprefs)
    out = [b for b in books if meets_constraints(b, cards.get(b["book_id"]), prefs, pprefs, exclusions, genres)]
    if prefs.get("genres"):
        labels.append("، ".join(prefs["genres"]))
    if prefs.get("subgenres"):
        labels.append("، ".join(prefs["subgenres"]))
    if prefs.get("length") == "short":
        labels.append("قصير")
    labels += origin_labels(prefs)
    labels += honor_labels(prefs)
    if audience in AGE_OK:
        labels.append("مناسب " + AGE_LABELS[audience])
    if exclusions["moods"]:
        labels.append("بدون أجواء " + "، ".join(MOOD_LABELS_FEM.get(m, m) for m in exclusions["moods"]))
    if exclusions["weight"]:
        labels.append("مو " + "، ".join(WEIGHT_LABELS_FEM.get(w, w) for w in exclusions["weight"]))
    if avoid:
        labels.append("بدون " + "، ".join(SENSITIVE_LABELS.get(a, a) for a in avoid))
    return out, labels


def constrain_relaxing(books, prefs, pprefs, exclusions, cards):
    """constrain(); when a named subgenre leaves nothing, drop only that wish (Ibrahim, 2026-10-03):
    the books that keep every other constraint are offered as suggestions and the visitor is told
    the subgenre could not be confirmed. -> (books, labels, prefs in force, dropped subgenres)."""
    out, labels = constrain(books, prefs, pprefs, exclusions, cards)
    if out or not prefs.get("subgenres"):
        return out, labels, prefs, []
    relaxed = dict(prefs, subgenres=[])
    alt, alt_labels = constrain(books, relaxed, pprefs, exclusions, cards)
    if not alt:
        return out, labels, prefs, []
    return alt, alt_labels, relaxed, list(prefs["subgenres"])


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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"results": [], "error": "invalid request"}), 400
    if not limits.allow_request(request.remote_addr):
        return jsonify({"results": [], "message": RATE_LIMITED}), 429
    query = str(data.get("query", "")).strip()[:500]
    exclude = data.get("exclude") if isinstance(data.get("exclude"), list) else []
    exclude = {int(i) for i in exclude if str(i).isdigit()}

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
    books, labels, prefs, relaxed_subgenres = constrain_relaxing(books, prefs, pprefs, exclusions, cards)
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

    if relaxed_subgenres:
        notes.insert(0, "ما لقينا كتابًا متوفرًا بتصنيف «" + "، ".join(relaxed_subgenres) + "» ضمن طلبك، فهذي اقتراحات تلتزم بباقي الطلب"
                     + (" (" + "، ".join(labels) + ")" if labels else "") + " وما نقدر نؤكد أنها من هذا التصنيف.")
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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    if not limits.allow_request(request.remote_addr):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    book_id = clean_id(data.get("book_id"))
    if book_id is None:
        return jsonify({"ok": False, "error": "unknown book"}), 400
    book = get_book(book_id)
    if book is None or not is_available(dict(book)):
        return jsonify({"ok": False, "error": "unknown book"}), 400
    choice_id = insights.record_choice(book_id, data.get("language"), data.get("match"))
    testlog.record("choice", {"conversation_id": testlog.clean_id(data.get("conversation_id")),
                              "book_id": book_id, "choice_id": choice_id})
    return jsonify({"ok": True, "choice_id": choice_id, "choice_token": choice_token(choice_id)}), 201


_ID_DIGITS = re.compile(r"[0-9]{1,10}\Z")


def clean_id(value):
    """A record id sent by a visitor, or None. Plain ASCII digits only: «²» and Arabic-Indic digits pass
    str.isdigit() but not int(), and a number past SQLite's range raises at the query (audits 2026-10-04/05)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and _ID_DIGITS.match(value):
        n = int(value)
    else:
        return None
    return n if 0 < n < 2 ** 31 else None


def choice_token(choice_id):
    """Proof that the caller is the visitor who made this choice: a keyed hash of the choice id, handed
    out only in the answer to «أبغى هذا الكتاب». Choice ids are sequential and guessable; the token is
    not. Nothing about the visitor is stored (audit 2026-10-02: ratings must belong to a real choice)."""
    key = app.secret_key if isinstance(app.secret_key, bytes) else str(app.secret_key).encode()
    return hmac.new(key, b"choice:" + str(int(choice_id)).encode(), hashlib.sha256).hexdigest()[:32]


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


@app.route("/api/admin/version")
def admin_version():
    """The version shown in الإعدادات → عن التطبيق; null when the VERSION file is missing."""
    return jsonify({"version": APP_VERSION})


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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    if not limits.allow_feedback(request.remote_addr):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    rating = data.get("rating")
    if not isinstance(rating, str) or rating not in FEEDBACK_RATINGS:
        return jsonify({"ok": False, "error": "invalid rating"}), 400
    # A rating belongs to a choice made with «أبغى هذا الكتاب» by the same visitor: the choice id and the
    # token that came with it are both required, and each choice takes one rating (2026-10-03).
    choice_id = clean_id(data.get("choice_id"))
    if choice_id is None:
        return jsonify({"ok": False, "error": "choice required"}), 400
    token = data.get("choice_token")  # compare_digest refuses non-ASCII text, so it is checked first
    if not isinstance(token, str) or not token.isascii() or not hmac.compare_digest(token, choice_token(choice_id)):
        return jsonify({"ok": False, "error": "unknown choice"}), 400
    status = insights.choice_status(choice_id)
    if status is None:
        return jsonify({"ok": False, "error": "unknown choice"}), 400
    if status[1]:
        return jsonify({"ok": False, "error": "already rated"}), 409
    insights.add_rating(choice_id, status[0], rating)
    testlog.record("feedback", {"conversation_id": testlog.clean_id(data.get("conversation_id")),
                                "rating": rating, "book_id": status[0], "choice_id": choice_id})
    return jsonify({"ok": True}), 201


# ---------------------------------------------------------------------------
# Visitor reports about a book's information, and visitor suggestions (2026-10-03).
# Anonymous; a report never changes the book. Shown in admin → «البلاغات».
# ---------------------------------------------------------------------------
@app.route("/api/report", methods=["POST"])
def report_book():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    if not limits.allow_note(request.remote_addr):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    book_id = clean_id(data.get("book_id"))
    book = get_book(book_id) if book_id is not None else None
    if book is None:
        return jsonify({"ok": False, "error": "unknown book"}), 400
    try:
        visitor_notes.add_report(dict(book), data.get("reason"), data.get("note"), data.get("language"))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True}), 201


@app.route("/api/suggestion", methods=["POST"])
def suggestion():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    if not limits.allow_note(request.remote_addr):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    try:
        visitor_notes.add_suggestion(data.get("note"), data.get("language"))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True}), 201


@app.route("/api/admin/inbox")
def admin_inbox():
    """Reports and suggestions for staff and managers. ?kind=report|suggestion &status=open|done|all"""
    kind = request.args.get("kind", "report")
    if kind not in visitor_notes.KINDS:
        kind = "report"
    status = request.args.get("status", "open")
    offset = request.args.get("offset", "0")
    offset = int(offset) if offset.isdigit() else 0
    items = visitor_notes.list_notes(kind, status, visitor_notes.PAGE, offset)
    for it in items:
        if it.get("cover_url") and not covers.has_local_cover(it):
            it["cover_url"] = None
    total = visitor_notes.total(kind, status)
    return jsonify({"counts": visitor_notes.counts(), "kind": kind, "items": items, "total": total,
                    "offset": offset, "has_more": offset + len(items) < total})


@app.route("/api/admin/inbox/export")
def admin_inbox_export():
    """All of «البلاغات» in one Markdown file (Ibrahim, 2026-10-09): ?status=open|done|all"""
    user = acting_user() or {}
    status = request.args.get("status", "open")
    body, counts = inbox_export.build(status, user.get("display_name"))
    auth.audit(user or None, "inbox.export", status if status in ("open", "done", "all") else "open", counts)
    stamp = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))
    resp = app.response_class(body, mimetype="text/markdown")
    resp.headers["Content-Disposition"] = f'attachment; filename="thalath-inbox-{stamp}.md"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/admin/inbox/count")
def admin_inbox_count():
    return jsonify(dict(visitor_notes.counts(), staff_open=staff_notes.counts()["open"]))


@app.route("/api/admin/inbox/<int:note_id>", methods=["POST"])
def admin_inbox_update(note_id):
    data = request.get_json(silent=True) or {}
    user = acting_user()
    try:
        note = visitor_notes.set_status(note_id, data.get("status"), user["display_name"] if user else None)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if note is None:
        return jsonify({"error": "غير موجود"}), 404
    auth.audit(user, "inbox." + note["status"], note_id, {"kind": note["kind"], "book_id": note["book_id"]})
    return jsonify({"ok": True, "counts": visitor_notes.counts()})


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
        # `other` = translated, original language not in our list or not known (staff list «أصل الكتاب», 2026-10-04)
        label = "Translated" if orig == "other" else f"Translated from {LANG_NAMES_EN.get(orig, orig)}"
        translator = (book.get("translator") or "").strip()
        return label + (f", translated by {translator}" if translator else "")
    if edition != "ar":
        return f"كتاب باللغة {LANG_NAMES.get(edition, edition)} (غير مترجم للعربية)"
    if not orig:
        return None
    if orig == "ar":
        return "عمل عربي أصلي"
    label = "مترجم" if orig == "other" else f"مترجم عن {LANG_NAMES.get(orig, orig)}"
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
        if "age_min" in data or "age_max" in data:
            lo = data.get("age_min", None)
            hi = data.get("age_max", None)
            lo = None if lo in ("", None) else int(lo)
            hi = None if hi in ("", None) else int(hi)
            if (lo is not None and not 0 <= lo <= 18) or (hi is not None and not 0 <= hi <= 18) \
                    or (lo is not None and hi is not None and lo > hi):
                raise ValueError
            clean["age_min"], clean["age_max"] = lo, hi
            clean["age_source"] = "staff" if (lo is not None or hi is not None) else None
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
    if book.get("publication_year"):
        fact["year"] = book["publication_year"]   # year of this edition, «after 2020» (QA2 2026-10-09)
    if book.get("language") in ("en", "ar"):
        fact["copy_language"] = {"en": "English", "ar": "Arabic"}[book["language"]] if language == "en" else \
            {"en": "الإنجليزية", "ar": "العربية"}[book["language"]]
    if book.get("age_min") is not None or book.get("age_max") is not None:
        fact["age_range"] = (f"ages {book.get('age_min') if book.get('age_min') is not None else '?'}–{book.get('age_max') if book.get('age_max') is not None else '+'}"
                             if language == "en" else AGE_RANGE_AR(book.get("age_min"), book.get("age_max")))
    if book.get("summary"):
        fact["summary"] = book["summary"]
    # Card v2 book-level facts (2026-09-28): what the book is about, never phrased as the "card"
    # prizes and screen adaptations from our records, each with a source (2026-10-08)
    h = honors.for_book(book["book_id"])
    tr = honors.to_english if language == "en" else (lambda x: x)
    if h["awards"]:
        fact["awards"] = [tr(x) for x in h["awards"][:4]]
    if h["author_awards"]:
        fact["author_awards"] = [tr(x) for x in h["author_awards"][:3]]
    if h["adaptations"]:
        fact["adaptations"] = [tr(x) for x in h["adaptations"][:3]]
    if book.get("hook"):
        fact["hook"] = book["hook"]
    for key in ("subjects", "subgenres", "themes"):
        vals = _json_list(book.get(key)) if key != "themes" else list((card or {}).get("themes") or [])
        vals = [v for v in vals if isinstance(v, str)][:6]
        if vals:
            fact[key] = vals
    if book.get("setting"):
        fact["setting"] = book["setting"]
    if book.get("series_name"):
        fact["series"] = book["series_name"] + (f" #{book['series_position']}" if book.get("series_position") else "")
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
    if "sexual_content" in (card.get("sensitive_content") or []):  # the only age statement (2026-10-04)
        items.append(("sensitive_content", "sexual_content", "Not suitable for children" if language == "en" else "لا يناسب الأطفال"))
    items += [("sensitive_content", c, ("Content note: " if language == "en" else "تنبيه محتوى: ") + sensitive_label(c))
              for c in card.get("sensitive_content") or [] if c != "none"]
    for attr, value, label in items:
        tier = card_tier(card, attr, value)
        if tier:
            fact[tier].append(label)
    return fact


CHILD_GENRES = {"أطفال", "يافعين"}

# «أبغاه بالإنجليزي»، "English novels" in the English chat: the language of the copy on the shelf
# (books.language), not the language the work was written in (Ibrahim 2026-10-09 «اضيفه»)
_EDITION = {"en": ["بالانجليزي", "بالانجليزيه", "بالانقليزي", "باللغه الانجليزيه", "نسخه انجليزيه", "نسخته الانجليزيه",
                   "طبعه انجليزيه", "كتب انجليزيه", "روايات انجليزيه", "in english", "english edition", "english version",
                   "english books", "english novels", "english book", "english novel"],
            "ar": ["بالعربي", "بالعربيه", "باللغه العربيه", "نسخه عربيه", "نسخته العربيه", "طبعه عربيه",
                   "in arabic", "arabic edition", "arabic version", "arabic translation"]}


def wanted_edition(text, language="ar"):
    """-> "en" / "ar" / None. In the English chat «English novels» means books in English."""
    words = normalize(text).split()
    for code, phrases in _EDITION.items():
        if any(keyword_prefs.find_phrase(words, p) >= 0 for p in phrases):
            return code
    if language == "en" and "english" in words:
        return "en"
    return None


def age_fit(book, age):
    """-> 1 the book's age range includes the reader's age, 0 unknown, -1 clearly not for that age
    (one year of slack on each side: a 9-year-old may read a 10+ book)."""
    lo, hi = book.get("age_min"), book.get("age_max")
    if age is None or (lo is None and hi is None):
        return 0
    if (lo is not None and age < lo - 1) or (hi is not None and age > hi + 1):
        return -1
    return 1


AGE_RANGE_AR = lambda lo, hi: (f"من {fmt_num(lo)} إلى {fmt_num(hi)} سنوات" if lo is not None and hi is not None
                               else f"من {fmt_num(lo)} سنوات وفوق" if lo is not None else f"حتى {fmt_num(hi)} سنوات")

# «كتاب فاز بجائزة حديثًا»، «الأحدث»: a prize of the last few years (library trial, 2026-10-08)
_RECENT_WORDS = {normalize(w) for w in "حديث حديثه حديثة حديثا حديثاً مؤخرا مؤخراً الاحدث الأحدث احدث أحدث جديد جديده جديدة الجديده الجديدة "
                 "الاخيره الأخيرة الاخيرة recent recently latest newest".split()}
RECENT_YEARS = 5


def wants_recent(texts):
    return any(w in _RECENT_WORDS for t in texts for w in normalize(t).split())


_YEAR_BEFORE = re.compile(r"(?:قبل|before|prior to|until|till)\s+(?:عام|سنه|year|the year)?\s*(1[89]\d\d|20\d\d)")
_YEAR_AFTER = re.compile(r"(?:بعد|after|since)\s+(?:عام|سنه|year|the year)?\s*(1[89]\d\d|20\d\d)")
_YEAR_BETWEEN = re.compile(r"(?:بين|between)\s+(?:عام|عامي|سنه|سنتي)?\s*(1[89]\d\d|20\d\d)\s*(?:و|and|to|الى|-)\s*(1[89]\d\d|20\d\d)")
_NEW_RELEASE = ["صدر حديثا", "صادر حديثا", "نشر حديثا", "منشور حديثا", "الاصدارات الجديده", "اصدارات جديده", "اصدار جديد",
                "اخر الاصدارات", "احدث الاصدارات", "new release", "new releases", "recently published", "published recently",
                "latest releases"]
NEW_RELEASE_YEARS = 3


def years_said(text):
    """«قبل ٢٠١٥» -> (None, 2014); «بعد 2020» -> (2021, None); «since 2020» -> (2020, None);
    «بين ٢٠١٠ و٢٠١٥» -> (2010, 2015). (QA2 2026-10-09: prizes before a year, publication year)"""
    t = " ".join(normalize(text).split())
    m = _YEAR_BETWEEN.search(t)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return a, b
    lo = hi = None
    m = _YEAR_BEFORE.search(t)
    if m:
        hi = int(m.group(1)) - 1
    m = _YEAR_AFTER.search(t)
    if m:
        lo = int(m.group(1)) + (0 if "since" in m.group(0) else 1)
    return lo, hi


def new_release_said(text):
    words = normalize(text).split()
    return any(keyword_prefs.find_phrase(words, normalize(p)) >= 0 for p in _NEW_RELEASE)


# «جائزة الملك فيصل»: a prize named by the visitor that has no key of its own (QA2 2026-10-09, case 34)
_PRIZE_NAMED = re.compile(r"(?:جائزه|جايزه|جوائز|بجائزه|بجايزه|prize|award)\s+((?:\S+\s*){1,4})")
_PRIZE_STOP = {normalize(w) for w in "عندكم عندك عندكم؟ وش ايش هل في موجود متوفر كتاب كتب رواية روايه لكاتب للكاتب حديثه حديثا "
               "عالميه عربيه ادبيه دوليه كبيره مهمه معروفه مشهوره قديمه الجديده جديده او ولا of for the".split()}


def prize_named(text):
    """-> the prize name as the visitor wrote it after «جائزة», when such a prize is stored under «other»."""
    m = _PRIZE_NAMED.search(" ".join(normalize(text).split()))
    if not m:
        return None
    words = []
    for w in m.group(1).split():
        if w in _PRIZE_STOP:
            break
        words.append(w)
    for n in range(len(words), 0, -1):
        phrase = " ".join(words[:n])
        if len(phrase) >= 4 and honors.name_exists(phrase):
            return phrase
    return None


def only_recency(phrase):
    words = [w for w in normalize(phrase).split() if w not in topics.AWARD_WORDS]
    return bool(words) and all(w in _RECENT_WORDS or strip_al(w) in _RECENT_WORDS for w in words)


# kinds of books we keep for children and for adults alike: after a child, «كتب تاريخ» is asked
# about, not guessed (Ibrahim, 2026-10-08: «للطفل والا للكبار»)
SHARED_GENRES = {"تاريخ", "علوم", "دين", "قصص مصورة", "شعر", "أدب رحلات"}
AUDIENCE_ASK_AR = "للطفل ولا للكبار"
_ADULT_SAID = ["للكبار", "الكبار", "كبار", "لي انا", "لنفسي", "for me", "myself", "adult", "adults", "grown up", "grownups"]


_CHILD_REF = ["لها", "له", "لبنتي", "لولدي", "لابني", "لطفلي", "لطفلتي", "للطفل", "للطفله", "للبنت", "للولد",
              "for her", "for him", "for the child", "for my"]


def child_said(text):
    """«وكتب علوم لها»: the request is for the child named before."""
    words = normalize(text).split()
    return bool(detect_audience(normalize(text))) or any(w in words for w in (normalize(x) for x in _CHILD_REF if " " not in x)) \
        or any(keyword_prefs.find_phrase(words, x) >= 0 for x in _CHILD_REF if " " in x)


def _ages(text):
    return [int(m.group(2) or m.group(3)) for m in _AGE_NUM.finditer(normalize(text))]


def adult_said(text):
    words = normalize(text).split()
    return (any(keyword_prefs.find_phrase(words, w) >= 0 for w in _ADULT_SAID)
            or any(age >= 18 for age in _ages(text)))   # «فوق ١٨ سنة»


# «لبنتي»، «ابني»: a child of the visitor whose age we do not know yet -> ask it (Ibrahim, 2026-10-08)
# «طفلي» already says «a child»; the age is asked only for a son or a daughter, who may be any age
_KID_F = ["بنتي", "ابنتي", "بناتي"]
_KID_M = ["ابني", "ولدي", "اولادي", "عيالي", "ابنائي"]
AGE_ASK_AR = "كم عمر"


def kid_mentioned(user_messages):
    """-> "f", "m" or None: the visitor named his daughter / son (any message)."""
    found = None
    for text in user_messages:
        for w in normalize(text).split():
            for v in keyword_prefs._variants(w):
                if v in _KID_F:
                    return "f"
                if v in _KID_M:
                    found = "m"
    return found


def audience_gender(user_messages):
    text = " ".join(normalize(t) for t in user_messages)
    if kid_mentioned(user_messages) == "f" or " عمرها" in " " + text or " طفلتي" in " " + text or " لطفلتي" in " " + text:
        return "f"
    return "m" if (kid_mentioned(user_messages) or " عمره" in " " + text) else None


# a subject asked for a child, looked for inside the children's and young readers' shelves:
# we keep no «history for children» category, but children's books about history exist (2026-10-08)
CHILD_THEMES = {
    "تاريخ": "تاريخ تاريخي تاريخيه حضاره حضارات سيره شخصيات الانبياء الصحابه معركه معارك الحرب مدن القديمه العصور الاندلس الفتوحات",
    "علوم": "علوم علمي فضاء الكون الكواكب جسم الحيوانات النباتات البيئه تجارب اختراعات الطبيعه",
    "دين": "الانبياء نبي الرسول القران الصلاه الاسلام الصحابه الاخلاق الحج رمضان",
    "شعر": "شعر قصائد اناشيد",
    "أدب رحلات": "رحله رحلات سفر البلدان المدن",
}


def audience_age(user_messages):
    """The child's age if the visitor said it («عمرها ٦»), for the «we are searching for» badge."""
    for text in reversed(user_messages):
        for m in _AGE_NUM.finditer(normalize(text)):
            age = int(m.group(2) or m.group(3))
            if 2 <= age <= 17:
                return age
    return None


def chat_audience(user_messages, ai_prefs, author_named, cleared=False):
    """Who the reader is. The latest message decides; an age said earlier («قصص أطفال») carries on
    only while the visitor keeps to that kind of request, not after he asks for adult biographies
    or an author (library trial, 2026-10-08: «سيرة ذاتية» after «قصص أطفال» found nothing)."""
    last = detect_audience(normalize(user_messages[-1]))
    if last:
        return last
    if cleared or adult_said(user_messages[-1]):
        # the visitor removed the «for a child» badge, or said «لي» / «للكبار»
        return None
    # only a child or a teenager changes the search; the interpreter's «adult» (a gift for a wife or
    # a father) means no special reader (QA2 2026-10-09: «هدية لزوجتي» crashed on KeyError('adult'))
    if ai_prefs.get("audience") not in ("children", "young_adult"):
        ai_prefs = dict(ai_prefs, audience=None)
    # «قصص» is read by the interpreter as the genre «رواية»: a novel / story wish does not leave the
    # child behind (QA2 2026-10-09, cases 10, 15, 16); only another kind of book or an author does
    genres = set(ai_prefs.get("genres") or []) - {"رواية"}
    # a kind of book the visitor asked for FOR the child earlier («بنتي ١٣ سنة وتحب القصص المصورة»)
    # is still for her when asked again («لا، ابي قصص مصورة ومانجا بالذات») (QA2 re-run, case 11)
    for t in user_messages[:-1]:
        if detect_audience(normalize(t)) or child_said(t):
            genres -= set(local_understand([t])["genres"])
    if child_said(user_messages[-1]) and not author_named:
        # «وعندكم شي عن الحيوانات له؟»: whatever kind of book the interpreter read in it, the
        # reader is still the child named before (QA 2026-10-08, cases 8 and 9)
        carried = ai_prefs.get("audience") if ai_prefs.get("audience") in ("children", "young_adult") else None
        earlier = carried or detect_audience(normalize(" ".join(user_messages[:-1])))
        if earlier:
            return earlier
    moved_on = author_named or (genres and not genres & CHILD_GENRES)
    if ai_prefs.get("audience"):
        # the interpreter may carry «children» on from an earlier message
        return None if moved_on and ai_prefs["audience"] in ("children", "young_adult") else ai_prefs["audience"]
    earlier = detect_audience(normalize(" ".join(user_messages[:-1])))
    if not earlier or moved_on:
        return None
    return earlier


_PUB_WORDS = {normalize(w) for w in "دار منشورات مكتبه مكتبة للنشر النشر والتوزيع التوزيع والنشر شركه مؤسسه الناشر ناشر publishing publisher press books".split()}


def publisher_key(name):
    words = [w for w in normalize(name or "").split() if w not in _PUB_WORDS]
    return " ".join(strip_al(w) if len(words) == 1 else w for w in words)


def find_publisher(unsupported, last_message, all_books):
    """«دار صفصافة»، «من منشورات الساقي»: a publisher we stock, named with «دار/منشورات».
    -> (key, publisher name as stored) or None (library trial, 2026-10-08)."""
    names = {}
    for b in all_books.values():
        k = publisher_key(b.get("publisher"))
        if len(k) >= 3 and is_available(b):
            names.setdefault(k, b.get("publisher"))
    said = [u for u in unsupported if set(normalize(u).split()) & _PUB_WORDS]
    words = normalize(last_message).split()
    for i, w in enumerate(words):
        if w in ("دار", "منشورات", "الدار", "ناشر", "الناشر"):
            said += [" ".join(words[i + 1:i + 1 + n]) for n in (3, 2, 1)]
    for phrase in said:
        k = publisher_key(phrase)
        if k in names:
            return k, names[k]
    return None


# «كاتب سعودي»، «رواية مصرية»: the author's country from his record (authors.origin), approved by
# Ibrahim 2026-10-08 19:31 («اعتمده الاضافة»). Arab nationalities always mean the author (the
# language cannot tell them apart); others only with «كاتب / مؤلف / روائي ...» before them, since
# «رواية روسية» is already the original language.
_ARAB_NATIONS = {"سعودي": ["السعودية"], "مصري": ["مصر"], "عراقي": ["العراق"], "سوري": ["سوريا"],
                 "لبناني": ["لبنان"], "مغربي": ["المغرب"], "تونسي": ["تونس"], "جزائري": ["الجزائر"],
                 "كويتي": ["الكويت"], "اماراتي": ["الإمارات"], "عماني": ["عُمان", "عمان"], "يمني": ["اليمن"],
                 "فلسطيني": ["فلسطين"], "اردني": ["الأردن"], "سوداني": ["السودان"], "ليبي": ["ليبيا"],
                 "قطري": ["قطر"], "بحريني": ["البحرين"], "موريتاني": ["موريتانيا"],
                 "خليجي": ["السعودية", "الكويت", "الإمارات", "قطر", "البحرين", "عُمان"]}
_OTHER_NATIONS = {"ياباني": ["اليابان"], "روسي": ["روسيا"], "فرنسي": ["فرنسا"], "امريكي": ["الولايات المتحدة"],
                  "بريطاني": ["المملكة المتحدة", "بريطانيا"], "انجليزي": ["المملكة المتحدة", "بريطانيا"],
                  "الماني": ["ألمانيا"], "ايطالي": ["إيطاليا"], "اسباني": ["إسبانيا"], "تركي": ["تركيا"],
                  "ايراني": ["إيران"], "كولومبي": ["كولومبيا"], "برازيلي": ["البرازيل"], "هندي": ["الهند"],
                  "صيني": ["الصين"], "كوري": ["كوريا"], "ارجنتيني": ["الأرجنتين"], "تشيلي": ["تشيلي"],
                  "مكسيكي": ["المكسيك"], "سويدي": ["السويد"], "نرويجي": ["النرويج"], "كندي": ["كندا"]}
_WRITER_WORDS = {normalize(w) for w in "كاتب كاتبه كاتبة كتاب كتّاب مؤلف مؤلفه مؤلفة مؤلفين روائي روائيه روائية روائيين اديب اديبه اديبة ادباء شاعر شاعره شاعرة شعراء لكاتب لكاتبه لمؤلف لروائي لاديب لشاعر".split()}


_PLACE_WORDS = {normalize(w) for w in "الدولة دولة الدول المملكة مملكة الحكومة حكومة الجمهورية جمهورية الإمارة إمارة "
                 "المنطقة البلاد الأراضي الأسرة العائلة الثورة الحرب الحدود الصحراء الجزيرة القومية الهوية العملة "
                 "الشركة الجامعة الدولة،".split()}


def _nation_key(word):
    if word in ("تركي", "هندي"):   # also given names («تركي الحمد»): only «تركية»، «هندية» count
        return None
    for v in keyword_prefs._variants(word):
        for cut in ("يين", "ين", "ون", "ه"):
            if v.endswith(cut) and len(v) - len(cut) >= 3:
                v2 = v[:-len(cut)]
                if v2 in _ARAB_NATIONS or v2 in _OTHER_NATIONS:
                    return v2
        if v in _ARAB_NATIONS or v in _OTHER_NATIONS:
            return v
    return None


def wished_country(text):
    """-> (adjective, [country names]) for «كاتب سعودي»، «روايات مصرية»، «لكاتبة كويتية», or None."""
    words = normalize(text).split()
    for i, w in enumerate(words):
        k = _nation_key(w)
        if not k:
            continue
        if i and words[i - 1] in _PLACE_WORDS:
            continue   # «تاريخ الدولة السعودية»، «المملكة المغربية»: a subject, not the author (QA2 2026-10-09)
        if k in _ARAB_NATIONS:
            return k, _ARAB_NATIONS[k]
        if any(x in _WRITER_WORDS for x in words[max(0, i - 2):i]):
            return k, _OTHER_NATIONS[k]
    return None


_origin_cache = {"at": 0.0, "map": None}


def book_origins():
    """{book_id: normalized origin text of its authors} from the author records, cached 2 minutes."""
    if _origin_cache["map"] is not None and time.time() - _origin_cache["at"] < 120:
        return _origin_cache["map"]
    out = {}
    conn = get_connection()
    try:
        for r in conn.execute("SELECT ba.book_id, a.origin FROM book_authors ba JOIN authors a USING(author_id) "
                              "WHERE ba.role='author' AND a.origin IS NOT NULL AND a.origin != ''"):
            out[r["book_id"]] = out.get(r["book_id"], "") + " | " + normalize(r["origin"])
    except sqlite3.Error:
        out = {}
    finally:
        conn.close()
    _origin_cache.update(at=time.time(), map=out)
    return out


def _title_fuzzy(title, mention):
    """«قراءة لوليتا في طهران» ~ «أن تقرأ لوليتا في طهران», «من نجد الي المجد» ~ «من نجد للمجد»:
    all but one meaningful word of each side found in the other (at least two)."""
    tw, mw = topics.content_words(title), topics.content_words(mention)
    if len(tw) < 2 or len(mw) < 2:
        return False
    hit_t = sum(1 for f in tw if any(f & g for g in mw))
    hit_m = sum(1 for g in mw if any(f & g for f in tw))
    return hit_t >= max(2, len(tw) - 1) and hit_m >= max(2, len(mw) - 1)


def match_titles(mentions, all_books, max_rank=3):
    """Books a mentioned title may mean, the closest first: the same title, then a title that
    starts with it, then one that contains it as whole words. Two per mention, so the second
    title of «العمى ولا 1984؟» is not pushed out by loose matches of the first (2026-10-07)."""
    found = []
    author_names = None
    for m in mentions:
        nm = " ".join(normalize(m).split())
        if len(nm) < 3:
            continue
        # «ثلاثية نجيب محفوظ»: an author named inside the mention keeps the match to his books, and
        # the rest may be a series name (QA2 2026-10-09, case 45: Jon Fosse's «ثلاثية» was taken)
        if author_names is None:
            author_names = {}
            for b in all_books.values():
                na = " ".join(normalize(author_flow.display(b.get("author"))).split())
                if len(na.split()) >= 2:
                    author_names.setdefault(na, []).append(b)
        books, own = all_books.values(), None
        for na, theirs in author_names.items():
            hit = next((x for x in (" " + na + " ", " ل" + na + " ") if x in " " + nm + " "), None)
            if hit and len(nm) > len(na) + 2:
                rest = " ".join((" " + nm + " ").replace(hit, " ").split())
                rest = " ".join(w for w in rest.split() if w not in ("ل", "حق", "تبع", "by", "of"))
                if len(rest) >= 3:
                    nm, books, own = rest, theirs, theirs
                    break
        if own:
            rw = {strip_al(w) for w in nm.split()}
            series = [b for b in own if b.get("series_name")
                      and rw <= {strip_al(w) for w in normalize(b["series_name"]).split()}]
            if series:
                series.sort(key=lambda b: (b.get("series_position") or 99, not is_available(b)))
                for b in series[:4]:
                    if b["book_id"] not in [x["book_id"] for x in found]:
                        found.append(b)
                continue
        ranked = []
        for b in books:
            nt = " ".join(normalize(b["title"]).split())
            if not nt:
                continue
            if nt == nm:
                rank = 0
            elif _title_fuzzy(nt, nm) and not nt.startswith(nm + " "):
                # the visitor's wording of the whole title («لوليتا في طهران» ~ «أن تقرأ لوليتا في
                # طهران») before a short title the message starts with («لوليتا») (QA 2026-10-08)
                rank = 0.5
            elif nt.startswith(nm + " ") or nm.startswith(nt + " "):
                rank = 1
            elif (" " + nm + " ") in (" " + nt + " ") or (" " + nt + " ") in (" " + nm + " "):
                rank = 2
            elif len(nm) >= 5 and (nt in nm or nm in nt):
                rank = 3
            else:
                continue
            if rank > max_rank or (rank > 0 and len(nt) < 4):
                continue   # a very short title («لا») only matches exactly
            ranked.append((rank, not is_available(b), b["book_id"], b))
        ranked.sort(key=lambda x: x[:3])
        best = ranked[0][0] if ranked else None
        for b in [b for rank, _, _, b in ranked if rank == best][:2]:
            if b["book_id"] not in [x["book_id"] for x in found]:
                found.append(b)
    return found[:4]


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


def _words(text):
    """Arabic-aware word set for loose theme matching (definite article dropped)."""
    out = set()
    for w in normalize(text or "").split():
        w = w[2:] if w.startswith("ال") and len(w) > 4 else w
        if len(w) >= 3:
            out.add(w)
    return out


_COMMON_NAMES = {"محمد", "احمد", "عبدالله", "علي", "حسن", "حسين", "ابراهيم", "الله", "خالد", "سعيد"}


def _same_author(a, b):
    """Normalized names; transliterations differ in first names (يوستاين/جوستاين غاردر), so a
    shared, uncommon family name of 4+ letters also counts."""
    if a == b:
        return True
    wa, wb = a.split(), b.split()
    if len(wa) < 2 or len(wb) < 2:
        return False
    if set(wa) <= set(wb) or set(wb) <= set(wa):
        return True
    last = wa[-1]
    return last == wb[-1] and len(last) >= 4 and last not in _COMMON_NAMES


def reference_matches(ref, all_books, cards, suitable, profiles_format):
    """Books in stock that share the author of, or resemble, a book the visitor named as a
    reference (card v2, 2026-09-28). Uses the model's general description of the reference only
    to search our stock; each match carries the reason shown to the visitor.
    -> [(book, why, "reference")] best first."""
    out, seen = [], set()
    ra = normalize(ref.get("author") or "")
    if ra:
        ra_words = set(ra.split())
        for b in all_books.values():
            ba = normalize(b.get("author") or "")
            if not ba or ba in ("غير معروف", "unknown") or not is_available(b) or b["book_id"] not in suitable:
                continue
            if _same_author(ra, ba):
                if normalize(b["title"]) != normalize(ref["title"]):
                    out.append((b, f"من كتب {b['author']} نفسه، كاتب «{ref['title']}»", "reference"))
                    seen.add(b["book_id"])
    out = out[:3]  # a few by the same author, leaving room for resembling books
    ref_themes = [(t, _words(t)) for t in ref.get("themes") or []]
    scored = []
    for bid, card in cards.items():
        b = all_books.get(bid)
        if not b or bid in seen or not is_available(b) or bid not in suitable:
            continue
        if normalize(b["title"]) == normalize(ref["title"]):
            continue
        moods = [m for m in ref.get("moods") or [] if m in (card.get("moods") or [])]
        themes = []
        for label, words in ref_themes:
            for t in card.get("themes") or []:
                if words & _words(t):
                    themes.append(t)
                    break
        same_format = bool(ref.get("format")) and profiles_format.get(bid) == ref.get("format")
        score = 2 * len(themes) + len(moods) + (1 if same_format else 0)
        if score >= 2 and (themes or moods) and (same_format or not ref.get("format")):
            parts = []
            if themes:
                parts.append("يلتقي معها في " + "، ".join(dict.fromkeys(themes)))
            if moods:
                parts.append("بأجواء " + "، ".join(MOOD_LABELS_FEM.get(m, m) for m in moods))
            scored.append((score, b, f"قريب من «{ref['title']}»: " + " و".join(parts)))
    scored.sort(key=lambda x: -x[0])
    out += [(b, why, "reference") for _, b, why in scored[:4]]
    return out


_GENERIC_SUBGENRE_WORDS = {"رواية", "روايه", "روايات", "ادب", "قصص", "قصة", "قصه", "كتاب"}


def _subgenre_words(text):
    out = set()
    for w in normalize(text or "").split():
        if w.startswith("و") and len(w) > 4:
            w = w[1:]
        if w.startswith("ال") and len(w) > 4:
            w = w[2:]
        if len(w) >= 3 and w not in _GENERIC_SUBGENRE_WORDS:
            out.add(w)
    return out


_MOOD_SUBGENRES = {"mysterious": ("غموض",), "suspenseful": ("تشويق", "إثارة"), "funny": ("ساخرة",),
                   "dark": ("قوطية",), "tense": ("إثارة",)}


def author_style_matches(style, name, all_books, cards, allowed, formats):
    """Researched, in-stock books close to what an author is generally known for, when the
    visitor asks for books similar to that author (Ibrahim, 2026-09-30). The model's general
    description of the author is used only to search; each match states where the likeness
    lies and where the book differs (length, pace). Unresearched books are never returned.
    -> [(book, why, "reference")] best first."""
    if not style:
        return []
    labels = list(style.get("subgenres") or [])
    # moods also point at shelf labels («غموض أدبي»، «رواية قوطية»، «رواية ساخرة»), so a style the
    # model described only by moods still reaches the right subgenres
    for m in style.get("moods") or []:
        labels += [x for x in _MOOD_SUBGENRES.get(m, ()) if x not in labels]
    wanted_subs = [(label, _subgenre_words(label)) for label in labels]
    wanted_themes = [(t, _words(t)) for t in style.get("themes") or []]
    scored = []
    for bid, fmt in formats.items():
        b, card = all_books.get(bid), cards.get(bid) or {}
        if not b or bid not in allowed or not is_available(b):
            continue
        if style.get("formats") and fmt.get("format") not in style["formats"]:
            continue
        subs, group = [], None
        for v in _json_list(b.get("subgenres")):
            words = _subgenre_words(v) if isinstance(v, str) else set()
            hit = next((label for label, w in wanted_subs if w & words), None) if words else None
            if hit:
                subs.append(v)
                group = group or hit
        themes = []
        for _, words in wanted_themes:
            for t in card.get("themes") or []:
                if words & _words(t) and t not in themes:
                    themes.append(t)
                    break
        moods = [m for m in style.get("moods") or [] if m in (card.get("moods") or [])]
        score = 3 * len(subs) + 2 * len(themes) + len(moods)
        if score < 3 and len(moods) < 2:
            continue
        diffs = []
        pages, length = b.get("page_count") or 0, card.get("perceived_length")
        if style.get("length") == "short" and (length in ("long", "very_long") or pages > 350):
            diffs.append("أطول")
        elif style.get("length") == "long" and (length in ("short", "very_short") or 0 < pages < 150):
            diffs.append("أقصر")
        pace = card.get("pace")
        if style.get("pace") == "fast" and pace in ("slow", "contemplative"):
            diffs.append("أبطأ")
        elif style.get("pace") in ("slow", "contemplative") and pace == "fast":
            diffs.append("أسرع")
        parts = []
        if subs:
            parts.append("تصنيفه «" + "، ".join(subs[:2]) + "»")
        if moods:
            parts.append("أجواؤه " + " و".join(MOOD_LABELS_FEM.get(m, m) for m in moods))
        if themes:
            parts.append("يتناول " + "، ".join(themes[:2]))
        why = f"قريب من أجواء {name}: " + "، ".join(parts)
        if diffs:
            why += "، لكنه " + " و".join(diffs) + " من كتبه المعروفة"
        scored.append((score - len(diffs), group or "moods", b, why))
    scored.sort(key=lambda x: -x[0])
    # one per side of the author's style first (horror, satire, mood...), so four satirical
    # novels do not crowd out the rest of what the visitor may like in that author
    picked, groups = [], []
    for entry in scored:
        if entry[1] not in groups:
            groups.append(entry[1])
            picked.append(entry)
    picked += [e for e in scored if e not in picked]
    return [(b, why, "reference") for _, _, b, why in picked[:4]]


STARTER_MOODS = ("funny", "warm", "uplifting", "inspiring", "suspenseful", "comforting")


def starter_picks(pool, cards, researched, language="ar", n=3):
    """«عطني أي شي حلو»: nothing to search by, so offer a few different, easy starting points
    instead of one random book (2026-10-06): researched books whose readers clearly name a
    pleasant mood, not emotionally heavy, without any content warning, each of a different
    category and mood. -> [(book, why, "filler")]"""
    books = list(pool)
    random.shuffle(books)
    picks, genres, moods = [], set(), set()
    for mood in STARTER_MOODS * 2:
        for b in books:
            card = cards.get(b["book_id"])
            if (not card or b["book_id"] not in researched or card.get("format") in ("children", "young_adult")
                    or b.get("genre") in genres or mood in moods or not b.get("summary")
                    or card.get("emotional_weight") in ("heavy", "intense")
                    or not _medium_or_better(card, "moods", mood)
                    or any(_medium_or_better(card, "sensitive_content", c) for c in SENSITIVE_LABELS if c != "none")):
                continue
            why = (f"A starting suggestion: readers describe its mood as {MOOD_LABELS_EN.get(mood, mood)}." if language == "en"
                   else f"اقتراح للبداية: القرّاء يوصفون أجواءه بأنها {MOOD_LABELS_FEM.get(mood, mood)}.")
            picks.append((b, why, "filler"))
            genres.add(b.get("genre"))
            moods.add(mood)
            break
        if len(picks) >= n:
            break
    return picks


def run_chat(messages, active=None, language="ar", clear_audience=False, drop_context=None):
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
            raw_ids = m.get("book_ids") if isinstance(m.get("book_ids"), list) else []
            ids = [int(i) for i in raw_ids if str(i).isdigit() and int(i) in all_books]
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
    drops = {str(k)[:80] for k in (drop_context or []) if isinstance(k, str)}
    if "audience" in drops:
        clear_audience = True
    user_text = " ".join(user_messages)
    cards = get_profile_cards()

    active = active if active is not None else llm.get_active()
    # questions we hold no data for (Ibrahim 2026-10-09): «وش أكثر كتاب مبيعًا؟» -> say so in one line and
    # ask first, offering recent prize winners; «ايه» to that offer -> the offer itself is the wish
    _nd = no_data.followup(conv)
    if _nd:
        conv[-1] = {"role": "user", "content": _nd}
        user_messages[-1] = _nd
        user_text = " ".join(user_messages)
    elif no_data.asked(user_messages[-1]):
        _reply, _chips = no_data.answer(user_messages[-1], language)
        open_questions.record("question", user_messages[-1])
        return {"reply": _reply, "books": [], "suggestions": _chips, "understanding": "ai",
                "provider": active[0] if active else None, "model": active[1] if active else None,
                "_debug": {"mode": "no_data", "asked": user_messages[-1]}}
    it = (chat.interpret(conv, active, language) if language == "en" else chat.interpret(conv, active)) if active else None
    if it is None:
        out = chat_keyword_fallback(user_messages, all_books, set(shown), cards, language)
        if active:
            out["_debug"].update(failed_stage="interpret", call=dict(chat.last_failure.get("call") or {}))
        return out

    interpret_call = dict(llm.last_call)
    author_request = it.get("author_request", {})
    author_reply, author_names, author_only = author_flow.resolve(
        author_request, conv, all_books.values(), language == "en")
    def author_answer(reply):
        return {"reply": reply, "books": [], "suggestions": [], "understanding": "ai",
                "provider": active[0], "model": active[1],
                "_debug": {"mode": "ai", "author_request": it.get("author_request"), "author_gate": True}}
    if author_reply:
        return author_answer(author_reply)
    if author_only:
        # An author's identity is not a genre request; do not invent genre filters.
        it["prefs"]["subgenres"] = []
        it["references"] = []
    prefs, pprefs, exclusions, unsupported = prefs_from_ai(it["prefs"], normalize(user_text))
    # the words of a title we do not have («الأب الغني والأب الفقير») never choose books by their own
    # titles («تقرير إلى أب مؤجل»), and after a prize question a title from an earlier message does
    # not crowd out the winners (QA2 2026-10-09, cases 26 and 27)
    _gone = [t for t in list(it["mentioned_titles"]) + [r.get("title") for r in it.get("references") or [] if isinstance(r, dict)]
             if t and not any(is_available(b) for b in match_titles([t], all_books, max_rank=1))]
    _gone_words = {strip_al(w) for t in _gone for w in normalize(t).split()}
    _last_words = {strip_al(w) for w in normalize(user_messages[-1]).split()}
    prefs["tokens"] = [t for t in prefs["tokens"] if t not in _gone_words
                       and not (prefs.get("awards") and not prefs.get("authors") and t not in _last_words)]
    pprefs["audience"] = chat_audience(user_messages, it["prefs"], author_only or bool(author_names), clear_audience)
    pprefs["reader_age"] = audience_age(user_messages) if pprefs["audience"] in ("children", "young_adult") else None
    if prefs.get("awards") and not prefs.get("award_year") and wants_recent(user_messages[-2:]):
        prefs["award_min_year"] = time.localtime().tm_year - RECENT_YEARS
    unsupported = [u for u in unsupported if not only_recency(u)]
    # a prize stored under «other» asked for by its name («جائزة الملك فيصل») (QA2 2026-10-09)
    _prize = prize_named(user_messages[-1]) if set(prefs.get("awards") or ["any"]) <= {"any"} else None
    if _prize:
        prefs["awards"] = [honors.named(_prize)]
        unsupported = [u for u in unsupported if not (set(normalize(u).split()) & set(normalize(_prize).split()))]
    # «وش فاز قبل ٢٠١٥؟»، "anything published after 2020?": years said in the latest message
    _lo, _hi = years_said(user_messages[-1])
    pub_years = None
    if (_lo or _hi) and (prefs.get("awards") or re.search(r"فاز|فائز|فايز|جائز|جايز|جوائز|prize|award|won", normalize(user_messages[-1]))):
        prefs["awards"] = prefs.get("awards") or ["any"]
        if prefs.get("award_year") in (_lo, _hi, (_lo or 0) - 1, (_hi or 0) + 1):
            prefs["award_year"] = None   # the interpreter may read «قبل ٢٠١٥» as «في ٢٠١٥»
        prefs.update(award_min_year=_lo, award_max_year=_hi, award_years_said=True)
    elif _lo or _hi:
        pub_years = (_lo, _hi)
    elif new_release_said(user_messages[-1]) and not prefs.get("awards"):
        pub_years = (time.localtime().tm_year - NEW_RELEASE_YEARS, None)
    if pub_years or prefs.get("award_years_said"):
        # «published after 2020» is the wish itself, not a subject «منشور» (QA2 re-run, case 8)
        _yr_words = {normalize(w) for w in "نشر منشور منشوره المنشوره صدر صادر صادره اصدار اصدارات الاصدارات نشرت صدرت حديثا "
                     "بعد قبل بين عام سنه سنة published publication released release after before since year new".split()}
        unsupported = [u for u in unsupported if not all(w in _yr_words or w.isdigit() for w in normalize(u).split())]
    if (clear_audience or adult_said(user_messages[-1])) and not child_said(user_messages[-1]):
        # the badge was removed or the visitor said «للكبار»: no children's shelf either, even if the
        # interpreter carried «أطفال» on from earlier messages
        prefs["genres"] = set(prefs["genres"]) - CHILD_GENRES
    # «أبغى كتاب لبنتي» without her age: ask it first (Ibrahim, 2026-10-08)
    kid = kid_mentioned(user_messages)
    age_asked = any(AGE_ASK_AR in c["content"] or "How old is" in c["content"] for c in conv if c["role"] == "assistant")
    if (kid and not any(_ages(t) for t in user_messages) and not age_asked and not clear_audience
            and not adult_said(user_messages[-1]) and not author_only and not it["mentioned_titles"]
            and it["intent"] in ("recommend", "clarify")):
        en = language == "en"
        f = kid == "f"
        if en:
            reply = f"How old is your {'daughter' if f else 'son'}? Then I can pick what suits {'her' if f else 'him'}."
            chips = ["4 years old", "7 years old", "10 years old", "14 years old", "Over 18"]
        else:
            reply = f"{AGE_ASK_AR} {'بنتك' if f else 'ولدك'}؟ عشان أختار اللي {'يناسبها' if f else 'يناسبه'}."
            pre = "عمرها" if f else "عمره"
            chips = [f"{pre} ٤ سنوات", f"{pre} ٧ سنوات", f"{pre} ١٠ سنوات", f"{pre} ١٤ سنة", "فوق ١٨ سنة"]
        return {"reply": reply, "books": [], "suggestions": chips,
                "understanding": "ai", "provider": active[0], "model": active[1],
                "audience": pprefs.get("audience"), "audience_age": None, "audience_gender": kid,
                "_debug": {"mode": "ai", "intent": it["intent"], "prefs": it["prefs"], "age_question": kid}}
    # a child earlier, now a kind of book we keep for both, and nothing says for whom: ask
    # (Ibrahim, 2026-10-08: «للطفل والا للكبار»)
    earlier_aud = (it["prefs"].get("audience") if it["prefs"].get("audience") in ("children", "young_adult")
                   else detect_audience(normalize(" ".join(user_messages[:-1]))))
    shared = [g for g in it["prefs"]["genres"] if g in SHARED_GENRES]
    # asked once per conversation, and not when the visitor asked for that kind of book for the
    # child himself («بنتي ١٣ سنة وتحب القصص المصورة») (QA2 2026-10-09, case 11)
    asked_before = any(AUDIENCE_ASK_AR in c["content"] or "for the child or for adults" in c["content"]
                       for c in conv if c["role"] == "assistant")
    if shared and any(detect_audience(normalize(t)) and shared[0] in local_understand([t])["genres"] for t in user_messages[:-1]):
        asked_before = True
    # asked even when the interpreter carried «children» on by itself: only the visitor's own
    # words («لها»، «للطفل»، «للكبار») settle it (live check 17:50: «عندكم كتب تاريخ؟» was not asked)
    if (earlier_aud and shared and not clear_audience and not child_said(user_messages[-1])
            and not adult_said(user_messages[-1]) and not author_only and not author_names
            and not it["mentioned_titles"] and it["intent"] in ("recommend", "clarify") and not asked_before):
        en = language == "en"
        return {"reply": (f"Are you looking for these books for the child or for adults?" if en
                          else f"تبي كتب {shared[0]} {AUDIENCE_ASK_AR}؟"),
                "books": [], "suggestions": (["For the child", "For adults"] if en else ["للطفل", "للكبار"]),
                "understanding": "ai", "provider": active[0], "model": active[1],
                "audience": earlier_aud, "audience_age": audience_age(user_messages),
                "audience_gender": audience_gender(user_messages),
                "_debug": {"mode": "ai", "intent": it["intent"], "prefs": it["prefs"], "audience_question": shared[0]}}
    # a subject from early in the conversation («إيروس») that the last two messages no longer
    # mention is not searched again after «أبغى شي ثاني» (library trial, 2026-10-08)
    def _said_in(texts):
        return set().union(*[topics.stems(w) for w in normalize(" ".join(texts)).split()] or [set()])
    _recent, _older = _said_in(user_messages[-2:]), _said_in(user_messages[:-2])
    def _stale(phrase):
        words = topics.content_words(phrase)
        return bool(words) and all(f & _older for f in words) and not any(f & _recent for f in words)
    unsupported = [u for u in unsupported if not _stale(u)]
    # the interpreter sometimes takes the whole request for a title («لا ابغي كتب عن ايران»)
    _req = {normalize(w) for w in "ابي ابغي ابغى ابغا ودي اريد عندكم عندك ابحث كتب كتاب".split()}
    _last = " ".join(normalize(user_messages[-1]).split())
    if any(" ".join(normalize(m).split()) == _last and set(_last.split()) & _req for m in it["mentioned_titles"]):
        it["mentioned_titles"] = [m for m in it["mentioned_titles"] if " ".join(normalize(m).split()) != _last]
        if it["intent"] == "about_book" and not it["mentioned_titles"]:
            it["intent"] = "recommend"
    # «كتب عن إيران» with no subject understood: the words after «عن» are the subject
    if not unsupported and not author_only:
        _m = re.search(r"(?:^| )عن ((?:\S+ ?){1,3})$", _last)
        if _m and topics.searchable([_m.group(1).strip()], topics.index(all_books)):
            unsupported = [_m.group(1).strip()]
    # wishes from earlier messages that the latest message did not repeat: dropped when they leave
    # nothing for a new subject or a publisher the visitor just named (library trial, 2026-10-08)
    latest = local_understand([user_messages[-1]])
    def _open_prefs(p):
        return dict(p, genres=set(p["genres"]) & set(latest["genres"]), subgenres=[], soft_subgenres=[],
                    length=p["length"] if latest["length"] else None,
                    original_languages=p["original_languages"] if latest["original_languages"] else set(),
                    translated=p.get("translated") if latest["translated"] else None)
    def _open_pprefs(pp):
        return dict(pp, moods=set(latest["moods_include"]), pace=set(latest["pace"]),
                    weight=set(latest["weight_include"]), use_cases=set(latest["use_cases"]),
                    reader_level=set(latest["reader_level"]))
    _open_excl = {"moods": set(latest["moods_exclude"]), "weight": set(latest["weight_exclude"])}
    # badges the visitor removed with ✕ (drop_context), unless this very message asks for it again
    def _restated(key):
        kind, _, val = key.partition(":")
        last = user_messages[-1]
        return {"genre": lambda: val in (latest.get("genres") or []),
                "length": lambda: bool(latest.get("length")),
                "lang": lambda: bool(latest.get("original_languages") or latest.get("translated")),
                "mood": lambda: val in (latest.get("moods_include") or []),
                "awards": lambda: bool(re.search(r"جائز|جوائز|بوكر|نوبل|فاز|فائز|حائز", normalize(last))),
                "recent": lambda: wants_recent([last]) or bool(years_said(last)[0]),
                "before": lambda: bool(years_said(last)[1]),
                "pubyear": lambda: any(years_said(last)) or new_release_said(last),
                "adaptation": lambda: bool(re.search(r"فلم|فيلم|افلام|مسلسل", normalize(last))),
                "publisher": lambda: bool(find_publisher([], last, all_books)),
                "country": lambda: bool(wished_country(last)),
                "edition": lambda: bool(wanted_edition(last, language)),
                "topic": lambda: any(f & _said_in([last]) for f in topics.content_words(val)),
                }.get(kind, lambda: False)()
    drops = {k for k in drops if k != "audience" and not _restated(k)}
    for k in drops:
        kind, _, val = k.partition(":")
        if kind == "genre":
            prefs["genres"] = set(prefs["genres"]) - {val}
        elif kind == "subgenre":
            prefs["subgenres"] = [s for s in prefs.get("subgenres") or [] if s != val]
            prefs["soft_subgenres"] = [s for s in prefs.get("soft_subgenres") or [] if s != val]
        elif kind == "length":
            prefs["length"] = None
        elif kind == "lang":
            prefs["original_languages"], prefs["translated"] = set(), None
        elif kind == "mood":
            pprefs["moods"] = set(pprefs.get("moods") or ()) - {val}
        elif kind == "awards":
            prefs.update(awards=[], award_year=None, award_min_year=None, award_max_year=None)
        elif kind == "recent":
            prefs["award_min_year"] = None
        elif kind == "before":
            prefs["award_max_year"] = None
        elif kind == "pubyear":
            pub_years = None
        elif kind == "adaptation":
            prefs["adaptation"] = None
        elif kind == "topic":
            unsupported = [u for u in unsupported if u != val]
    if author_only:
        # «هل في كتاب لرضوى عاشور» after «قصص أطفال»: the author's books, without the earlier
        # genre, length or language unless this message asks for them (library trial, 2026-10-08)
        prefs, pprefs, exclusions = _open_prefs(prefs), _open_pprefs(pprefs), _open_excl
    pool = [b for b in all_books.values() if is_available(b)]
    author_book_ids = set()
    if author_only:
        # books linked to the chosen author record too (co-authored books, other spellings)
        for n in author_names:
            rec = authors.lookup(author_flow.display(n))
            if rec:
                author_book_ids |= authors.book_ids(rec["author_id"])
        pool = [b for b in pool if b.get("author") in author_names or b["book_id"] in author_book_ids]
        offered = any(c["role"] == "assistant" and ("تبغى أقترح لك بدائل" in c["content"] or "Would you like alternatives" in c["content"])
                      for c in conv[-2:])
        if not pool and offered:
            # «ايه عطني بدائل» after we offered them: books close to the author, not the same answer
            # again (QA re-run 2026-10-08, case 17)
            author_only = False
            author_request["mode"] = "similar"
            pool = [b for b in all_books.values() if is_available(b)]
        elif not pool:
            if author_names:
                # the author is in our catalog, only without copies now (QA2 2026-10-09: say it plainly)
                return author_answer("We have no copies of this author’s books in stock at the moment. Would you like alternatives?" if language == "en" else
                                     "ما عندنا الآن نسخ متوفرة من كتب هذا المؤلف. تبغى أقترح لك بدائل؟")
            return author_answer("I couldn’t find an author by this name in our catalog. Would you like alternatives?" if language == "en" else
                                 "ما لقيت عندنا مؤلفًا بهذا الاسم. تبغى أقترح لك بدائل؟")
    if it["wants_new_options"]:
        pool = [b for b in pool if b["book_id"] not in shown]
    child_theme = None
    _shared_now = [g for g in prefs["genres"] if g in CHILD_THEMES]
    if pprefs.get("audience") in ("children", "young_adult") and _shared_now and not author_only:
        shelves = CHILD_GENRES | {"قصص مصورة"} if pprefs["audience"] == "young_adult" else {"أطفال", "قصص مصورة"}
        t_idx = topics.index(all_books)[0]
        want = [topics.stems(w) for w in CHILD_THEMES[_shared_now[0]].split()]
        def _theme_score(bid):
            title, subj, text = t_idx.get(bid, (set(), set(), set()))
            return sum(3 * bool(f & title) + 2 * bool(f & subj) + bool(f & text) for f in want)
        theme_score = {b["book_id"]: _theme_score(b["book_id"]) for b in pool if b.get("genre") in shelves}
        themed = [b for b in pool if theme_score.get(b["book_id"])]
        if themed:
            child_theme = _shared_now[0]
            pool = themed
            prefs = dict(prefs, genres=set(prefs["genres"]) - {child_theme})
    pool_before = pool
    pool, labels, prefs, relaxed_subgenres = constrain_relaxing(pool, prefs, pprefs, exclusions, cards)
    if not pool and prefs.get("award_min_year") and not prefs.get("award_years_said"):
        # nothing that recent in stock: the newest prizes we do have, ranked by year
        prefs = dict(prefs, award_min_year=None)
        pool, labels, prefs, relaxed_subgenres = constrain_relaxing(pool_before, prefs, pprefs, exclusions, cards)
    # A category with nothing in stock («سيرة نبوية») whose subject other categories cover («فقه
    # السيرة» under دين): the category name is searched as a subject instead (2026-10-07)
    genre_as_topic = []
    if not pool and prefs.get("genres") and not author_only:
        named = [g for g in prefs["genres"]]
        open_prefs = dict(prefs, genres=set())
        alt, alt_labels, open_prefs, _ = constrain_relaxing(pool_before, open_prefs, pprefs, exclusions, cards)
        t_index = topics.index(all_books)
        alt = [b for b in alt if topics.match(named, b["book_id"], t_index)[1]]
        if alt:
            pool, labels, prefs, genre_as_topic = alt, alt_labels, open_prefs, named
            unsupported = named + [u for u in unsupported if u not in named]
    publisher_name = None
    found_pub = None if (author_only or genre_as_topic or "publisher" in drops) else (
        find_publisher(unsupported, user_messages[-1], all_books)
        # «روايات بس، ومترجمة» after «عندكم من دار الآداب؟»: the publisher stays (QA 2026-10-08)
        or (find_publisher([], user_messages[-2], all_books)
            if len(user_messages) > 1 and len(user_messages[-1].split()) <= 6 and not it["mentioned_titles"] else None))
    if found_pub:
        key, publisher_name = found_pub
        pub_pool = [b for b in pool_before if publisher_key(b.get("publisher")) == key]
        p_pool, p_labels, p_prefs, _ = constrain_relaxing(pub_pool, prefs, pprefs, exclusions, cards)
        if p_pool:
            pool, labels, prefs = p_pool, p_labels, p_prefs
        else:
            o_prefs, o_pprefs = _open_prefs(prefs), _open_pprefs(pprefs)
            p_pool, p_labels, o_prefs, _ = constrain_relaxing(pub_pool, o_prefs, o_pprefs, _open_excl, cards)
            if p_pool:
                pool, labels, prefs, pprefs, exclusions = p_pool, p_labels, o_prefs, o_pprefs, _open_excl
            else:
                publisher_name = None
        unsupported = [u for u in unsupported if publisher_key(u) != key]
    # the author's country («كاتب سعودي»): latest message first, else the one before it
    country, country_missing = None, False
    if not author_only and not genre_as_topic and "country" not in drops:
        country = wished_country(user_messages[-1]) or (
            wished_country(user_messages[-2]) if len(user_messages) > 1 and len(user_messages[-1].split()) <= 4 else None)
    if country:
        adj, names = country
        origins = book_origins()
        keys = [normalize(n) for n in names]
        c_pool = [b for b in pool_before if any(k in origins.get(b["book_id"], "") for k in keys)]
        p2, l2, pr2, _ = constrain_relaxing(c_pool, prefs, pprefs, exclusions, cards)
        if not p2 and c_pool:
            o_prefs, o_pprefs = _open_prefs(prefs), _open_pprefs(pprefs)
            p2, l2, o_prefs, _ = constrain_relaxing(c_pool, o_prefs, o_pprefs, _open_excl, cards)
            if p2:
                prefs, pprefs, exclusions = o_prefs, o_pprefs, _open_excl
        if p2:
            pool, labels = p2, l2
        else:
            country_missing = True
        unsupported = [u for u in unsupported if not wished_country(u)]
        if prefs.get("original_languages") and adj in _ARAB_NATIONS:
            prefs = dict(prefs, original_languages=set(prefs["original_languages"]) & {"ar"})
    # the language of the copy («بالإنجليزي»)
    edition, edition_missing = None, False
    if "edition" not in drops and not author_only:
        edition = wanted_edition(user_messages[-1], language) or (
            wanted_edition(user_messages[-2], language) if len(user_messages) > 1 and len(user_messages[-1].split()) <= 4 else None)
    if edition:
        if edition == "en" and prefs.get("original_languages"):
            # «English novels» is about the copy; a translated / original-language wish read into it is dropped
            prefs = dict(prefs, original_languages=set(prefs["original_languages"]) - {"en"}, translated=None)
        e_pool = [b for b in pool if ((b.get("language") or "ar").strip() or "ar") == edition]
        if not e_pool:
            e_all = [b for b in pool_before if ((b.get("language") or "ar").strip() or "ar") == edition]
            e_pool, _l, prefs2, _ = constrain_relaxing(e_all, _open_prefs(prefs), _open_pprefs(pprefs), _open_excl, cards)
            if e_pool:
                prefs, pprefs, exclusions, labels = prefs2, _open_pprefs(pprefs), _open_excl, _l
        if e_pool:
            pool = e_pool
        else:
            edition_missing = True
    # the publication year («after 2020»، «صدر حديثًا»), from books.publication_year (QA2 2026-10-09)
    pub_missing = False
    if pub_years and not author_only:
        lo, hi = pub_years
        def _pub_ok(b):
            y = b.get("publication_year")
            try:
                y = int(str(y)[:4])
            except (TypeError, ValueError):
                return False
            return (not lo or y >= lo) and (not hi or y <= hi)
        y_pool = [b for b in pool if _pub_ok(b)]
        if not y_pool:
            y_all = [b for b in pool_before if _pub_ok(b)]
            y_pool, _l, prefs2, _ = constrain_relaxing(y_all, _open_prefs(prefs), _open_pprefs(pprefs), _open_excl, cards)
            if y_pool:
                prefs, pprefs, exclusions, labels = prefs2, _open_pprefs(pprefs), _open_excl, _l
        if y_pool:
            pool = y_pool
        else:
            pub_missing = True
    # the reader's age against the books' researched age range: books clearly for another age are
    # left out, books for that age come first (children's age research, 2026-10-09)
    reader_age = audience_age(user_messages) if pprefs.get("audience") in ("children", "young_adult") else None
    if reader_age is not None:
        fitting = [b for b in pool if age_fit(b, reader_age) >= 0]
        if fitting:
            pool = fitting
    researched = _profile_formats()
    # «كتب تشبه فلان»: only researched books close to the author's known style, never generic
    # fillers or unresearched books (Ibrahim, 2026-09-30).
    similar_author = (not author_only and author_request.get("mode") in ("similar", "both")
                      and bool(str(author_request.get("full_name") or author_request.get("name_as_written") or "").strip()))
    scored = score_books(pool, prefs, pprefs, cards)
    if publisher_name:
        scored = [(s + 1, ["من إصدارات «" + publisher_name + "»"] + r, b) for s, r, b in scored]
    if reader_age is not None:
        scored = sorted(((s + (2 if age_fit(b, reader_age) > 0 else 0),
                          ([f"عمره المناسب {AGE_RANGE_AR(b.get('age_min'), b.get('age_max'))}"] if age_fit(b, reader_age) > 0 else []) + r, b)
                         for s, r, b in scored), key=lambda x: x[0], reverse=True)
    _asked_kind = set(prefs.get("genres") or ()) - CHILD_GENRES
    if pprefs.get("audience") in ("children", "young_adult") and _asked_kind:
        # «قصص مصورة» for a 13-year-old: the kind asked for first, before the young readers' shelf that
        # was only added for her age (QA2 re-run, case 11)
        scored = sorted(((s + (4 if b.get("genre") in _asked_kind else 0), r, b) for s, r, b in scored),
                        key=lambda x: x[0], reverse=True)
    if language == "en" and not edition:
        # the English chat: books in English a little higher
        scored = sorted(((s + (1.5 if (b.get("language") or "ar") == "en" else 0), r, b) for s, r, b in scored),
                        key=lambda x: x[0], reverse=True)
    if country and not country_missing:
        _place = " أو ".join(country[1][:2]) if country[0] != "خليجي" else "الخليج"
        scored = [(s + 1, [f"كاتبه من {_place}"] + r, b) for s, r, b in scored]
    if child_theme:
        scored = sorted(((s + 1 + theme_score.get(b["book_id"], 0), [f"كتاب للصغار فيه {child_theme}"] + r, b)
                         for s, r, b in scored), key=lambda x: x[0], reverse=True)
    # A subject the visitor named («عن الأندلس»، «أترك التسويف»): looked up in title, subjects and
    # summary; a book that covers it ranks first and says so (2026-10-06).
    topic_index = topics.index(all_books)
    # a shelf label nothing in stock carries («أدب السجون») is still a subject to look for (2026-10-07)
    unsupported = unsupported + [x for x in relaxed_subgenres if x not in unsupported]
    # a subject the visitor does NOT want («ولا فيها موت») is never searched for (2026-10-07)
    _said = normalize(user_text).split()
    def _unwanted(phrase):
        if any(w in keyword_prefs.NEGATORS for w in normalize(phrase).split()):
            return False   # «حبكة غير خطية»: the negation is part of the wish itself
        for w in normalize(phrase).split():
            for i, said in enumerate(_said):
                if len(w) >= 3 and (topics.stems(w) & topics.stems(said)) and keyword_prefs.negated(_said, i):
                    return True
        return False
    unsupported = [u for u in unsupported if not _unwanted(u)]
    topic_phrases = topics.searchable(unsupported, topic_index)
    topics_found, topics_mentioned, topics_semantic = [], [], []
    topic_widened = False
    if topic_phrases and not any(topics.match(topic_phrases, b["book_id"], topic_index)[0] for b in pool_before):
        # no book in stock has the whole subject: its rarest word alone («سقوط غرناطة» -> «غرناطة») (QA 2026-10-08)
        core = [w for w in (topics.core_word(p, topic_index) for p in topic_phrases) if w]
        if core and any(topics.match(core, b["book_id"], topic_index)[0] for b in pool_before):
            topic_phrases = core
    # search by meaning (layer 2, 2026-10-08): «الغربة» also finds a summary about «المنفى»
    sem = {}
    if topic_phrases and not author_only and not similar_author and not publisher_name:
        sem = embeddings.search(topic_phrases, [b["book_id"] for b in pool_before])
    sem_ids = {i: p for p, hits in sem.items() for i, _ in hits}
    if topic_phrases and not author_only and not similar_author and not publisher_name and not any(
            topics.match(topic_phrases, b["book_id"], topic_index)[0] or b["book_id"] in sem_ids for _, _, b in scored):
        # «أبغى كتب عن إيران» after Russian short biographies: the subject is searched in the whole
        # stock, keeping only what the latest message itself asked for (library trial, 2026-10-08)
        o_prefs, o_pprefs = _open_prefs(prefs), _open_pprefs(pprefs)
        wide = [b for b in pool_before if topics.match(topic_phrases, b["book_id"], topic_index)[0] or b["book_id"] in sem_ids]
        wide, w_labels, o_prefs, _ = constrain_relaxing(wide, o_prefs, o_pprefs, _open_excl, cards)
        if wide:
            pool, labels, prefs, pprefs, exclusions, topic_widened = wide, w_labels, o_prefs, o_pprefs, _open_excl, True
            scored = score_books(pool, prefs, pprefs, cards)
    if topic_phrases:
        rescored = []
        for s, r, b in scored:
            ts, about, mentioned = topics.match(topic_phrases, b["book_id"], topic_index)
            if about:
                in_title = ts >= topics.TITLE * len(about)
                r = [("عنوانه عن «" if in_title and not mentioned else "يتناول «") + "»، «".join(about) + "»"] + r
            elif mentioned:
                r = ["نبذته تذكر «" + "»، «".join(mentioned) + "»"] + r
            close = [p for p, hits in sem.items() if p not in about and p not in mentioned and any(i == b["book_id"] for i, _ in hits)]
            if close and not about:
                r = ["قريب من موضوع «" + "»، «".join(close) + "» حسب نبذته"] + r
                ts += 2
            rescored.append((s + ts * 2, r, b))
        scored = sorted(rescored, key=lambda x: x[0], reverse=True)
        for s, r, b in scored[:6]:   # what the visitor may actually be shown
            _, about, mentioned = topics.match(topic_phrases, b["book_id"], topic_index)
            topics_found += [x for x in about if x not in topics_found]
            topics_mentioned += [x for x in mentioned if x not in topics_mentioned]
            topics_semantic += [p for p, hits in sem.items() if p not in topics_semantic and any(i == b["book_id"] for i, _ in hits)]
        topics_mentioned = [x for x in topics_mentioned if x not in topics_found]
        topics_semantic = [x for x in topics_semantic if x not in topics_found and x not in topics_mentioned]
    if prefs.get("awards") and not author_only and not wants_recent(user_messages[-2:]) and not topic_phrases:
        # «مين من الفايزين بنوبل عندكم؟»: one book per prize-winning author first, not the newest
        # author's whole shelf (QA2 2026-10-09, case 33)
        _first, _rest, _seen_a = [], [], set()
        for x in scored:
            a = author_flow.display(x[2].get("author"))
            (_rest if a in _seen_a else _first).append(x)
            _seen_a.add(a)
        scored = _first + _rest
    _people = [set(c) for c in (author_request.get("people") or {}).values() if c]
    if author_only and len(_people) >= 2:
        # several authors named together: their books take turns, so each of them is in facts
        lanes = [[x for x in scored if x[2].get("author") in cats] for cats in _people]
        mixed = [x for group in itertools.zip_longest(*lanes) for x in group if x is not None]
        scored = mixed + [x for x in scored if x not in mixed]
        scored = [(max(s, 1), r, b) for s, r, b in scored]
    candidates = [] if similar_author else [(b, reason_text(r, language), "direct") for s, r, b in scored if s > 0][:6]
    if len(candidates) < 3 and not similar_author:
        # fillers come from the constrained pool, so they never break a stated wish, and only from
        # researched books: an unresearched book is never offered as an alternative (2026-09-30)
        used = {b["book_id"] for b, _, _ in candidates}
        candidates += [(b, (("By " if language == "en" else "من كتب ") + author_flow.display(b.get("author"))) if author_only
                        else filler_reason(labels, language), "filler") for s, r, b in scored
                       if b["book_id"] not in used and (b["book_id"] in researched or author_only)][:4 - len(candidates)]

    # A request with nothing to search by gets a few different starting points, not random fillers
    starters = False
    if (it["intent"] == "clarify" and not similar_author and not author_only and not topic_phrases
            and not publisher_name and not match_titles(it["mentioned_titles"], all_books)
            and not any(m == "direct" for _, _, m in candidates)):
        picks = starter_picks(pool, cards, researched, language)
        if len(picks) >= 2:
            candidates, starters = picks, True
    # «عندكم الخيميائي؟» and we do not have it: say so; only books chosen for their likeness to it
    # (reference matches below) may follow, never a random alternative (2026-10-06)
    asked_books = match_titles(it["mentioned_titles"], all_books)
    # «The Power of Habit موجود؟»: the interpreter may give the Arabic title; the visitor's own
    # Latin-script words are tried too (QA 2026-10-08, cases 22 and 38)
    for latin in re.findall(r"[A-Za-z][A-Za-z0-9'’:,\- ]{4,}", user_messages[-1]):
        if len(latin.split()) >= 2:
            for b in match_titles([latin.strip()], all_books, max_rank=1):
                if b["book_id"] not in [x["book_id"] for x in asked_books]:
                    asked_books.insert(0, b)
    # «عندكم نسخته الانجليزية؟» / an asked book out of stock: its other editions in stock by the
    # same author record (English editions are linked to the author by name_latin)
    if asked_books and (not any(is_available(b) for b in asked_books)
                        or re.search(r"انجليز|انقليز|english", normalize(user_messages[-1]))):
        for b in list(asked_books):
            rec = authors.lookup(b.get("author") or "")
            for bid in (authors.book_ids(rec["author_id"]) if rec else ()):
                other = all_books.get(bid)
                if (other and is_available(other) and other.get("language") == "en"
                        and other["book_id"] not in [x["book_id"] for x in asked_books]):
                    asked_books.append(other)
    if (not asked_books and not it["mentioned_titles"] and not topic_phrases and not author_only
            and not similar_author and not publisher_name
            and 2 <= len(normalize(user_messages[-1]).split()) <= 6
            and not keyword_prefs.is_greeting(user_messages[-1])
            and not keyword_prefs.understood_anything(latest)):
        # «خبز على»: the visitor typed the start of a title (library trial, 2026-10-08)
        guess = match_titles([user_messages[-1]], all_books, max_rank=1)
        if guess:
            asked_books, it["mentioned_titles"], it["intent"] = guess, [user_messages[-1]], "about_book"
            candidates, starters = [], False
    missing_title = (it["intent"] == "about_book" and bool(it["mentioned_titles"]) and not asked_books
                     and not similar_author and not author_only)
    if missing_title:
        candidates = []

    facts, reason_by_id, seen = [], {}, set()

    def add(book, why, match):
        if author_only and book.get("author") not in author_names and book["book_id"] not in author_book_ids:
            return
        if book["book_id"] in seen or len(facts) >= CHAT_MAX_FACTS:
            return
        seen.add(book["book_id"])
        facts.append(book_fact(book, cards.get(book["book_id"]), why, language))
        reason_by_id[book["book_id"]] = (why, match)

    for b in asked_books:
        add(b, "سأل عنه الزائر", "asked")
    # a title the visitor asked about is discussed even if unsuitable, but only
    # age-suitable books may be shown as cards (see the id validation below)
    suitable = {b["book_id"] for b in all_books.values()
                if age_allowed(cards.get(b["book_id"]), pprefs.get("audience"), pprefs.get("avoid") or (), b, pprefs.get("reader_age"))
                and (not prefs.get("subgenres") or matching_subgenres(b, prefs))
                and origin_allowed(b, prefs)}
    # every stated constraint (length, genre, excluded moods ... as in constrain()): required of
    # each book shown as a card except a title the visitor asked about by name
    _genres = wanted_genres(prefs, pprefs)
    strict = {b["book_id"] for b in all_books.values()
              if meets_constraints(b, cards.get(b["book_id"]), prefs, pprefs, exclusions, _genres)}
    # A reference book the visitor wants something similar to ("تشبه فتاة البرتقال"): only when
    # it is not in stock with a researched card, the model's general description of it is used to
    # find in-stock books by the same author or with shared themes/moods (Ibrahim, 2026-09-28).
    ref_notes = []
    if it.get("references") and (it["intent"] != "about_book" or missing_title):
        formats = {bid: (p or {}).get("format") for bid, p in _profile_formats().items()}
        for ref in it["references"]:
            inside = [b for b in match_titles([ref["title"]], all_books)
                      if is_available(b) and b["book_id"] in cards and b["book_id"] in formats and formats[b["book_id"]]]
            if inside:
                continue  # researched and in stock: its own card and related books are used instead
            in_stock = any(is_available(b) for b in match_titles([ref["title"]], all_books))
            found = reference_matches(ref, all_books, cards, suitable, formats) if ref["known"] else []
            for b, why, match in found:
                add(b, why, match)
            ref_notes.append((ref, in_stock, bool(found)))
    similar_found, style_source, author_record = False, None, None
    own_shown = 0
    if similar_author:
        author_name = str(author_request.get("full_name") or author_request.get("name_as_written")).strip()[:80]
        # the stored style of this author (research, else computed from our cards) comes first; the
        # interpreter's per-conversation description is only a fallback (Ibrahim, 2026-09-30)
        for n in [author_request.get("full_name"), author_request.get("name_as_written")] + list(author_request.get("aliases") or [])[:8]:
            author_record = authors.lookup(n) if isinstance(n, str) and n.strip() else None
            if author_record:
                break
        # several authors named together («يشبه دوستويفسكي وتولستوي»): the interpreter's description
        # covers them all, and none of their own books is offered as «like them» (2026-10-06)
        named_records = {}
        for n in [author_request.get("full_name"), author_request.get("name_as_written")] + list(author_request.get("aliases") or [])[:8]:
            rec = authors.lookup(n) if isinstance(n, str) and n.strip() else None
            if rec:
                named_records[rec["author_id"]] = rec
        several = len(named_records) >= 2
        style, style_source = authors.style_for(author_record)
        if several and it.get("author_style"):
            style, style_source = it["author_style"], "model"
        if author_record and not several:
            author_name = author_record["name_ar"]
        if style is None and it.get("author_style"):
            style, style_source = it["author_style"], "model"
        allowed = {b["book_id"] for b in pool} & suitable
        if several:
            author_name = str(author_request.get("name_as_written") or author_name).strip()[:80]
            for rec in named_records.values():
                allowed -= authors.book_ids(rec["author_id"])
            author_record = None
        if author_record:  # his own books are not «books like him»
            own_ids = authors.book_ids(author_record["author_id"])
            allowed -= own_ids
            if author_request.get("mode") == "both":
                # «من كتبه أو مشابه» (Ibrahim, 2026-10-02): his available books first, then the similar ones
                for b in [x for x in pool if x["book_id"] in own_ids and x["book_id"] in suitable][:2]:
                    add(b, ("By " if language == "en" else "من كتب ") + author_name, "direct")
                    own_shown += 1
        for b, why, match in author_style_matches(style, author_name, all_books, cards, allowed, researched):
            add(b, why, match)
            similar_found = True
    for b, why, match in candidates:
        add(b, why, match)
    # Related books (card v2 §12.5): alternatives / same author / next part of the first few
    # books, only when in stock and suitable for this reader; each keeps its stated reason.
    rel = {} if starters else relations.for_books([f["id"] for f in facts[:3]])  # starters stay different from each other
    for f in list(facts[:3]):
        listed = []
        for r in rel.get(f["id"], []):
            t = all_books.get(r["target_book_id"])
            if not t or not is_available(t) or t["book_id"] not in strict:
                continue
            label = relations.TYPES_AR.get(r["relation_type"], "")
            listed.append({"id": t["book_id"], "title": t["title"], "relation": label, "reason": r["reason_ar"]})
            if len(listed) <= 2:
                matched = matching_subgenres(t, prefs)
                why = ("تصنيفه الفرعي «" + "، ".join(matched) + "» يوافق طلبك" if matched else
                       f"{label} لـ«{f['title']}»: {r['reason_ar']}")
                add(t, why, "related")
            if len(listed) >= 4:
                break
        if listed:
            f["related"] = listed
    for i in ([] if missing_title else shown[-4:]):
        if age_allowed(cards.get(i), pprefs.get("audience"), pprefs.get("avoid") or (), all_books[i], pprefs.get("reader_age")):
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
    if starters:
        guidance = ("The request is open: present the books in facts briefly as different starting points (one short phrase each, from its facts), then ask one friendly question about mood or a recent favourite."
                    if language == "en" else
                    "الطلب مفتوح: اعرض الكتب الموجودة في facts كبدايات متنوعة (جملة قصيرة لكل كتاب من صفاته في facts) وضعها كلها في show_book_ids، ثم اسأل سؤالًا واحدًا لطيفًا عن المزاج أو آخر كتاب عجبه.")
    if missing_title:
        title = it["mentioned_titles"][0]
        found_like = any(found for _, _, found in ref_notes)
        if language == "en":
            guidance = (f"The visitor asked for «{title}» and it is not available here: say so plainly first. "
                        + ("Then offer the books in facts, which were chosen for their likeness to it (each reason says how)." if found_like
                           else "Show no books; ask whether they would like something close to it and what they like about it."))
        else:
            guidance = (f"الزائر سأل عن «{title}» وهو غير متوفر عندنا حاليًا: قل ذلك بوضوح أولًا. "
                        + ("ثم اعرض الكتب الموجودة في facts، وهي مختارة لقربها منه (سبب كل كتاب يوضح وجه الشبه)، وضعها في show_book_ids." if found_like
                           else "لا تعرض أي كتاب، واسأله إن كان يبغى شيئًا قريبًا منه ووش اللي يعجبه فيه."))
    if pprefs.get("audience") in AGE_OK:
        guidance += ((" The reader is " + ("a child" if pprefs["audience"] == "children" else "a teenager")
                      + ". Recommend only from facts; do not mention any other title from your knowledge.") if language == "en" else
                     (" القارئ " + ("طفل" if pprefs["audience"] == "children" else "مراهق")
                      + ": اقترح فقط من facts (كلها مفحوصة لعمره). لا تذكر كتابًا آخر من معرفتك."))
    for ref, in_stock, found in ref_notes:
        title = ref["title"]
        if language == "en":
            guidance += (f" The visitor wants something like «{title}»" + (" (we have it, but without reader details yet)" if in_stock else " (not available here)")
                         + (". Books chosen for that likeness are in facts with the reason; say the likeness is based on what is generally known about that book and do not describe its plot." if found
                            else ". We could not match it to our books: say so briefly and ask what they liked about it."))
        else:
            guidance += (f" الزائر يبغى كتابًا يشبه «{title}»" + ("، وهو موجود عندنا لكن بدون تفاصيل من القرّاء بعد" if in_stock else "، وهو غير متوفر عندنا")
                         + ("؛ الكتب المختارة للشبه موجودة في facts مع سببها. قل إن الشبه حسب ما هو معروف عن الكتاب عمومًا، ولا تصف أحداثه ولا تضف عنه معلومات." if found
                            else "؛ ما قدرنا نربطه بكتبنا: قل ذلك باختصار واسأله وش اللي عجبه فيه."))
    if similar_author and own_shown:
        guidance += (f" The visitor asked for books by «{author_name}» or similar ones: present his own available books first (their reason in facts says they are his), then the similar ones." if language == "en"
                     else f" الزائر طلب كتبًا لـ«{author_name}» أو مشابهة لها: اعرض كتبه المتوفرة أولًا (سببها في facts أنها من كتبه) ثم الكتب المشابهة.")
        if not similar_found:
            guidance += (" None of our researched books is close enough to his style: say so in one short sentence after his books." if language == "en"
                         else " ما لقينا بين كتبنا المبحوثة شيئًا قريبًا كفاية من أسلوبه: قل ذلك في جملة قصيرة بعد كتبه.")
    if similar_author and (similar_found or not own_shown):
        if language == "en":
            guidance += (f" The visitor wants books similar to the author «{author_name}»."
                         + (" The books chosen for that likeness are in facts, each reason saying where it is close and where it differs. Present the likeness honestly (mood/genre, not identical style) and mention the stated difference" + ("." if own_shown else "; do not describe the author's own books.") if similar_found
                            else " None of our researched books is close enough: say so plainly, show no books, and ask what they enjoy in that author's books (e.g. horror, mystery, humour, short fast reads)."))
        else:
            guidance += (f" الزائر يبغى كتبًا تشبه كتابات «{author_name}»."
                         + ("؛ الكتب المختارة للشبه موجودة في facts، وسبب كل كتاب يوضح وين الشبه ووين الاختلاف. قل الشبه بصراحة (في الجو أو النوع، مو نفس الأسلوب تمامًا) واذكر الاختلاف إن وُجد" + ("." if own_shown else "، ولا تتكلم عن كتب المؤلف نفسه.") if similar_found
                            else "؛ ما لقينا بين كتبنا المبحوثة شيئًا قريبًا بما يكفي: قل ذلك بصراحة، لا تعرض كتبًا، واسأله وش يعجبه في كتبه (الرعب؟ الغموض؟ السخرية؟ القِصر والسرعة؟)."))
        # the stored, researched style may be named in one general phrase (Ibrahim's choice,
        # 2026-09-30): no biography, awards or other facts about the author
        if style_source == "research" and author_record:
            known = "، ".join(author_record["subgenres"][:3] + [MOOD_LABELS_FEM.get(m, m) for m in author_record["moods"][:2]])
            if language == "en":
                guidance += (f" What {author_name}'s writing is generally known for: {known}. You may name this in one general phrase"
                             " (e.g. «close to the mystery and dark mood he is known for»); say nothing else about the author.")
            else:
                guidance += (f" المعروف عن كتابات {author_name} عمومًا: {known}. لك أن تذكر ذلك بجملة عامة واحدة"
                             " (مثل «قريب من أجواء الغموض اللي يُعرف فيها»)، ولا تذكر عنه أي معلومة أخرى (سيرة، جوائز، حياته).")
    soft_moods = [m for m in MOOD_SUBGENRES if m in pprefs["moods"]]
    if soft_moods and it["intent"] == "recommend":
        named = "، ".join(MOOD_LABELS_FEM.get(m, m) for m in soft_moods)
        guidance += ((" The visitor asked for a " + " / ".join(MOOD_LABELS_EN.get(m, m) for m in soft_moods) + " read: present first the books whose facts say so. For any other book you show, say plainly that you cannot confirm it.") if language == "en" else
                     f" الزائر طلب قراءة «{named}»: قدّم أولًا الكتب التي تذكر facts أنها كذلك (في سببها أو صفاتها). أي كتاب آخر تعرضه قل بصراحة إنك ما تقدر تؤكد أنه «{named}».")
    if relaxed_subgenres:
        named = "، ".join(relaxed_subgenres)
        guidance += ((f" No available book is classified as «{named}» within the rest of the request" + (" (" + ", ".join(localize_reason(x) for x in labels) + ")" if labels else "")
                      + ". The books in facts keep every other wish: offer them as suggestions and say plainly that you cannot confirm they are «" + named + "». Do not say we have nothing.") if language == "en" else
                     (f" ما لقينا كتابًا متوفرًا مصنّفًا «{named}» ضمن باقي الطلب" + (" (" + "، ".join(labels) + ")" if labels else "")
                      + f". الكتب في facts تلتزم بباقي الطلب: اعرضها كاقتراح، وقل بصراحة إنك ما تقدر تؤكد أنها «{named}». لا تقل إنه ما عندنا شيء."))
    if topics_found:
        named = "، ".join(topics_found)
        guidance += ((f" The visitor asked about the subject: {named}. The books in facts whose reason says they are about it are the matches: present those first, and say plainly of any other book that it only shares the category.") if language == "en" else
                     f" الزائر يبغى موضوع: {named}. الكتب في facts التي يذكر سببها أنها عن هذا الموضوع هي المطابقة: قدّمها أولًا، وأي كتاب آخر تعرضه قل بصراحة إنه من نفس التصنيف فقط وليس عن الموضوع.")
    if genre_as_topic:
        named = "، ".join(genre_as_topic)
        guidance += ((f" No available book is shelved under «{named}», but the books in facts from other categories are about it: say that briefly and offer them.") if language == "en" else
                     f" ما عندنا الآن كتاب متوفر مصنّف «{named}»، لكن الكتب في facts من تصنيفات أخرى وتتناول الموضوع نفسه: قل ذلك باختصار ثم اعرضها.")
    if topics_mentioned:
        named = "، ".join(topics_mentioned)
        guidance += ((f" Subject asked for: {named}. Some books in facts only mention it in their summary (their reason says so): offer one of them only if its summary really is about that subject, and never claim more than the summary says.") if language == "en" else
                     f" موضوع مطلوب: {named}. بعض الكتب في facts تذكره نبذتها فقط (سببها يقول ذلك): لا تقدّم كتابًا منها على أنه عن الموضوع إلا إذا كانت نبذته (summary) فعلًا عنه، ولا تزد على ما في النبذة.")
    if topics_semantic:
        named = "، ".join(topics_semantic)
        guidance += ((f" Subject asked for: {named}. Books in facts whose reason says «close to the subject» were found by meaning, not by the word itself: read their summary, present them as close to the subject only if the summary really is, and say so honestly.")
                     if language == "en" else
                     f" موضوع مطلوب: {named}. الكتب في facts التي يقول سببها «قريب من موضوع» اختيرت بالمعنى لا بالكلمة نفسها: اقرأ نبذتها (summary)، وقدّمها على أنها قريبة من الموضوع فقط إذا كانت نبذتها فعلًا كذلك، وقل ذلك بصراحة.")
    if topics_found or topics_mentioned:
        # «أتعلم الاستثمار من الصفر» with only economics books, «كتب طبخ» with no recipe book (2026-10-07)
        guidance += (" If none of these books is the kind the visitor asked for (for example a practical beginner's guide or a recipe book, when we only have books that discuss the subject), say that plainly first, then offer the closest ones as what they are."
                     if language == "en" else
                     " إذا لم يكن بين هذه الكتب النوع الذي طلبه الزائر فعلًا (مثل دليل عملي للمبتدئ أو كتاب وصفات، وعندنا فقط كتب تتكلم عن الموضوع)، فقل ذلك بصراحة أولًا ثم اعرض الأقرب على حقيقته.")
    unsupported = [u for u in unsupported if u not in topics_found and u not in topics_mentioned and u not in topics_semantic]
    if unsupported:
        guidance += ((" The search cannot reliably apply these wishes: " + ", ".join(unsupported) + ". Do not claim the catalog lacks this data.") if language == "en" else
                     " البحث لا يستطيع تطبيق هذه الرغبات بدقة: " + "، ".join(unsupported) + ". لا تقل إن بياناتها غير موجودة في المكتبة.")
    # prizes and screen adaptations asked for (2026-10-08): only what our records hold, said exactly
    honor_wish = honor_labels(prefs)
    if honor_wish and prefs.get("awards") and pool and not author_only:
        # the whole count, so «مين عندكم» / «وش فاز غيره» is not answered as if facts were all we have;
        # won by the book itself, by its author, or only on a list are counted apart (QA2 re-run, case 27)
        _kinds = {"book": set(), "author": set(), "list": set()}
        for b in pool:
            labels_ = honors.matches_award(b["book_id"], prefs["awards"], prefs.get("award_year"),
                                           prefs.get("award_min_year"), prefs.get("award_max_year"))
            k = (normalize(b["title"]), normalize(b.get("author")))
            _kinds["book" if any(x.startswith("فاز") for x in labels_) else "author" if any(x.startswith("مؤلفه فاز") for x in labels_) else "list"].add(k)
        _kinds["author"] -= _kinds["book"]
        _kinds["list"] -= _kinds["book"] | _kinds["author"]
        _n_people = len({author_flow.display(b.get("author")) for b in pool})
        if _n_people > 1:
            nb, na, nl = len(_kinds["book"]), len(_kinds["author"]), len(_kinds["list"])
            guidance += ((f" In stock now for this prize wish: {nb} books that won it themselves, {na} more whose author won it, {nl} more that were only shortlisted / longlisted / nominated ({_n_people} authors in all). facts hold only some: when asked who / how many / what else, give the number that fits the question (a «who won» question: the winners), then a few examples, and offer more.")
                         if language == "en" else
                         f" المتوفر عندنا الآن لطلب الجائزة: {fmt_num(nb)} كتابًا فاز هو نفسه، و{fmt_num(na)} كتابًا فاز مؤلفه، و{fmt_num(nl)} كتابًا وصل للقوائم أو رُشّح فقط (لـ{fmt_num(_n_people)} مؤلفًا). facts فيها بعضها فقط: إذا سأل «مين» أو «كم» أو «وش غيره» فقل العدد المناسب لسؤاله (سؤال «وش فاز» = الفائزة) ثم أمثلة، واعرض أن تذكر المزيد.")
    if prefs.get("award_max_year") or prefs.get("award_years_said"):
        _span = ((f"from {prefs['award_min_year']} " if prefs.get("award_min_year") else "") + (f"until {prefs['award_max_year']}" if prefs.get("award_max_year") else "")).strip()
        _span_ar = ((f"من {prefs['award_min_year']} " if prefs.get("award_min_year") else "") + (f"حتى {prefs['award_max_year']}" if prefs.get("award_max_year") else "")).strip()
        guidance += ((f" The visitor asked for prizes {_span}: the books in facts are in that span; give each prize's year.")
                     if language == "en" else f" الزائر طلب جوائز {_span_ar}: كتب facts جوائزها ضمن هذه السنوات؛ اذكر سنة كل جائزة.")
    if honor_wish:
        named = "، ".join(honor_wish)
        if any(f["available"] and (f.get("awards") or f.get("author_awards") or f.get("adaptations")) for f in facts):
            guidance += ((f" The visitor asked for: {honors.to_english(named)}. The matching books in facts carry it under awards / author_awards / adaptations: name the prize or the film exactly as written there, with its year.")
                         if language == "en" else
                         f" الزائر طلب: {named}. الكتب المطابقة في facts مذكور فيها ذلك (awards أو author_awards أو adaptations): اذكر الجائزة أو العمل المرئي كما هو مكتوب هناك وبسنته.")
        else:
            guidance += ((f" We found no available book recorded as: {honors.to_english(named)}. Say so plainly, add that our records of prizes and adaptations are still being completed, and ask what else they would like. Never name a prize or a film from your own knowledge.")
                         if language == "en" else
                         f" ما لقينا في سجلاتنا كتابًا متوفرًا {named}. قل ذلك بصراحة، وأضف أن سجلات الجوائز والأفلام عندنا ما زالت تُستكمل، واسأله وش يبغى بداله. لا تذكر جائزة أو فيلمًا من معرفتك.")
            open_questions.record("adaptation" if prefs.get("adaptation") and not prefs.get("awards") else "award", named)
    guidance += " Facts are a selected candidate list, NOT the entire inventory. Never infer that an author's works are absent from the library from their absence here. Author metadata may be incomplete."
    people = author_request.get("people") or {}
    if len(people) >= 2:
        # «لغازي القصيبي أو تركي الحمد»: each named author on his own (QA2 2026-10-09)
        def _has_copies(cats):
            return any(is_available(b) and b.get("author") in cats for b in all_books.values())
        # in the catalog without copies now, or not in it at all: either way «none of his books now»
        none_now = [p for p, cats in people.items() if not cats or not _has_copies(cats)]
        have = [p for p in people if p not in none_now]
        guidance += ((" The visitor named several authors: " + ", ".join(people) + ". The books in facts are by "
                      + ", ".join(have) + "; mention each of them.") if language == "en" else
                     (" الزائر سمّى أكثر من مؤلف: " + "، ".join(people) + ". كتب facts لـ" + " و".join(have)
                      + "؛ اذكر كل واحد منهم بكتبه."))
        if none_now:
            guidance += ((" We have no books in stock now by: " + ", ".join(none_now) + ". Say so plainly (\"we have none of his books at the moment\").")
                         if language == "en" else
                         " ما عندنا الآن كتب متوفرة لـ: " + "، ".join(none_now) + ". قل ذلك بوضوح («ما عندنا له كتب متوفرة الآن»).")
    if prefs.get("authors"):
        available_authors = [b.get("author") for b in all_books.values() if is_available(b)]
        shown_authors = [all_books[f["id"]].get("author") for f in facts if f["available"] and f["id"] in all_books]
        def _in_stock(name):
            # «لدان براون»: the name without a joined preposition; «هاروكي موركامي»: a misspelling of an
            # author whose books are in facts is not «missing» (QA2 2026-10-09, cases 18 and 20)
            for n in [name] + [name[len(p):] for p in ("لل", "ل", "و", "ب") if name.startswith(p) and len(name.split()[0]) > len(p) + 2]:
                # the author's record by any of his spellings («دستوفسكي»، "kafka") and its books in stock;
                # only without a record, the name as written (QA 2026-10-08, case 49: Kundera «missing»)
                rec = authors.lookup(n) or authors.lookup(author_flow.display(n))
                if rec:
                    if any(is_available(all_books[i]) for i in authors.book_ids(rec["author_id"]) if i in all_books) \
                            or any(author_matches(x, y) for x in authors.names_of(rec) for y in available_authors):
                        return True
                elif any(author_matches(n, x) for x in available_authors):
                    return True
                want = authors.key(n).split()
                if len(want) >= 2 and any(all(any(authors._same_word(w, x) for x in authors.key(a).split()) for w in want)
                                          for a in shown_authors if a):
                    return True
            return False
        covered = [authors.key(p) for p in people] if len(people) >= 2 else []
        missing_authors = [a for a in prefs["authors"]
                           if not any(c and c in authors.key(a) for c in covered) and not _in_stock(a)]
        if missing_authors:
            # each named author is checked on his own: «درويش ونزار» with only Darwish in stock (2026-10-06)
            guidance += (" No available book was found under: " + ", ".join(missing_authors)
                         + ". Say plainly that you found no available books by that author in our current records and that a bookseller can double-check; do not say you lack details.")
    if pub_years:
        lo, hi = pub_years
        _span = (f"{lo}–{hi}" if lo and hi else f"from {lo} on" if lo else f"up to {hi}")
        _span_ar = (f"من {lo} إلى {hi}" if lo and hi else f"من {lo} وبعدها" if lo else f"حتى {hi}")
        guidance += ((f" The visitor wants books published {_span}: " + ("none available is recorded with such a year; say so and ask what else they would like." if pub_missing
                       else "the books in facts carry that publication year (field year); mention it."))
                     if language == "en" else
                     (f" الزائر يبغى كتبًا منشورة {_span_ar}: " + ("ما لقينا كتابًا متوفرًا مسجلة سنة نشره ضمن ذلك؛ قل ذلك واسأله وش يبغى بداله." if pub_missing
                       else "كتب facts سنة نشرها ضمن ذلك (الحقل year)؛ اذكرها.")))
    if edition:
        _ed = {"en": ("English", "الإنجليزية"), "ar": ("Arabic", "العربية")}[edition]
        guidance += ((f" The visitor wants copies in {_ed[0]}: " + ("none is available; say so and ask what else they would like." if edition_missing
                       else "the books in facts are copies in that language."))
                     if language == "en" else
                     (f" الزائر يبغى نسخًا باللغة {_ed[1]}: " + ("ما لقينا نسخة متوفرة بهذه اللغة؛ قل ذلك واسأله وش يبغى بداله." if edition_missing
                       else "كتب facts نسخها بهذه اللغة.")))
    if country:
        _place = "الخليج" if country[0] == "خليجي" else " أو ".join(country[1][:2])
        if country_missing:
            guidance += (f" The visitor asked for authors from {_place}: we found no available book by an author recorded as from there. Say so, add that our records of authors' countries are still being completed, and ask what else they would like."
                         if language == "en" else
                         f" طلب الزائر كتّابًا من {_place}: ما لقينا كتابًا متوفرًا لمؤلف مسجّل أنه من هناك. قل ذلك، وأضف أن بيانات بلدان المؤلفين عندنا ما زالت تُستكمل، واسأله وش يبغى بداله.")
        else:
            guidance += (f" The visitor asked for authors from {_place}: the books in facts are by authors recorded as from there."
                         if language == "en" else
                         f" طلب الزائر كتّابًا من {_place}: كتب facts لمؤلفين مسجّلين أنهم من هناك.")
    if prefs.get("awards") and prefs.get("award_min_year") and wants_recent(user_messages[-2:]):
        guidance += (f" The visitor wants recent prizes: start with the newest, give each prize's year, and prefer the book's own prize over its author's."
                     if language == "en" else
                     f" الزائر يبغى جوائز حديثة: ابدأ بالأحدث واذكر سنة كل جائزة، وقدّم جائزة الكتاب نفسه على جائزة مؤلفه.")
    if child_theme:
        guidance += (f" The visitor asked for {child_theme} books for a child: we keep no such category, so these are children's books that touch on it; say so in a few words."
                     if language == "en" else
                     f" طلب الزائر كتب {child_theme} لطفل: ما عندنا تصنيف بهذا الاسم للصغار، وهذه كتب أطفال تتناول الموضوع؛ قل ذلك بكلمات قليلة.")
    if (clear_audience or adult_said(user_messages[-1])) and not child_said(user_messages[-1]) and earlier_aud:
        guidance += (" The visitor now wants books for an adult, not for the child mentioned earlier: present these books for an adult reader and do not mention the child or the child's age."
                     if language == "en" else
                     " الزائر يبغى الآن كتبًا للكبار وليس للطفل المذكور قبل: قدّم الكتب لقارئ كبير ولا تذكر الطفل ولا عمره.")
    if publisher_name:
        for f in facts:
            if f["id"] in all_books:
                f["publisher"] = all_books[f["id"]].get("publisher")
        guidance += (f" The visitor asked for books from the publisher «{publisher_name}»: the books in facts marked with it are from that publisher. Never say you lack publisher details."
                     if language == "en" else
                     f" طلب الزائر كتبًا من «{publisher_name}»: الكتب في facts المذكور فيها هذا الناشر من إصداراته. لا تقل إنه ما عندك تفاصيل عن دور النشر.")
    if topic_widened:
        guidance += (" The subject was searched across the whole library, without the wishes from earlier messages; do not describe these books with those earlier wishes."
                     if language == "en" else
                     " بحثنا عن الموضوع في المكتبة كلها دون شروط الرسائل السابقة؛ لا تصف هذه الكتب بتلك الشروط.")
    if not any(f["available"] for f in facts):
        guidance += (" No matching books are available; say so plainly and ask about an alternative."
                     if language == "en" else " لا توجد كتب متوفرة مطابقة؛ قل ذلك بصراحة واسأله عن بديل.")

    debug = {"mode": "ai", "intent": it["intent"], "prefs": it["prefs"],
             "author_request": author_request, "author_style": it.get("author_style"), "style_source": style_source,
             "author_record": author_record and author_record["author_id"],
             "mentioned_titles": it["mentioned_titles"], "wants_new_options": it["wants_new_options"],
             "relaxed_subgenres": relaxed_subgenres,
             "candidates": [{"id": f["id"], "title": f["title"], "available": f["available"],
                             "why": f["why_it_matches"]} for f in facts],
             "guidance": guidance}
    if language != "en":
        # English guidance lines say "a bookseller"; the model translated it as «البوّاق» / «الكتبّاس»
        # (QA 2026-10-08): the Arabic words are given
        guidance = guidance.replace("a bookseller", "«موظف المكتبة»").replace("bookseller", "«موظف المكتبة»")
    resp = (chat.respond(conv, json.dumps(facts, ensure_ascii=False), guidance, active, language)
            if language == "en" else chat.respond(conv, json.dumps(facts, ensure_ascii=False), guidance, active))
    if resp is None:
        out = chat_keyword_fallback(user_messages, all_books, set(shown), cards, language)
        out["understanding"] = "keywords"
        out["_debug"].update(interpreted=debug, failed_stage="respond",
                             call=dict(chat.last_failure.get("call") or {}))
        return out
    # what the visitor asked and our data could not answer: short phrases only (layer 4, 2026-10-08)
    for p in topic_phrases:
        if p not in topics_found and p not in topics_mentioned and p not in topics_semantic:
            open_questions.record("topic", p)
    for p in unsupported:
        if p not in topic_phrases:
            open_questions.record("wish", p)
    if missing_title:
        open_questions.record("missing_book", it["mentioned_titles"][0])
    if resp.get("unanswered"):
        open_questions.record("question", resp["unanswered"], asked_books[0]["title"] if asked_books else None)
    # a book the visitor named is shown whatever the earlier wishes (language, length ...), but
    # never past the age check (library trial, 2026-10-08)
    valid = {f["id"] for f in facts if f["available"]
             and ((f["id"] in suitable and f["id"] in strict)
                  or (reason_by_id[f["id"]][1] == "asked"
                      and age_allowed(cards.get(f["id"]), pprefs.get("audience"), pprefs.get("avoid") or (),
                                      all_books.get(f["id"]), pprefs.get("reader_age"))))}
    ids = []
    for i in resp["show_book_ids"]:
        if i in valid and i not in ids:
            ids.append(i)
    # the visitor named it and the reply talks about it: its card is shown (library trial, 2026-10-08:
    # «أن تقرأ لوليتا في طهران» was described but not shown)
    _said_reply = " " + " ".join(normalize(resp["reply"]).split()) + " "
    for b in reversed(asked_books):
        if (b["book_id"] in valid and b["book_id"] not in ids
                and " " + " ".join(normalize(b["title"]).split()) + " " in _said_reply):
            ids.insert(0, b["book_id"])
    # a book the visitor asked about by name is never pushed out of the four cards by others
    ids = [i for i in ids if reason_by_id[i][1] == "asked"] + [i for i in ids if reason_by_id[i][1] != "asked"]
    # the same book in several editions shows as one card (library trial, 2026-10-08: «1984» ×4)
    _seen_books, _unique = set(), []
    for i in ids:
        k = (" ".join(normalize(all_books[i]["title"]).split()), " ".join(normalize(all_books[i].get("author")).split()))
        if k not in _seen_books:
            _seen_books.add(k)
            _unique.append(i)
    ids = _unique
    books = []
    for i in ids[:4]:
        why, match = reason_by_id[i]
        # a book shown earlier that still fits is not «a book you asked about» (2026-10-06)
        reason = (why if match in ("direct", "filler", "related", "reference")
                  else ("Shown to you earlier, and it still fits." if language == "en" else "عرضته لك قبل، وما زال يناسب طلبك.") if match == "shown"
                  else "سألت عنه، وهو متوفر الآن.")
        books.append(dict(all_books[i], reason=reason, match=match))
    debug["model_book_ids"] = resp["show_book_ids"]
    debug["calls"] = {"interpret": interpret_call, "respond": dict(llm.last_call)}
    debug["dropped_ids"] = [i for i in resp["show_book_ids"] if i not in valid]
    return {"reply": resp["reply"], "books": [with_origin(b, language) for b in books], "suggestions": resp["suggestions"],
            "understanding": "ai", "provider": active[0], "model": active[1],
            "audience": pprefs.get("audience"),
            "audience_age": audience_age(user_messages) if pprefs.get("audience") else None,
            "audience_gender": audience_gender(user_messages) if pprefs.get("audience") else None,
            "context": chat_context(prefs, pprefs, user_messages, publisher_name, country if country and not country_missing else None,
                                    topics_found + [x for x in topics_mentioned if x not in topics_found], language,
                                    edition if edition and not edition_missing else None,
                                    pub_years if pub_years and not pub_missing else None),
            "_debug": debug}


RATE_LIMITED = "وصلنا حد الرسائل المسموح لهذا الجهاز مؤقتًا. انتظر دقائق بسيطة ثم كمّل، أو اسأل موظف المكتبة."
CHAT_FAULT = "صار عندي خلل بسيط وأنا أبحث. جرّب تكتب طلبك مرة ثانية، أو اسأل موظف المكتبة."
CHAT_FAULT_EN = "Something went wrong while I was searching. Please try again, or ask a bookseller."
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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400
    language = data.get("language") if data.get("language") in ("ar", "en") else "ar"
    if not limits.allow_request(request.remote_addr, testlog.clean_id(data.get("conversation_id"))):
        return jsonify({"error": "rate_limited", "reply": RATE_LIMITED_EN if language == "en" else RATE_LIMITED, "books": [], "suggestions": []}), 429
    t0 = time.time()
    active = active_within_budget()
    try:
        result = run_chat(data.get("messages"), active=active if active else False, language=language,
                          clear_audience=data.get("clear_audience") is True,
                          drop_context=[x for x in (data.get("drop_context") or [])[:20] if isinstance(x, str)]
                          if isinstance(data.get("drop_context"), list) else None)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        # a fault in the chat must never reach the visitor as a server error (QA2 2026-10-09): it is
        # logged, the keyword search answers instead, and only if that fails too a polite line
        app.logger.exception("run_chat failed")
        try:
            result = run_chat(data.get("messages"), active=False, language=language)
            result.setdefault("_debug", {})["failed_stage"] = "exception"
        except Exception:
            app.logger.exception("keyword fallback failed")
            result = {"reply": CHAT_FAULT_EN if language == "en" else CHAT_FAULT, "books": [], "suggestions": [],
                      "understanding": "error", "_debug": {"failed_stage": "exception"}}
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


@app.route("/api/admin/llm/models", methods=["GET"])
def admin_llm_models():
    """Models visible to this provider account; keys never leave the server."""
    pid = str(request.args.get("provider", ""))
    if pid not in llm.PROVIDERS:
        return jsonify({"ok": False, "error": "مزوّد غير معروف"}), 400
    try:
        result = llm.list_provider_models(pid, refresh=request.args.get("refresh") == "1")
        return jsonify({"ok": True, **result})
    except llm.LLMError as e:
        provider = next((p for p in llm.status()["providers"] if p["id"] == pid), {})
        return jsonify({"ok": False, "error": str(e), "fallback": provider.get("model_options", [])}), 502


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
              "moods", "emotional_weight", "reader_level", "use_cases", "sensitive_content",  # age_suitability hidden 2026-10-04
              # card v2 (2026-09-28)
              "style_complexity", "opening_grip", "ending_tone", "narrative_structure",
              "practicality", "prior_knowledge", "series_dependency"]
ATTR_NAMES_AR = {
    "age_suitability": "الفئة العمرية", "sensitive_content": "تنبيهات المحتوى",
    "format": "الشكل", "genres": "التصنيفات", "perceived_length": "الطول المُدرك",
    "language_difficulty": "سهولة اللغة", "pace": "الإيقاع", "moods": "المزاج",
    "emotional_weight": "الثقل العاطفي", "reader_level": "مستوى القارئ", "use_cases": "يناسب",
    "style_complexity": "الأسلوب", "opening_grip": "البداية", "ending_tone": "نبرة النهاية",
    "narrative_structure": "بناء السرد", "practicality": "طبيعة المحتوى", "prior_knowledge": "الخلفية المطلوبة",
    "series_dependency": "السلسلة",
}
FORMAT_LABELS = {"novel": "رواية", "short_stories": "قصص قصيرة", "biography": "سيرة", "history": "تاريخ",
                 "thought": "فكر", "psychology": "علم نفس", "self_dev": "تطوير ذاتي", "science": "علوم",
                 "poetry": "شعر", "play": "مسرحية", "travel": "أدب رحلات", "religious": "ديني",
                 "children": "أطفال", "young_adult": "يافعين", "business": "أعمال",
                 "comics": "قصص مصورة", "essays": "مقالات ونصوص", "criticism": "نقد أدبي", "arts": "فنون وسينما", "sociology": "علم اجتماع",
                 "other": "آخر"}
VALUE_LABELS = {
    "format": FORMAT_LABELS, "genres": FORMAT_LABELS,
    "perceived_length": {"very_short": "قصير جدًا", "short": "قصير", "medium": "متوسط",
                         "long": "طويل", "very_long": "طويل جدًا"},
    "language_difficulty": LANG_LABELS,
    "pace": PACE_SHORT, "moods": MOOD_LABELS, "emotional_weight": WEIGHT_LABELS,
    "reader_level": LEVEL_LABELS, "use_cases": USE_LABELS,
    "age_suitability": AGE_LABELS, "sensitive_content": SENSITIVE_LABELS,
    "style_complexity": {"direct": "مباشر", "literary": "أدبي", "philosophical": "فلسفي", "experimental": "تجريبي"},
    "opening_grip": {"fast": "تشد من البداية", "slow": "تحتاج صبرًا في البداية"},
    "ending_tone": {"happy": "سعيدة", "uplifting": "متفائلة", "bittersweet": "حلوة مُرّة", "sad": "حزينة",
                    "open": "مفتوحة", "shocking": "صادمة"},
    "narrative_structure": {"linear": "خطي", "interwoven": "متداخل الأزمنة", "multiple_voices": "عدة رواة",
                            "letters": "رسائل", "essays": "مقالات مستقلة"},
    "practicality": {"theoretical": "نظري", "practical": "عملي", "exercises": "فيه تمارين",
                     "personal_experience": "من تجربة شخصية"},
    "prior_knowledge": {"none": "لا يحتاج خلفية", "some": "يفيد وجود خلفية", "specialist": "للمتخصصين"},
    "series_dependency": {"standalone": "يُقرأ وحده", "standalone_start": "الجزء الأول من سلسلة",
                          "requires_previous": "يحتاج قراءة ما قبله"},
}
SEVERITY_LABELS = {1: "خفيفة", 2: "واضحة", 3: "قوية"}
FREQUENCY_LABELS = {"passing": "عابر", "recurring": "متكرر"}
RESEARCH_LABELS = {"matched": "تم التعرف على الكتاب", "needs_confirmation": "يحتاج تأكيد هوية الكتاب",
                   "not_found": "لم يُعثر على الكتاب في المصادر", "not_a_book": "منتج وليس كتابًا للقراءة"}
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


def _profile_formats():
    conn = get_connection()
    try:
        return {r["book_id"]: {"format": r["format"]} for r in conn.execute(
            "SELECT book_id, format FROM book_profiles WHERE overall_status IN ('enriched','needs_review','approved')")}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


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
    answered = confirmations.answered_ids()
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
            # «المكتبة» view (2026-09-30): grouping, sorting and chip filters
            "publisher": b.get("publisher"), "language": b.get("language"),
            "original_language": b.get("original_language"), "translated": bool(b.get("translator")),
            "research_status": b.get("research_status"), "researched_at": b.get("researched_at"),
            "publication_year": b.get("publication_year"), "origin": origin_label(b),
            "confirmation_answered": b["book_id"] in answered,
        })
    return jsonify(out)


STYLE_ATTRS = {"formats": "format", "subgenres": None, "moods": "moods", "pace": "pace",
               "typical_length": "perceived_length", "themes": None}


def _style_labels(style):
    """Arabic labels for an author's catalog style (subgenres/themes are already Arabic text)."""
    if not style:
        return None
    out = {"books": style.get("books", 0)}
    for k, attr in STYLE_ATTRS.items():
        v = style.get(k)
        vals = v if isinstance(v, list) else ([v] if v else [])
        out[k] = [value_label(attr, x) if attr else x for x in vals if x]
    return out


@app.route("/api/admin/authors")
def admin_authors():
    rows = authors.list_authors()
    for a in rows:
        a["style"] = _style_labels(a["style"])
    return jsonify(rows)


@app.route("/api/admin/authors/<int:author_id>")
def admin_author(author_id):
    d = authors.author_detail(author_id)
    if d is None:
        return jsonify({"error": "author not found"}), 404
    d["style"] = _style_labels(d["style"])
    d["awards"] = honors.rows_for_author(author_id)
    d["honor_options"] = honors.options()
    for b in d["books_list"]:
        b["cover_url"] = b["cover_url"] if covers.has_local_cover(b) else None
        b["status_label"] = STATUS_LABELS.get(b["status"], b["status"])
    return jsonify(d)


@app.route("/api/admin/authors/<int:author_id>", methods=["POST"])
def admin_author_update(author_id):
    data = request.get_json(silent=True) or {}
    user = acting_user() or {}
    try:
        before, after = authors.update_author(author_id, data, user.get("display_name") or user.get("username"))
    except LookupError:
        return jsonify({"ok": False, "error": "المؤلف غير موجود"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    auth.audit(acting_user(), "author.update", author_id,
               {k: [before[k], v] for k, v in after.items() if before[k] != v})
    return jsonify({"ok": True})


@app.route("/api/admin/authors/merge", methods=["POST"])
def admin_author_merge():
    data = request.get_json(silent=True) or {}
    try:
        res = authors.merge_authors(int(data.get("keep")), int(data.get("drop")))
    except LookupError:
        return jsonify({"ok": False, "error": "المؤلف غير موجود"}), 404
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc) if isinstance(exc, ValueError) and str(exc) and not str(exc).startswith("invalid") else "طلب غير صالح"}), 400
    auth.audit(acting_user(), "author.merge", data.get("keep"), res)
    return jsonify({"ok": True, **res})


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
    for a in card_review.V2_SINGLE:
        card_values[a] = [p.get(a)] if p.get(a) else []
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
                "severity": ev.get("severity"),
                "severity_label": SEVERITY_LABELS.get(ev.get("severity")) or ("غير معلومة" if attr == "sensitive_content" and ev["attribute_value"] != "none" else None),
                "frequency_label": FREQUENCY_LABELS.get(ev.get("frequency")), "note": ev.get("note_ar"),
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
                    "last_enriched_at": p.get("last_enriched_at"), "themes": _json_list(p.get("themes"))},
        "about": {"hook": book.get("hook"), "subjects": _json_list(book.get("subjects")),
                  "subgenres": _json_list(book.get("subgenres")), "setting": book.get("setting"),
                  "series": book.get("series_name"), "series_position": book.get("series_position"),
                  "learning_outcomes": book.get("learning_outcomes"),
                  "research_status": book.get("research_status"),
                  "research_label": RESEARCH_LABELS.get(book.get("research_status")),
                  "research_note": book.get("research_note"),
                  "related": [{"id": r["target_book_id"], "title": r["target_title"],
                               "relation": relations.TYPES_AR.get(r["relation_type"], r["relation_type"]),
                               "reason": r["reason_ar"]} for r in relations.for_books([book_id]).get(book_id, [])]},
        "attributes": attributes, "sources": sources, "review": review,
        "confirmation": confirmations.for_card(book),
        "honors": honors.rows_for_book(book_id), "honor_options": honors.options(),
        "staff_notes": staff_notes.for_book(book_id),
    })


# Prizes and screen adaptations (2026-10-08): staff add a row with its source, or remove a wrong one.
@app.route("/api/admin/books/<int:book_id>/honors", methods=["POST"])
def admin_book_honor_add(book_id):
    data = request.get_json(silent=True) or {}
    table = "book_adaptations" if data.get("type") == "adaptation" else "book_awards"
    if get_book(book_id) is None:
        return jsonify({"ok": False, "error": "الكتاب غير موجود"}), 404
    user = acting_user() or {}
    try:
        rid = honors.add(table, book_id, data, "staff", user.get("display_name") or user.get("username"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    auth.audit(acting_user(), "book.honor.add", book_id, {"table": table, "row_id": rid, "data": {k: data.get(k) for k in
               ("award_key", "award_name", "result", "year", "kind", "title", "source_url")}})
    return jsonify({"ok": True, "duplicate": rid is None, "honors": honors.rows_for_book(book_id)})


@app.route("/api/admin/books/<int:book_id>/honors/<kind>/<int:row_id>", methods=["DELETE"])
def admin_book_honor_delete(book_id, kind, row_id):
    table = {"award": "book_awards", "adaptation": "book_adaptations"}.get(kind)
    row = honors.delete(table, row_id, book_id) if table else None
    if row is None:
        return jsonify({"ok": False, "error": "غير موجود"}), 404
    auth.audit(acting_user(), "book.honor.delete", book_id, {"table": table, "row": row})
    return jsonify({"ok": True, "honors": honors.rows_for_book(book_id)})


@app.route("/api/admin/authors/<int:author_id>/awards", methods=["POST"])
def admin_author_award_add(author_id):
    data = request.get_json(silent=True) or {}
    if authors.author_detail(author_id) is None:
        return jsonify({"ok": False, "error": "المؤلف غير موجود"}), 404
    user = acting_user() or {}
    try:
        rid = honors.add("author_awards", author_id, data, "staff", user.get("display_name") or user.get("username"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    auth.audit(acting_user(), "author.award.add", author_id, {"row_id": rid, "award_key": data.get("award_key"), "year": data.get("year")})
    return jsonify({"ok": True, "duplicate": rid is None, "awards": honors.rows_for_author(author_id)})


@app.route("/api/admin/authors/<int:author_id>/awards/<int:row_id>", methods=["DELETE"])
def admin_author_award_delete(author_id, row_id):
    row = honors.delete("author_awards", row_id, author_id)
    if row is None:
        return jsonify({"ok": False, "error": "غير موجود"}), 404
    auth.audit(acting_user(), "author.award.delete", author_id, {"row": row})
    return jsonify({"ok": True, "awards": honors.rows_for_author(author_id)})


# Questions the chat could not answer (layer 4, 2026-10-08): staff and managers read them in «البلاغات».
@app.route("/api/admin/inbox/questions")
def admin_open_questions():
    status = request.args.get("status", "open")
    offset = request.args.get("offset", "0")
    rows, total = open_questions.list_rows(status, int(offset) if offset.isdigit() else 0)
    return jsonify({"items": rows, "total": total, "counts": open_questions.counts()})


@app.route("/api/admin/inbox/questions/<int:q_id>", methods=["POST"])
def admin_open_question_update(q_id):
    data = request.get_json(silent=True) or {}
    user = acting_user() or {}
    try:
        row = open_questions.set_status(q_id, data.get("status"), user.get("display_name"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if row is None:
        return jsonify({"error": "غير موجود"}), 404
    return jsonify({"ok": True, "counts": open_questions.counts()})


# «تعديلات مطلوبة» (Ibrahim, 2026-10-08): staff write in the book's card what should be changed;
# the note is kept with the book and listed in «البلاغات» → «تعديلات الموظفين». The book is not changed.
@app.route("/api/admin/books/<int:book_id>/notes", methods=["POST"])
def admin_book_note_add(book_id):
    book = get_book(book_id)
    if book is None:
        return jsonify({"error": "book not found"}), 404
    data = request.get_json(silent=True) or {}
    user = acting_user() or {}
    try:
        note_id = staff_notes.add(book, data.get("note"), user.get("display_name"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    auth.audit(user or None, "staff_note.add", note_id, {"book_id": book_id})
    return jsonify({"ok": True, "staff_notes": staff_notes.for_book(book_id), "counts": staff_notes.counts()})


@app.route("/api/admin/inbox/staff-notes")
def admin_staff_notes():
    status = request.args.get("status", "open")
    offset = request.args.get("offset", "0")
    rows, total = staff_notes.list_rows(status, int(offset) if offset.isdigit() else 0)
    for it in rows:
        if it.get("cover_url") and not covers.has_local_cover(it):
            it["cover_url"] = None
    return jsonify({"items": rows, "total": total, "counts": staff_notes.counts()})


@app.route("/api/admin/inbox/staff-notes/<int:note_id>", methods=["POST"])
def admin_staff_note_update(note_id):
    data = request.get_json(silent=True) or {}
    user = acting_user() or {}
    try:
        row = staff_notes.set_status(note_id, data.get("status"), user.get("display_name"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if row is None:
        return jsonify({"error": "غير موجود"}), 404
    auth.audit(user or None, "staff_note." + row["status"], note_id, {"book_id": row["book_id"]})
    return jsonify({"ok": True, "counts": staff_notes.counts(), "staff_notes": staff_notes.for_book(row["book_id"])})


# «تأكيد هوية الكتاب» (2026-10-04): the staff answer is stored for the manager, the book is not changed here.
@app.route("/api/admin/books/<int:book_id>/confirmation", methods=["POST"])
def admin_book_confirmation(book_id):
    book = get_book(book_id)
    if book is None:
        return jsonify({"error": "book not found"}), 404
    data = request.get_json(silent=True) or {}
    user = acting_user() or {}
    name = user.get("display_name") or user.get("username")
    try:
        if data.get("undo") is True:
            confirmations.undo(book_id, name)
            auth.audit(acting_user(), "book.confirmation.undo", book_id, None)
        else:
            confirmations.save(dict(book), data.get("choice"), data.get("comment"), name)
            auth.audit(acting_user(), "book.confirmation", book_id,
                       {"choice": data.get("choice"), "comment": str(data.get("comment") or "")[:200]})
    except LookupError:
        return jsonify({"error": "لا يوجد تأكيد للتراجع عنه"}), 404
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    return admin_book_card(book_id)


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


# Manager only (not under STAFF_PREFIXES): take the open answers out to Claude, bring corrections back.
@app.route("/api/admin/confirmations")
def admin_confirmations_export():
    return jsonify(confirmations.export_package(request.args.get("all") == "1"))


@app.route("/api/admin/confirmations/corrections", methods=["POST"])
def admin_confirmations_corrections():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):  # a list or a bare value is not a corrections request (audit 2026-10-07)
        return jsonify({"ok": False, "error": "طلب غير صالح", "items": []}), 400
    package = data.get("package")
    if data.get("apply") is not True:
        return jsonify(confirmations.plan_corrections(package))
    user = acting_user() or {}
    try:
        res = confirmations.apply_corrections(package, user.get("display_name") or user.get("username"))
    except ValueError as e:
        plan = e.args[0] if e.args and isinstance(e.args[0], dict) else {"ok": False, "error": "طلب غير صالح", "items": []}
        return jsonify(plan), 400
    auth.audit(acting_user(), "confirmations.corrections", None,
               {"books": res["books"], "handled": res["handled"], "backup": res["backup"],
                "book_ids": [r["book_id"] for r in res["items"]][:100]})
    return jsonify({"ok": True, "applied": True, **res})


# ---------------------------------------------------------------------------
# Rewaa inventory import (admin). Dry-run first; apply writes one transaction.
# ---------------------------------------------------------------------------

COVER_UPLOAD_MAX = 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# Library events (Ibrahim, 2026-10-08): staff add them in «الفعاليات»; upcoming ones are shown on
# the visitor's start page under «فعالياتنا». See backend/events.py.
# ---------------------------------------------------------------------------

def _event_out(ev, books_by_id, language="ar", admin=False):
    out = {k: ev.get(k) for k in ("event_id", "title", "description", "event_date", "event_time", "end_time", "book_id")}
    out["image_url"] = ev.get("image_url") if events.has_image(ev) else None
    b = books_by_id.get(ev.get("book_id")) if ev.get("book_id") else None
    if b is not None and (admin or b.get("active")):
        out["book"] = with_origin(dict(b), language)
    if admin:
        out.update({k: ev.get(k) for k in ("active", "past", "created_by", "created_at", "updated_by", "updated_at")})
        out["own_image"] = bool(out["image_url"])
    if not out["image_url"] and out.get("book") and out["book"].get("cover_url"):
        out["image_url"] = out["book"]["cover_url"]   # no picture of its own: the book's cover
    return out


def _books_by_id():
    return {b["book_id"]: dict(b) for b in list_books(only_active=False)}


# «كتب الكاتب نفسه / من نفس الدار» (Ibrahim, 2026-10-08): the visitor taps the author or the
# publisher on a book page and sees the other books in stock by that author or from that house.
_NO_AUTHOR = {normalize(x) for x in ("غير معروف", "مجهول", "unknown", "")}


@app.route("/api/more")
def more_books():
    language = request.args.get("lang") if request.args.get("lang") in ("ar", "en") else "ar"
    kind = request.args.get("by")
    try:
        bid = int(request.args.get("book_id", ""))
    except ValueError:
        return jsonify({"error": "book_id required"}), 400
    book = get_book(bid)
    if book is None or kind not in ("author", "publisher"):
        return jsonify({"error": "not found"}), 404
    book = dict(book)
    all_books = [dict(b) for b in list_books(only_active=True)]
    if kind == "author":
        name = book.get("author") or ""
        if normalize(name).strip() in _NO_AUTHOR:
            return jsonify({"books": [], "name": name})
        ids = set()
        for n in [x.strip() for x in re.split(r"[،,]| و(?=\S)", name) if x.strip()][:3] or [name]:
            rec = authors.lookup(n)
            if rec:
                ids |= set(authors.book_ids(rec["author_id"]))
        found = [b for b in all_books if b["book_id"] in ids or normalize(b.get("author") or "") == normalize(name)]
        label = name
    else:
        key = publisher_key(book.get("publisher"))
        if len(key) < 3:
            return jsonify({"books": [], "name": book.get("publisher") or ""})
        found = [b for b in all_books if publisher_key(b.get("publisher")) == key]
        label = book.get("publisher")
    # the same work in several editions once; the book the visitor is reading is left out
    seen, out = {(normalize(book["title"]).strip(), normalize(book.get("author") or "").strip())}, []
    for b in sorted(found, key=lambda b: (-(b.get("quantity") or 0), b["title"])):
        k = (normalize(b["title"]).strip(), normalize(b.get("author") or "").strip())
        if b["book_id"] == bid or k in seen:
            continue
        seen.add(k)
        out.append(with_origin(b, language))
    return jsonify({"books": out[:60], "total": len(out), "name": label})


@app.route("/api/events")
def public_events():
    language = request.args.get("lang") if request.args.get("lang") in ("ar", "en") else "ar"
    rows = events.upcoming()
    books = _books_by_id() if any(r.get("book_id") for r in rows) else {}
    return jsonify({"events": [_event_out(r, books, language) for r in rows], "today": events.today()})


@app.route("/api/events/<int:event_id>/calendar.ics")
def public_event_calendar(event_id):
    """«أضف إلى التقويم» (Ibrahim, 2026-10-09): the event as an .ics file for the phone's calendar."""
    ev = events.get(event_id)
    if not ev or not ev.get("active") or ev.get("past"):
        return jsonify({"error": "not found"}), 404
    b = get_book(ev["book_id"]) if ev.get("book_id") else None
    b = dict(b) if b else None
    body = events.ics(ev, b["title"] if b and b.get("active") else None)
    resp = app.response_class(body, mimetype="text/calendar")
    resp.headers["Content-Disposition"] = f'attachment; filename="thalath-event-{event_id}.ics"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/admin/events")
def admin_events():
    books = _books_by_id()
    return jsonify({"events": [_event_out(r, books, admin=True) for r in events.list_all()], "today": events.today()})


def _event_fields():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise events.EventError("طلب غير صالح")
    return data, events.validate(data, lambda bid: get_book(bid) is not None)


@app.route("/api/admin/events", methods=["POST"])
def admin_event_add():
    try:
        _, clean = _event_fields()
    except events.EventError as e:
        return jsonify({"error": str(e)}), 400
    user = acting_user() or {}
    event_id = events.add(clean, user.get("display_name") or user.get("username"))
    auth.audit(acting_user(), "event.add", event_id, {"title": clean["title"], "date": clean["event_date"]})
    return jsonify({"ok": True, "event_id": event_id})


@app.route("/api/admin/events/<int:event_id>", methods=["PUT"])
def admin_event_update(event_id):
    if events.get(event_id) is None:
        return jsonify({"error": "الفعالية غير موجودة"}), 404
    try:
        data, clean = _event_fields()
    except events.EventError as e:
        return jsonify({"error": str(e)}), 400
    user = acting_user() or {}
    active = data.get("active") if isinstance(data.get("active"), bool) else None
    events.update(event_id, clean, user.get("display_name") or user.get("username"), active)
    auth.audit(acting_user(), "event.update", event_id, {"title": clean["title"], "date": clean["event_date"]})
    return jsonify({"ok": True})


@app.route("/api/admin/events/<int:event_id>", methods=["DELETE"])
def admin_event_delete(event_id):
    ev = events.get(event_id)
    if ev is None or not events.delete(event_id):
        return jsonify({"error": "الفعالية غير موجودة"}), 404
    auth.audit(acting_user(), "event.delete", event_id, {"title": ev["title"], "date": ev["event_date"]})
    return jsonify({"ok": True})


@app.route("/api/admin/events/<int:event_id>/image", methods=["POST"])
def admin_event_image(event_id):
    if events.get(event_id) is None:
        return jsonify({"error": "الفعالية غير موجودة"}), 404
    f = request.files.get("file")
    if f is None:
        return jsonify({"error": "اختر صورة"}), 400
    data = f.read(COVER_UPLOAD_MAX + 1)
    try:
        url = events.save_image(event_id, data)
    except events.EventError as e:
        return jsonify({"error": str(e)}), 400
    auth.audit(acting_user(), "event.image", event_id, {"bytes": len(data)})
    return jsonify({"ok": True, "image_url": url})


@app.route("/api/admin/events/<int:event_id>/image", methods=["DELETE"])
def admin_event_image_delete(event_id):
    if events.get(event_id) is None:
        return jsonify({"error": "الفعالية غير موجودة"}), 404
    events.remove_image(event_id)
    auth.audit(acting_user(), "event.image.remove", event_id, {})
    return jsonify({"ok": True})


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


# «ليس كتابًا» (2026-09-30): delete a non-book product after a confirmation; restorable.
@app.route("/api/admin/books/<int:book_id>/delete", methods=["POST"])
def admin_book_delete(book_id):
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return jsonify({"ok": False, "error": "يلزم تأكيد الحذف"}), 400
    user = acting_user() or {}
    try:
        res = deleted_books.delete_book(book_id, data.get("reason") or "not_a_book",
                                        user.get("display_name") or user.get("username"))
    except LookupError:
        return jsonify({"ok": False, "error": "book not found"}), 404
    except ValueError:
        return jsonify({"ok": False, "error": "سبب غير صالح"}), 400
    authors.sync()
    auth.audit(acting_user(), "book.delete", book_id, {"title": res["title"], "reason": data.get("reason") or "not_a_book",
                                                       "deletion_id": res["deletion_id"]})
    return jsonify({"ok": True, **res})


@app.route("/api/admin/books/deleted")
def admin_books_deleted():
    return jsonify(deleted_books.list_deleted())


@app.route("/api/admin/books/deleted/<int:deletion_id>/restore", methods=["POST"])
def admin_book_restore(deletion_id):
    user = acting_user() or {}
    try:
        res = deleted_books.restore(deletion_id, user.get("display_name") or user.get("username"))
    except LookupError:
        return jsonify({"ok": False, "error": "غير موجود"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    authors.sync()
    auth.audit(acting_user(), "book.restore", res["book_id"], {"title": res["title"], "deletion_id": deletion_id})
    return jsonify({"ok": True, **res})


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
        user = acting_user() or {}
        result = rewaa_import.apply_plan(data.get("upload_id"), data.get("mapping"),
                                         data.get("missing_mode", "zero"),
                                         applied_by=user.get("display_name") or user.get("username"))
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
    try:
        relations.rebuild()  # relations between books are recomputed from the cards on every start
    except Exception as exc:  # never block the server on this
        print("relations rebuild failed:", exc, flush=True)
    try:
        authors.sync()  # author records and links are refreshed from the books on every start
    except Exception as exc:  # never block the server on this
        print("authors sync failed:", exc, flush=True)
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
