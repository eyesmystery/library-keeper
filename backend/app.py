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
import threading
import time

import auth
import author_flow
import authors
import card_review
import confirmations
import relations
import second_look
import dev_notes
import channel
import inbox_send
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
import series_parts
import open_questions
import staff_notes
import staff_ideas
import events
import picks
import start_paths
import embeddings
import outside_books
import chat_rules

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
                  "/api/admin/inbox", "/api/admin/events", "/api/admin/picks",
                  # «الإعدادات» for staff too (Ibrahim 2026-10-13 «ابغاك تعطي للموظفين صلاحية يدخلون عالاعدادات»، all of
                  # it): QR, library sections, staff accounts, AI model / keys / usage / test mode, audit log, about.
                  # «استيراد من رواء», «الإحصاءات» and applying identity corrections stay manager-only.
                  "/api/admin/trial", "/api/admin/sections", "/api/admin/users", "/api/admin/llm", "/api/admin/usage",
                  "/api/admin/testlog", "/api/admin/audit", "/api/admin/version")


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
    normalized_query = chat_rules._num_words(" ".join(normalized_query.split()))   # «عمره عشر سنين» (QAD 2026-10-13)
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
    for age in chat_rules.kid_ages(normalized_query):   # «بنتي ١٥» (QAD 2026-10-12, case 38)
        if 2 <= age <= 12:
            return "children"
        if 13 <= age <= 17:
            return "young_adult"
    words = _without_prize_name(normalized_query)
    for aud in ("children", "young_adult"):
        for h in AUDIENCE_KEYWORDS[aud]:
            i = keyword_prefs.find_phrase(words, h)
            # «مو رعب أطفال» / «ليس للأطفال» / "not for kids": the reader is NOT a child (2026-10-06)
            if i >= 0 and not _audience_negated(words, i):
                return aud
    return None


def _without_prize_name(normalized_query):
    """The query's words without those of a prize named in it: «غونكور طلاب الثانوية» is a prize for adult
    novels, the reader is not a secondary-school pupil (QAD 2026-10-13, case 7)."""
    words = normalized_query.split()
    try:
        name = honors.other_name_in(normalized_query)
    except Exception:
        name = None
    if not name:
        return words
    drop = set()
    for w in keyword_prefs.normalize(name).split():
        drop |= {w, keyword_prefs.strip_al(w), honors._stem(w)}
    return [w for w in words if w not in drop and keyword_prefs.strip_al(w) not in drop and honors._stem(w) not in drop]


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


_ANOTHER_ONE = re.compile(r" (?:شي|شيء|كتاب|قصه|روايه|واحد|غيره|غيرها|غير) (?:ثاني|ثانيه|اخر|اخري|غيره|غيرها|كذا) "
                          r"| (?:فيه|في|عندكم|عندك) (?:غيره|غيرها|ثاني|ثانيه) | كمان | (?:another|something else|anything else) ")
_YOUNG_EDITION = re.compile(r"(?:^| |\()(?:لل|ل)?(?:فتيان|ناشئه|ناشئين|اطفال|صغار|يافعين)(?:\)| |$)|for (?:kids|children|young readers)")

# QADFIX10 (AGY-20261020-01, Ibrahim 2026-10-20 22:5x «اصلحها الان»; QAD 2026-10-20 case 39.3): a message made only of
# follow-up words («شي ثاني»، «أقصر»، «أرخص»، «أبيها بالعربي») names no subject of its own
_Q10_FOLLOW = {normalize(w) for w in "شي شيء ثاني ثانيه ثانية اخر اخرى اخري غير غيره غيرها كمان بعد المزيد زياده زيادة طيب وش ايش "
               "اقصر الاقصر ارخص الارخص اخف اسهل واحد وحده وحدة ابي ابغى ابغي ودي اريد بس more another other else shorter cheaper "
               "something".split()}


def follow_up_only(text):
    n = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split())
    w = n.split()
    if not w or len(w) > 6:
        return False
    return bool(chat_rules.shortest_asked(text) or _ANOTHER_ONE.search(" " + n + " ") or chat_rules.refine_only(text)
                or all(x in _Q10_FOLLOW for x in w))


# QADFIX10 (AGY-20261020-01; QAD 2026-10-20 case 11.2): «شي أخف وأقصر من سر الزعفرانة» — the book compared with is not offered
_COMPARE_LEAD = re.compile(r"(?:(?:^| )[وب]?(?:اخف|اقصر|اسهل|اطول|اصعب|اثقل|اقل|اكثر|احسن|افضل|ارخص|اغلي|ابسط|اعمق|اسرع|اقوي|اجمل"
                           r"|اهدا|اهدي|اوضح|الطف|اصغر|اكبر|امتع|اقدم|احدث|اجدد)(?: \S+){0,3} من|(?:^| )(?:غير|بدل|بدال|عدا|ماعدا)"
                           # QADFIX11 (QAD 2026-10-21, case 11.1): «شي يشبه موسم الهجرة إلى الشمال» — the book named for likeness
                           r"|(?:^| )(?:يشبه|تشبه|شبه|يشابه|مثل|زي|زى|غرار|على غرار|بنفس اسلوب|بنفس جو)"
                           r"|lighter than|shorter than|easier than|better than|other than|instead of|rather than|like|similar to)$")


def compared_title(text, title, all_books=None):
    """True when the visitor names this title only to compare with it («أقصر من …»، «غير …»).
    QADFIX13 (replay case 1.2): the model may give the title in another form than the visitor typed it («جريمة وعقاب» for
    «الجريمة والعقاب»); with all_books, the catalog titles it matches are looked for in the message too."""
    t = " ".join(normalize(text or "").split())
    names = [title] + ([b.get("title") for b in match_titles([title], all_books)] if all_books and title else [])
    for name in names:
        ti = " ".join(normalize(name or "").split())
        k = t.find(ti) if ti else -1
        if k > 0 and _COMPARE_LEAD.search(t[:k].rstrip()):
            return True
    return False


def named_age_note(card, audience, avoid=(), book=None, age=None, language="ar"):
    """QADFIX5 #5 (QAD 2026-10-17, cases 7.1, 26.1): a book the visitor named for a child that the age check keeps
    off the cards -> (show_card, note for the model). Only off the children's / young readers' shelf, no blocking
    content flag: its card is shown and the model says it is a book for older readers. A blocking flag: no card,
    and the model says why instead of describing it as if it were offered."""
    if audience not in AGE_OK or age_allowed(card, audience, avoid, book, age):
        return True, None
    flags = [c for c in set(AGE_BLOCKING[audience]) | SEVERE_ONLY if _warning_blocks(card, audience, c)]
    flags += [c for c in avoid if _medium_or_better(card, "sensitive_content", c)]
    if not flags:
        return True, ("on the adult shelf, not a children's book: say so in a few words (it may suit a strong reader of that age)"
                      if language == "en" else "من رف الكبار وليس من كتب الأطفال واليافعين: قل ذلك بكلمات قليلة (قد يناسب قارئًا متمكنًا في هذا العمر)")
    return False, (f"not suitable for this reader ({', '.join(sorted(set(flags)))}): if you mention it, say plainly it does not suit the reader's age"
                   if language == "en" else
                   f"لا يناسب عمر القارئ ({'، '.join(sorted(set(flags)))}): إن ذكرته فقل بوضوح إنه لا يناسب عمره، ولا تقدّمه كترشيح")


def _age_range_ok(book, audience, age=None, animated=False):
    """A book outside the children's / young readers' shelves (a comic, a manga) whose researched or
    staff-given age range fits this reader (QA2 2026-10-09: no comics for a 13-year-old). Unknown
    range -> False: such a book is never offered to a child unchecked."""
    if not book:
        return False
    if book.get("age_min") is None and book.get("age_max") is None:
        # an edition made for young readers says so in its title: «روبنسون كروزو (للفتيان)» (QAD 2026-10-15, case 16.2)
        # a story the visitor asked for as a cartoon («قصص صارت أفلام كرتون لولدي ٨ سنين») and that was made into
        # an animated film (White Fang, Kidnapped): the cartoon says it suits a child (Ibrahim 2026-10-15, QAD case 16.2)
        return bool(_YOUNG_EDITION.search(normalize(book.get("title") or ""))) or bool(animated)
    if age is not None:
        return age_fit(book, age) > 0
    lo = book.get("age_min") or 0
    return lo <= (10 if audience == "children" else 15)


def age_allowed(card, audience, avoid=(), book=None, age=None, animated=False):
    """Eligibility of a book for the stated reader.

    audience children/young_adult: the book must be on that shelf (card format
    children / young_adult) and carry no blocking content flag. Anyone else:
    everything stays (meets_constraints keeps children's books out of a request
    that names no genre). Content the visitor asked to avoid excludes a book
    whenever the card flags it."""
    for c in avoid:
        if _medium_or_better(card, "sensitive_content", c):
            return False
    if audience == "young_adult":
        # QADFIX12 #7 (QAD 2026-10-22, cases 47.2–47.3; Ibrahim 2026-10-22: «ما ابغى الجواب يكون مفلتر … الجواب يضم الكتب اللي تناسب
        # الطلب … مع تحذير للروايات الي فيها محتوى للكبار»): a teenager gets every book that fits the request — the young readers'
        # shelf first (score_books) — and the reply warns about adult content from the book's facts; only content the visitor
        # asked to avoid keeps a book out
        return True
    if audience in AGE_OK:
        if (not card or card.get("format") not in AGE_OK[audience]) and not _age_range_ok(book, audience, age, animated):
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
    if ({"beginner"} & set(pprefs["reader_level"]) or {"habit_start"} & set(pprefs["use_cases"])) \
            and not tier_of("reader_level", "beginner"):
        # «كتب لقارئ مبتدئ»: few books carry a reader level, so the readers' other signs count — easy
        # language, no prior knowledge needed, a light or quick read, a short book; and specialist,
        # philosophical or experimental books step back (staff chat 2026-10-11)
        easy = [("language_difficulty", "very_easy", "لغته سهلة جدًا"), ("language_difficulty", "easy", "لغته سهلة"),
                ("prior_knowledge", "none", "ما يحتاج معرفة سابقة"), ("use_cases", "light_reading", "قراءة خفيفة"),
                ("use_cases", "quick_read", "قراءة سريعة"), ("use_cases", "slump_recovery", "يرجّعك للقراءة")]
        for attr, val, label in easy:
            tier = tier_of(attr, val)
            if tier == "high":
                score += 2
                if len(reasons) < 4:
                    reasons.append(label)
            elif tier == "medium":
                score += 1
        if tier_of("perceived_length", "short") or tier_of("perceived_length", "very_short"):
            score += 1
        hard = [("language_difficulty", "advanced"), ("prior_knowledge", "specialist"), ("prior_knowledge", "some"),
                ("style_complexity", "philosophical"), ("style_complexity", "experimental"), ("reader_level", "advanced"),
                ("perceived_length", "very_long")]
        if any(tier_of(a, v) for a, v in hard):
            score -= 3
    return score, reasons


def token_in(token, words):
    """Match a query word, allowing one attached prefix letter (لشكسبير → شكسبير)."""
    if token in words:
        return True
    return len(token) > 3 and token[0] in "لبوكف" and strip_al(token[1:]) in words


def fmt_num(n):
    return str(n).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))


def score_book(book, prefs, card=None):
    score = 0
    reasons = []
    if prefs.get("awards"):
        won = honors.matches_award(book["book_id"], prefs["awards"], prefs.get("award_year"), prefs.get("award_min_year"), prefs.get("award_max_year"), prefs.get("award_results"))
        if won:
            # the book's own prize first, then its author's, then a list / nomination (2026-10-08)
            # (QAD 2026-10-13, case 8.1: «رواية فازت بهوغو» showed Fire & Blood for its author's prize before Dune,
            # Hyperion and «كتاب المقبرة», which won it themselves: the prize's year outweighed whose prize it was)
            score += 6 if won[0].startswith("فاز") else 3 if won[0].startswith("مؤلفه") else 2
            # a newer prize ranks higher: 2024 before 1984 (library trial, 2026-10-08), within the same kind of win
            newest = honors.newest_award_year(book["book_id"], prefs["awards"], prefs.get("award_min_year"), prefs.get("award_max_year"))
            score += min(1.5, max(0, newest - 1950) / 50)
            reasons.append(won[0])
    if prefs.get("adaptation"):
        made = honors.matches_adaptation(book["book_id"], prefs["adaptation"], prefs.get("adaptation_max_year"), prefs.get("adaptation_origin"))
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
    for subgenre in matching_subgenres(book, prefs, card=card) + matching_subgenres(book, prefs, "soft_subgenres", card=card):
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


def matching_subgenres(book, prefs, key="subgenres", card=None):
    """The book's subgenres that answer the wished ones. QADFIX13 #4 (AGY-20261022-01 (3), QAD 2026-10-22 case 32.1
    «Hugo award winning sci-fi»): a book with no subgenre recorded is read from its own card's themes (the English
    «Dune», «Hyperion», «Foundation» carry «الخيال العلمي» there), so an empty field does not hide a book."""
    def words(value):
        return {strip_al(w) for w in normalize(value).split()}
    wanted = [words(v) for v in prefs.get(key, [])]
    own = _json_list(book.get("subgenres")) or ((card or {}).get("themes") or [])
    return [v for v in own
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


_ENGLISH_WORD = re.compile(r"(?:^| )(?:بال)?(?:انجليزي|انجليزيه|انقليزي|انقليزيه|الانجليزي|الانجليزيه|الانقليزي|الانقليزيه|بريطاني|بريطانيه|english|british)(?: |$)")


_GORE_NOT = re.compile(r"(?:nothing|no|not|without|non)[\s-]+(?:too\s+)?(?:gory|gore|bloody|graphic|violent|violence)"
                       r"|(?:بدون|بلا|من غير|مو|ما فيه|ماتكون|ما تكون|لا)\s+(?:\S+\s+)?(?:دم|دماء|دموي|دمويه|عنف|عنيف|عنيفه|قتل)")
_GORE_WORD = re.compile(r"دم|دماء|دموي|عنف|عنيف|قتل|gore|gory|blood|violen")
# QADFIX6 (QAD 2026-10-18, case 5.1): «ابغى شي يفصلني عن الدنيا» is a wish for a book that takes the reader away,
# not a book about «الدنيا»
_ESCAPE = re.compile(r"(?:يفصلني|تفصلني|يبعدني|تبعدني|ينسيني|تنسيني|يطلعني|تطلعني|ياخذني|تاخذني|اهرب|اتهرب|انسي|انسى|ابعد)"
                     r"(?: (?:عن|من|فيه|فيها|بعيد عن|من جو))? (?:ال)?(?:دنيا|واقع|عالم|دوام|شغل|هموم|ضغط|زحمه|روتين)(?: |$)"
                     r"|escape (?:from )?(?:reality|the world|work|it all)|forget (?:about )?(?:work|everything|the world)")
_ESCAPE_WORDS = {normalize(w) for w in "دنيا الدنيا واقع الواقع عالم العالم دوام الدوام شغل الشغل هموم الهموم ضغط الضغط "
                 "زحمه الزحمه روتين الروتين reality world work everything".split()}
# QADFIX6 (QAD 2026-10-18, case 29.1): «ابي كتاب تطوير ذات بس مو كله نصايح مكررة» — what follows «مو كله» is
# what the visitor does not want, never the subject searched for
_NEG_LEAD = re.compile(r"(?:^| )(?:مو|مب|مش|بدون|بلا|من غير|غير|لا|ما ابي|مابي|ما ابغى|مابغى|not|without|no)"
                       r"((?: \S+){1,4})")
_CARRY_FILLER = {normalize(w) for w in "دراما drama روايه روايات قصه قصص كتاب كتب ابي ابغى ابغي اريد ودي شي شيء حاجه "
                 "عن فيه عندكم عندك تنصحني رشح رشحي اقترح اقترحي".split()}
_hz = lambda w: w.replace("ئ", "ي").replace("ؤ", "و").replace("ء", "")
_NEG_STOP = {normalize(w) for w in "عن حول بخصوص about on بس لكن bas but".split()}
# QADFIX12 #1 (QAD 2026-10-22, case 20.2; the negation fixes of 2026-10-07 and QADFIX6 came back): «شي ثاني لنفس العمر بس مو عن
# الكورة» — the model read it right (no subject), but a fallback of the code took «الكورة» from «عن …» and the negation lists
# did not know «مو عن». One general rule for every subject the CODE takes from the visitor's words: a negation word before it in
# the same clause, with only linking words between («مو عن»، «ولا فيها»، «بدون»، «ما أبي شي عن»، "not about"), rejects it.
# «ما عندكم كتب عن …؟» asks, and «لا أبي عن …» answers: neither rejects. The model's own reading of what the visitor does not
# want (avoid_topics) is applied first, in any wording.
_NEG_HEADS = {normalize(w) for w in "مو مب مش ما لا ولا بدون بلا دون غير ليس ماهو مهو مافي مافيه ماابي مابي ماابغى مابغى "
              "not no without nothing never dont don doesnt isnt except".split()}
_NEG_LINK = {normalize(w) for w in "عن حول بخصوص في فيه فيها فيهم على ب شي شيء كتاب كتب روايه روايات قصه قصص ابي ابغى ابغي ابغا "
             "اريد ودي يكون تكون اي نوع ابدا about on of the a an any anything something books book stories novel novels want "
             "t to be with in".split()}
_NEG_ASKS = {normalize(w) for w in "عند عندك عندكم عندهم فيه في تعرف تعرفين تعرفون ودك ودكم لقيت".split()}
_NEG_WANTS = {normalize(w) for w in "ابي ابغى ابغي ابغا اريد ودي بدي".split()}


def negated_in(text, phrase):
    """True when the phrase stands in a clause of the text after a word of negation, with only linking words between."""
    pf = [topics.stems(w) for w in normalize(phrase).split() if len(w) >= 3]
    if not pf:
        return False
    for clause in re.split(r"[،,.؟?!؛;\n]|(?:^| )(?:بس|لكن|but|however|bas)(?= )", str(text or "")):
        ws = normalize(clause).split()
        hit = next((i for i, w in enumerate(ws) if len(w) >= 3 and any(topics.stems(w) & f for f in pf)), None)
        if hit is None:
            continue
        for k in range(hit - 1, max(-1, hit - 7), -1):
            w = ws[k]
            if w in _NEG_HEADS or (len(w) > 2 and w[0] in "وف" and w[1:] in _NEG_HEADS):
                nxt = ws[k + 1] if k + 1 < len(ws) else ""
                if nxt in _NEG_ASKS or (w == "لا" and nxt in _NEG_WANTS):
                    break   # «ما عندكم …؟» asks for it; «لا، أبي …» wants it
                return True
            if w not in _NEG_LINK:
                break
    return False


# QADFIX7 #1 (QAD 2026-10-19, case 6.1): «أقوم الصبح بدري وأتمرن» — a verb of the reader's day is not a subject;
# «القيام بدري» was cut to «القيام» and matched «قيام إسرائيل». It becomes the subject the books are written under.
_ROUTINE = [
    (re.compile(r"(?:^| )(?:ال)?(?:قيام|اقوم|نقوم|يقوم|تقوم|اصحى|اصحي|نصحى|صحيان|الصحيان|استيقاظ|استيقظ|اصحو|اقعد)(?: |$)"
                r"|(?:^| )(?:بدري|مبكر|مبكرا|الصبح|الصباح|الفجر)(?: |$)|wake up|waking up|early riser|get up early"), ["الصباح", "العادات"]),
    (re.compile(r"(?:^| )(?:بال|لل|ال)?(?:تمرين|تمارين|اتمرن|نتمرن|التمرن|تمرن|اتدرب|التدريب|تدريب|رياضه|الجيم|جيم|لياقه|اللياقه)(?: |$)"
                r"|exercise|workout|working out|\bgym\b|fitness"), ["الرياضة", "العادات"]),
    (re.compile(r"(?:^| )(?:ال)?(?:التزام|الالتزام|انضباط|الانضباط|استمرار|الاستمرار|اداوم|المداومه)(?: |$)|discipline|consistency"), ["العادات"]),
    (re.compile(r"(?:^| )(?:اذاكر|نذاكر|المذاكره|مذاكره|ذاكر)(?: |$)|\bstudy(?:ing)?\b"), ["المذاكرة"]),
    # QADFIX9 (QAD 2026-10-20, case 1.1): «ودي أبطل السهر» — the books are «لماذا ننام»، «نوم هانئ»
    (re.compile(r"(?:^| )(?:ال)?(?:سهر|اسهر|نسهر|نوم|النوم|نومي|انام|ننام|ارق|الارق)(?: |$)|insomnia|\bsleep"), ["النوم"]),
]


def _routine_topics(unsupported):
    """«القيام بدري»، «الالتزام بالتمرين» -> «الصباح»، «الرياضة»، «العادات» (the words our books are written under)."""
    out = []
    for u in unsupported:
        t = " " + " ".join(normalize(u).split()) + " "
        hits = [subj for rx, subjs in _ROUTINE if rx.search(t) for subj in subjs]
        if hits and len(t.split()) <= 5:
            out += [h for h in hits if h not in out]
        elif u not in out:
            out.append(u)
    return out


# QADFIX7 #3 (QAD 2026-10-19, case 43): «اللي مؤلفها سعودي منها»، «ابي اللي طلعت ٢٠٢٥ بس» narrow what was just asked:
# the year and the country of the message before stay
_NARROW = re.compile(r"(?:^| )(?:منها|منهم|فيها|بينها|منهن|بس|فقط|وبس|only|of them|among them|from them|just)(?: |$)")


def _narrow_followup(text):
    t = " ".join(normalize(text).split())
    return len(t.split()) <= 7 and bool(_NARROW.search(t))


# QADFIX7 #5 (QAD 2026-10-19, case 8): «سلسلة روايات أجزاء كثيرة أتابعها» — a wish for a series, searched in series_name
_SERIES_WISH = re.compile(r"(?:^| )(?:سلسله|سلاسل|السلسله|السلاسل|اجزاء|الاجزاء|ثلاثيه|رباعيه|خماسيه|series|trilogy)(?: |$)")
# QADFIX7 #12 (QAD 2026-10-19, case 49): «شعر لشاعرات خليجيات» is not an author named «مجموعة شاعرات»
_GENERIC_AUTHOR = {normalize(w) for w in "مجموعه مجموعة شاعرات شاعره شاعرة شعراء الشعراء كاتبات كاتبه كاتبة كتاب كتّاب الكتاب "
                   "مؤلفين مؤلفات مؤلفه مؤلفة نساء نسائي نسائيه امراه امرأة خليجيات خليجيه خليجية سعوديات عربيات عربيه "
                   "عدة عده مختلفين متنوع women woman female poets writers authors various".split()}
_PEOPLE_WOMEN = re.compile(r"(?:^| )(?:ال|لل|ل|و)?(?:شاعرات|شاعره|كاتبات|كاتبه|مؤلفات|روائيات|روائيه|امراه|نساء|نسائي|نسائيه)(?: |$)"
                           r"|\b(?:women|woman|female)\b")
# QADFIX11 (QAD 2026-10-21, case 33.2; Ibrahim «صححهم»): «شي ثاني مو لنفس الكاتبة» points back at the author shown, it is
# not a wish for women authors: «نفس / غير / بدل + الكاتبة» and a bare definite «الكاتبة» are references
_WOMEN_REF = re.compile(r"(?:^| )(?:مو |مب |ماهو |مش )?(?:نفس|لنفس|بنفس|غير|بدل|بدال)\s+(?:ال)?(?:كاتبه|مؤلفه|روائيه|شاعره)(?= |$)"
                        r"|(?:^| )ال(?:كاتبه|مؤلفه|روائيه|شاعره)(?= |$)|same (?:author|writer)")


def women_wish(text):
    """A wish for books by women («روايات لروائيات سعوديات»), not a reference back to the author shown."""
    t = " " + " ".join(normalize(text or "").split()) + " "
    return bool(_PEOPLE_WOMEN.search(" ".join(_WOMEN_REF.sub(" ", t).split())))


# QADFIX11 (case 32.1): «البلوك تشين» is one name, not «البلوك» + «تشين»: a definite word followed by a bare one is a
# borrowed compound (Arabic adjectives agree in «ال»), so it is never cut to its rarest word
_NO_CUT_SMALL = {normalize(w) for w in "من عن في على الى مع او و".split()}


def no_cut(phrase):
    w = normalize(phrase or "").split()
    return len(w) == 2 and w[0].startswith("ال") and not w[1].startswith("ال") and w[1] not in _NO_CUT_SMALL


def core_hits_ok(core, books, topic_index):
    """QADFIX11 (case 32.1): a subject word found only as the author's own name («بلوك» in «قانون مورفي» by آرثر بلوك) is no subject."""
    cn = strip_al(normalize(core))
    for b in books:
        if topics.match([core], b["book_id"], topic_index)[0]:
            if cn not in {strip_al(w) for w in normalize(author_flow.display(b.get("author")) or "").split()}:
                return True
    return False


# QADFIX11 (case 7.1): «عندي أرق وما أعرف أنام» names the subject (sleep) with no «عن …»
_SLEEP_PROBLEM = re.compile(r"(?:^| )(?:ال)?ارق(?: |$)|(?:ما|مو|ماني|مب) ?(?:اقدر|اعرف|قادر|قادره|اعرفش) ?انام|(?:ابطل|اترك) (?:ال)?سهر"
                            r"|مشاكل (?:ال)?نوم|اضطراب(?:ات)? (?:ال)?نوم|insomnia|can'?t sleep|trouble sleeping")
_BEDTIME = re.compile(r"(?:قبل|وقت|عند) (?:ال)?نوم|before (?:bed|sleep)|bedtime")

# QADFIX11 (case 34.1): «أي شي إلا الروايات المترجمة، أبي عربي أصلي» refuses translated novels, not novels
_NOT_TRANSLATED_KIND = re.compile(r"(?:^| )(?:الا|غير|ماعدا|عدا|بدون|بلا|مو|مب|ما ابي|ماابي|ما ابغى|لا)\s+(?:ال)?(روايات|روايه|قصص|كتب)\s+(?:ال)?(?:مترجمه|مترجم|مترجمات)(?= |$)")


def generic_author(name):
    words = [w for w in normalize(name).split() if w]
    # «مجموعة مؤلفين» stays (a real credit in our catalog); a kind of people («شاعرات»، «كاتبات خليجيات») is no author
    return (bool(words) and all(w in _GENERIC_AUTHOR or strip_al(w) in _GENERIC_AUTHOR for w in words)
            and any(_PEOPLE_WOMEN.search(w) or w in ("شعراء", "الشعراء", "poets", "writers") for w in words))


# QADFIX7 #10 (QAD 2026-10-19, case 31): a novel with the word in its title is not a book about the subject
_Q7_FICTION = {"رواية", "قصص قصيرة", "قصص مصورة", "مسرحية", "شعر", "أطفال", "يافعين"}


def prefs_from_ai(ai, normalized):
    """Map cleaned AI preferences to the internal ranking structures."""
    prefs = {"genres": set(ai["genres"]), "length": ai["length"],
             "tokens": ai_tokens(ai), "normalized": normalized,
             "authors": [a for a in ai.get("authors", []) if not generic_author(a)], "subgenres": ai.get("subgenres", []),
             "original_languages": set(ai.get("original_languages") or ()), "translated": ai.get("translated"),
             # prizes and screen adaptations (2026-10-08, backend/honors.py)
             "awards": list(ai.get("awards") or []), "award_year": ai.get("award_year"), "adaptation": ai.get("adaptation")}
    if "ipaf" in prefs["awards"] and "booker" in prefs["awards"] and _ENGLISH_WORD.search(normalized or ""):
        # QADFIX5 #3: «رواية انقليزية فازت بالبوكر» is the (English) Booker, not the Arabic one as well
        prefs["awards"] = [a for a in prefs["awards"] if a != "ipaf"]
    if prefs["adaptation"] in (None, "film", "screen") and keyword_prefs.animated_said(normalized):
        # «انمي / رسوم متحركة / كرتون»: only the animated adaptations, not live-action films (QAD 2026-10-14, case 27)
        prefs["adaptation"] = "animated"
    unsupported = list(ai["unsupported_requests"])
    if keyword_prefs.old_screen_said(normalized) and prefs["adaptation"] != "animated":
        # «رواية صارت فيلم قديم، أبيض وأسود»: an adaptation made by 1975, not an unmet wish (QAD 2026-10-15, case 34)
        prefs["adaptation"] = prefs["adaptation"] or "film"
        prefs["adaptation_max_year"] = keyword_prefs.OLD_SCREEN_YEAR
        _old = {normalize(w) for w in "قديم قديمه القديم القديمه ابيض اسود واسود الابيض والاسود زمان كلاسيكي فيلم سينما black white old".split()}
        if not keyword_prefs.old_film_said(normalized):
            # QADFIX7 #9 (QAD 2026-10-19, case 29): «مسلسل مصري قديم» — the «made by 1975» rule is for old films;
            # an old series keeps the series wish without a year we cannot check
            prefs.pop("adaptation_max_year", None)
        unsupported = [u for u in unsupported if not (set(normalize(u).split()) & _old)]
    if prefs["adaptation"]:
        # «رواية صارت فيلم هوليوودي» / «فيلم أجنبي» / «فيلم عربي أو مصري» (QADFIX4, QAD 2026-10-16 case 31.1)
        if re.search(r"هوليود|هوليوود|هوليودي|هوليوودي|امريكي|امريكيه|hollywood|american", normalized):
            prefs["adaptation_origin"] = "american"   # a US production, not any foreign one (AUDFIX, audit 2026-10-16 P2)
        elif re.search(r"اجنبي|اجنبيه|غربي|foreign", normalized):
            prefs["adaptation_origin"] = "foreign"
        elif re.search(r"(?:فيلم|فلم|افلام|مسلسل|مسلسلات) (?:عربي|عربيه|مصري|مصريه|سوري|سوريه|خليجي|خليجيه)|السينما (?:العربيه|المصريه)", normalized):
            prefs["adaptation_origin"] = "arab"
        if prefs.get("adaptation_origin"):
            _o = {normalize(w) for w in "هوليود هوليوود هوليودي هوليوودي امريكي امريكيه اجنبي اجنبيه غربي عربي عربيه مصري مصريه مصر hollywood american".split()}
            unsupported = [u for u in unsupported if not (set(normalize(u).split()) & _o)]
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
    return prefs, pprefs, exclusions, unsupported


_MYSTERY_TITLE = re.compile(r" (?:ال)?(?:لغز|ألغاز|الغاز|محقق|المحقق|المحققه|المحققون|التحري|المخبر) |mystery|detective")


def score_books(books, prefs, pprefs, cards):
    """Phase 1 rules + profile matches; returns [(score, reasons, book)] best first."""
    has_profile_prefs = any((pprefs["moods"], pprefs["pace"], pprefs["weight"],
                             pprefs["use_cases"], pprefs["reader_level"], pprefs.get("audience")))
    books = list(books)
    random.shuffle(books)  # vary order among equally scored books
    scored = []
    for book in books:
        card = cards.get(book["book_id"])
        score, reasons = score_book(book, prefs, card)
        if card and has_profile_prefs:
            profile_score, profile_reasons = score_profile_card(book["book_id"], card, pprefs)
            score += profile_score
            reasons.extend(profile_reasons)
        if pprefs["moods"] & {"mysterious", "suspenseful"} and _MYSTERY_TITLE.search(" " + normalize(book.get("title") or "") + " "):
            # «لغز المدرسة»، «لغز كرة القدم» for a girl who wants mystery: children's books often have no mood on their
            # card, the title says it (QADFIX4, QAD 2026-10-16 case 12)
            score += 2
            reasons.append("عنوانه يدل على لغز يُحل")
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
    _cartoon = (prefs.get("adaptation") == "animated" and pprefs.get("audience") == "children"
                and bool(honors.matches_adaptation(b["book_id"], "animated", prefs.get("adaptation_max_year"))))
    if not age_allowed(card, pprefs.get("audience"), pprefs.get("avoid") or (), b, pprefs.get("reader_age"), _cartoon):
        return False
    if not genres and pprefs.get("audience") not in AGE_OK and card and card.get("format") == "children":
        return False  # a request that names neither a genre nor a child gets no children's books
    if (exclusions["moods"] or exclusions["weight"]) and excluded_by_card(card, exclusions):
        return False
    if genres and b.get("genre") not in genres and not (
            # «قصص صارت أفلام كرتون لولدي»: the child's shelves the interpreter names do not hide a novel made into
            # a cartoon («ناب أبيض», «المخطوف») (QAD 2026-10-15, case 16.2)
            _cartoon and set(genres) <= {"أطفال", "قصص مصورة", "قصص قصيرة"}):
        return False
    if prefs.get("subgenres") and not matching_subgenres(b, prefs, card=card):
        return False
    if prefs.get("length") == "short" and is_long(b, card):
        return False
    if prefs.get("awards") and not honors.matches_award(b["book_id"], prefs["awards"], prefs.get("award_year"),
                                                       prefs.get("award_min_year"), prefs.get("award_max_year"), prefs.get("award_results")):
        return False
    if prefs.get("adaptation") and not honors.matches_adaptation(b["book_id"], prefs["adaptation"], prefs.get("adaptation_max_year"),
                                                                 prefs.get("adaptation_origin")):
        return False
    return origin_allowed(b, prefs)


LENGTH_LABELS = {"short": "قصير", "medium": "متوسط الطول", "long": "طويل"}
LENGTH_LABELS_EN = {"short": "short", "medium": "medium length", "long": "long"}


def chat_context(prefs, pprefs, user_messages, publisher_name=None, country=None, topic_phrases=(), language="ar", edition=None, pub_years=None, extra=None):
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
        place = country_place(list(country[1])) or " أو ".join(_names)
        add("country", ("author from " if en else "كاتب من ") + place)
    for key, label in extra or ():   # a price limit, refused kinds of books, refused countries (QAFIX 2026-10-11)
        add(key, label)
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
        out.append({"film": "تحوّل إلى فيلم", "series": "تحوّل إلى مسلسل", "animated": "تحوّل إلى رسوم متحركة"}.get(prefs["adaptation"], "تحوّل إلى فيلم أو مسلسل")
                   + (f" قديم (حتى {prefs['adaptation_max_year']})" if prefs.get("adaptation_max_year") else ""))
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
    return jsonify(dict(visitor_notes.counts(), staff_open=staff_notes.counts()["open"],
                        ideas_open=staff_ideas.counts()["open"]))


# «التنبيهات» and «ملاحظات من المطور» (QADFIX4, Ibrahim 2026-10-16): what needs a look now, in one box, and the
# developer's notes that staff mark done. Under /api/admin/inbox so staff accounts reach them too.
def _latest(conn, sql, args=()):
    try:
        r = conn.execute(sql, args).fetchone()
        return (r[0] or 0, r[1]) if r else (0, None)
    except sqlite3.Error:
        return 0, None


@app.route("/api/admin/inbox/alerts")
def admin_alerts():
    conn = get_connection()
    try:
        dev_notes.sync(conn)
        staff_notes.ensure_table(conn)
        staff_ideas.ensure_table(conn)
        rows = [
            ("report", "بلاغات الكتب من الزوار", "inbox:report",
             _latest(conn, "SELECT COUNT(*), MAX(created_at) FROM visitor_notes WHERE kind = 'report' AND status = 'open'")),
            ("suggestion", "اقتراحات الزوار", "inbox:suggestion",
             _latest(conn, "SELECT COUNT(*), MAX(created_at) FROM visitor_notes WHERE kind = 'suggestion' AND status = 'open'")),
            ("staff", "تعديلات مطلوبة على الكتب", "inbox:staff",
             _latest(conn, "SELECT COUNT(*), MAX(created_at) FROM staff_notes WHERE status = 'open'")),
            ("ideas", "اقتراحات الموظفين على التطبيق", "inbox:ideas",
             _latest(conn, "SELECT COUNT(*), MAX(created_at) FROM staff_ideas WHERE status = 'open'")),
            ("dev", "ملاحظات من المطور", "alerts:dev",
             _latest(conn, "SELECT COUNT(*), MAX(added_at) FROM dev_notes WHERE status = 'open'")),
            ("update", "تحديث جديد لـ«منار» ينتظر موافقة المدير", "alerts:update",
             ((1, (channel.state().get("update") or {}).get("published") or channel.state().get("last_ok"))
              if (channel.state().get("update") or {}).get("status") in ("available", "ready") else (0, None))),
            ("send", "المطوّر طلب ملف البلاغات — يحتاج موافقة المدير على الإرسال", "alerts:send",
             ((1, (inbox_send.state().get("pending_request") or {}).get("at"))
              if (inbox_send.state().get("pending_request") or {}).get("id") and inbox_send.ready() else (0, None))),
            ("confirm", "كتب تحتاج تأكيد هويتها", "books:confirm",
             _latest(conn, "SELECT COUNT(*), MAX(researched_at) FROM books WHERE research_status = 'needs_confirmation' AND active = 1")),
        ]
    finally:
        conn.close()
    items = [{"key": k, "label": label, "go": go, "open": n, "latest": latest} for k, label, go, (n, latest) in rows]
    return jsonify({"items": items, "attention": sum(i["open"] for i in items if i["key"] not in ("confirm",))})


# «تحديثات منار» (AUTOUPD, Ibrahim 2026-10-16 «approve»): updates and messages Ibrahim sends through the signed
# channel (backend/channel.py). Everyone sees the state and may check now; only the manager approves an install
# (/api/admin/channel/install is outside STAFF_PREFIXES, so guard() refuses staff accounts).
@app.route("/api/admin/inbox/channel")
def admin_channel_status():
    return jsonify(channel.status((acting_user() or {}).get("role")))


@app.route("/api/admin/inbox/channel/check", methods=["POST"])
def admin_channel_check():
    channel.check()
    return jsonify(channel.status((acting_user() or {}).get("role")))


@app.route("/api/admin/channel/install", methods=["POST"])
def admin_channel_install():
    data = request.get_json(silent=True) or {}
    user = acting_user() or {}
    up = channel.state().get("update") or {}
    if str(data.get("version") or "") != str(up.get("version") or ""):
        return jsonify({"error": "رقم التحديث تغيّر؛ حدّث الصفحة وحاول مرة ثانية"}), 409
    ok, msg = channel.install(user.get("display_name") or user.get("username"))
    if not ok:
        return jsonify({"error": msg}), 400
    auth.audit(user or None, "update.approved", up.get("version"), {"from": channel.installed_version(), "sha256": up.get("sha256")})
    return jsonify({"ok": True, "message": msg, "status": channel.status(user.get("role"))})


# «إرسال البلاغات للمطوّر» (INBOXSEND, Ibrahim 2026-10-23 «رفع تلقائي مشفّر»): the inbox file sealed for Ibrahim, sent
# every week and when he asks (backend/inbox_send.py). Everyone sees the state; only the manager turns it on or off,
# saves the upload token or sends now (/api/admin/send-inbox/* is outside STAFF_PREFIXES, so guard() refuses staff).
@app.route("/api/admin/inbox/send")
def admin_inbox_send_status():
    return jsonify(inbox_send.status((acting_user() or {}).get("role")))


@app.route("/api/admin/send-inbox/settings", methods=["POST"])
def admin_inbox_send_settings():
    data = request.get_json(silent=True) or {}
    user = acting_user() or {}
    try:
        st = inbox_send.set_settings(enabled=data.get("enabled") if isinstance(data.get("enabled"), bool) else None,
                                     token=data.get("token") or None, clear_token=bool(data.get("clear_token")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    auth.audit(user or None, "inbox_send.settings", None,
               {"enabled": st["enabled"], "token_saved": bool(data.get("token")), "token_cleared": bool(data.get("clear_token"))})
    return jsonify(st)


@app.route("/api/admin/send-inbox/now", methods=["POST"])
def admin_inbox_send_now():
    data = request.get_json(silent=True) or {}
    user = acting_user() or {}
    why = inbox_send.ready()
    if why:
        return jsonify({"error": why}), 400
    if inbox_send.state().get("sending"):
        return jsonify({"error": "الإرسال يعمل الآن"}), 409
    inbox_send.send_async(reason="manual", scope="all" if data.get("scope") == "all" else "open",
                          by=user.get("display_name") or user.get("username"), audit=auth.audit)
    return jsonify({"ok": True, "message": "بدأ الإرسال", "status": inbox_send.status(user.get("role"))})


@app.route("/api/admin/inbox/dev-notes")
def admin_dev_notes():
    status = request.args.get("status", "all")
    return jsonify({"notes": dev_notes.list_rows(status), "counts": dev_notes.counts()})


@app.route("/api/admin/inbox/dev-notes/<int:note_id>", methods=["POST"])
def admin_dev_note_update(note_id):
    data = request.get_json(silent=True) or {}
    user = acting_user() or {}
    try:
        row = dev_notes.set_status(note_id, data.get("status"), user.get("display_name") or user.get("username"), data.get("comment"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if row is None:
        return jsonify({"error": "غير موجود"}), 404
    auth.audit(user or None, "dev_note." + row["status"], note_id,
               {"ref": row.get("ref"), "title": row.get("title"), "comment": row.get("handled_note")})
    return jsonify({"ok": True, "note": row, "counts": dev_notes.counts()})


@app.route("/api/admin/inbox/dev-notes/<int:note_id>/books")
def admin_dev_note_books(note_id):
    note, books = dev_notes.books_for(note_id)
    if note is None:
        return jsonify({"error": "غير موجود"}), 404
    return jsonify({"note": note, "books": books or []})


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


# «البيانات الأساسية» editable by staff (Ibrahim 2026-10-09): books.genre <-> book_profiles.format, as the importers write it
GENRE_FORMAT = {"رواية": "novel", "قصص قصيرة": "short_stories", "أطفال": "children", "يافعين": "young_adult",
                "تطوير ذاتي": "self_dev", "علم نفس": "psychology", "تاريخ": "history", "سيرة ذاتية": "biography",
                "دين": "religious", "فكر": "thought", "علوم": "science", "أعمال": "business", "شعر": "poetry",
                "مسرحية": "play", "أدب رحلات": "travel", "قصص مصورة": "comics", "مقالات ونصوص": "essays",
                "نقد أدبي": "criticism", "فنون وسينما": "arts", "علم اجتماع": "sociology"}


def _validate_book_fields(data):
    clean = {}
    try:
        for key, n, required in (("title", 300, True), ("author", 200, True), ("publisher", 150, False)):
            if key in data:
                v = re.sub(r"\s+", " ", str(data[key] or "")).strip()[:n]
                if required and not v:
                    raise ValueError
                clean[key] = v or None
        if "genre" in data:
            g = str(data["genre"] or "").strip()
            if g and g not in GENRE_FORMAT:
                raise ValueError
            clean["genre"] = g or None
        if "page_count" in data:
            v = data["page_count"]
            v = None if v in ("", None) else int(v)
            if v is not None and not 1 <= v <= 20000:
                raise ValueError
            clean["page_count"] = v
        if "publication_year" in data:
            v = data["publication_year"]
            v = None if v in ("", None) else int(v)
            if v is not None and not 1000 <= v <= time.localtime().tm_year + 1:
                raise ValueError
            clean["publication_year"] = v
        if "original_year" in data:
            # the work's first publication, typed by staff from the book itself (ORIGYR 2026-10-11)
            v = data["original_year"]
            v = None if v in ("", None) else int(v)
            if v is not None and not 1000 <= v <= time.localtime().tm_year + 1:
                raise ValueError
            clean["original_year"] = v
            clean["original_year_source"] = "staff" if v is not None else None
            clean["original_year_ref"] = None
            clean["original_year_checked_at"] = auth._now()
        if "isbn" in data:
            v = re.sub(r"[\s\-]", "", str(data["isbn"] or "")).upper()
            if v and not rewaa_import.is_isbn(v):
                raise ValueError
            clean["isbn"] = v or None
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
    if "genre" in fields and fields["genre"] != before.get("genre"):
        conn = get_connection()
        try:  # the card's format follows the genre, as the importers keep them (one to one)
            conn.execute("INSERT OR IGNORE INTO book_profiles (book_id, overall_status) VALUES (?, 'pending')", (book_id,))
            fmt = GENRE_FORMAT.get(fields["genre"] or "", "other")
            conn.execute("UPDATE book_profiles SET format = ? WHERE book_id = ?", (fmt, book_id))
            # Q-MGR-9 (2026-10-18): the format evidence follows too, so a later card recompute or import
            # does not bring the old format back
            conn.execute("""UPDATE attribute_evidence SET attribute_value = ?
                            WHERE book_id = ? AND attribute_name = 'format' AND COALESCE(rejected, 0) = 0
                                  AND attribute_value <> ?""", (fmt, book_id, fmt))
            conn.commit()
        finally:
            conn.close()
    if "author" in fields and fields["author"] != before.get("author"):
        try:
            authors.sync()  # links the book to the right author record
        except Exception:
            app.logger.exception("authors sync after a staff edit failed")
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


def _year4(v):
    try:
        y = int(str(v)[:4])
    except (TypeError, ValueError):
        return None
    # a year after this one is a typing slip in the record (book 4350: 2028), never «the newest» (QAD 2026-10-13, case 44)
    return y if 1000 <= y <= time.localtime().tm_year + 1 else None


def first_published(book):
    """The year the work was first published (for a translation: the original's), from a source
    (books.original_year, ORIGYR 2026-10-11), else None. Never the year of our edition."""
    return _year4(book.get("original_year"))


def edition_year(book):
    """The year of OUR edition (books.publication_year, from Rewaa / research)."""
    return _year4(book.get("publication_year"))


def year_for_new(book):
    """The year «كتب جديدة / صدر حديثًا / بعد ٢٠٢٠» goes by: when the work first came out, or our
    edition's year when that is not known (a recent reprint of a classic is not new, ORIGYR 2026-10-11)."""
    return first_published(book) or edition_year(book)


def known_absent_author(author_request, all_books):
    """The stored record of the author asked for when none of his books is in stock -> record or None.
    A record with books in stock (or a catalog author string that is his) is not absent."""
    names = [author_request.get("full_name"), author_request.get("name_as_written")] + list(author_request.get("aliases") or [])[:8]
    rec = next((r for r in (authors.lookup(n) for n in names if isinstance(n, str) and n.strip()) if r), None)
    if not rec:
        return None
    if any(i in all_books and is_available(all_books[i]) for i in authors.book_ids(rec["author_id"])):
        return None
    spellings = {authors.key(n) for n in authors.names_of(rec)}
    if any(is_available(b) and authors.key(author_flow.display(b.get("author") or "")) in spellings for b in all_books.values()):
        return None
    return rec


def library_overview(all_books, language="ar"):
    """The shelves in numbers: available titles, copies, and each category with its count."""
    avail = [b for b in all_books.values() if is_available(b)]
    counts = {}
    for b in avail:
        if b.get("genre"):
            counts[b["genre"]] = counts.get(b["genre"], 0) + 1
    ranked = sorted(counts.items(), key=lambda x: -x[1])
    copies = sum(int(b.get("quantity") or 0) for b in avail)
    # the language of the copy too («وكم كتاب بالانجليزي؟», QAD 2026-10-14, case 38)
    n_en = sum(1 for b in avail if (b.get("language") or "ar") == "en")
    n_ar = sum(1 for b in avail if (b.get("language") or "ar") == "ar")
    n_other = len(avail) - n_en - n_ar
    # each of the larger categories by the language of the copy: «كم كتاب عربي؟ وكم منها روايات؟» is 2,033 Arabic
    # novels, not 2,159 novels in every language (QAD 2026-10-15, case 50)
    by_lang = {}
    for b in avail:
        if b.get("genre"):
            d = by_lang.setdefault(b["genre"], {"ar": 0, "en": 0})
            lg = (b.get("language") or "ar")
            if lg in d:
                d[lg] += 1
    top_lang = [(g, by_lang.get(g, {})) for g, _ in ranked[:8]]
    if language == "en":
        return (f" The visitor asks about the library itself. In stock now: {len(avail)} titles ({copies} copies)."
                " Categories, most titles first: " + ", ".join(f"{g} ({n})" for g, n in ranked) + "."
                f" By the language of the copy: {n_ar} in Arabic, {n_en} in English" + (f", {n_other} in other languages" if n_other else "") + "."
                " Largest categories by the language of the copy: " + "; ".join(f"{g}: {d.get('ar', 0)} Arabic, {d.get('en', 0)} English" for g, d in top_lang) + "."
                " Answer from these numbers (rounded is fine, e.g. «over 5,000 titles»); when asked for the categories, name them all"
                " (in English); the most common category is the first one and the smallest is the last. For one kind of book"
                " («how many novels»), give that category's number. Then ask one question.")
    return (f" الزائر يسأل عن المكتبة نفسها. المتوفر الآن: {len(avail)} عنوانًا ({copies} نسخة)."
            " التصنيفات من الأكثر عناوين: " + "، ".join(f"{g} ({n})" for g, n in ranked) + "."
            f" وبلغة النسخة: {n_ar} بالعربية، و{n_en} بالإنجليزية" + (f"، و{n_other} بلغات أخرى" if n_other else "") + "."
            " وإذا سأل «كم كتاب بالإنجليزي» فهذا رقمه."
            " وأكبر التصنيفات بلغة النسخة: " + "؛ ".join(f"{g}: {d.get('ar', 0)} بالعربية و{d.get('en', 0)} بالإنجليزية" for g, d in top_lang) + "."
            " وإذا سأل «كم كتاب عربي؟ وكم منها روايات؟» فاذكر العدد العربي ثم عدد الروايات بالعربية من هذا السطر."
            " أجب من هذه الأرقام (يجوز التقريب مثل «أكثر من ٥٠٠٠ عنوان»)؛ وإذا سأل عن التصنيفات فاذكرها كلها، وأكثر تصنيف هو الأول"
            " وأقلها هو الأخير. وإذا سأل عن نوع واحد («كم رواية عندكم؟») فاذكر رقم تصنيفه. ثم سؤال واحد.")


_BA_CACHE = {"at": 0.0, "map": {}}


def book_author_map(max_age=60):
    """{book_id: [author_id, ...]} of every book's authors (role author), cached for a minute."""
    if time.time() - _BA_CACHE["at"] > max_age:
        conn = get_connection()
        try:
            m = {}
            for r in conn.execute("SELECT book_id, author_id FROM book_authors WHERE role = 'author'"):
                m.setdefault(r[0], []).append(r[1])
        except sqlite3.Error:
            m = {}
        finally:
            conn.close()
        _BA_CACHE.update(at=time.time(), map=m)
    return _BA_CACHE["map"]


def same_book_editions(book, all_books, exclude=()):
    """Other available copies of the same work: the same title (title_key) by the same author."""
    tk = chat_rules.title_key(book.get("title") or "")
    ak = " ".join(normalize(book.get("author") or "").split())
    # the same author written in another script («George Orwell» / «جورج أورويل») is the same person when the
    # authors table links both books to him: the Penguin «1984» is an edition too (QAD 2026-10-15, case 44.2)
    # QADFIX5 #14 (QAD 2026-10-17, case 50.2): every author link, not only those of prize-winning authors
    # (honors.load keeps those alone), so the Penguin «1984» of the record George Orwell is found again
    _ba = book_author_map()
    aids = set(_ba.get(book["book_id"], []))
    out = []
    for b in all_books.values():
        if (b["book_id"] != book["book_id"] and b["book_id"] not in exclude and is_available(b)
                and chat_rules.title_key(b.get("title") or "") == tk
                and (" ".join(normalize(b.get("author") or "").split()) == ak or (aids and aids & set(_ba.get(b["book_id"], []))))):
            out.append(b)
    return sorted(out, key=lambda b: (b.get("price") or 0, b["book_id"]))


_LANGS_ASKED = re.compile(r"(?:^| )(?:بأي|باي|بايش|بكم|كم|وش|ايش) (?:ال)?لغ|(?:^| )(?:كم|وش|ايش) (?:طبعه|طبعات|نسخه|نسخ|ترجمه|ترجمات)(?: |$)|(?:^| )(?:ال)?لغات(?: |$)|(?:^| )(?:نسخه|نسخ) (?:بال)?(?:انجليزي|انقليزي|انجليزيه)"
                         r"|which languages|what languages|in (?:what|which) language|english (?:copy|edition|version)")


def work_copies_other_titles(book, all_books, exclude=()):
    """Copies in stock by the same author (authors table link) that may be the same work under another title:
    another language («Animal Farm» beside «مزرعة الحيوان»), or a title sharing its main word («مزرعة الحيوانات»).
    The answer decides which are the same work (QADFIX6)."""
    _ba = book_author_map()
    aids = set(_ba.get(book["book_id"], []))
    if not aids:
        return []
    own = {w for w in chat_rules.title_key(book.get("title") or "").split() if len(w) >= 4}
    own |= {strip_al(w) for w in own}
    out = []
    for b in all_books.values():
        if b["book_id"] == book["book_id"] or b["book_id"] in exclude or not is_available(b):
            continue
        if not (aids & set(_ba.get(b["book_id"], []))):
            continue
        words = {w for w in chat_rules.title_key(b.get("title") or "").split() if len(w) >= 4}
        words |= {strip_al(w) for w in words}
        if (b.get("language") or "ar") != (book.get("language") or "ar") or (own & words):
            out.append(b)
    return out


def edition_fact(book, language="ar"):
    e = {"id": book["book_id"], "publisher": (book.get("publisher") or "").strip() or None,
         "price_sar": book.get("price"), "pages_approx": book.get("page_count"),
         "copies": book.get("quantity") or 0, "edition_year": edition_year(book)}
    if book.get("language") in ("en", "ar"):
        e["copy_language"] = {"en": "English", "ar": "Arabic"}[book["language"]] if language == "en" else \
            {"en": "الإنجليزية", "ar": "العربية"}[book["language"]]
    return {k: v for k, v in e.items() if v not in (None, "")}


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
    # the publisher of OUR edition: «مين ناشر فن الحرب عندكم؟» was answered «ما عندي تفاصيل» (QAD 2026-10-12, case 10)
    if (book.get("publisher") or "").strip():
        fact["publisher"] = book["publisher"].strip()
    # «صدر سنة …» is the work's first publication (books.original_year, ORIGYR 2026-10-11); the year of
    # our edition is said only when asked about the edition, or as «طبعتنا» when the first is unknown
    if first_published(book):
        fact["first_published"] = first_published(book)
    if edition_year(book):
        fact["edition_year"] = edition_year(book)
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
        if language == "en":
            # QADFIX5 #19 (QAD 2026-10-17, case 22): an Arabic title of a film is not given in an English reply
            # («أغنياء آسيويون مجانين») — the kind and the year stay, the Arabic title goes
            fact["adaptations"] = [re.sub(r"\s*«[^»]*[\u0600-\u06FF][^»]*»", "", x) for x in fact["adaptations"]]
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
                   "طبعه انجليزيه", "كتب انجليزيه", "روايات انجليزيه",
                   # «قصص انجليزي سهلة»، «أي كتاب إنجليزي»، «عادي لو إنجليزي» (QAD 2026-10-15, cases 12, 31, 7)
                   "قصص انجليزي", "قصص انجليزيه", "قصه انجليزي", "قصه انجليزيه", "كتاب انجليزي", "كتب انجليزي",
                   "روايات انجليزي", "عادي لو انجليزي", "عادي لو بالانجليزي",
                   "ولو انجليزي", "ولو بالانجليزي", "لو انجليزي عادي", "حتى لو انجليزي", "مو مشكله لو انجليزي",
                   "عادي انجليزي", "اقبل انجليزي", "انجليزي عادي",
                   # QADFIX5 #3 (QAD 2026-10-17, case 20.1): «رواية انقليزية» in the dialect = a copy in English
                   "روايه انقليزيه", "روايه انقليزي", "روايات انقليزيه", "روايات انقليزي", "كتاب انقليزي", "كتب انقليزي",
                   "كتب انقليزيه", "قصص انقليزي", "قصه انقليزي", "انقليزي عادي", "بالانقليزيه",
                   # Ibrahim 2026-10-17: «انقليزي وانجليزي كلها نفس الشيء» — every phrase above in both spellings
                   "روايه انجليزيه", "روايه انجليزي", "قصص انقليزيه", "قصه انقليزيه", "روايات انجليزيه",
                   "عادي لو انقليزي", "عادي لو بالانقليزي", "ولو انقليزي", "ولو بالانقليزي", "لو انقليزي عادي",
                   "حتى لو انقليزي", "مو مشكله لو انقليزي", "عادي انقليزي", "اقبل انقليزي", "نسخه انقليزيه",
                   "نسخته الانقليزيه", "طبعه انقليزيه", "باللغه الانقليزيه",
                   "in english", "english edition", "english version",
                   "english books", "english novels", "english book", "english novel"],
            "ar": ["بالعربي", "بالعربيه", "باللغه العربيه", "نسخه عربيه", "نسخته العربيه", "طبعه عربيه",
                   "in arabic", "arabic edition", "arabic version", "arabic translation"]}


# «تاريخ بس مو ممل»، «تاريخ بأسلوب ممتع زي الحكايات»: history told in a lively way (Ibrahim 2026-10-17: historical novels too)
_LIVELY = re.compile(r"(?:مو|مش|غير|بدون|ما يكون|لا يكون) (?:ممل|مملل|ثقيل)|ممتع|مشوق|خفيف|حكايات|حكايه|قصصي|بأسلوب|باسلوب|not boring|fun|lively")


def wanted_edition(text, language="ar"):
    """-> "en" / "ar" / None. In the English chat «English novels» means books in English."""
    words = normalize(text).split()
    # «رواية انجليزية مترجمة» names the original language: only an explicit copy wish («بالانجليزي، نسخة انجليزية») counts then
    translated = any(w.startswith(("مترجم", "translat")) for w in words)
    for code, phrases in _EDITION.items():
        if translated:
            phrases = [p for p in phrases if p.startswith(("بال", "باللغه", "نسخه", "نسخته", "طبعه", "in ", "arabic ", "english edition", "english version"))]
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
    if hi is None and lo is not None and age - lo >= 5:
        # «1+» (a toddler's lift-the-flap book) is not «for» an 8-year-old: no age bonus over the books
        # made for her age (QAD 2026-10-14, case 6)
        return 0
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
_BARE_YEAR = re.compile(r"(?<!\d)(18\d\d|19\d\d|20[0-3]\d)(?!\d)")
_TITLE_NUMBER = re.compile(r"(?:روايه|رواية|كتاب|فيلم|مسلسل|novel|book|film)\s+(?:\d{4})(?!\d)")
_YEAR_WORD = re.compile(r"(?:^| )(?:سنه|عام|year|in the year)(?: |$)")
_NEWISH = re.compile(r"(?:^| )(?:جديد|جديده|الجديد|الجديده|جديدة|حديث|حديثه|new|newer|recent)(?: |$)")
_YEAR_BETWEEN = re.compile(r"(?:بين|between|من|from)\s+(?:عام|عامي|سنه|سنتي)?\s*(1[89]\d\d|20\d\d)\s*(?:و|and|to|الى|الي|الين|لين|لحد|لغايه|لغايت|حتي|حتى|-|–)\s*(1[89]\d\d|20\d\d)")
_NEW_RELEASE = ["كتب جديده", "روايات جديده", "الكتب الجديده", "الروايات الجديده", "صدر حديثا", "صادر حديثا", "نشر حديثا", "منشور حديثا", "الاصدارات الجديده", "اصدارات جديده", "اصدار جديد",
                "اخر الاصدارات", "احدث الاصدارات", "new release", "new releases", "recently published", "published recently",
                "latest releases",
                # «وش عندكم جديد هالأسبوع؟» (QAD 2026-10-12, case 1: answered with a question only)
                "جديد هالاسبوع", "جديد هذا الاسبوع", "جديد الاسبوع", "جديد هالشهر", "جديد هذا الشهر", "وش الجديد",
                "ايش الجديد", "وش جديدكم", "ايش جديدكم", "عندكم جديد", "وصلكم جديد", "وصل جديد", "الجديد عندكم",
                "whats new", "what is new", "new arrivals", "new this week", "anything new",
                # «وش أحدث كتاب وصلكم؟» (QAD 2026-10-13, case 44: «الأحدث» became a subject searched in summaries)
                "احدث كتاب", "احدث كتب", "احدث الكتب", "احدث روايه", "احدث روايات", "احدث الروايات", "اخر كتاب وصل",
                "اخر كتاب وصلكم", "اخر الكتب", "اخر الروايات", "اخر ما وصل", "وش احدث", "ايش احدث", "شو احدث",
                "newest book", "newest books", "latest book", "latest books", "latest novel", "most recent book"]
NEW_RELEASE_YEARS = 3


_THIS_YEAR = re.compile(r"(?:^| )(?:هالسنه|هالعام|هذي السنه|هذه السنه|هذا العام|هالسنه هذي|السنه هذي|السنه الحاليه|العام الحالي"
                        r"|this year)(?: |$)")
# QADFIX9 (QAD 2026-10-20, case 3.1): «نزلت السنة اللي فاتت» is last year; «من نفس السنة» keeps the year said before
_LAST_YEAR = re.compile(r"(?:^| )(?:ال)?(?:سنه|عام) (?:اللي|الي|الذي) (?:فاتت|فات|راحت|راح|قبل)(?: |$)|(?:^| )(?:ال)?(?:سنه|عام) (?:ال)?(?:ماضيه|ماضي|فائته|فائت|سابقه|سابق)(?: |$)"
                        r"|(?:^| )(?:السنه|العام) (?:اللي )?قبل (?:ذي|هذي|هذه)(?: |$)|last year|past year")
_SAME_YEAR = re.compile(r"(?:^| )(?:من |في )?نفس (?:ال)?(?:سنه|عام)(?: |$)|same year")
_RELEASE_VERB = re.compile(r"(?:^| )(?:صدر|صدرت|صادر|صادره|الصادره|نشر|نشرت|منشور|المنشوره|طلع|طلعت|نزل|نزلت|اصدار|اصدارات"
                           r"|published|released|came out|out)(?: |$)")


_WHO_WON = re.compile(r"(?:^| )و?(?:مين|من|منو|مين اللي|وش|ايش|اي كتاب|اي روايه)\s*(?:اللي )?(?:فاز|فازت|فايز|حصل|اخذ|ربح)"
                      r"|(?:^| )(?:الفائز|الفايز|الفائزه|الفائزين|winner|winners|who won|which book won)(?: |$)")


# «روايات انكتبت في الستينات» = 1960–1969 (QAD 2026-10-15, case 43: the decade went to unsupported_requests)
_DECADES = {"عشرين": 1920, "ثلاثين": 1930, "اربعين": 1940, "خمسين": 1950, "ستين": 1960, "سبعين": 1970,
            "ثمانين": 1980, "تسعين": 1990}
_DECADE_RX = re.compile(r"(?:^| )(?:ال)?(عشرين|ثلاثين|اربعين|خمسين|ستين|سبعين|ثمانين|تسعين)(?:ات|يات)(?: |$)"
                        r"|(?:^| )(?:the )?(twenties|thirties|forties|fifties|sixties|seventies|eighties|nineties)(?: |$)"
                        r"|(?:^| )(1[89][0-9]0)(?:s|ات|يات)(?: |$)")
_DECADES_EN = {"twenties": 1920, "thirties": 1930, "forties": 1940, "fifties": 1950, "sixties": 1960, "seventies": 1970,
               "eighties": 1980, "nineties": 1990}


def decade_said(text):
    """«في الستينات»، «الستينيات»، "the sixties", «1960s» -> (1960, 1969); else None."""
    m = _DECADE_RX.search(" ".join(normalize(text).split()))
    if not m:
        return None
    start = _DECADES.get(m.group(1) or "") or _DECADES_EN.get(m.group(2) or "") or int(m.group(3))
    return start, start + 9


# «وش الروايات اللي وصلت القائمة القصيرة للبوكر العربية؟»: shortlisted ones (and the winners, which were on
# it), not the longlist or a prize of the author (QAD 2026-10-15, case 29.1); «الفائزة» = winners only
_SHORTLIST_RX = re.compile(r"(?:ال)?قائمه (?:ال)?قصيره|shortlist|short list")
_LONGLIST_RX = re.compile(r"(?:ال)?قائمه (?:ال)?طويله|longlist|long list")
_WINNER_RX = re.compile(r"(?:^| )(?:ال)?(?:فائزه|فايزه|فائزين|فايزين|فازت|فاز)(?: |$)|winners?(?: |$)|(?:^| )won(?: |$)")


_PLATFORM = re.compile(r"نتفلكس|نتفليكس|نيتفلكس|نتفلیکس|netflix|شاهد vip|osn|امازون برايم|برايم فيديو|prime video|hbo|ديزني بلس|disney")


def award_results_said(text):
    t = " ".join(normalize(text).split())
    if _SHORTLIST_RX.search(t):
        return ["shortlist", "winner"]
    if _LONGLIST_RX.search(t):
        return ["longlist", "shortlist", "winner"]
    if _WINNER_RX.search(t):
        return ["winner"]
    return None


def years_said(text):
    """«قبل ٢٠١٥» -> (None, 2014); «بعد 2020» -> (2021, None); «since 2020» -> (2020, None);
    «بين ٢٠١٠ و٢٠١٥» -> (2010, 2015). (QA2 2026-10-09: prizes before a year, publication year)
    «الكتب اللي صدرت هالسنة» -> (this year, this year) (QAD 2026-10-13, case 44.2)."""
    t = " ".join(normalize(text).split())
    if _THIS_YEAR.search(t) and (_RELEASE_VERB.search(t) or re.search(r"فاز|فائز|جائز|جوائز|won|prize", t)):
        y = time.localtime().tm_year
        return y, y
    if _LAST_YEAR.search(t) and (_RELEASE_VERB.search(t) or re.search(r"فاز|فائز|جائز|جوائز|won|prize|published|released", t)):
        y = time.localtime().tm_year - 1
        return y, y
    m = _YEAR_BETWEEN.search(t)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return a, b
    d = decade_said(t)
    if d:
        return d
    lo = hi = None
    m = _YEAR_BEFORE.search(t)
    if m:
        hi = int(m.group(1)) - 1
    m = _YEAR_AFTER.search(t)
    if m:
        lo = int(m.group(1)) + (0 if "since" in m.group(0) else 1)
    if lo is None and hi is None:
        # QADFIX5 #1 (QAD 2026-10-17, case 31): a bare year with a release word («طلعت ٢٠٢٤ أو ٢٠٢٥»، «سنة ٢٠٢٤»،
        # «published 2023») is that year or that span; after «جديدة» («٢٠٢٥ مثلا») it is the earliest year wanted.
        # A number right after «رواية / كتاب / فيلم» is a title («رواية 1984»), not a year.
        ys = [int(y) for y in _BARE_YEAR.findall(_TITLE_NUMBER.sub(" ", t))]
        if ys:
            said = bool(_RELEASE_VERB.search(t) or _YEAR_WORD.search(t))
            if said:
                lo, hi = min(ys), max(ys)
            elif _NEWISH.search(t) and len(ys) == 1:
                lo = ys[0]
    return lo, hi


# «رواية تدور أحداثها في أكتوبر» with no war or event said: the month itself, so its season (Ibrahim
# 2026-10-13: «كان المفروض تقترح الأقرب لشهر أكتوبر بما أنه ما تم ذكر حرب أو أحداث»)
_SEASONS = {"winter": ("الشتاء", "winter"), "spring": ("الربيع", "spring"), "summer": ("الصيف", "summer"), "autumn": ("الخريف", "autumn")}
MONTH_SEASON = {normalize(m): season for season, months in {
    "winter": "ديسمبر يناير فبراير شباط december january february",
    "spring": "مارس آذار ابريل أبريل نيسان مايو أيار march april may",
    "summer": "يونيو حزيران يوليو تموز اغسطس أغسطس june july august",
    "autumn": "سبتمبر أيلول اكتوبر أكتوبر نوفمبر september october november"}.items() for m in months.split()}
_EVENT_WORDS = re.compile(r"حرب|حروب|ثوره|انتفاضه|معركه|نصر|انقلاب|احداث|حادثه|مجزره|war|revolution|battle|uprising|coup|massacre")


def months_in(phrase):
    return [w for w in normalize(phrase).split() if w in MONTH_SEASON]


def month_as_event(month, text):
    """«حرب أكتوبر»، «أكتوبر 1973»، «يوليو 1911 - أكتوبر 1931»: the month names an event or a date, not the month."""
    t = " ".join(normalize(text or "").split())
    return bool(re.search(r"(حرب|ثوره|انتفاضه|معركه|نصر|انقلاب|حروب)(\s+\S+){0,3}?\s+(ال)?" + month + r"|" + month + r"\s*\d{4}|\d{4}\s*" + month
                          + r"|(war|revolution)\s+of\s+" + month, t))


# «روايات صدرت مؤخرًا» (staff chat 2026-10-13 23:04: a 1953 novel came first), «طلعت هالسنة»، «أحدث الروايات»
_NEW_RELEASE_RX = re.compile(r"(?:صدر|صدرت|صادر|صادره|نشر|نشرت|منشور|منشوره|طلع|طلعت|نزل|نزلت|اصدار|اصدارات)"
                             r"(?: \S+)? (?:مؤخرا|موخرا|حديثا|جديد|جديده|هالسنه|هذي السنه|السنه هذي|هذا العام|العام هذا|السنه الماضيه)"
                             r"|(?:احدث|اخر|اجدد) (?:ال)?(?:روايات|كتب|اصدارات|الاصدارات)")


def new_release_said(text):
    words = normalize(text).split()
    return (any(keyword_prefs.find_phrase(words, normalize(p)) >= 0 for p in _NEW_RELEASE)
            or bool(_NEW_RELEASE_RX.search(" ".join(words))))


# «فاجئيني بكتب جديدة» (start button) and then «رواية»: the books must still be new; «هذي مو جديدة. المفروض
# تعتمد جديدة في السياق بعد» (Ibrahim's staff chat 2026-10-11 21:14). Taken back by «مو لازم جديدة» / «أي سنة».
_NEW_START = {" ".join(normalize(x).split()) for x in ("فاجئيني بكتب جديدة", "Surprise me with new books")}
_NEW_WORD = re.compile(r"(^| )(جديد|جديده|الجديد|الجديده|حديث|حديثه|حديثا|new|newer|newest|recent)( |$)")
_NOT_NEW = re.compile(r"(مو|مب|ما|مش|غير) (لازم|شرط|ضروري|مهم) (تكون |يكون )?(جديد|حديث)|(اي|كل) (سنه|السنوات)"
                      r"|(^| )(قديم|قديمه|القديمه|كلاسيك|كلاسيكي|كلاسيكيه)( |$)|any year|not new|doesn.?t have to be new|older|classic")


_EDITION_ASK = re.compile(r"(?:احدث|اخر|اجدد|اقدم|كم|اي|وش|ايش) (?:ال)?(?:طبعه|طبعات|نسخه|نسخ|edition|editions)|(?:^| )(?:ال)?طبعات(?: |$)|which edition|latest edition|newest edition")


def new_release_wished(user_messages):
    """-> 'said' when the latest message asks for new releases; 'carried' when an earlier one did (the
    «فاجئيني بكتب جديدة» start button included) and nothing since took it back; else None."""
    last = " ".join(normalize(user_messages[-1] if user_messages else "").split())
    if _NOT_NEW.search(last) or _EDITION_ASK.search(last):
        # «وش أحدث طبعة منها؟» asks about the editions of one book, not for new releases (QAD 2026-10-15, case 44)
        return None
    if new_release_said(last) or last in _NEW_START:
        return "said"
    earlier = [" ".join(normalize(m).split()) for m in user_messages[:-1]]
    for t in reversed(earlier):
        if _NOT_NEW.search(t):
            return None
        if new_release_said(t) or t in _NEW_START:
            # «هذي مو جديدة» right after: the visitor insists on it
            return "said" if _NEW_WORD.search(last) else "carried"
    return None


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
    # «ولدي بأولى ابتدائي» says the age too (QAFIX 2026-10-11: the age was asked although the grade was given)
    # «بنتي ١٥» too, an age right after the child (QAD 2026-10-12, case 38)
    return chat_rules.ages_in(text)


def adult_said(text):
    words = normalize(text).split()
    return (any(keyword_prefs.find_phrase(words, w) >= 0 for w in _ADULT_SAID)
            or chat_rules.parent_said(text)   # «لي أنا كأم» (QAD 2026-10-12, case 38)
            or any(age >= 18 for age in _ages(text)))   # «فوق ١٨ سنة»


def said_for_adult(user_messages):
    """The latest message asks for a grown-up reader: «للكبار»، «فوق ١٨»، or the visitor himself
    («وأنا ابي رواية رومانسية لي» after two children, QAFIX 2026-10-11 case 12) unless he is the young
    reader himself («أنا بثاني متوسط»)."""
    last = user_messages[-1] if user_messages else ""
    return (adult_said(last) or chat_rules.parent_reader(user_messages)
            or (chat_rules.self_said(last) and not chat_rules.minor_self(user_messages)))


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
    # the child the latest request is about, not any child named in the conversation (QAFIX 2026-10-11,
    # case 12: «ولأخوها عمره ١٦» after «بنتي» was shown as «يافعة»)
    who = chat_rules.reader_person(user_messages)[0]
    if who:
        return who
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


_model_reader_age = threading.local()


def set_model_reader_age(user_messages, age):
    """QADFIX14 #2 (QAD 2026-10-23, case 20.3): the age the model read for the reader of this turn («ولأختهم الصغيرة سنة
    ونص؟» = 1.5, not her brother's 10), used by audience_age for this conversation's messages."""
    _model_reader_age.msgs, _model_reader_age.age = user_messages, age


def audience_age(user_messages):
    """The age of the child the latest request is about («عمرها ٦»، «بأولى ابتدائي»), for the search and
    the «we are searching for» badge. With two children named, each keeps his own age (QAFIX 2026-10-11,
    case 12: «نرجع لبنتي» got her brother's 16). The model's reading of this turn comes first (QADFIX14 #2)."""
    if getattr(_model_reader_age, "msgs", None) is user_messages and getattr(_model_reader_age, "age", None):
        return max(1, int(_model_reader_age.age))
    age = chat_rules.reader_person(user_messages)[1]
    return age if age is not None and 2 <= age <= 17 else None


def chat_audience(user_messages, ai_prefs, author_named, cleared=False):
    """Who the reader is (see _chat_audience); a child whose age is known is «children» up to 12 and a
    teenager from 13, whichever word the conversation used first."""
    aud = _chat_audience(user_messages, ai_prefs, author_named, cleared)
    if aud in ("children", "young_adult"):
        age = audience_age(user_messages)
        if age is not None:
            aud = "children" if age <= 12 else "young_adult"
    return aud


# a book on bringing up children: said in a summary only by these strong words (not «المراهقة» or «الآباء»,
# which memoirs and novels use too)
_PARENTING = re.compile(r"(?:^| )(?:وال|بال|لل|ال)?تربيه(?: |$)|(?:^| )تربيه (?:ال)?(?:ابناء|اطفال|طفل)|التربويه|تربية الابناء|اطفالك|طفلك|ابنائك|ابنك"
                        r"|(?:^| )(?:لل|ال)والدين(?: |$)|(?:^| )الوالدان(?: |$)|(?:^| )(?:لل|ال)ابوين(?: |$)|(?:^| )طفلي(?: |$)"
                        r"|(?:^| )للاباء(?: |$)|(?:^| )للامهات(?: |$)|parenting|for parents")
# in a title or the library's keywords a wider set is enough («دليل الآباء …»، «أسرة»)
_PARENTING_NAMED = re.compile(_PARENTING.pattern + r"|(?:^| )(?:ال)?اباء(?: |$)|(?:^| )(?:ال)?امهات(?: |$)|(?:^| )(?:ال)?ابناء(?: |$)"
                              r"|(?:^| )(?:ال)?مراهقين(?: |$)")
# QADFIX8 (QAD 2026-10-19, cases 47.2/47.3): a book on healing from harmful parents («أسر مسممة»: «الآباء المؤذون»)
# is not a parenting book for a father who wants to get closer to his son
_PARENT_HARM = re.compile(r"مسمم|(?:^| )(?:ال)?مؤذ(?:ون|ين|ي|يه)?(?: |$)|(?:^| )(?:ال)?سامه(?: |$)|(?:^| )سموم(?: |$)|الاجرامي"
                          r"|ندوب|الارث المؤلم|(?:^| )(?:ال)?اساءه(?: |$)|toxic|abusive")


# «سعرها؟»، «بكم؟»، «متى توصلكم؟»: a question about «it», the book just talked about
# a woman's first name (only to word «كتاباتها» / «كتاباته»)
_FEMININE_NAME = re.compile(r"^(?:احلام|رضوي|غاده|ليلي|سحر|نوال|اثير|بثينه|رجاء|مي|فدوي|نازك|سعاد|هدي|اليف|ايزابيل|اجاثا|جين|فرجينيا|توني|مارجريت|ماري|اميلي|شارلوت|سيلفيا|جوليا|ايلينا|اليف|اناييس|منى|سلمي|ريم|هيفاء|هيفا|نجوى|سلوى)(?: |$)")
_PRONOUN_ASK = re.compile(r"(?:^| )(?:سعرها|سعره|بكم|ب كم|كم سعر|كم كان سعر|كم السعر|متى توصل|متى يوصل|متى توصلكم|متى يوصلكم|متى تتوفر|متى يتوفر"
                          r"|متى ينزل|متى تنزل|متى بتوصل|متى بيوصل|تقدرون توفرونها|تقدرون توفرونه|توفرونها|توفرونه|how much|when will it)")


def _chat_audience(user_messages, ai_prefs, author_named, cleared=False):
    """Who the reader is. The latest message decides; an age said earlier («قصص أطفال») carries on
    only while the visitor keeps to that kind of request, not after he asks for adult biographies
    or an author (library trial, 2026-10-08: «سيرة ذاتية» after «قصص أطفال» found nothing)."""
    # «كتب عن الصحة النفسية للمراهقين، لي أنا كأم»: teenagers are the subject, the reader is the mother
    # (QAD 2026-10-12, case 38)
    if chat_rules.parent_reader(user_messages):
        return None
    if chat_rules.overview_asked(user_messages[-1]):
        # «كم قصة أطفال عندكم؟ ووش أكثر قسم»: a question about the shelves, not a child reader whose badge
        # then turned «وكم كتاب بالانجليزي؟» into «no English copy for a child» (QAD 2026-10-14, case 38)
        return None
    last = detect_audience(normalize(user_messages[-1]))
    if last:
        return last
    _raw = normalize(" ".join(user_messages))
    _gone = set(_raw.split()) - set(_without_prize_name(_raw))
    if _gone and not child_said(user_messages[-1]) and any(keyword_prefs.find_phrase(sorted(_gone), h) >= 0
                     for aud in ("children", "young_adult") for h in AUDIENCE_KEYWORDS[aud]):
        # «غونكور طلاب الثانوية»: the interpreter read the prize's «طلاب الثانوية» as a teenage reader
        ai_prefs = dict(ai_prefs, audience=None)
    if cleared or said_for_adult(user_messages):
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


_pub_cache = {"n": None, "at": 0.0, "names": None}


def publisher_names(all_books):
    """{key: publisher name as stored} for the publishers we have copies of now (cached 2 minutes)."""
    if _pub_cache["names"] is not None and _pub_cache["n"] == len(all_books) and time.time() - _pub_cache["at"] < 120:
        return _pub_cache["names"]
    names = {}
    for b in all_books.values():
        k = publisher_key(b.get("publisher"))
        if len(k) >= 3 and is_available(b):
            names.setdefault(k, b.get("publisher"))
    _pub_cache.update(n=len(all_books), at=time.time(), names=names)
    return names


_PUB_TRIGGERS = {normalize(w) for w in "دار الدار منشورات مطبوعات إصدارات الإصدارات اصدارات ناشر الناشر".split()}
# «شي خفيف من مسكلياني»، «اصدارات تكوين حقت ٢٠٢٦»: a bare name counts only beside a publishing word or
# when a publisher is already being talked about (QAFIX 2026-10-11, cases 29 and 30)
_PUB_LOOSE = {normalize(w) for w in "من حقت حق تبع لدى from by".split()}
_PUB_ANY = re.compile(r"(?:اي|من اي|كل) (?:دار|ناشر|الدور)|(?:مو|مب|مش) (?:شرط|لازم|مهم) (?:من )?(?:نفس )?(?:ال)?(?:دار|ناشر)|"
                      r"الدار (?:مو|مب|مش) (?:مهم|مهمه|شرط)|any publisher")
_PUB_STOPS = {normalize(w) for w in "عندكم عندك موجود موجودة متوفر متوفرة وش ايش هل في فيه ابي ابغى اريد كتب كتاب روايات "
              "رواية يعني او ولا اللي حقت حق الجديدة الجديد جديد نشر النشر للنشر".split()}


def find_publisher(unsupported, last_message, all_books, context=False):
    """«دار صفصافة»، «من منشورات الساقي»، «اصدارات تكوين»: a publisher we stock, named with a publishing
    word; with context=True (a publisher is already the subject) also «من مسكلياني».
    -> (key, publisher name as stored) or None (library trial, 2026-10-08)."""
    names = publisher_names(all_books)
    said = [u for u in unsupported if set(normalize(u).split()) & _PUB_WORDS]
    words = normalize(last_message).split()
    named = False
    for i, w in enumerate(words):
        if keyword_prefs._variants(w) & _PUB_TRIGGERS:
            named = True
            said += [" ".join(words[i + 1:i + 1 + n]) for n in (3, 2, 1)]
    for i, w in enumerate(words):
        if w in _PUB_LOOSE:
            for n in (3, 2, 1):
                ph = " ".join(words[i + 1:i + 1 + n])
                # «عندكم شي من مسكلياني؟»: a publisher's own name that no title uses counts without «دار»
                # (QAFIX re-run, case 30); «من التراث» does not, unless a publisher is already the subject
                if named or context or _pub_distinct(publisher_key(ph), all_books):
                    said.append(ph)
    for phrase in said:
        k = publisher_key(phrase)
        if k in names:
            return k, names[k]
    return None


_distinct_cache = {}


def _pub_distinct(key, all_books):
    """A publisher name that is not an ordinary word: found in fewer than 3 titles of the catalog."""
    if len(key) < 4 or key not in publisher_names(all_books):
        return False
    if key not in _distinct_cache:
        k = " " + key + " "
        _distinct_cache[key] = sum(1 for b in all_books.values()
                                   if k in " " + " ".join(strip_al(w) for w in normalize(b.get("title")).split()) + " ") < 3
    return _distinct_cache[key]


def publisher_asked(last_message):
    """The name the visitor wrote after «دار / منشورات», whether or not we stock that publisher
    («دار عصير الكتب»: «ما عندنا إصداراتها» is the answer, not books about juice; QAFIX 2026-10-11, case 31)."""
    words = normalize(last_message).split()
    for i, w in enumerate(words):
        if keyword_prefs._variants(w) & {"دار", "منشورات"} and not (i and words[i - 1] in ("عن", "في", "حول", "الي", "about")):
            name = []
            for x in words[i + 1:i + 4]:
                if x in _PUB_STOPS or x.isdigit():
                    break
                name.append(x)
            if name:
                return " ".join(name)
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
_ABOUT_WORDS = {normalize(w) for w in "عن حول about".split()}
_WRITER_WORDS = {normalize(w) for w in "كاتب كاتبه كاتبة كتاب كتّاب مؤلف مؤلفه مؤلفة مؤلفين روائي روائيه روائية روائيين اديب اديبه اديبة ادباء شاعر شاعره شاعرة شعراء لكاتب لكاتبه لمؤلف لروائي لاديب لشاعر".split()}


_PLACE_WORDS = {normalize(w) for w in "الدولة دولة الدول المملكة مملكة الحكومة حكومة الجمهورية جمهورية الإمارة إمارة "
                 "المنطقة البلاد الأراضي الأسرة العائلة الثورة الحرب الحدود الصحراء الجزيرة القومية الهوية العملة "
                 "الشركة الجامعة الدولة،".split()}


# regions, and a place named after a writer word («كاتب من الشام أو المغرب العربي») (QAFIX 2026-10-11, case 41)
_ARAB_NATIONS.update({"شامي": ["سوريا", "لبنان", "فلسطين", "الأردن"],
                      "مغاربي": ["المغرب", "تونس", "الجزائر", "ليبيا", "موريتانيا"]})
_REGION_LABELS = [("الخليج", _ARAB_NATIONS["خليجي"]), ("الشام", _ARAB_NATIONS["شامي"]), ("المغرب العربي", _ARAB_NATIONS["مغاربي"])]
_PLACE_NOUNS = {}
for _adj, _names in list(_ARAB_NATIONS.items()) + list(_OTHER_NATIONS.items()):
    if _adj not in ("خليجي", "شامي", "مغاربي"):
        for _n in _names:
            _PLACE_NOUNS.setdefault(normalize(_n), _adj)
_PLACE_NOUNS.update({normalize("الشام"): "شامي", normalize("بلاد الشام"): "شامي", normalize("المغرب العربي"): "مغاربي",
                     normalize("الخليج"): "خليجي", normalize("دول الخليج"): "خليجي", normalize("شمال أفريقيا"): "مغاربي"})
# «مو مصرية ولا خليجية»، «غير سعودي»، «ما أبي روايات مصرية»: the country is refused, not asked for
_NATION_NEG = {normalize(w) for w in "مو مب مش مهو موب غير ليس ليست بدون بلا إلا عدا ماعدا not except non".split()}
_NATION_NEG_WANT = re.compile(r"(?:^| )(?:(?:ما|لا) (?:ابي|ابغي|ابغا|اريد|احب|ودي|نبي)|مابي|مابغي|ماابي|ماابغي)(?: \S+){0,2}$")
_JOIN_WORDS = ("او", "و", "ولا")


def _nation_key(word):
    if word in ("تركي", "هندي"):   # also given names («تركي الحمد»): only «تركية»، «هندية» count
        return None
    for v in keyword_prefs._variants(word):
        for cut in ("يين", "يات", "ين", "ون", "ات", "ه"):   # «سعوديات»، «مصريين»
            if v.endswith(cut) and len(v) - len(cut) >= 3:
                v2 = v[:-len(cut)] + ("ي" if cut in ("يين", "يات") else "")
                for cand in (v2, v[:-len(cut)]):
                    if cand in _ARAB_NATIONS or cand in _OTHER_NATIONS:
                        return cand
        if v in _ARAB_NATIONS or v in _OTHER_NATIONS:
            return v
    return None


def _countries_said(text):
    """-> [(adjective key, [country names], refused)] for every author's country in the text, in order."""
    words = normalize(text).split()
    out, i, last_end, last_neg = [], 0, None, False
    while i < len(words):
        k, span = None, 1
        chained = last_end is not None and all(w in _JOIN_WORDS for w in words[last_end:i]) and i - last_end <= 1
        for n in (2, 1):
            ph = " ".join(words[i:i + n])
            if len(words[i:i + n]) == n and ph in _PLACE_NOUNS:
                after_writer = (i >= 1 and words[i - 1] == "من"
                                and any(x in _WRITER_WORDS for x in words[max(0, i - 4):i - 1]))
                if chained or after_writer:
                    k, span = _PLACE_NOUNS[ph], n
                    break
        if k is None:
            k = _nation_key(words[i])
            if k and i and words[i - 1] in _PLACE_WORDS:
                k = None   # «تاريخ الدولة السعودية»، «المملكة المغربية»: a subject, not the author (QA2 2026-10-09)
            if k and k in _OTHER_NATIONS and not any(x in _WRITER_WORDS for x in words[max(0, i - 2):i]):
                k = None
            # «كتاب عن تاريخ عمان والبحارة العمانيين»: after «عن / حول» the place or people is what the book is about,
            # not where its author comes from; a writer word after «عن» («عن الحب لكاتبة سعودية») keeps the country
            # (QADFIX4, Ibrahim 2026-10-16: «يفرق بين "من" و "عن"»; QAD case 27.1)
            _about = [j for j in range(max(0, i - 8), i) if words[j] in _ABOUT_WORDS]
            if k and _about and not any(x in _WRITER_WORDS for x in words[_about[-1]:i]):
                k = None
        if k:
            neg = (any(w in _NATION_NEG for w in words[max(0, i - 2):i])
                   or bool(_NATION_NEG_WANT.search(" ".join(words[max(0, i - 4):i])))
                   or (chained and last_neg))
            out.append((k, list(_ARAB_NATIONS.get(k) or _OTHER_NATIONS[k]), neg))
            last_end, last_neg = i + span, neg
        i += span
    return out


def wished_country(text):
    """-> (adjective, [country names]) for «كاتب سعودي»، «روايات مصرية»، «لكاتبة كويتية»، «كاتب من الشام أو
    المغرب العربي» (the names of all the places asked for), or None. A refused country is not a wish."""
    asked = [(k, names) for k, names, neg in _countries_said(text) if not neg]
    if not asked:
        return None
    names = []
    for _, ns in asked:
        names += [n for n in ns if n not in names]
    return asked[0][0], names


def unwanted_countries(text):
    """-> the country names the visitor refuses («مو مصرية ولا خليجية»)."""
    out = []
    for _, names, neg in _countries_said(text):
        if neg:
            out += [n for n in names if n not in out]
    return out


_LATAM = ["الأرجنتين", "البرازيل", "تشيلي", "كولومبيا", "المكسيك", "البيرو", "الأوروغواي", "فنزويلا", "كوبا", "نيكاراغوا",
          "غواتيمالا", "الإكوادور", "بوليفيا", "باراغواي", "بنما", "كوستاريكا", "هندوراس", "السلفادور", "الدومينيكان", "بورتوريكو"]


def country_place(names):
    """«الخليج»، «الشام أو المغرب العربي»، «مصر أو العراق»: the places as one short label."""
    if len(names) >= 5 and sum(1 for n in names if strip_al(normalize(n)) in {strip_al(normalize(m)) for m in _LATAM}) >= 5:
        return "أمريكا اللاتينية"   # QADFIX14 #5: the model's countries for «أمريكا اللاتينية / الجنوبية»
    left, parts = [n for i, n in enumerate(names) if normalize(n) not in [normalize(x) for x in names[:i]]], []
    for label, members in _REGION_LABELS:
        got = [n for n in left if normalize(n) in [normalize(m) for m in members]]
        if len({normalize(n) for n in got}) >= len({normalize(m) for m in members}):
            parts.append(label)
            left = [n for n in left if n not in got]
    return " أو ".join((parts + left)[:3])


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


_gender_cache = {"at": 0.0, "map": None}
_NOTE_F = re.compile(r"^(?:كاتبه|شاعره|روائيه|اديبه|مؤلفه|باحثه|قاصه|صحفيه|ناشطه|مترجمه|اكاديميه|طبيبه|معلمه|ممثله|رساميه|فنانه)(?: |$)")
_NOTE_M = re.compile(r"^(?:كاتب|شاعر|روائي|اديب|مؤلف|باحث|قاص|صحفي|ناشط|مترجم|اكاديمي|طبيب|معلم|ناظم|مفكر|فيلسوف|مؤرخ|ممثل|رسام|فنان)(?: |$)")
_NOTE_F_WORD = re.compile(r"(?:^| )(?:كتابها|شعرها|ديوانها|روايتها|رواياتها|اعمالها|قصائدها|مجموعتها|نصوصها)(?: |$)")
_NOTE_M_WORD = re.compile(r"(?:^| )(?:كتابه|شعره|ديوانه|روايته|رواياته|اعماله|قصائده|مجموعته|نصوصه)(?: |$)")


def note_gender(note):
    """«كاتبة …»، «كتابها …» -> "f"; «شاعر …»، «كتابه …» -> "m"; else None. From the author record's own words only."""
    n = " ".join(normalize(note or "").split())
    if not n:
        return None
    if _NOTE_F.search(n):
        return "f"
    if _NOTE_M.search(n):
        return "m"
    f, m = bool(_NOTE_F_WORD.search(n)), bool(_NOTE_M_WORD.search(n))
    return "f" if f and not m else "m" if m and not f else None


def book_author_gender():
    """{book_id: "f" | "m"} from the style notes of its author records (every author of the book agrees), cached 2 minutes."""
    if _gender_cache["map"] is not None and time.time() - _gender_cache["at"] < 120:
        return _gender_cache["map"]
    seen = {}
    conn = get_connection()
    try:
        for r in conn.execute("SELECT ba.book_id, a.style_note_ar FROM book_authors ba JOIN authors a USING(author_id) "
                              "WHERE ba.role='author'"):
            seen.setdefault(r["book_id"], set()).add(note_gender(r["style_note_ar"]))
    except sqlite3.Error:
        seen = {}
    finally:
        conn.close()
    out = {i: next(iter(g)) for i, g in seen.items() if len(g) == 1 and None not in g}
    _gender_cache.update(at=time.time(), map=out)
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


_BOOK_DETAIL_WORDS = {normalize(w) for w in (
    "دار نشر النشر ناشر الناشر طبعه طبعة الطبعه الطبعة طبعات الطبعات سعر السعر سعره صفحات الصفحات عدد صفحاته "
    "publisher publishing edition editions price pages").split()}

_TITLE_ASKING = {normalize(w) for w in (
    "هل فيه في عندكم عندك تبيعون متوفر متوفره موجود موجوده ابي ابغى ابغي ابغا اريد ودي كتاب روايه رواية "
    "مين من وش ايش دار نشر ناشر الناشر طبعه طبعة طبعات سعر بكم").split()}


def _month_picks(all_books, limit=5):
    """The library's picks of the month that are in stock -> [(book, line)] (picks.public_books), else []."""
    try:
        _m, rows = picks.public_books()
    except Exception:  # noqa: BLE001 — picks tables missing on an old database: no picks
        return []
    out = []
    for r in rows or []:
        b = all_books.get(r.get("book_id"))
        if b and is_available(b):
            out.append((b, r.get("line")))
    return out[:limit]


def catalog_author_in(message, all_books):
    """A catalog author whose full name (two words at least) is written in the message and who has a book in stock
    -> his record, else None. The longest name wins. «هل تركي بن فهد آل سعود شاعر؟» was declined as off topic although
    he has two history books here (library inbox 2026-10-11 §3.8; MGR 2026-10-17, Ibrahim «نفذ الاصلاحين»)."""
    words = normalize(message or "").split()
    if len(words) < 2:
        return None
    stocked = {b["book_id"] for b in all_books.values() if is_available(b)}
    for size in range(min(6, len(words)), 1, -1):
        for i in range(len(words) - size + 1):
            rec = authors.lookup(" ".join(words[i:i + size]), spelling=False)
            if rec and authors.book_ids(rec["author_id"]) & stocked:
                return rec
    return None


def exact_title_books(message, all_books):
    """Available books whose whole title is the message, once a few asking words are taken off its start
    («دار نشر فن الحرب» -> «فن الحرب»). Titles of two words at least; every edition, the most copies first."""
    words = normalize(message).split()
    while words and words[0] in _TITLE_ASKING:
        words = words[1:]
    want = " ".join(words)
    if len(words) < 2 or keyword_prefs.understood_anything(keyword_prefs.understand([want])):
        return []   # «قصص قصيرة»، «رواية رومانسية»: a wish in reading words, even if a book bears that title
    hits = [b for b in all_books.values() if is_available(b) and " ".join(normalize(b.get("title") or "").split()) == want]
    return sorted(hits, key=lambda b: -(b.get("quantity") or 0))


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
        # compared with numbers in digits and hamza seats folded: «عشر دقايق و٣٨ ثانية» is «10 دقائق و38
        # ثانية …», «مية عام من العزلة» is «مئة عام من العزلة» (QAFIX 2026-10-11, case 23)
        nm = chat_rules.title_key(nm)
        ranked = []
        for b in books:
            nt = chat_rules.title_key(b["title"])
            if not nt:
                continue
            extra = 0
            if nt == nm:
                rank = 0
            elif len(nm) >= 8 and len(nm.split()) >= 2 and chat_rules.title_skeleton(nt) == chat_rules.title_skeleton(nm):
                rank = 0.2   # «مرتفعات ويذرنغ» = «مرتفعات ويذرنج» (QAD 2026-10-13, case 29)
            elif _title_fuzzy(nt, nm) and not nt.startswith(nm + " "):
                # the visitor's wording of the whole title («لوليتا في طهران» ~ «أن تقرأ لوليتا في
                # طهران») before a short title the message starts with («لوليتا») (QA 2026-10-08)
                rank = 0.5
            elif nt.startswith(nm + " ") or nm.startswith(nt + " "):
                # QADFIX11 (QAD 2026-10-21, case 8.3): «حكاية» (one word) is not «حكاية الطفلة الضائعة» asked for
                rank = 3.5 if (nm.startswith(nt + " ") and len(nt.split()) == 1) else 1
            elif (" " + nm + " ") in (" " + nt + " ") or (" " + nt + " ") in (" " + nm + " "):
                rank = 2
            elif chat_rules.title_subset(nt, nm) is not None:
                # every word the visitor wrote is in the title, in any order
                rank, extra = 2.5, chat_rules.title_subset(nt, nm)
            elif len(nm) >= 5 and (nt in nm or nm in nt):
                rank = 3
            else:
                continue
            if rank > max_rank or (rank > 0 and len(nt) < 4):
                continue   # a very short title («لا») only matches exactly
            ranked.append((rank, extra, not is_available(b), b["book_id"], b))
        ranked.sort(key=lambda x: x[:4])
        best = ranked[0][:2] if ranked else None
        if (best is None or best[0] > 0.5) and not own:
            # «ثلاثية القاهرة»، «رباعية نابولي»: a series by its name alone, its parts in order (QAFIX
            # 2026-10-11, case 48: only looked up when the author's name stood beside it)
            series = chat_rules.series_books(nm, all_books)
            if series:
                for b in series[:4]:
                    if b["book_id"] not in [x["book_id"] for x in found]:
                        found.append(b)
                continue
        for b in [x[4] for x in ranked if x[:2] == best][:2]:
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
    # «فن الحرب» / «دار نشر فن الحرب» with the model down: the stocked book of that title, every edition with its
    # publisher (it was read as books about war; inbox 2026-10-11 §3.3, audit 2026-10-12 #8)
    exact = exact_title_books(user_messages[-1], all_books) if user_messages else []
    if exact:
        en = language == "en"
        pubs = [b.get("publisher") for b in exact if b.get("publisher")]
        title = exact[0]["title"]
        if en:
            reply = f"Yes, «{title}» is available" + (f" in {len(exact)} editions" if len(exact) > 1 else "") + \
                    (": " + ", ".join(pubs) + "." if pubs else ".") + " Tap a book to see its details."
        else:
            reply = f"إيه، «{title}» متوفر عندنا" + (f" في {len(exact)} طبعات" if len(exact) > 1 else "") + \
                    (": " + "، ".join(pubs) + "." if pubs else ".") + " اضغط على أي كتاب تشوف تفاصيله."
        return {"reply": reply,
                "books": [with_origin(dict(b, reason=((("Publisher: " if en else "الناشر: ") + b["publisher"]) if b.get("publisher") else ""),
                                           match="direct"), language) for b in exact[:4]],
                "suggestions": (["Something similar", "Show me others"] if en else ["شي يشبهه", "ورّني غيره"]),
                "understanding": "keywords", "_debug": {"mode": "keywords", "why": "exact title"}}
    prefs, pprefs, exclusions, _ = prefs_from_ai(understood, normalize(" ".join(user_messages)))
    pool = [b for b in all_books.values() if is_available(b) and b["book_id"] not in shown]
    if user_messages and (chat_rules.parent_reader(user_messages)
                          or (adult_said(user_messages[-1]) and not child_said(user_messages[-1]))):
        # «… للمراهقين، لي أنا كأم» with the model down: the reader is the parent, as in run_chat
        # (audit 2026-10-12 evening #1: only YA books were offered to the mother here)
        prefs = dict(prefs, genres=set(prefs.get("genres") or ()) - CHILD_GENRES)
        pprefs = dict(pprefs, audience=None)
        pool = [b for b in pool if b.get("genre") not in CHILD_GENRES]
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
        # the same person written in another script: «يوفال نوح هراري» / «Yuval Noah Harari» through the author records
        # (QADFIX4, QAD 2026-10-16 case 50.1: «21 Lessons for the 21st Century» missed for «like Sapiens»)
        try:
            _rec = authors.lookup(ref.get("author") or "")
            _rec_ids = authors.book_ids(_rec["author_id"]) if _rec else set()
        except Exception:  # noqa: BLE001
            _rec_ids = set()
        for b in all_books.values():
            ba = normalize(b.get("author") or "")
            if not ba or ba in ("غير معروف", "unknown") or not is_available(b) or b["book_id"] not in suitable:
                continue
            if _same_author(ra, ba) or b["book_id"] in _rec_ids:
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
_KIND_SKIP = _GENERIC_SUBGENRE_WORDS | {"اليافعين", "يافعين", "الاطفال", "اطفال", "للاطفال", "لليافعين", "سلسله", "سلسلة"}


def reference_kind_matches(ref, all_books, suitable, reader_age=None, exclude=(), language="ar"):
    """Books of the same KIND as a well-known book we do not have («يشبه هاري بوتر» -> fantasy / magic /
    adventure for her age), from the bookshop labels the interpreter gives for it and our books' own
    subgenres and keywords — never a claim that they resemble its story (QAD 2026-10-14, case 48).
    -> [(book, why, "reference")] best first, at most 4."""
    want = set()
    for t in list(ref.get("subgenres") or []) + list(ref.get("themes") or []):
        want |= {w for w in _subgenre_words(t) if w not in _KIND_SKIP}
    if not want:
        return []
    scored = []
    for bid in suitable:
        b = all_books.get(bid)
        if not b or bid in exclude or not is_available(b) or normalize(b["title"]) == normalize(ref["title"]):
            continue
        have = set()
        for t in _json_list(b.get("subgenres")) + _json_list(b.get("keywords")) + [b.get("genre") or ""]:
            if isinstance(t, str):
                have |= _subgenre_words(t)
        hit = sorted(want & have)
        if not hit:
            continue
        fit = age_fit(b, reader_age) if reader_age is not None else 0
        if fit < 0:
            continue
        scored.append((len(hit) + (1 if fit > 0 else 0), b, hit))
    scored.sort(key=lambda x: (-x[0], x[1]["book_id"]))
    out = []
    for _s, b, hit in scored[:4]:
        why = (f"Same kind of book as «{ref['title']}»: " + ", ".join(hit)) if language == "en" else (
            f"من نفس نوع «{ref['title']}»: " + "، ".join(hit))
        out.append((b, why, "reference"))
    return out


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


def _run_chat_once(messages, active=None, language="ar", clear_audience=False, drop_context=None, second_look_note=None):
    """Core conversation turn. Raises ValueError for malformed input. run_chat() below may call it a second time."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages required")
    chat_rules.set_model_parent(False)   # QADFIX12 #3: set again below from this turn's interpretation
    set_model_reader_age(None, None)      # QADFIX14 #2: likewise
    all_books = {b["book_id"]: dict(b) for b in list_books(only_active=False)}
    conv, shown, streak, last_batch = [], [], 0, []
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
                last_batch = ids
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
    # «ورّني كتب على طول»: the visitor skips the remaining questions
    skip_questions = start_paths.is_skip(user_messages[-1])
    if skip_questions:
        streak = max(streak, 2)
    cards = get_profile_cards()

    active = active if active is not None else llm.get_active()
    # a tapped start button: Manar asks that path's first question before any book (Ibrahim 2026-10-11)
    _opening = start_paths.opener(user_messages, language)
    if _opening:
        return {"reply": _opening[0], "books": [], "suggestions": list(_opening[1]), "understanding": "ai",
                "provider": active[0] if active else None, "model": active[1] if active else None,
                "_debug": {"mode": "start_path", "path": user_messages[0]}}
    # questions we hold no data for (Ibrahim 2026-10-09): «وش أكثر كتاب مبيعًا؟» -> say so in one line and
    # ask first, offering recent prize winners; «ايه» to that offer -> the offer itself is the wish
    _nd = no_data.followup(conv)
    if _nd:
        conv[-1] = {"role": "user", "content": _nd}
        user_messages[-1] = _nd
        user_text = " ".join(user_messages)
    elif no_data.design_asked(user_messages[-1]):
        # who designed a book or its cover: outside the library's field, said plainly, not logged as unanswered
        # (Ibrahim 2026-10-17, open question 4)
        _reply, _chips = no_data.design_answer(language)
        return {"reply": _reply, "books": [], "suggestions": _chips, "understanding": "ai",
                "provider": active[0] if active else None, "model": active[1] if active else None,
                "_debug": {"mode": "no_data", "asked": user_messages[-1], "kind": "design"}}
    elif no_data.asked(user_messages[-1]) and _month_picks(all_books):
        # sales: «اختياراتنا هذا الشهر» of the library, not other shops' best-seller lists (Ibrahim 2026-10-17,
        # open questions 9 and 14)
        _mp = _month_picks(all_books)
        _reply, _chips = no_data.picks_answer([b["title"] for b, _ in _mp], language)
        return {"reply": _reply, "books": [with_origin(dict(b, reason=line or ""), language) for b, line in _mp],
                "suggestions": _chips, "understanding": "ai",
                "provider": active[0] if active else None, "model": active[1] if active else None,
                "_debug": {"mode": "no_data", "asked": user_messages[-1], "kind": "picks"}}
    elif no_data.asked(user_messages[-1]):
        _reply, _chips = no_data.answer(user_messages[-1], language)
        open_questions.record("question", user_messages[-1])
        return {"reply": _reply, "books": [], "suggestions": _chips, "understanding": "ai",
                "provider": active[0] if active else None, "model": active[1] if active else None,
                "_debug": {"mode": "no_data", "asked": user_messages[-1]}}
    it = (chat.interpret(conv, active, language) if language == "en" else chat.interpret(conv, active)) if active else None
    if it is not None and streak < 2 and start_paths.is_undecided(user_messages[-1]):
        it["intent"] = "clarify"   # «ما أدري وش يحب»: one more question first (Ibrahim 2026-10-11)
    if it is None:
        out = chat_keyword_fallback(user_messages, all_books, set(shown), cards, language)
        if active:
            out["_debug"].update(failed_stage="interpret", call=dict(chat.last_failure.get("call") or {}))
        return out

    interpret_call = dict(llm.last_call)
    # QADFIX12 #6 (QAD 2026-10-22, case 32): a visitor who writes in English, asks for English or does not read Arabic is
    # answered in English on every path, whatever the page language (the model reads it, in any wording)
    if language != "en" and it.get("reply_language") == "en":
        language = "en"
    # QADFIX12 #3 (QAD 2026-10-22, case 13.1; QADFIX5 #9 and QADFIX9 ج4 came back): «كتاب لي أنا عن تربية طفل عمره سنتين» —
    # the model says the reader is the visitor, an adult; the child's age is what the book is about. Every parent-reader
    # check of this turn (chat_rules.parent_reader) honours it, not only the phrasings in its word lists.
    _model_parent = bool(it.get("reader_is_visitor")) and (it.get("prefs") or {}).get("audience") == "adult"
    chat_rules.set_model_parent(_model_parent)
    if not _model_parent:
        set_model_reader_age(user_messages, it.get("reader_age"))
    author_request = it.get("author_request", {})
    if it.get("intent") in ("off_topic", "smalltalk") and (author_request or {}).get("mode") in (None, "", "none"):
        # a question about a catalog author is a reading question: «هل تركي بن فهد آل سعود شاعر؟» -> what we have by
        # him and what his books are (library inbox 2026-10-11 §3.8; MGR 2026-10-17)
        _named_author = catalog_author_in(user_messages[-1], all_books)
        if _named_author:
            author_request = dict(author_request or {}, name_as_written=_named_author["name_ar"],
                                  full_name=_named_author["name_ar"], aliases=[], mode="works")
            it["author_request"], it["intent"], it["author_question"] = author_request, "recommend", _named_author["name_ar"]
    # a publisher's name is not an author: «كتب مسكلياني» and then a tapped «أبغى قراءة خفيفة» was answered
    # with «وش اسم المؤلف كامل؟» (QAFIX 2026-10-11, case 30)
    _aw = str(author_request.get("name_as_written") or "").strip()
    if (_aw and author_request.get("mode") in ("works", "similar", "both")
            and publisher_key(_aw) in publisher_names(all_books) and not authors.lookup(_aw, spelling=False)
            and any(find_publisher([], t, all_books, context=True) for t in user_messages)):
        author_request = dict(author_request, mode="none")
        it["author_request"] = author_request
    if _aw and author_request.get("mode") not in (None, "", "none") and generic_author(_aw) and not authors.lookup(_aw, spelling=False):
        # QADFIX7 #12 (QAD 2026-10-19, case 49.1): «شعر لشاعرات خليجيات» — «مجموعة شاعرات» is no author
        author_request = dict(author_request, mode="none")
        it["author_request"] = author_request
        if isinstance(it.get("prefs"), dict):
            it["prefs"]["authors"] = [a for a in it["prefs"].get("authors") or [] if not generic_author(a)]
    author_reply, author_names, author_only = author_flow.resolve(
        author_request, conv, all_books.values(), language == "en")
    def author_answer(reply):
        return {"reply": reply, "books": [], "suggestions": [], "understanding": "ai",
                "provider": active[0], "model": active[1],
                "_debug": {"mode": "ai", "author_request": it.get("author_request"), "author_gate": True}}
    if author_reply:
        return author_answer(author_reply)
    # a well-known author we hold no book of («عندكم شي لنزار قباني؟», Ibrahim 2026-10-12): say he is known and
    # that we have none of his books now, offer the closest books by his recorded style, and log the request
    # for the library (أسئلة بلا جواب) instead of «تقصد صباح قباني؟»
    absent_author = known_absent_author(author_request, all_books) if author_only and author_request.get("mode") == "works" else None
    if absent_author:
        author_only, author_names = False, None
        author_request = dict(author_request, mode="similar", full_name=absent_author["name_ar"])
        it["author_request"] = author_request
        open_questions.record("missing_author", absent_author["name_ar"])
    if author_only:
        # An author's identity is not a genre request; do not invent genre filters.
        it["prefs"]["subgenres"] = []
        it["references"] = []
    if author_only and it["intent"] == "clarify":
        # «عندكم شي لسالينجر؟» answered «إيه عندنا الحارس في حقل الجاودار» with no card and a mood question: the
        # author's books are the answer (QADFIX4, QAD 2026-10-16 case 21.1)
        it["intent"] = "recommend"
    prefs, pprefs, exclusions, unsupported = prefs_from_ai(it["prefs"], normalize(user_text))
    # QADFIX14 #2 (QAD 2026-10-23, cases 20.1, 20.3): what the book should be like as an object («كتب قماش»، «كرتون مقوى»،
    # «صور كبيرة وكلام قليل») is read by the model apart from subjects: never a subject to find (it emptied the answer or
    # matched «الكبيرة» in titles), never a cartoon adaptation; books whose own text says it rank first (below)
    _phys = [p for p in (it.get("physical_wishes") or []) if " ".join(normalize(p).split())]
    _phys_words = {strip_al(w).rstrip("ه") for p in _phys for w in normalize(p).split() if len(w) > 2}
    if _phys_words:
        unsupported = [u for u in unsupported if not ({strip_al(w).rstrip("ه") for w in normalize(u).split()} & _phys_words)]
        if prefs.get("adaptation") == "animated" and any("كرتون" in normalize(p) for p in _phys):
            prefs["adaptation"] = None
    # QADFIX12 #1: the subjects the model itself read as wanted; any other subject comes from a fallback of the code
    _model_subjects = {" ".join(normalize(u).split()) for u in unsupported}
    q7 = {}   # QADFIX7 (QAD 2026-10-19): notes for the guidance
    if prefs.get("adaptation_max_year") and re.search(r"(?:مو|مب|مش|ما|ماهو|مهو|ليس|غير) (?:شرط|لازم|ضروري|مهم)|ما يهم|مايهم|مو مهم|not necessar|doesn.t matter|any (?:year|time)",
                                                     " ".join(normalize(user_messages[-1]).split())):
        # QADFIX7 #9 (case 29.2): «لا مو شرط قبل ١٩٧٥» lifts the year limit said before
        prefs.pop("adaptation_max_year", None)
    if (prefs.get("length") == "short" and chat_rules.shortest_asked(user_messages[-1]) == "shorter"
            and not re.search(r"قصير|short(?!er|est)", normalize(user_messages[-1]))):
        # «أقصر» after some books is shorter than THOSE (worked out below from the page counts), not «a short
        # book»: read as length=short it left only very short books, none fitting «يونّس» (QAD 2026-10-14, case 43.2)
        prefs["length"] = None
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
    if (chat_rules.parent_said(user_messages[-1]) or _model_parent) and pprefs["audience"] in ("children", "young_adult"):
        # QADFIX5 #9: a guide for dealing with teenagers is for the parent; the teenagers are its subject
        pprefs["audience"] = None
        prefs["genres"] = set(prefs["genres"]) - CHILD_GENRES
    if it["mentioned_titles"] and pprefs["audience"] in ("children", "young_adult"):
        # QADFIX11 (QAD 2026-10-21, case 8.3): «حكاية الطفلة الضائعة» — a word of the title asked for is not a child reader
        _lm10 = " " + " ".join(normalize(user_messages[-1]).split()) + " "
        for _t10 in it["mentioned_titles"]:
            _lm10 = _lm10.replace(" " + " ".join(normalize(_t10).split()) + " ", " ")
        if (not detect_audience(_lm10.strip()) and not child_said(_lm10.strip())
                and not any(detect_audience(normalize(t)) or child_said(t) for t in user_messages[:-1])):
            pprefs["audience"] = None
    _nt11 = _NOT_TRANSLATED_KIND.search(" " + " ".join(normalize(user_messages[-1]).split()) + " ")
    if _nt11:
        # QADFIX11 (QAD 2026-10-21, case 34.1): «إلا الروايات المترجمة» — Arabic originals of that kind, not «no novels»
        prefs["translated"] = prefs.get("translated") or "no"
        if _nt11.group(1) in ("روايات", "روايه"):
            prefs["genres"] = set(prefs.get("genres") or ()) | {"رواية"}
        if it["intent"] in ("clarify", "smalltalk"):
            it["intent"] = "recommend"
    if it["intent"] == "clarify" and (prefs.get("awards") or (last_batch and wanted_edition(user_messages[-1], language))):
        # QADFIX12 (replay of QAD 2026-10-22 case 32.2): a prize asked for, or «in English please, I don't read Arabic» after books
        # were shown, is a wish concrete enough to answer with books, not with one more question
        it["intent"] = "recommend"
    _prev_a = next((c.get("content") or "" for c in reversed(conv[:-1]) if c.get("role") == "assistant"), "")
    if (it["intent"] == "clarify" and _prev_a and not last_batch and _prev_a.strip().endswith(("؟", "?"))
            and not start_paths.is_undecided(user_messages[-1])
            and (prefs.get("genres") or prefs.get("subgenres") or prefs.get("length") or unsupported
                 or pprefs.get("moods") or pprefs.get("use_cases") or pprefs.get("weight") or pprefs.get("pace"))):
        # QADFIX14 ب9 (QAD 2026-10-23, case 49.2): the visitor answered the question asked («أبي شي أقرأه بالطيارة»): books
        # now, not a second question in a row
        it["intent"] = "recommend"
    pprefs["reader_age"] = audience_age(user_messages) if pprefs["audience"] in ("children", "young_adult") else None
    _lastq = " ".join(normalize(user_messages[-1]).split())
    if (re.search(r"(?:^| )(?:ال)?تاريخ(?: |$)", _lastq) and not re.search(r"روايه|روايات|سيره|سير |مذكرات|novel|biograph", _lastq)
            and ("سيرة ذاتية" in prefs["genres"] or _LIVELY.search(_lastq))):
        # QADFIX5 #7 (QAD 2026-10-17, case 3.1): «تاريخ؟ بس مو ممل» is history told in a lively way, not biographies
        prefs["genres"] = (set(prefs["genres"]) - {"سيرة ذاتية"}) | {"تاريخ", "رواية"}
        # Ibrahim 2026-10-17: a historical novel is also history that is not boring — offer it too
        prefs["soft_subgenres"] = list(dict.fromkeys(list(prefs.get("soft_subgenres") or []) + ["رواية تاريخية"]))
        it["lively_history"] = True
    if re.search(r"(?:^| )(?:ال)?(?:روايه|روايات) (?:ال)?تاريخي(?:ه)?(?: |$)|historical novel", _lastq) and "تاريخ" in prefs["genres"]:
        # QADFIX6 (replay of QAD 2026-10-18 case 34.1): «رواية تاريخية» is a novel, not history books beside novels
        prefs["genres"] = (set(prefs["genres"]) - {"تاريخ"}) | {"رواية"}
        if not prefs.get("subgenres"):
            prefs["subgenres"] = ["تاريخية"]
    if _GORE_NOT.search(_lastq):
        # QADFIX5 #8 (QAD 2026-10-17, case 35): «nothing gory / بدون دم» is content to avoid, never a subject
        pprefs["avoid"] = set(pprefs.get("avoid") or ()) | {"violence"}
        unsupported = [u for u in unsupported if not _GORE_WORD.search(" ".join(normalize(u).split()))]
    if _ESCAPE.search(_lastq):
        unsupported = [u for u in unsupported if not (set(normalize(u).split()) <= _ESCAPE_WORDS)]
        if not prefs["genres"] and not prefs.get("subgenres"):
            prefs["genres"] = {"رواية"}   # a book to get lost in: a novel
    if (wanted_edition(user_messages[-1], language) and len(_lastq.split()) <= 6 and len(user_messages) > 1
            and not prefs["genres"] and not prefs.get("subgenres")):
        # QADFIX5 #8: «English books only please» after «a cozy mystery» keeps the kind of book asked for before
        _prev = local_understand(user_messages[:-1])
        prefs["genres"] = set(_prev.get("genres") or ())
        prefs["subgenres"] = list(_prev.get("subgenres") or [])
    if (not prefs["genres"] and last_batch and _ANOTHER_ONE.search(" " + _lastq + " ")
            and not chat_rules.excluded_genres(user_messages)):
        # QADFIX5 #16 (QAD 2026-10-17, case 44.2): «شي ثاني بنفس الطول بس عربي» after a novel is another novel,
        # not an essay or a comic: the one kind of the books just shown stays the kind asked for
        _kinds = {all_books[i].get("genre") for i in last_batch if i in all_books} - {None}
        if len(_kinds) == 1:
            prefs["genres"] = set(_kinds)
    if prefs.get("awards") and not prefs.get("award_year") and wants_recent(user_messages[-2:]):
        prefs["award_min_year"] = time.localtime().tm_year - RECENT_YEARS
    unsupported = [u for u in unsupported if not only_recency(u)]
    # a prize stored under «other» asked for by its name («جائزة الملك فيصل») (QA2 2026-10-09)
    _prize = prize_named(user_messages[-1]) if set(prefs.get("awards") or ["any"]) <= {"any"} else None
    if _prize:
        prefs["awards"] = [honors.named(_prize)]
        unsupported = [u for u in unsupported if not (set(normalize(u).split()) & set(normalize(_prize).split()))]
    # a prize stored under «other» written out in full, though one of its words is a prize of its own:
    # «غونكور للرواية الأولى» is not the main Goncourt (QAD 2026-10-12, case 16)
    _sub = honors.other_name_in(user_messages[-1])
    if _sub:
        prefs["awards"] = [honors.named(_sub)]
        unsupported = [u for u in unsupported if not (set(normalize(u).split()) & set(normalize(_sub).split()))]
    # «أبيها بالعربي»، «عربية» right after a prize or a span of years: that wish still holds, only the
    # language / origin is added (QAD 2026-10-12, cases 13.2 and 19.2)
    _carry_from = None
    if len(user_messages) > 1 and chat_rules.refine_only(user_messages[-1]):
        _carry_from = next((t for t in reversed(user_messages[:-1]) if not chat_rules.refine_only(t)), None)
        if _carry_from and it.get("intent") == "clarify" and last_batch:
            # books were shown and only the language changes: show those books, no new question (case 19.2)
            it["intent"] = "recommend"
    if _carry_from and set(prefs.get("awards") or ["any"]) <= {"any"}:
        _sub_c = honors.other_name_in(_carry_from)
        _keys_c = [k for k in local_understand([_carry_from]).get("awards") or [] if k != "any"]
        _named_c = None if (_sub_c or _keys_c) else prize_named(_carry_from)
        _aw = ([honors.named(_sub_c)] if _sub_c else _keys_c or ([honors.named(_named_c)] if _named_c else [])
               or list(local_understand([_carry_from]).get("awards") or []))
        if _aw:
            prefs["awards"] = _aw
    # «ومين فاز فيها ٢٠٢٥؟» after a prize was talked about: the same prize with the new year, and the wishes
    # of the earlier request (short, light ...) do not pick the winner (QAFIX 2026-10-11, case 35)
    _lastn = " ".join(normalize(user_messages[-1]).split())
    if re.search(r"فاز|فائز|فايز|الفائز|(?:^| )(?:won|winner)(?: |$)", _lastn):
        if not prefs.get("awards") and len(user_messages) > 1:
            _prev_aw = local_understand(user_messages[:-1]).get("awards") or []
            if _prev_aw:
                prefs["awards"] = list(_prev_aw)
        _yr = re.search(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", _lastn)
        if prefs.get("awards") and _yr and not any(years_said(user_messages[-1])):
            prefs["award_year"] = int(_yr.group(1))
        if prefs.get("awards") and re.search(r"(?:^| |و)(?:مين|من|منو|وش|ايش|who|which)(?: |$)", _lastn):
            prefs.update(length=None, subgenres=[], soft_subgenres=[])
            pprefs.update(moods=set(), pace=set(), weight=set(), use_cases=set())
    # «وش فاز قبل ٢٠١٥؟»، "anything published after 2020?": years said in the latest message
    # QADFIX12 #9 (QAD 2026-10-22, case 6.1): «similar to 1984 but more recent» — a number that is the title of a book the
    # model named («1984») is the novel, never a year
    _title_nums = {w for t in (list(it["mentioned_titles"]) + list(it.get("title_variants") or [])
                               + [r.get("title") for r in it.get("references") or [] if isinstance(r, dict)])
                   for w in normalize(t or "").split() if w.isdigit()}
    _lo, _hi = years_said(" ".join(w for w in normalize(user_messages[-1]).split() if w not in _title_nums)
                          if _title_nums else user_messages[-1])
    _ysrc = user_messages[-1]
    if not (_lo or _hi) and it.get("publication_years") and not prefs.get("awards"):
        # QADFIX14 #1 (QAD 2026-10-23, case 12): the years the model read in any wording, including «من نفس الفترة» after an
        # earlier range («روايات عربية من نفس الفترة» kept 2000–2005); the code's own reading still comes first
        _lo, _hi = it["publication_years"]
    if not (_lo or _hi) and _carry_from:
        _lo, _hi = years_said(_carry_from)
        _ysrc = _carry_from
    if not (_lo or _hi) and len(user_messages) > 1 and _narrow_followup(user_messages[-1]):
        # QADFIX7 #3 (QAD 2026-10-19, case 43.2): «اللي مؤلفها سعودي منها» after «روايات طلعت في ٢٠٢٥» keeps 2025
        _lo, _hi = years_said(user_messages[-2])
        _ysrc = user_messages[-2]
    if not (_lo or _hi) and len(user_messages) > 1 and _SAME_YEAR.search(" ".join(normalize(user_messages[-1]).split())):
        # QADFIX9 (QAD 2026-10-20, case 3.2): «عطني غيرها من نفس السنة» — the year said before
        for _m in reversed(user_messages[-4:-1]):
            _lo, _hi = years_said(_m)
            if _lo or _hi:
                _ysrc = _m
                break
    pub_years = None
    _new_wish = new_release_wished(user_messages)
    if (_lo or _hi) and (prefs.get("awards") or re.search(r"فاز|فائز|فايز|جائز|جايز|جوائز|prize|award|won", normalize(_ysrc))):
        prefs["awards"] = prefs.get("awards") or ["any"]
        if prefs.get("award_year") in (_lo, _hi, (_lo or 0) - 1, (_hi or 0) + 1):
            prefs["award_year"] = None   # the interpreter may read «قبل ٢٠١٥» as «في ٢٠١٥»
        prefs.update(award_min_year=_lo, award_max_year=_hi, award_years_said=True)
    elif _lo or _hi:
        pub_years = (_lo, _hi)
    elif _new_wish and not prefs.get("awards"):
        pub_years = (time.localtime().tm_year - NEW_RELEASE_YEARS, None)
    if pub_years and it["intent"] == "clarify":
        # QADFIX14 ب7 (QAD 2026-10-23, case 46.1): «رواية انكتبت في العشرينات» is concrete enough: books from those years
        it["intent"] = "recommend"
    if pub_years and _new_wish and it["intent"] in ("clarify", "smalltalk"):
        # «وش وصلكم جديد هالشهر؟» → «أبي رواية منها»: the new novels, not «وش آخر رواية عجبتك؟» (QAD 2026-10-13, case 6.2)
        it["intent"] = "recommend"
    unsupported = [u for u in unsupported if not _PLATFORM.search(normalize(u))]
    if wanted_edition(user_messages[-1], language) or (len(user_messages) > 1 and wanted_edition(user_messages[-2], language)
                                                      and len(user_messages[-1].split()) <= 6):
        # «يبي يقوي انجليزيه … قصص انجليزي»: the English language is the copies asked for, not a subject to find
        # (QAD 2026-10-15, case 12.2: «ما لقينا كتابًا عن اللغة الإنجليزية»)
        unsupported = [u for u in unsupported if not re.fullmatch(
            r"(?:(?:تقويه|تعلم|تحسين|تقوي|يقوي) )?(?:ال)?(?:لغه )?(?:ال)?(?:انجليزي|انجليزيه|انقليزي|english)(?: language)?",
            " ".join(normalize(u).split()))]
    if _PLATFORM.search(normalize(user_messages[-1])) and not prefs.get("adaptation"):
        prefs["adaptation"] = "screen"
    _res = award_results_said(user_messages[-1]) if prefs.get("awards") else None
    if _res == ["winner"] and set(prefs["awards"]) <= {"any"}:
        _res = None   # «كتاب فاز بجائزة»: the book's or its author's prize, as before
    if _res:
        prefs["award_results"] = _res
    if prefs.get("awards") and _WHO_WON.search(normalize(user_messages[-1])):
        # «ميدالية نجيب محفوظ» → «ومين فاز فيها سنة ٢٠٢٢؟»: the winner, whatever its kind; «روايات» said before
        # kept the 2022 winner (a memoir) out (QAD 2026-10-13, case 35.2)
        if not local_understand([user_messages[-1]])["genres"]:
            prefs["genres"] = set()
        if it["intent"] in ("clarify", "smalltalk"):
            it["intent"] = "recommend"
    if pub_years or prefs.get("award_years_said"):
        # «published after 2020» is the wish itself, not a subject «منشور» (QA2 re-run, case 8)
        _yr_words = {normalize(w) for w in "نشر منشور منشوره المنشوره صدر صادر صادره اصدار اصدارات الاصدارات نشرت صدرت حديثا "
                     "بعد قبل بين عام سنه سنة published publication released release after before since year new "
                     "احدث الاحدث اخر الاخيره جديد جديده الجديد الجديده كتاب كتب الكتب روايه روايات الروايات وصل وصلكم وصلت اللي "
                     "هالسنه هالعام هذي هذه هذا السنه العام الحاليه الحالي هالشهر الشهر الصادره صدرت latest newest this books book "
                     # «روايات صدرت مؤخرًا» (staff chat 2026-10-13 23:04)
                     "حديث حديثه الحديثه مؤخرا موخرا طلعت طلع نزلت نزل اجدد recent recently novels novel".split()}
        # QADFIX5 #2 (QAD 2026-10-17, case 32): «ما قبل ١٩٠٠» is the year wish already read, not a subject to find
        unsupported = [u for u in unsupported if not all(w in _yr_words or w.isdigit() for w in normalize(u).split())
                       and not decade_said(u) and not any(years_said(u))]
    if (clear_audience or said_for_adult(user_messages)) and (not child_said(user_messages[-1]) or chat_rules.parent_reader(user_messages)):
        # «… للمراهقين، لي أنا كأم»: the teenagers are the subject (QAD 2026-10-12, case 38)
        # the badge was removed or the visitor said «للكبار»: no children's shelf either, even if the
        # interpreter carried «أطفال» on from earlier messages
        prefs["genres"] = set(prefs["genres"]) - CHILD_GENRES
    # The latest message names a grown-up reader («لي أنا كأم»، «للكبار»، «فوق ١٨»): no children's or
    # young readers' book is offered, whichever order the search ranks ties in. Before, a YA book about
    # teenagers could still reach the facts for the mother (audit 2026-10-12 evening #1: hash seed 7).
    # A title asked for by name and an author's own books are not affected.
    adult_reader = bool(user_messages) and (chat_rules.parent_reader(user_messages)
                                            or (adult_said(user_messages[-1]) and not child_said(user_messages[-1])))
    # «أبغى كتاب لبنتي» without her age: ask it first (Ibrahim, 2026-10-08)
    kid = kid_mentioned(user_messages)
    age_asked = any(AGE_ASK_AR in c["content"] or "How old is" in c["content"] for c in conv if c["role"] == "assistant")
    # QADFIX14 #2 / م8 (QAD 2026-10-23, cases 202, 50.2): an age the model read («عمرها سنة») or a book the visitor wants for
    # themselves is no reason to ask «كم عمر بنتك؟»
    if (kid and not any(_ages(t) for t in user_messages) and not age_asked and not clear_audience
            and not it.get("reader_age") and not chat_rules.parent_reader(user_messages)
            and not said_for_adult(user_messages) and not author_only and not it["mentioned_titles"]
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
            and not said_for_adult(user_messages) and not author_only and not author_names
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
    # «شي ثاني لنفس العمر» right after «بنتي تخاف من الظلام»: a short «another one» names no subject of its own,
    # so the subject of the message before it carries on (Ibrahim 2026-10-15, QAD case 10.2). Only the words of a
    # known subject group (topics._SAME: الخوف، اللؤلؤ، …) are carried, never the whole earlier sentence.
    if (len(user_messages) > 1 and len(normalize(user_messages[-1]).split()) <= 6
            and _ANOTHER_ONE.search(" " + normalize(user_messages[-1]) + " ")):
        # also when the interpreter kept only part of it («الظلام» of «تخاف من الظلام»): the group word is added
        _have = set().union(*[f for u in unsupported for f in topics.content_words(u)] or [set()])
        _carry = []
        for w in normalize(user_messages[-2]).split():
            grp = topics._SAME_AS.get(w) or topics._SAME_AS.get(strip_al(w))
            if grp:
                head = next((g for g in topics._SAME if normalize(g[0]) in grp), None)
                phrase = head[0] if head else w
                if phrase not in _carry and not (grp & _have):
                    _carry.append(phrase)
        unsupported = unsupported + _carry[:2]
    if not unsupported and len(user_messages) > 1 and follow_up_only(user_messages[-1]) and not it["mentioned_titles"]:
        # QADFIX10 (AGY-20261020-01, QAD 2026-10-20 case 39.3): «أقصر» after «شي ثاني» after «رواية عن الهجرة والغربة» kept no
        # subject (the interpreter gave none, and the subject two messages back counted as «stale»): the subject of the last
        # message that said one («عن …») carries on while only follow-ups came after it
        _q10_src = next((t for t in reversed(user_messages[:-1]) if not follow_up_only(t)), None)
        if _q10_src:
            _m10 = re.search(r"(?:^| )عن ((?:\S+ ?){1,4})$", " ".join(normalize(re.sub(r"[؟?!.،,]", " ", _q10_src)).split()))
            if _m10 and topics.searchable([_m10.group(1).strip()], topics.index(all_books)):
                unsupported = [_m10.group(1).strip()]
    # the interpreter sometimes takes the whole request for a title («لا ابغي كتب عن ايران»)
    _req = {normalize(w) for w in "ابي ابغي ابغى ابغا ودي اريد عندكم عندك ابحث كتب كتاب".split()}
    _last = " ".join(normalize(user_messages[-1]).split())
    if any(" ".join(normalize(m).split()) == _last and set(_last.split()) & _req for m in it["mentioned_titles"]):
        it["mentioned_titles"] = [m for m in it["mentioned_titles"] if " ".join(normalize(m).split()) != _last]
        if it["intent"] == "about_book" and not it["mentioned_titles"]:
            it["intent"] = "recommend"
    # «كتب عن إيران» with no subject understood: the words after «عن» are the subject
    # «ابي كتب أنشطة وتلوين للأطفال» with no subject understood: the kind of activity book is the subject (QADFIX4
    # re-run, case 11.1: the interpreter sometimes leaves «التلوين» out and only children's stories came)
    for _kw in ("تلوين", "انشطه", "ملصقات", "متاهات", "كلمات متقاطعه"):
        if re.search(r"(?:^| )[وبل]?(?:ال)?" + _kw + r"(?: |$)", " ".join(normalize(user_messages[-1]).split())) and not any(
                _kw in " ".join(strip_al(w) for w in normalize(u).split()) for u in unsupported):
            unsupported.append("ال" + _kw if " " not in _kw else _kw)
    if not unsupported and not author_only and not pub_years:   # «ابحث عن روايات صدرت مؤخرًا» is the year wish
        _m = re.search(r"(?:^| )عن ((?:\S+ ?){1,3})$", _last)
        if _m and topics.searchable([_m.group(1).strip()], topics.index(all_books)):
            unsupported = [_m.group(1).strip()]
    # wishes from earlier messages that the latest message did not repeat: dropped when they leave
    # nothing for a new subject or a publisher the visitor just named (library trial, 2026-10-08)
    latest = local_understand([user_messages[-1]])
    # QADFIX12 (replay of QAD 2026-10-22 case 22.2): «شي واقعي عن المدرسة والصداقات» — a realistic story is a novel (the model
    # read it so); the subject searched in the whole stock keeps that kind, not «تاريخ المدرسة النصرية بغرناطة»
    _story_now = {"رواية"} if re.search(r"(?:^| )(?:شي|شيء|قصه|قصص|روايه|روايات|حكايه|حكايات)? ?واقعي(?:ه|ة)?(?: |$)|realistic",
                                        " ".join(normalize(user_messages[-1]).split())) else set()
    def _open_prefs(p):
        return dict(p, genres=set(p["genres"]) & (set(latest["genres"]) | _story_now), subgenres=[], soft_subgenres=[],
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
                "pubyear": lambda: any(years_said(last)) or new_release_wished(user_messages) == "said",
                "adaptation": lambda: bool(re.search(r"فلم|فيلم|افلام|مسلسل|انمي|انيمي|كرتون|كارتون|رسوم متحرك|anime|cartoon", normalize(last))),
                "publisher": lambda: bool(find_publisher([], last, all_books, context=True)),
                "country": lambda: bool(wished_country(last)),
                "edition": lambda: bool(wanted_edition(last, language)),
                "topic": lambda: any(f & _said_in([last]) for f in topics.content_words(val)),
                "price": lambda: chat_rules.price_said(last),
                "nogenre": lambda: val in chat_rules.excluded_genres([last]),
                "nocountry": lambda: bool(unwanted_countries(last)),
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
        offered = any(c["role"] == "assistant" and ("تبغى أقترح لك بدائل" in c["content"] or "Would you like alternatives" in c["content"]
                                                     or "تبغى أقترح لك كتبًا قريبة من أسلوب" in c["content"] or "books close to their writing" in c["content"])
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
            # an author the interpreter knows by a full name and a Latin spelling («احلام مستغانمي» -> «أحلام مستغانمي»,
            # Ahlam Mosteghanemi) is a real author we hold nothing of: say that, not «no author by this name»
            # (Ibrahim 2026-10-12 for well-known authors; QAD 2026-10-14, case 46)
            _full = str(author_request.get("full_name") or "").strip()
            if _full and any(re.search(r"[A-Za-z]{3}", str(a or "")) for a in author_request.get("aliases") or []):
                open_questions.record("missing_author", _full)
                _lat = next((a for a in author_request.get("aliases") or [] if re.search(r"[A-Za-z]{3}", str(a or ""))), _full)
                return author_answer(f"We have no books by {_lat} in stock now. Would you like books close to their writing?" if language == "en" else
                                     f"ما عندنا الآن كتب لـ{_full}. تبغى أقترح لك كتبًا قريبة من أسلوب كتاباتها؟"
                                     if _FEMININE_NAME.search(normalize(_full)) else
                                     f"ما عندنا الآن كتب لـ{_full}. تبغى أقترح لك كتبًا قريبة من أسلوب كتاباته؟")
            return author_answer("I couldn’t find an author by this name in our catalog. Would you like alternatives?" if language == "en" else
                                 "ما لقيت عندنا مؤلفًا بهذا الاسم. تبغى أقترح لك بدائل؟")
    if adult_reader and not author_only:
        pool = [b for b in pool if b.get("genre") not in CHILD_GENRES]
        if (chat_rules.parent_reader(user_messages)
                # a novel / story asked for by name keeps them («إدارة» read as «أعمال», «الأطفال» are subjects)
                and not re.search(r"(?:^| )[وبل]?(?:ال)?(?:روايه|روايات|قصه|قصص|novel|novels|story|stories|fiction)(?: |$)",
                                  normalize(user_messages[-1]))
                and not it["mentioned_titles"]):
            # «كتب عن التعامل مع عناد الأطفال، لي أنا كأب»، «كتاب عن إدارة الصف لي كمعلمة»: a parent or a teacher
            # asking for himself wants guidance, not a novel whose summary has the word (QAD 2026-10-13, cases 4, 13)
            _np = [b for b in pool if b.get("genre") not in ("رواية", "قصص مصورة")]
            if _np:
                pool = _np
    # QADFIX14 م3 (replay of case 16.2): «وفيه أقصر منها بس رواية عربية؟» — with an order asked for and a new wish, a book shown
    # before that now fits best («المموه», 56 pages) is still the answer: the order itself keeps out what is not shorter
    _order_now = bool(chat_rules.shortest_asked(user_messages[-1])) or it.get("order_by") in ("shortest", "cheapest", "oldest", "newest")
    _lu_last = local_understand([user_messages[-1]]) if _order_now else {}
    _new_kind = bool(_lu_last.get("translated") or _lu_last.get("original_languages"))
    if it["wants_new_options"] and not (_order_now and len(user_messages) > 1 and _new_kind):
        # QADFIX7 #2 (QAD 2026-10-19, case 39.2): «أقصر» after a book with no page count keeps it — it may be the only one on the subject
        _keep_np = bool(chat_rules.shortest_asked(user_messages[-1])) and not _ANOTHER_ONE.search(" " + normalize(user_messages[-1]) + " ")
        pool = [b for b in pool if b["book_id"] not in shown or (_keep_np and not b.get("page_count"))]
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
    # Wishes that stay in force until the visitor lifts them (QAFIX 2026-10-11: «سياق ثابت»): each one that
    # is applied adds its test to `hard`, and every later widening of the search (a subject looked up in the
    # whole stock) keeps them. Before, the publisher lived for one short follow-up and a subject carried
    # from an earlier message threw the author's country away (cases 5, 29, 30).
    hard = []
    publisher_name, publisher_unknown, publisher_left = None, None, None
    _last_msg = user_messages[-1]
    _pub_before = None
    for _t in reversed(user_messages[:-1]):
        _pub_before = find_publisher([], _t, all_books)
        if _pub_before:
            break
    _pub_carried, found_pub = False, None
    if not (author_only or genre_as_topic or "publisher" in drops):
        found_pub = find_publisher(unsupported, _last_msg, all_books, context=bool(_pub_before))
        if (not found_pub and _pub_before and not it["mentioned_titles"]
                and not _PUB_ANY.search(" ".join(normalize(_last_msg).split()))):
            # the publisher named earlier stays until the visitor removes its badge, names another one or says
            # «أي دار» (it used to stay for one short message only: «روايات بس، ومترجمة», QA 2026-10-08)
            found_pub, _pub_carried = _pub_before, True
    if found_pub:
        key, publisher_name = found_pub
        pub_pool = [b for b in pool_before if publisher_key(b.get("publisher")) == key]
        # the publisher's own words («عصير» of «عصير الكتب») are not a subject either
        _kw = {strip_al(w) for w in key.split()}
        prefs["authors"] = [a for a in prefs.get("authors") or [] if publisher_key(a) != key]
        _pubish = _PUB_WORDS | _PUB_TRIGGERS | _PUB_LOOSE | {normalize(w) for w in "كتب كتاب اصدار".split()}
        unsupported = [u for u in unsupported if publisher_key(u) != key
                       and not ({strip_al(w) for w in normalize(u).split() if w not in _pubish} <= _kw)]
        # only a subject the latest message names can push an earlier publisher aside («أبغى قراءة خفيفة» does not)
        _subj = [p for p in topics.searchable(unsupported, topics.index(all_books))
                 if any(f & _said_in([_last_msg]) for f in topics.content_words(p))] if _pub_carried else []
        p_pool, p_labels, p_prefs, _ = constrain_relaxing(pub_pool, prefs, pprefs, exclusions, cards)
        if _subj and not any(topics.match(_subj, b["book_id"], topics.index(all_books))[0] for b in pub_pool):
            # a publisher from an earlier message, and now a subject none of its books covers: the new
            # request wins and the visitor is told
            publisher_left, publisher_name = publisher_name, None
        elif p_pool:
            pool, labels, prefs = p_pool, p_labels, p_prefs
        elif _pub_carried:
            publisher_left, publisher_name = publisher_name, None
        else:
            o_prefs, o_pprefs = _open_prefs(prefs), _open_pprefs(pprefs)
            p_pool, p_labels, o_prefs, _ = constrain_relaxing(pub_pool, o_prefs, o_pprefs, _open_excl, cards)
            if p_pool:
                pool, labels, prefs, pprefs, exclusions = p_pool, p_labels, o_prefs, o_pprefs, _open_excl
            else:
                publisher_left, publisher_name = publisher_name, None
        if publisher_name:
            hard.append(lambda b, _k=key: publisher_key(b.get("publisher")) == _k)
    elif not author_only and not it["mentioned_titles"] and "publisher" not in drops:
        # «دار عصير الكتب» and we stock nothing of theirs: said plainly, and its words are not a subject
        _asked_pub = publisher_asked(_last_msg)
        if _asked_pub:
            publisher_unknown = _asked_pub
            _pw = {strip_al(w) for w in normalize(_asked_pub).split()}
            unsupported = [u for u in unsupported if not ({strip_al(w) for w in normalize(u).split()} & _pw)]
    # the author's country («كاتب سعودي»): latest message first, else the one before it
    country, country_missing = None, False
    if not author_only and not genre_as_topic and "country" not in drops:
        country = wished_country(user_messages[-1]) or (
            wished_country(user_messages[-2]) if len(user_messages) > 1 and (len(user_messages[-1].split()) <= 4
                                                                       or _narrow_followup(user_messages[-1])) else None)
    if not country and not author_only and not genre_as_topic and "country" not in drops and it.get("author_countries"):
        # QADFIX14 #5 (QAD 2026-10-23, case 4): the countries the model read for the authors, in any wording and for a region
        # («أمريكا اللاتينية / الجنوبية» = its countries, not the Spanish and Portuguese languages: «دون كيخوته» is Spanish)
        country = ("model", list(it["author_countries"]))
        if set(prefs.get("original_languages") or ()) <= {"es", "pt"}:
            prefs["original_languages"] = set()
    _ed_said = wanted_edition(user_messages[-1], language) or (
        wanted_edition(user_messages[-2], language) if len(user_messages) > 1 and len(user_messages[-1].split()) <= 6 else None)
    if country and _ed_said == "en" and re.search(r"انجليز|انقليز|english", normalize(country[0])):
        country = None   # «أي كتاب إنجليزي يناسب عمره» is the copy's language, not a British author (QAD 2026-10-15, case 12.2)
    if country:
        adj, names = country
        origins = book_origins()
        keys = [normalize(n) for n in names]
        def _from_there(b, _keys=tuple(keys), _o=origins):
            return any(k in _o.get(b["book_id"], "") for k in _keys)
        c_pool = [b for b in pool_before if all(h(b) for h in hard) and _from_there(b)]
        p2, l2, pr2, _ = constrain_relaxing(c_pool, prefs, pprefs, exclusions, cards)
        if not p2 and c_pool:
            o_prefs, o_pprefs = _open_prefs(prefs), _open_pprefs(pprefs)
            p2, l2, o_prefs, _ = constrain_relaxing(c_pool, o_prefs, o_pprefs, _open_excl, cards)
            if p2:
                prefs, pprefs, exclusions = o_prefs, o_pprefs, _open_excl
        if p2:
            pool, labels = p2, l2
            hard.append(_from_there)
        else:
            country_missing = True
        # «الخليج» / «الشام» given as a subject beside the country wish is that wish, not a subject (QAFIX re-run)
        _places = {normalize(n) for n in names} | {p for p, a in _PLACE_NOUNS.items() if _ARAB_NATIONS.get(a, [None])[0] in names}
        unsupported = [u for u in unsupported if not wished_country(u)
                       and " ".join(normalize(u).split()) not in _places and strip_al(" ".join(normalize(u).split())) not in {strip_al(p) for p in _places}]
        if prefs.get("original_languages") and adj in _ARAB_NATIONS:
            prefs = dict(prefs, original_languages=set(prefs["original_languages"]) & {"ar"})
    if it["intent"] == "clarify" and len(user_messages) > 1 and (pub_years or country) and _narrow_followup(user_messages[-1]):
        # QADFIX7 #3 (case 43.3): «ابي اللي طلعت ٢٠٢٥ بس» after Saudi novels is a narrower list, not a mood question
        it["intent"] = "recommend"
    # countries the visitor refused («رواية عربية بس مو مصرية ولا خليجية»): kept through the conversation
    # until one of them is asked for or the badge is removed; only authors whose country we know pass
    # (QAFIX 2026-10-11, case 41: the first nationality in the message was taken as the wish)
    no_country = []
    if not author_only and "nocountry" not in drops:
        for _t in user_messages:
            for _k, _names, _neg in _countries_said(_t):
                if _neg:
                    no_country += [n for n in _names if n not in no_country]
                else:
                    no_country = [n for n in no_country if n not in _names]
    if no_country:
        _origins = book_origins()
        def _not_from(b, _keys=tuple(normalize(n) for n in no_country), _o=_origins):
            o = _o.get(b["book_id"], "")
            return bool(o) and not any(k in o for k in _keys)
        n_pool = [b for b in pool if _not_from(b)]
        if not n_pool:
            n_all = [b for b in pool_before if all(h(b) for h in hard) and _not_from(b)]
            n_pool, _l, _p, _ = constrain_relaxing(n_all, prefs, pprefs, exclusions, cards)
            if n_pool:
                labels, prefs = _l, _p
        if n_pool:
            pool = n_pool
            hard.append(_not_from)
        else:
            no_country = []
        _refused = {normalize(n) for n in no_country} | {strip_al(normalize(n)) for n in no_country}
        unsupported = [u for u in unsupported if not _countries_said(u)
                       and strip_al(" ".join(normalize(u).split())) not in _refused]
    # the language of the copy («بالإنجليزي»)
    edition, edition_missing = None, False
    if "edition" not in drops and not author_only:
        edition = wanted_edition(user_messages[-1], language) or (
            wanted_edition(user_messages[-2], language) if len(user_messages) > 1 and len(user_messages[-1].split()) <= 4 else None)
    elif "edition" not in drops and author_only:
        # «تشارلز دكنز وش عندكم له؟» → «ابي بالانجليزي»: the author's books in English (Great Expectations),
        # not «no English copy of the books shown» (QAD 2026-10-13, case 26.2)
        edition = wanted_edition(user_messages[-1], language)
    if ("edition" not in drops and not edition and len(conv) > 1 and conv[-2].get("role") == "assistant"
            and "نسخ إنجليزية من كتبه" in (conv[-2].get("content") or "")
            and author_flow.is_yes(user_messages[-1])):
        # «ايه» to «ويوجد بعد نسخ إنجليزية من كتبه، تبغى تلقي نظرة عليها؟»: his English copies (QAD 2026-10-15, case 19.2)
        edition = "en"
    if "edition" not in drops and not edition and it.get("copy_language") and not author_only:
        # QADFIX14 م7 (QAD 2026-10-23, case 45.3): «Something much simpler, short and modern» after "an Arabic novel" — the copy
        # language the visitor asked for stays (read by the model), and is not replaced by «prefer English» for an English writer
        edition = it["copy_language"]
    if edition:
        if edition == "en" and (prefs.get("original_languages") or prefs.get("translated")):
            # «English novels» is about the copy; a translated / original-language wish read into it is dropped
            # («أقصد نسخة إنجليزية، مو كتاب مترجم للعربي» is not «not translated», QAD 2026-10-15, case 12.3)
            prefs = dict(prefs, original_languages=set(prefs.get("original_languages") or ()) - {"en"}, translated=None)
        e_pool = [b for b in pool if ((b.get("language") or "ar").strip() or "ar") == edition]
        if not e_pool and not author_only:
            e_all = [b for b in pool_before if all(h(b) for h in hard) and ((b.get("language") or "ar").strip() or "ar") == edition]
            e_pool, _l, prefs2, _ = constrain_relaxing(e_all, _open_prefs(prefs), _open_pprefs(pprefs), _open_excl, cards)
            if not e_pool:
                # the candidates were all Arabic: look in the whole stock in that language, the reader's age and
                # the other hard limits kept, before saying «ما لقينا» (QAD 2026-10-15, case 12.3: Magic Tree House,
                # Wonder and Junie B. Jones in stock for a 9-year-old)
                e_wide = [b for b in all_books.values() if is_available(b) and all(h(b) for h in hard)
                          and ((b.get("language") or "ar").strip() or "ar") == edition]
                e_pool, _l, prefs2, _ = constrain_relaxing(e_wide, _open_prefs(prefs), _open_pprefs(pprefs), _open_excl, cards)
            _forms = set(prefs.get("genres") or ()) & {"شعر", "قصص مصورة", "مسرحية", "قصص قصيرة"}
            if e_pool and _forms and not any(b.get("genre") in _forms for b in e_pool):
                # QADFIX14 م6 (QAD 2026-10-23, case 44.2): «Something in English instead» for a poetry lover — no poetry in
                # English is said plainly; English novels (and «Lolita» as a gift) are not offered in its place
                e_pool = []
            if e_pool:
                prefs, pprefs, exclusions, labels = prefs2, _open_pprefs(pprefs), _open_excl, _l
        if e_pool:
            pool = e_pool
            hard.append(lambda b, _e=edition: ((b.get("language") or "ar").strip() or "ar") == _e)
        else:
            edition_missing = True
    # the publication year («after 2020»، «صدر حديثًا»): the work's first publication, or our edition's
    # year when that is unknown (QA2 2026-10-09; original year ORIGYR 2026-10-11)
    pub_missing = False
    if pub_years and not author_only:
        lo, hi = pub_years
        def _pub_ok(b):
            # QADFIX12 #11 (QAD 2026-10-22, case 40.2): «كتبت أول مرة سنة ٢٠٢٥، مو طبعة جديدة» — only the year the work first came
            # out counts (books.original_year, sourced); our edition's year never stands in for it then
            y = first_published(b) if it.get("first_publication") else year_for_new(b)
            if y is None:
                return False
            return (not lo or y >= lo) and (not hi or y <= hi)
        y_pool = [b for b in pool if _pub_ok(b)]
        if not y_pool:
            # QADFIX7 #3 (case 43.2): the wishes already made hard (the author's country) still hold when the years widen the pool
            y_all = [b for b in pool_before if _pub_ok(b) and all(h(b) for h in hard)]
            y_pool, _l, prefs2, _ = constrain_relaxing(y_all, _open_prefs(prefs), _open_pprefs(pprefs), _open_excl, cards)
            if y_pool:
                prefs, pprefs, exclusions, labels = prefs2, _open_pprefs(pprefs), _open_excl, _l
        if y_pool:
            pool = y_pool
        elif _new_wish == "carried" and not (_lo or _hi):
            pub_years = None   # an earlier «جديدة» that this request cannot meet is let go, not announced
        else:
            pub_missing = True
    if pub_years and not pub_missing and not author_only:
        hard.append(_pub_ok)
    # a kind of book the visitor refused («أي شي إلا تطوير الذات والروايات») stays refused in the next
    # messages too, until he asks for it or removes the badge (QAFIX 2026-10-11, case 42)
    no_genres = [] if author_only else [g for g in chat_rules.excluded_genres(user_messages) if "nogenre:" + g not in drops]
    if no_genres:
        # «أي شي إلا …»: the interpreter may list every other kind of book; that is no kind at all (QAFIX re-run, case 42)
        _left = set(prefs["genres"]) - set(no_genres)
        prefs = dict(prefs, genres=set() if len(_left) >= 4 else _left)
        def _kind_ok(b, _ng=tuple(no_genres)):
            return b.get("genre") not in _ng
        g_pool = [b for b in pool if _kind_ok(b)]
        if not g_pool:
            g_all = [b for b in pool_before if all(h(b) for h in hard) and _kind_ok(b)]
            g_pool, _l, _p, _ = constrain_relaxing(g_all, prefs, pprefs, exclusions, cards)
            if g_pool:
                labels, prefs = _l, _p
        if g_pool:
            pool = g_pool
            hard.append(_kind_ok)
        else:
            no_genres = []
        def _names_refused(u):
            p_, f_ = keyword_prefs._parse_one(u)
            return bool(set(p_["genres"] + f_["genres_negated"]) & set(no_genres))
        unsupported = [u for u in unsupported if not _names_refused(u)]   # «بدون تطوير الذات» is not a subject
    # the price («رواية ما تتعدى ٢٥ ريال»، «بعشرة ريال»): a limit on books.price that stays until lifted; the
    # words are never a subject («ريال» once brought a book about Real Madrid; QAFIX 2026-10-11, case 16)
    price_max, cheapest = chat_rules.price_wish(user_messages)
    if "price" in drops:
        price_max = None
    price_missing = False
    if price_max:
        def _price_ok(b, _m=price_max):
            return bool(b.get("price")) and 0 < b["price"] <= _m
        pr_pool = [b for b in pool if _price_ok(b)]
        if pr_pool:
            pool = pr_pool
            hard.append(_price_ok)
        else:
            price_missing = True
    # pages as a number («كتب أقل من ١٠٠ صفحة»): a limit on page_count over the whole request, not the general «short»
    # (QADFIX4, QAD 2026-10-16 case 7.1: one book offered when 254 in stock have fewer than 100 pages)
    pages_max, pages_min = chat_rules.page_limit(user_messages)
    if "pages" in drops:
        pages_max = pages_min = None
    pages_missing, pages_n = False, 0
    if pages_max or pages_min:
        def _pages_ok(b, _hi=pages_max, _lo=pages_min):
            n = b.get("page_count") or 0
            return n > 0 and (not _hi or n <= _hi) and (not _lo or n >= _lo)
        _np = dict(prefs, length=None)
        pg_pool = [b for b in pool_before if all(h(b) for h in hard) and _pages_ok(b)]
        p2, l2, pr2, _ = constrain_relaxing(pg_pool, _np, pprefs, exclusions, cards)
        if p2:
            pool, labels, prefs = p2, l2, pr2
            hard.append(_pages_ok)
            pages_n = len(p2)
        else:
            pages_missing = True
        unsupported = [u for u in unsupported if not chat_rules.is_pages_phrase(u)]
        prefs["tokens"] = [t for t in prefs.get("tokens") or [] if not chat_rules.is_pages_phrase(t)]
        if it["intent"] in ("clarify", "smalltalk"):
            it["intent"] = "recommend"
    unsupported = [u for u in unsupported if not chat_rules.is_price_phrase(u)]
    prefs["tokens"] = [t for t in prefs.get("tokens") or [] if not chat_rules.is_price_phrase(t)]
    if chat_rules.write_asked(_last_msg) == "poem" and "شعر" not in (prefs.get("genres") or ()):
        # «اكتب لي قصيدة عن المكتبة»: poetry books, not a search for a book «about the library» (QADFIX4, case 48.1)
        prefs["genres"] = {"شعر"}
        unsupported = []
        it["intent"] = "recommend"
        _wp = [b for b in pool_before if b.get("genre") == "شعر" and all(h(b) for h in hard)]
        if _wp:
            pool = _wp
    if it["intent"] in ("clarify", "smalltalk") and (chat_rules.price_said(_last_msg) or chat_rules.shortest_asked(_last_msg)):
        # «ما فيه رواية بعشرة ريال؟» is a request the database answers, not a vague one (QAFIX re-run, case 16)
        it["intent"] = "recommend"
    # «وش عندكم غيرهم؟»: other authors than the ones already shown (QAFIX 2026-10-11, case 21)
    if chat_rules.other_authors_asked(_last_msg) and shown and not author_only:
        _prev_authors = {author_flow.display(all_books[i].get("author")) for i in shown if i in all_books}
        o_pool = [b for b in pool if author_flow.display(b.get("author")) not in _prev_authors]
        if o_pool:
            pool = o_pool
    _extra_badges = []
    if (pages_max or pages_min) and not pages_missing:
        _extra_badges.append(("pages", (f"under {pages_max + 1} pages" if pages_max else f"{pages_min}+ pages") if language == "en"
                              else (f"حتى {fmt_num(pages_max)} صفحة" if pages_max else f"{fmt_num(pages_min)} صفحة فأكثر")))
    if price_max and not price_missing:
        _extra_badges.append(("price", f"up to {price_max} SAR" if language == "en" else f"حتى {fmt_num(price_max)} ريال"))
    for _g in no_genres:
        _extra_badges.append(("nogenre:" + _g, f"no {_g}" if language == "en" else f"بدون «{_g}»"))
    if no_country:
        _extra_badges.append(("nocountry", ("no authors from " if language == "en" else "بدون كتّاب من ") + country_place(no_country)))
    # the reader's age against the books' researched age range: books clearly for another age are
    # left out, books for that age come first (children's age research, 2026-10-09)
    reader_age = audience_age(user_messages) if pprefs.get("audience") in ("children", "young_adult") else None
    if reader_age is not None and pprefs.get("audience") == "children":   # QADFIX12 #7: a teenager's books are not cut by age
        fitting = [b for b in pool if age_fit(b, reader_age) >= 0]
        if fitting:
            pool = fitting
        elif pool:
            # QADFIX6 (QAD 2026-10-18, cases 11.2, 14.3): «in English please» for a 5-year-old — nothing in stock for
            # that age in that language; the answer says so instead of offering 8–12 books as if they fitted
            it["no_age_fit"] = reader_age
    researched = _profile_formats()
    # «كتب تشبه فلان»: only researched books close to the author's known style, never generic
    # fillers or unresearched books (Ibrahim, 2026-09-30).
    similar_author = (not author_only and author_request.get("mode") in ("similar", "both")
                      and bool(str(author_request.get("full_name") or author_request.get("name_as_written") or "").strip()))
    scored = score_books(pool, prefs, pprefs, cards)
    if _phys:
        # QADFIX14 #2: a physical wish found in the book's own title or summary («سلسلة الكتب القماشية» for «كتب قماش»)
        def _phys_hit(b):
            text = " ".join(normalize((b.get("title") or "") + " " + (b.get("summary") or "")).split())
            return [p for p in _phys if all(strip_al(w).rstrip("ه") in text for w in normalize(p).split() if len(w) > 2)]
        scored = sorted(((s + (5 if _phys_hit(b) else 0), ([f"يذكر وصفه «{_phys_hit(b)[0]}»"] if _phys_hit(b) else []) + r, b)
                         for s, r, b in scored), key=lambda x: x[0], reverse=True)
    if (price_max and not price_missing and last_batch and len(user_messages) > 1 and not it["mentioned_titles"]
            and chat_rules.price_wish([user_messages[-1]])[0]
            and chat_rules.price_wish([user_messages[-1]])[0] != chat_rules.price_wish(user_messages[:-1])[0]):
        # QADFIX14 م2 (QAD 2026-10-23, case 11.2): «tab a2alla men 10 riyal?» after books about healthy food — a new price limit
        # keeps the subject: a book just shown that fits the limit («أنثروبولوجيا الطعام والجسد» at 5.67) comes first
        _lb_ids = set(last_batch)
        scored = sorted(((s + (6 if b["book_id"] in _lb_ids else 0), r, b) for s, r, b in scored), key=lambda x: x[0], reverse=True)
    if "gift" in (pprefs.get("use_cases") or ()):
        # QADFIX14 م6 (case 44.2): a gift («هدية لمديرتي») is not a book with bold scenes when others fit
        scored = sorted(((s - (4 if _medium_or_better(cards.get(b["book_id"]), "sensitive_content", "sexual_content") else 0), r, b)
                         for s, r, b in scored), key=lambda x: x[0], reverse=True)
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
        # the English chat: books in English higher — a visitor writing in English was given two Arabic novels
        # without a word about their language (QAD 2026-10-14, case 41.1); an Arabic copy that fits much better
        # can still come, and the guidance has the model say its language
        scored = sorted(((s + (3 if (b.get("language") or "ar") == "en" else 0), r, b) for s, r, b in scored),
                        key=lambda x: x[0], reverse=True)
    if country and not country_missing:
        _place = country_place(list(country[1]))
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
    _neg_spans = []
    for _seg in re.split(r"[،,.؟?!؛;\n]", user_messages[-1] if user_messages else ""):
        _sg = " ".join(normalize(_seg).split())
        for _m in _NEG_LEAD.finditer(_sg):
            _span = []
            for _n, _w in enumerate(_m.group(1).split()):
                if _w in _NEG_STOP and _n > 0:
                    break   # QADFIX12 #1: «مو عن الكورة» — «عن» right after the negation leads to what is rejected
                if _w not in _NEG_STOP:
                    _span.append(_w)
            _neg_spans.append({f for w in _span for f in topics.stems(_hz(w))})
    # QADFIX12 #1: what the model read as NOT wanted, in any wording
    _avoid_f = [topics.content_words(a) for a in it.get("avoid_topics") or []]
    def _unwanted(phrase):
        _pf = topics.content_words(phrase)
        if _pf and any(af and any(f & g for f in _pf for g in af) for af in _avoid_f):
            return True
        if (" ".join(normalize(phrase).split()) not in _model_subjects and user_messages
                and negated_in(user_messages[-1], phrase)):
            return True   # a subject the code took from the words: rejected when a negation stands before it
        if any(w in keyword_prefs.NEGATORS for w in normalize(phrase).split()):
            return False   # «حبكة غير خطية»: the negation is part of the wish itself
        _pw = [_hz(w) for w in normalize(phrase).split() if len(w) >= 3]
        for sp in _neg_spans:
            _in = [w for w in _pw if topics.stems(w) & sp]
            # «مو كله نصايح مكررة» read as «نصائح مكررة» (QADFIX6): the whole phrase, or a short phrase with a long word, in
            # what the visitor does not want
            if _pw and (len(_in) == len(_pw) or (len(_pw) <= 3 and any(len(w) >= 4 for w in _in))):
                return True
        if any(w in keyword_prefs.NEGATORS for w in normalize(phrase).split()):
            return False   # «حبكة غير خطية»: the negation is part of the wish itself
        for w in normalize(phrase).split():
            for i, said in enumerate(_said):
                if len(w) >= 3 and (topics.stems(w) & topics.stems(said)) and keyword_prefs.negated(_said, i):
                    return True
        return False
    unsupported = [u for u in unsupported if not _unwanted(u)]
    # QADFIX12 #1: a book about what the visitor rejected is not offered either — not only never searched for
    _avoid_p = [a for a in it.get("avoid_topics") or [] if topics.searchable([a], topic_index)]
    if _avoid_p and not author_only:
        _rejected = {b["book_id"] for b in pool_before if topics.match(_avoid_p, b["book_id"], topic_index)[0]}
        if _rejected:
            pool = [b for b in pool if b["book_id"] not in _rejected]
            pool_before = [b for b in pool_before if b["book_id"] not in _rejected]
            scored = [x for x in scored if x[2]["book_id"] not in _rejected]
            it["rejected_topics"] = _avoid_p
    # «وش دار نشره؟» / «كم سعره؟» asks about the book in view, not for books about «دار النشر» (the chip
    # «عن «دار النشر»» showed; library inbox 2026-10-11 §3.3, MGR 2026-10-17)
    unsupported = [u for u in unsupported if not (set(normalize(u).split()) <= _BOOK_DETAIL_WORDS)]
    if _carry_from and not unsupported and not prefs["genres"] and not prefs.get("subgenres"):
        # QADFIX6 (QAD 2026-10-18, case 2.2): «شي عربي» after «دراما عائلية» keeps the kind of book asked for
        _cw = [w for w in normalize(_carry_from).split() if w not in chat_rules._REFINE and w not in _CARRY_FILLER]
        if _cw and len(_cw) <= 4:
            unsupported = [" ".join(_cw)]
    if _ESCAPE.search(" ".join(normalize(user_messages[-1]).split())):
        # QADFIX6 (case 5.1): «يفصلني عن الدنيا» — «الدنيا» taken from «عن …» is not a subject either
        unsupported = [u for u in unsupported if not (set(normalize(u).split()) <= _ESCAPE_WORDS)]
    unsupported = _routine_topics(unsupported)
    if (not unsupported and _SLEEP_PROBLEM.search(" " + " ".join(normalize(user_messages[-1]).split()) + " ")
            and not _BEDTIME.search(" ".join(normalize(user_messages[-1]).split()))):
        unsupported = ["النوم"]   # QADFIX11 (QAD 2026-10-21, case 7.1): «عندي أرق وما أعرف أنام»
    _q7_series = (not it["mentioned_titles"] and not author_only
                  and bool(_SERIES_WISH.search(" " + " ".join(normalize(user_messages[-1]).split()) + " ")))
    if _q7_series:
        unsupported = [u for u in unsupported if not _SERIES_WISH.search(" " + " ".join(normalize(u).split()) + " ")
                       and not re.search(r"مسلسل|اتابع|نتابع|متابعه", normalize(u))]
    topic_phrases = topics.searchable(unsupported, topic_index)
    topics_found, topics_mentioned, topics_semantic = [], [], []
    topic_widened = False
    # «شي ثاني» after «فوق السطح» (the only book with the whole subject «الخوف من الظلام»): the books already
    # shown do not count, so the subject falls back to its rarest word («الخوف» -> «خووويف») (QAD 2026-10-15, case 10.2)
    # QADFIX7 #2 (QAD 2026-10-19, case 39.2): «أقصر» after «السندباد الأعمى» (the only novel about the invasion of Kuwait) is
    # not «another one»: the book shown still has the subject, so «غزو الكويت» is not cut to «غزو»
    _another = (it["wants_new_options"] and shown
                and (_ANOTHER_ONE.search(" " + normalize(user_messages[-1]) + " ") or not chat_rules.shortest_asked(user_messages[-1])))
    _fresh = [b for b in pool_before if b["book_id"] not in shown] if _another else pool_before
    if topic_phrases and not any(topics.match(topic_phrases, b["book_id"], topic_index)[0] for b in _fresh):
        # no book in stock has the whole subject: its rarest word alone («سقوط غرناطة» -> «غرناطة») (QA 2026-10-08)
        core = [w for w in (topics.core_word(p, topic_index) for p in topic_phrases if not no_cut(p)) if w
                and core_hits_ok(w, _fresh, topic_index)]
        if core and any(topics.match(core, b["book_id"], topic_index)[0] for b in _fresh):
            topic_phrases = core
    elif len(topic_phrases) > 1:
        # one subject of several found nowhere as a whole: that one alone falls back to its rarest word
        # («النظافة الشخصية» beside «الحمّام» -> «النظافة», found as «الاستحمام»; QAD 2026-10-14, case 11.2)
        _new = []
        for p in topic_phrases:
            if any(topics.match([p], b["book_id"], topic_index)[0] for b in pool_before):
                _new.append(p)
                continue
            c = None if no_cut(p) else topics.core_word(p, topic_index)
            _new.append(c if c and core_hits_ok(c, pool_before, topic_index) else p)
        topic_phrases = list(dict.fromkeys(_new))
    # A book whose own title is the subject asked for («ما هو التصوف؟» for «كتاب في التصوف الإسلامي») is
    # offered whatever shelf it stands on: a category the interpreter guessed from the subject must not hide
    # it. Not when the visitor asked for a form (a novel, poetry ...) (QAFIX 2026-10-11, case 38)
    topic_title_ids = set()
    # QADFIX13 #2 (AGY-20261022-01 (2), QAD 2026-10-22 cases 22.2–22.3): «شي واقعي عن المدرسة» asks for a story (_story_now),
    # and «أبيها بالعربي» after it keeps that story wish — a subject carried from an earlier message keeps the form the
    # request already has (not «تاريخ المدرسة النصرية بغرناطة» from the history shelf)
    _form_genres = (set(latest.get("genres") or ()) | _story_now) - {"دين", "فكر", "تاريخ", "علوم", "علم نفس", "علم اجتماع", "أعمال", "تطوير ذاتي", "أدب", "فنون وسينما"}
    _last_flat = " ".join(normalize(user_messages[-1]).split())
    if not _form_genres and topic_phrases and not any(" ".join(normalize(p).split()) in _last_flat for p in topic_phrases):
        _form_genres = set(prefs.get("genres") or ()) & {"رواية", "قصص قصيرة", "شعر", "مسرحية", "قصص مصورة"}
    if topic_phrases and prefs.get("genres") and not _form_genres and not author_only and not similar_author and not publisher_name:
        _in_pool = {b["book_id"] for b in pool}
        _open_g = dict(prefs, genres=set(), subgenres=[], soft_subgenres=[])
        _t_phr = list(topic_phrases) + [w for w in (topics.core_word(p, topic_index) for p in topic_phrases) if w]
        _t_hits = [b for b in pool_before if b["book_id"] not in _in_pool and all(h(b) for h in hard)
                   and topics.match(_t_phr, b["book_id"], topic_index)[0] >= topics.TITLE
                   # QADFIX7 #10 (QAD 2026-10-19, case 31.1): «جلسة قهوة» (a crime novel) is not a book about coffee
                   and (b.get("genre") or "").strip() not in _Q7_FICTION]
        _t_hits = constrain_relaxing(_t_hits, _open_g, pprefs, exclusions, cards)[0][:4]
        if _t_hits:
            topic_title_ids = {b["book_id"] for b in _t_hits}
            pool = pool + _t_hits
            scored = scored + score_books(_t_hits, _open_g, pprefs, cards)
    if topic_phrases and not author_only and not similar_author and not publisher_name and not it["mentioned_titles"]:
        # QADFIX7 #7 (QAD 2026-10-19, case 12.1): «تحب الألغاز» at 9 — the one book tagged «ألغاز» (in English) narrowed
        # the pool, and «لغز كرة القدم» / «لغز المدرسة» (the subject in their titles) were never looked at
        _in_pool7 = {b["book_id"] for b in pool}
        if sum(1 for b in pool if topics.match(topic_phrases, b["book_id"], topic_index)[1]) < 3:
            _ex7 = [b for b in pool_before if b["book_id"] not in _in_pool7 and all(h(b) for h in hard)
                    and topics.match(topic_phrases, b["book_id"], topic_index)[0] >= topics.SUBJECTS]
            if _ex7:
                _p7 = dict(prefs, subgenres=[], soft_subgenres=[])
                _ex7 = constrain_relaxing(_ex7, _p7, pprefs, exclusions, cards)[0][:6]
                if _ex7:
                    pool = pool + _ex7
                    scored = scored + score_books(_ex7, _p7, pprefs, cards)
    # search by meaning (layer 2, 2026-10-08): «الغربة» also finds a summary about «المنفى»
    sem = {}
    if topic_phrases and not author_only and not similar_author and not publisher_name:
        _event_said = bool(_EVENT_WORDS.search(normalize(" ".join(user_messages))))
        _sem_q = {p: (p + " " + _SEASONS[MONTH_SEASON[months_in(p)[0]]][0]) if months_in(p) and not _event_said else p
                  for p in topic_phrases}
        _sem_raw = embeddings.search(list(_sem_q.values()), [b["book_id"] for b in pool_before])
        sem = {p: _sem_raw[q] for p, q in _sem_q.items() if q in _sem_raw}
        for p in list(sem):
            if months_in(p) and not _event_said:
                # «أكتوبر»: not «العاشرة والنصف ليلًا، في الصيف» because its meaning is near the autumn's
                _own = _SEASONS[MONTH_SEASON[months_in(p)[0]]][0]
                _other = [v[0] for v in _SEASONS.values() if v[0] != _own]
                sem[p] = [(i, x) for i, x in sem[p]
                          if not any(o in normalize((all_books.get(i) or {}).get("title") or "") for o in map(normalize, _other))]
    sem_ids = {i: p for p, hits in sem.items() for i, _ in hits}
    # only for a subject the latest message itself names: a subject carried from an earlier message never
    # throws away what the visitor asks for now («ابيها لكاتبة خليجية» after «دراما عائلية» ended with one
    # play about drama instead of Gulf novelists; QAFIX 2026-10-11, case 5)
    _topic_now = any(f & _said_in([user_messages[-1]]) for p in topic_phrases for f in topics.content_words(p))
    if topic_phrases and _topic_now and not author_only and not similar_author and not publisher_name and not any(
            topics.match(topic_phrases, b["book_id"], topic_index)[0] or b["book_id"] in sem_ids for _, _, b in scored):
        # «أبغى كتب عن إيران» after Russian short biographies: the subject is searched in the whole
        # stock, keeping only what the latest message itself asked for (library trial, 2026-10-08)
        o_prefs, o_pprefs = _open_prefs(prefs), _open_pprefs(pprefs)
        wide = [b for b in pool_before if all(h(b) for h in hard)
                and (topics.match(topic_phrases, b["book_id"], topic_index)[0] or b["book_id"] in sem_ids)]
        wide, w_labels, o_prefs, _ = constrain_relaxing(wide, o_prefs, o_pprefs, _open_excl, cards)
        # «أدب روسي» earlier, «صراع النفس» now and no Russian book on it: say so and ask (staff chat 2026-10-10)
        langs_left = [l for l in (prefs.get("original_languages") or ()) if l not in (o_prefs.get("original_languages") or ())]
        if wide:
            pool, labels, prefs, pprefs, exclusions, topic_widened = wide, w_labels, o_prefs, o_pprefs, _open_excl, True
            scored = score_books(pool, prefs, pprefs, cards)
    topic_ids, topic_about_ids = set(), set()
    _sem_ok = bool(sem) or bool(topic_phrases and not author_only and not similar_author and not publisher_name and embeddings.load())
    if topic_phrases:
        rescored = []
        _event_said = bool(_EVENT_WORDS.search(normalize(" ".join(user_messages))))
        for s, r, b in scored:
            ts, about, mentioned = topics.match(topic_phrases, b["book_id"], topic_index)
            if not _event_said and (about or mentioned):
                _txt = " ".join(str(b.get(k) or "") for k in ("title", "summary", "hook", "subjects"))
                _ev = [p for p in about + mentioned if months_in(p) and all(month_as_event(m, _txt) for m in months_in(p))]
                if _ev:   # «حرب أكتوبر» for «أكتوبر»: not the month the visitor means
                    about, mentioned = [x for x in about if x not in _ev], [x for x in mentioned if x not in _ev]
                    ts = 0 if not (about or mentioned) else ts
            if about and (_sem_ok or (b.get("genre") or "").strip() in _Q7_FICTION) and b.get("genre") not in CHILD_GENRES:
                # QADFIX9 (QAD 2026-10-20, case 34.1): «الجوالون» (a novel about sandstorms) for «الإدمان على الجوال»
                # (QADFIX7 #7, case 12.1: not for a children's book — «لغز كرة القدم» is a mystery for a 9-year-old)
                # the word only in the title, the summary about something else, and not near in meaning:
                # «الأبراج الرملية» / «أبراج بابل» are towers, not the zodiac asked for (staff chat 2026-10-13)
                _other = [x for x in about if topics.title_only(x, b["book_id"], topic_index)
                          and not any(i == b["book_id"] for i, _ in sem.get(x, []))]
                if _other:
                    about = [x for x in about if x not in _other]
                    ts = 0 if not (about or mentioned) else ts
            if about:
                topic_about_ids.add(b["book_id"])
                in_title = ts >= topics.TITLE * len(about)
                r = [("عنوانه عن «" if in_title and not mentioned else "يتناول «") + "»، «".join(about) + "»"] + r
            elif mentioned:
                r = ["نبذته تذكر «" + "»، «".join(mentioned) + "»"] + r
            close = [p for p, hits in sem.items() if p not in about and p not in mentioned and any(i == b["book_id"] for i, _ in hits)]
            if close and not about:
                r = ["قريب من موضوع «" + "»، «".join(close) + "» حسب نبذته"] + r
                ts += 2
            if about or mentioned or close:
                topic_ids.add(b["book_id"])
            rescored.append((s + ts * 2, r, b))
        scored = sorted(rescored, key=lambda x: x[0], reverse=True)
        for s, r, b in scored[:6]:   # what the visitor may actually be shown
            if b["book_id"] not in topic_ids:
                continue
            _, about, mentioned = topics.match(topic_phrases, b["book_id"], topic_index)
            topics_found += [x for x in about if x not in topics_found]
            topics_mentioned += [x for x in mentioned if x not in topics_mentioned]
            topics_semantic += [p for p, hits in sem.items() if p not in topics_semantic and any(i == b["book_id"] for i, _ in hits)]
        topics_mentioned = [x for x in topics_mentioned if x not in topics_found]
        topics_semantic = [x for x in topics_semantic if x not in topics_found and x not in topics_mentioned]
    if adult_reader and chat_rules.parent_reader(user_messages) and not author_only and not it["mentioned_titles"]:
        # a parent asking for himself about his child («ابي كتاب يساعدني انا افهم ولدي المراهق»): books on
        # bringing up children first — before, «بيولوجيا السلوك الإجرامي» came first because of «عصبي»
        # and «200 خطوة نحو التربية برفق» was not offered (QAD 2026-10-14, case 8)
        def _harm(b):
            return bool(_PARENT_HARM.search(normalize(" ".join(str(b.get(k) or "") for k in ("title", "keywords", "subjects")))))
        def _parenting(b):
            return (bool(_PARENTING.search(normalize(" ".join(str(b.get(k) or "") for k in ("title", "keywords", "subjects", "summary", "hook")))))
                    and not _harm(b))
        # QADFIX8 (QAD 2026-10-19, case 47.2): a parenting book by its title first, about teenagers first when the child is
        # one («عمره ١٦»), the ones shown already after the others; a novel, a travel book or a memoir is not a parenting book
        _nonpar = CHILD_GENRES | {"رواية", "قصص مصورة", "قصص قصيرة", "أدب رحلات", "سيرة ذاتية", "شعر", "مسرحية"}
        _age = audience_age(user_messages)
        _teen = bool(re.search(r"مراهق|teen", normalize(" ".join(user_messages[-3:])))) or bool(_age and 12 < _age < 20)
        def _pbonus(b):
            _t = normalize(" ".join(str(b.get(k) or "") for k in ("title", "summary", "keywords", "subjects")))
            _small = bool(re.search(r"(?:^| )(?:طفلي|طفلك|(?:ال)?طفل|(?:ال)?اطفال|(?:ال)?رضيع|(?:ال)?رضع|ينام|(?:ال)?حضانه)(?: |$)",
                                    normalize(b.get("title") or "")))
            return ((4 if _PARENTING_NAMED.search(normalize(b.get("title") or "")) else 0) + (2 if _teen and re.search(r"مراهق", _t) else 0)
                    - (5 if _teen and _small else 0) - (3 if b["book_id"] in shown else 0))
        scored = sorted(((s + 8 + _pbonus(b), ["كتاب للوالدين عن التربية"] + r, b)
                         if _parenting(b) and (b.get("genre") or "").strip() not in _nonpar else (s, r, b) for s, r, b in scored),
                        key=lambda x: x[0], reverse=True)
        def _parenting_named(b):   # the title or the library's keywords say it, not one word of a summary
            return (bool(_PARENTING_NAMED.search(normalize(" ".join(str(b.get(k) or "") for k in ("title", "keywords", "subjects")))))
                    and not _harm(b) and (b.get("genre") or "").strip() not in _nonpar)
        if not any(_parenting_named(b) and b["book_id"] not in shown for _, _, b in scored[:6]):
            # the subject words («عصبي»، «المراهقة») narrowed the books to ones without a parenting book: the
            # parenting books in stock are brought in, about teenagers first when the visitor speaks of one
            _par = [b for b in pool_before if is_available(b) and _parenting_named(b)]
            if _par:
                _ids = {b["book_id"] for b in _par}
                _top = max([x[0] for x in scored] or [0])
                _add = [(_top + 2 + _pbonus(b), ["كتاب للوالدين عن التربية"], b) for b in _par]
                scored = sorted(_add + [x for x in scored if x[2]["book_id"] not in _ids], key=lambda x: x[0], reverse=True)
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
    # «وش أرخص رواية؟»، «أقصر كتاب له»، «أقصر» after some books: the order comes from the database (price,
    # page count) over everything the request allows, not from the model's look at the few candidates it
    # is given (QAFIX 2026-10-11: «أرخص رواية 16٫96» when the cheapest was 5٫67; cases 16, 3, 14, 17, 20)
    order_by, order_first = None, None
    shorter_none, order_list = None, []
    _short = chat_rules.shortest_asked(_last_msg)
    # QADFIX12 #4 (QAD 2026-10-22, case 1.1; Ibrahim: «المفروض النموذج يفهم هالشيء ومو لازم نفصل كل الصيغ لكل الطلبات»):
    # «أقدمها تاريخًا»، «أرخصها»، «أقصر شوي» — the order the visitor wants is read by the model in any wording; the word
    # lists below stay only as a second reader
    _model_order = it.get("order_by")
    if not _short and _model_order == "shortest":
        _short = "shorter" if last_batch and len(normalize(_last_msg).split()) <= 4 else "shortest"
    if _model_order == "cheapest":
        cheapest = True
    # QADFIX13 #1 (case 6.1): «شي يشبه 1984 بس أحدث» is not «the newest books in the shop»: the books like it that came out
    # after it are found from its links below (_newer_than), so the whole stock is not sorted by year here
    _like_pre = any(isinstance(r, dict) and r.get("relation") == "similar" and match_titles([r.get("title") or ""], all_books)
                    for r in it.get("references") or [])
    if (((pub_years and not pub_missing and re.search(r"(?:^| )(?:احدث|الاحدث|اخر كتاب|اخر الكتب|اخر الروايات|اخر ما)|latest|newest|most recent",
                                                       normalize(_last_msg))) or _model_order == "newest")
            and not (cheapest or _short) and not _like_pre):
        # «وش أحدث كتاب وصلكم؟»: the newest first, with its year (QAD 2026-10-13, case 44)
        scored = sorted(((max(s, 1), r + [f"صدر سنة {year_for_new(b)}"] if year_for_new(b) else r, b) for s, r, b in scored),
                        key=lambda x: (-(year_for_new(x[2]) or 0), -x[0], x[2]["book_id"]))
    _oldest = ((bool(re.search(r"(?:^| )(?:اقدم|الاقدم)(?: |$)|oldest|earliest", normalize(_last_msg))) or _model_order == "oldest")
               and not _EDITION_ASK.search(normalize(_last_msg)))
    # QADFIX11 (model replay, case 1.3): «أبي الأقدم منها، أول رواية عربية كتبت من اللي عندكم» — the titles the interpreter
    # took from it are the books just shown: the oldest is still ordered over the whole request
    _mt_shown = bool(it["mentioned_titles"]) and all(any(b["book_id"] in shown for b in match_titles([m], all_books))
                                                      for m in it["mentioned_titles"])
    # QADFIX12 #4 (replay, case 1.2): «ما عندكم أقدم من الجريمة والعقاب؟» — a title named only to compare with does not stop the order
    _mt_compared = bool(it["mentioned_titles"]) and all(compared_title(_last_msg, m, all_books) for m in it["mentioned_titles"])
    if _oldest and not (cheapest or _short) and not similar_author and (not it["mentioned_titles"] or _mt_shown or _mt_compared):
        # QADFIX9 (QAD 2026-10-20, case 43.1): «وش أقدم رواية عندكم كتابةً؟» — by the year the work first came out
        # (books.original_year, with its source), over every book the request allows; not «1977» from a few candidates
        _og = set(prefs.get("genres") or ()) or (set(it["prefs"].get("genres") or ()) - CHILD_GENRES if author_only else set())
        # QADFIX11 (QAD 2026-10-21, case 1): «أقدم رواية عربية» — the visitor's origin wish too (not «دون كيخوته»)
        _ob = [b for b in pool_before if is_available(b) and first_published(b) and all(h(b) for h in hard)
               and (not _og or (b.get("genre") or "").strip() in _og) and origin_allowed(b, prefs)]
        _ob.sort(key=lambda b: (first_published(b), b["book_id"]))
        # QADFIX14 #5 (QAD 2026-10-23, case 4.3): «زي يوسا وماركيز وإيزابيل الليندي، وش أقدم وحدة لهم؟» — the oldest of the
        # authors named in this request, not of every book the region allows
        _oa = [a for a in (prefs.get("authors") or []) if str(a or "").strip()] if not author_only else []
        _ob_a = [b for b in _ob if any(author_matches(a, b.get("author")) for a in _oa)] if _oa else []
        if _ob_a:
            _ob = _ob_a
        if it.get("about_shown") and last_batch:
            # QADFIX14 م4: «أيهم أقدم؟» — among the books just shown
            _ob = [b for b in _ob if b["book_id"] in set(last_batch)] or _ob
        if _ob and _mt_shown:
            it["mentioned_titles"] = []
            if it.get("intent") == "about_book":
                it["intent"] = "recommend"
        if _ob:
            order_by, order_first, order_list = "oldest", _ob[0], _ob[:4]
            scored = [(1000 - n, [f"first published in {first_published(b)}" if language == "en" else f"صدر العمل أول مرة سنة {first_published(b)}"], b)
                      for n, b in enumerate(_ob[:6])]
            if it.get("intent") in ("clarify", "smalltalk"):
                it["intent"] = "recommend"
    # QADFIX14 م4/م5 (QAD 2026-10-23, cases 33.2, 36.2): «مين فيهم أقصر؟»، "Which of those is the shortest?" — the order is
    # taken among the books just shown, in the editions shown (the model reads it: about_shown), not over the whole stock
    # and not in another edition of one of them («Great Expectations» in Arabic, 720 pages)
    _shown_only = bool(it.get("about_shown")) and bool(last_batch) and bool(cheapest or _short or _oldest)
    if _shown_only:
        it["mentioned_titles"] = []
        if _short == "shorter":
            _short = "shortest"
    if (cheapest or _short) and not similar_author:
        _val = (lambda b: b.get("price")) if cheapest else (lambda b: b.get("page_count"))
        _base = [x for x in scored if x[0] > 0]
        if _shown_only:
            _base = [(1, [], all_books[i]) for i in last_batch if i in all_books and is_available(all_books[i])]
        if author_only or len(_base) < 3:
            _base = list(scored)
        elif (pprefs.get("moods") or pprefs.get("weight") or topic_phrases) and not cheapest:
            # «أقصر» after «شي يونّس، ماله خص بالحزن»: shorter among the books that fit the wish well, not any book
            # with a point («ما أقدر أؤكد إنه مرح» for three short poetry books, QAD 2026-10-14, case 43.2)
            _top = max(x[0] for x in _base)
            _fit = [x for x in _base if x[0] >= 0.6 * _top]
            if len(_fit) >= 2:
                _base = _fit
        if prefs.get("awards"):
            # «ابي رواية فازت بكتارا» → «أقصر وحدة»: the shortest of the books that won it themselves, when
            # there are any, not a book whose author won it (QAD 2026-10-13, case 34.2)
            _own = [x for x in _base if any(str(r).startswith("فاز") for r in x[1])]
            if _own:
                _base = _own
        if prefs.get("genres") and not author_only:
            # «رواية تاريخية تدور في مصر» → «أقصر»: the shortest novels, not two studies about Egypt (QADFIX6, QAD 2026-10-18 case 34.2)
            _gk = [x for x in _base if x[2].get("genre") in prefs["genres"]]
            if _gk:
                _base = _gk
        if author_only and (last_batch or prefs.get("genres")):
            _lg0 = set(prefs.get("genres") or ()) or {all_books[i].get("genre") for i in last_batch if i in all_books}
            if _lg0 and (len(_lg0) == 1 or prefs.get("genres")) and None not in _lg0:
                # «روايات إليف شافاك» → «وش أقصر وحدة؟»: the shortest of the kind shown (her novels), not her essay
                _base = [x for x in _base if x[2].get("genre") in _lg0] or _base
        if not author_only and last_batch:
            # «وش أقصر وحدة؟» after a list of one author's novels: the shortest of HER books, not a Darwish
            # booklet beside them (QAD 2026-10-15, case 20.2)
            _sa = [author_flow.display(all_books[i].get("author")) for i in last_batch if i in all_books]
            _main = max(set(_sa), key=_sa.count) if _sa else None
            if _main and _sa.count(_main) * 3 >= len(_sa) * 2:
                _same = [x for x in _base if author_flow.display(x[2].get("author")) == _main]
                if not _same:
                    _same = [(1, [], b) for b in all_books.values() if is_available(b)
                             and author_flow.display(b.get("author")) == _main]
                _lg = {all_books[i].get("genre") for i in last_batch if i in all_books}
                if len(_lg) == 1 and None not in _lg:
                    # «روايات إليف شافاك» → «وش أقصر وحدة؟»: the shortest NOVEL of hers, not her 64-page essay
                    _same = ([x for x in _same if x[2].get("genre") in _lg]
                             or [(1, [], b) for b in all_books.values() if is_available(b) and b.get("genre") in _lg
                                 and author_flow.display(b.get("author")) == _main] or _same)
                if _same:
                    _base = _same
        if language == "en" and not edition:
            # an English-speaking visitor: the shortest of the copies in English when there are any (QAD 2026-10-15, case 47.2)
            _en = [x for x in _base if (x[2].get("language") or "ar") == "en" and _val(x[2])]
            if _en:
                _base = _en
        _have = [x for x in _base if _val(x[2])]
        if _short == "shorter" and not cheapest:
            # QADFIX14 م3 (QAD 2026-10-23, case 16.2): «وفيه أقصر منها بس رواية عربية مو مترجمة؟» — «shorter» is measured against
            # the shown books that still meet the request as it is now; when none does (the translated «الشاب»), it is the
            # shortest under the new wish, not «nothing shorter than 48 pages»
            _lb_pages = sorted((all_books[i]["page_count"], i) for i in last_batch if i in all_books and all_books[i].get("page_count"))
            _lim = 0
            if _lb_pages:
                _ref = all_books[_lb_pages[0][1]]   # the shortest shown, which «أقصر منها» points at
                if (all(h(_ref) for h in hard) and origin_allowed(_ref, prefs)
                        and (not prefs.get("genres") or _ref.get("genre") in prefs["genres"])):
                    _lim = _lb_pages[0][0]
            if not _lim:
                _short = "shortest"   # the shown book no longer fits the request: the shortest under the new wish
            _less = [x for x in _have if x[2]["page_count"] < _lim]
            if not _less and _lim:
                # QADFIX5 #10 (QAD 2026-10-17, cases 5.2, 27.3): nothing shorter among the best fits — look wider, among
                # every scored book, then the same kind of book as the ones shown; never offer one as long or longer
                _less = [x for x in scored if _val(x[2]) and x[2]["page_count"] < _lim]
                if not _less:
                    _lg = {all_books[i].get("genre") for i in last_batch if i in all_books} - {None}
                    _less = [(1, [], b) for b in pool_before if is_available(b) and b.get("page_count")
                             and b["page_count"] < _lim and b.get("genre") in _lg]
                if not _less:
                    shorter_none = _lim
            if _less:
                _have = _less
            elif _lim:
                _have = []
        if (not cheapest and topic_phrases and any(x[2]["book_id"] in topic_about_ids and not _val(x[2]) for x in _base)
                and not any(x[2]["book_id"] in topic_about_ids for x in _have)):
            # QADFIX7 #2 (case 39.2): the books about the subject have no page count: they stay, and the reply says so
            q7["nopages"] = [x[2]["title"] for x in _base if x[2]["book_id"] in topic_about_ids and not _val(x[2])][:2]
            _have = []
        _have.sort(key=lambda x: (_val(x[2]), x[2]["book_id"]))
        if _have:
            order_by, order_first = ("cheapest" if cheapest else _short), _have[0][2]
            order_list = [x[2] for x in _have[:4]]
            scored = [(1000 - n, [(f"سعره {b['price']:g} ريال" if cheapest else f"عدد صفحاته {fmt_num(b['page_count'])}")] + r, b)
                      for n, (_s, r, b) in enumerate(_have[:6])]
    elif price_max and not price_missing:
        scored = [(s, r + [f"سعره {b['price']:g} ريال"], b) for s, r, b in scored]
    # QADFIX5 #17 (QAD 2026-10-17, case 9.1): when hardly any Arabic copy fits the wish (a romance for a 14-year-old),
    # the English copies that do fit are not pushed below unrelated Arabic books; the reply says they are in English
    _ar_fit = sum(1 for _s, _r, b in scored if _s > 0 and (b.get("language") or "ar") != "en")
    _en_fit = sum(1 for _s, _r, b in scored if _s > 0 and (b.get("language") or "ar") == "en")
    if language != "en" and not edition and not order_by and _ar_fit < 2 and _en_fit:
        guidance_extra_en = True
    else:
        guidance_extra_en = False
    if language != "en" and not edition and not order_by and not guidance_extra_en:
        # a visitor writing in Arabic who did not ask for English: copies in English go after the Arabic
        # ones (QAFIX 2026-10-11: «Inferno» offered to three Arabic readers without a word about its language)
        # … except a copy whose title or subjects are about the subject asked for: «سحر وتنانين وممالك» has
        # «A Game of Thrones» in English only, and «ما لقيت رواية عن التنانين» was said (QADFIX4, QAD 2026-10-16 case 8.1)
        scored = sorted(((s - (1.5 if (b.get("language") or "ar") == "en" and b["book_id"] not in topic_about_ids else 0), r, b)
                         for s, r, b in scored),
                        key=lambda x: x[0], reverse=True)
    if not order_by and not author_only and not it["mentioned_titles"]:
        # a later part of a series whose first part is in stock: the first part is offered instead
        # («البلدة الأخيرة» (3) before «غابة الصنوبر» (1), QAD 2026-10-15, case 4.1)
        _ids = {b["book_id"] for _s, _r, b in scored}
        _swapped = []
        for _s, _r, b in scored:
            _pos, _ser = b.get("series_position"), (b.get("series_name") or "").strip()
            if _ser and isinstance(_pos, (int, float)) and _pos > 1:
                _first = next((x for x in all_books.values() if is_available(x) and (x.get("series_name") or "").strip() == _ser
                               and x.get("series_position") == 1 and x["book_id"] not in shown and x["book_id"] not in _ids), None)
                if _first:
                    _ids.add(_first["book_id"])
                    _swapped.append((_s, _r + ([f"the first part of «{_ser}»"] if language == "en" else [f"الجزء الأول من «{_ser}»"]), _first))
                    continue
            _swapped.append((_s, _r, b))
        scored = _swapped
    if it.get("lively_history"):
        # Ibrahim 2026-10-17 («ممكن تقترح رواية تاريخية ايضا»): history books and historical novels side by side;
        # a novel that is not historical is not history
        _is_nov = lambda b: (b.get("genre") or "").strip() == "رواية"
        _hb = [x for x in scored if not _is_nov(x[2])]
        _hn = [x for x in scored if _is_nov(x[2]) and "رواية تاريخية" in _json_list(x[2].get("subgenres"))]
        scored = [x for i in range(max(len(_hb), len(_hn))) for x in (_hb[i:i + 1] + _hn[i:i + 1])]
    if _q7_series and not similar_author:
        # QADFIX7 #5 (QAD 2026-10-19, case 8): a series wish is searched in series_name, one book per series (its
        # first part in stock), with the number of its parts in stock
        _parts = {}
        for _b in all_books.values():
            _sn = (_b.get("series_name") or "").strip()
            if _sn and is_available(_b):
                _parts.setdefault(_sn, []).append(_b)
        # a series is one author's parts («نوبل» is a publisher's collection of many authors, not a series to follow)
        _parts = {k: v for k, v in _parts.items() if len({author_flow.display(x.get("author")) for x in v}) == 1}
        _ok = lambda b: len(_parts.get((b.get("series_name") or "").strip(), ())) >= 2
        _cand = [x for x in scored if _ok(x[2])]
        if len(_cand) < 2:
            _seen0 = {x[2]["book_id"] for x in _cand}
            _cand += [(1, [], b) for b in pool_before if b["book_id"] not in _seen0 and is_available(b) and _ok(b) and all(h(b) for h in hard)]
        _picks, _seen_s = [], set()
        for _sc, _r, _b in sorted(_cand, key=lambda x: (-len(_parts[(x[2].get("series_name") or "").strip()]), -x[0])):
            _sn = (_b.get("series_name") or "").strip()
            if _sn in _seen_s:
                continue
            _seen_s.add(_sn)
            _first = min(_parts[_sn], key=lambda b: (b.get("series_position") or 99, b["book_id"]))
            _n = len(_parts[_sn])
            _picks.append((max(_sc, 1) + 5, [f"of the series «{_sn}», {_n} parts in stock" if language == "en"
                                             else f"من سلسلة «{_sn}»، عندنا منها {_n} أجزاء متوفرة"] + _r, _first))
        if _picks:
            _pid = {x[2]["book_id"] for x in _picks}
            scored = _picks + [x for x in scored if x[2]["book_id"] not in _pid]
            q7["series"] = [(x[2].get("series_name"), len(_parts[x[2]["series_name"].strip()])) for x in _picks[:4]]
            if it.get("intent") in ("clarify", "about_book"):
                it["intent"] = "recommend"   # QADFIX8 (case 8.2): the series found are shown as cards, not only named
    if "شعر" in (prefs.get("genres") or ()) and not author_only and not it["mentioned_titles"] and not similar_author:
        # QADFIX7 #11 (QAD 2026-10-19, case 40): «تحب الشعر والقصص الخفيفة» offers poetry too; «شعر بس، شي سهل» puts
        # Arabic, easy poetry before translated epics
        _easy = bool(re.search(r"(?:^| )(?:سهل|سهله|بسيط|بسيطه|خفيف|خفيفه|easy|simple|light)(?: |$)", " ".join(normalize(user_messages[-1]).split())))
        _poems = [x for x in scored if (x[2].get("genre") or "").strip() == "شعر"]
        if _poems:
            def _pk(x):
                _b, _c = x[2], cards.get(x[2]["book_id"]) or {}
                _ar = (_b.get("original_language") or "") == "ar" or ((_b.get("language") or "ar") == "ar" and not (_b.get("translator") or "").strip())
                _ld = _c.get("language_difficulty")
                _epic = bool(re.search(r"ملحمه|الكوميديا الالهيه|الالياذه|الاوديسه|الشاهنامه|epic", normalize(_b.get("title"))))
                return (0 if _ar else 2) + (0 if _ld in ("easy", "very_easy") else 1 if _ld in (None, "", "unknown", "medium") else 2) + (3 if _epic else 0)
            _poems = sorted(_poems, key=lambda x: (_pk(x), -x[0]))
        if _poems and _easy:
            scored = [(max(s, 1), r, b) for s, r, b in _poems] + [x for x in scored if x not in _poems]
        elif _poems and len(prefs["genres"]) > 1 and not any((x[2].get("genre") or "").strip() == "شعر" for x in scored[:4]):
            scored = [(max(s, 1), r, b) for s, r, b in _poems[:2]] + [x for x in scored if x not in _poems[:2]]
    if (any(women_wish(t) for t in user_messages[-3:]) and not author_only and not similar_author
            and not (it["mentioned_titles"] and it["intent"] == "about_book")):
        # QADFIX9 (QAD 2026-10-20, cases 11.2/11.3): «شي أخف وأقصر من سر الزعفرانة» after «روايات لروائيات سعوديات» keeps
        # the women authors; the book compared with is not offered again
        _named = {b["book_id"] for b in match_titles(it["mentioned_titles"], all_books)} if it["mentioned_titles"] else set()
        # QADFIX8 (QAD 2026-10-19, case 49.1): «شعر لشاعرات خليجيات» — the author records that say «كاتبة …»، «كتابها …»
        # (or a title «شاعرات …») come first; books whose author record says «شاعر …» are not offered as a woman's
        _g = book_author_gender()
        def _fem(b):
            # a title «… النساء» counts only when the author record does not say a man wrote it (QADFIX9 replay, case 11.1:
            # «مقامات النساء» by عبد العزيز الصقعبي)
            return _g.get(b["book_id"]) == "f" or (_g.get(b["book_id"]) != "m" and bool(_PEOPLE_WOMEN.search(normalize(b.get("title")))))
        _wf = [x for x in scored if _fem(x[2]) and x[2]["book_id"] not in _named]
        _ids_f = {x[2]["book_id"] for x in _wf} | _named
        _g_genres = set(prefs.get("genres") or ())
        _wf += [(1, [], b) for b in pool_before if b["book_id"] not in _ids_f and is_available(b) and _fem(b)
                and all(h(b) for h in hard) and (not _g_genres or (b.get("genre") or "").strip() in _g_genres)]
        if not _wf:
            # QADFIX8 (model replay, case 49.2): the visitor corrects the request after the women's books were shown —
            # they are still the answer (the «new options» pool had left them out)
            _wf = [(1, [], b) for b in all_books.values() if b["book_id"] in shown and is_available(b) and _fem(b)
                   and all(h(b) for h in hard) and (not _g_genres or (b.get("genre") or "").strip() in _g_genres)]
        if _wf:
            _why = "its author is a woman, according to her author record" if language == "en" else "مؤلفته امرأة حسب سجل المؤلفة عندنا"
            scored = [(max(s, 1) + 6, [_why] + r, b) for s, r, b in _wf]
            q7["women"] = [x[2]["title"] for x in _wf[:4]]
    candidates = [] if similar_author else [(b, reason_text(r, language), "direct") for s, r, b in scored if s > 0][:6]
    # A subject the visitor names now (or keeps, answering only «رواية») narrows the books: only the ones
    # about it, mentioning it or close to it in meaning; never «a novel, as you asked» beside them, and no
    # filler. None at all: say so and ask (Ibrahim 2026-10-13, «رواية تدور أحداثها في أكتوبر»: «الهدف مساعدة
    # القارئ مو تشتيته… الكتب تتحدد أكثر مع كل سؤال»)
    _form_words = {normalize(w) for w in "رواية روايه روايات قصة قصه قصص قصيرة قصيره كتاب كتب شعر ديوان مسرحية مسرحيه "
                   "سيرة سيره ذاتية ذاتيه تاريخي تاريخية تاريخيه ابغى ابي اريد بدي ودي novel novels story stories book books poetry".split()}
    _more_words = {normalize(w) for w in "هل في فيه عندك عندكم اقتراحات اقتراح ترشيحات خيارات اخرى أخرى اخر آخر غير غيرها غيره غيرهم "
                   "كمان زياده زيادة المزيد ثاني ثانيه ثانية ثانيين بعد شي شيء شيء ثاني بس طيب وش ايش more other others another else "
                   "any options suggestions".split()}
    _last_words = normalize(user_messages[-1]).split()
    _form_only = bool(_last_words) and all(w in _form_words for w in _last_words)
    # «هل في اقتراحات أخرى؟» after books on a subject: more on that subject, not random novels
    _more_only = bool(_last_words) and all(w in _form_words or w in _more_words for w in _last_words) and not _form_only
    # a subgenre with no book that has it («غزل» for Gulf poets) was only relaxed into a subject: the books that keep
    # the rest of the wish are offered and the subgenre is said to be unconfirmed (QADFIX6, QAD 2026-10-18 case 36.3)
    # QADFIX12 #2 (QAD 2026-10-22, case 46.1): «شعر حديث لشاعرة» — a label that only describes the kind of book asked for
    # (it carries the kind's own word: «شعر حديث»، «رواية قصيرة الفصول») never empties the answer: the books of that kind
    # are offered and the description is said to be unconfirmed, as for any relaxed label kept with other wishes
    _kind_w = {strip_al(w) for g in set(prefs.get("genres") or ()) | set(it["prefs"].get("genres") or ()) for w in normalize(g).split()}
    _describes_kind = bool(topic_phrases) and bool(_kind_w) and all(
        p in (relaxed_subgenres or ()) and {strip_al(w) for w in normalize(p).split()} & _kind_w for p in topic_phrases)
    _relaxed_only = bool(topic_phrases) and all(p in (relaxed_subgenres or ()) for p in topic_phrases) and (bool(hard) or _describes_kind)
    topic_strict = bool(topic_phrases and (_topic_now or _form_only or _more_only or q7.get("nopages")) and not author_only and not similar_author
                        and not publisher_name and not order_by and not it["mentioned_titles"] and not _relaxed_only)
    topic_nothing = False
    def _fits_topic(i):
        """A book (also one shown earlier) about the subject, mentioning it (not as an event) or close in meaning."""
        if i in topic_ids or i in sem_ids:
            return True
        b = all_books.get(i) or {}
        _, about, mentioned = topics.match(topic_phrases, i, topic_index)
        hits = about + mentioned
        if hits and not _EVENT_WORDS.search(normalize(" ".join(user_messages))):
            _txt = " ".join(str(b.get(k) or "") for k in ("title", "summary", "hook", "subjects"))
            hits = [p for p in hits if not (months_in(p) and all(month_as_event(m, _txt) for m in months_in(p)))]
        return bool(hits)
    if topic_strict:
        # a parent's own question about his child: the parenting books brought in above stay beside the subject
        # ones («عصبي»، «المراهقة» are what the child is, the father's book is about bringing him up; QAD 2026-10-14, case 8)
        _keep_par = set()
        if adult_reader and chat_rules.parent_reader(user_messages):
            _keep_par = {b["book_id"] for b, why, _m in candidates if "للوالدين عن التربية" in str(why)}
        candidates = [c for c in candidates if c[0]["book_id"] in topic_ids or c[0]["book_id"] in _keep_par]
        # the books shown earlier that fit the subject count too: «رواية تدور أحداثها في أكتوبر» right after
        # «الخريف» and «Drums of Autumn» were shown is not «nothing» (they come back as «shown» below)
        topic_nothing = not candidates and (_more_only or not any(_fits_topic(i) for i in shown[-4:]))
        if candidates and it["intent"] == "clarify":
            it["intent"] = "recommend"   # «ايش الكتب اللي تتكلم عن أكتوبر؟»: the books that do, not a question first
    if (it["intent"] == "clarify" and len(user_messages) > 1 and _carry_from and candidates
            and (prefs.get("subgenres") or prefs.get("genres")) and any(m == "direct" for _, _, m in candidates)):
        # QADFIX7 #8 (QAD 2026-10-19, case 38.2): «خيال علمي» → «شي عربي» answered with a description of «هيدرا» and no card:
        # the visitor refined a kind he named, so the books that fit are shown, not a second question
        it["intent"] = "recommend"
    if it["intent"] == "clarify" and publisher_name and any(m == "direct" for _, _, m in candidates):
        # QADFIX9 (QAD 2026-10-20, case 29.1): «كتب دار رشم وش فيها حلو؟» — the publisher's books, not «وش جوّك اليوم؟»
        it["intent"] = "recommend"
    if (it["intent"] == "clarify" and len(user_messages) > 1 and adult_reader and chat_rules.parent_reader(user_messages)
            and any("للوالدين عن التربية" in str(why) for _b, why, _m in candidates)):
        # QADFIX8 (model replay, case 47.2): «عمره ١٦» after «ابي كتاب لي يساعدني اتقرب منه» — the father answered the
        # question; the parenting books that fit are shown, not a question about mood
        it["intent"] = "recommend"
    # QADFIX5 #18 (QAD 2026-10-17, cases 8.2, 36.1): a subject that found its books is answered with them alone —
    # no unrelated fillers beside them (three «جورج» space books after prayer stories for an 8-year-old)
    if (len(candidates) < 3 and not similar_author and not topic_strict and not (topic_phrases and candidates)
            and not q7.get("women")):
        # fillers come from the constrained pool, so they never break a stated wish, and only from
        # researched books: an unresearched book is never offered as an alternative (2026-09-30)
        used = {b["book_id"] for b, _, _ in candidates}
        candidates += [(b, (("By " if language == "en" else "من كتب ") + author_flow.display(b.get("author"))) if author_only
                        else filler_reason(labels, language), "filler") for s, r, b in scored
                       if b["book_id"] not in used and (b["book_id"] in researched or author_only)][:4 - len(candidates)]

    # A request with nothing to search by gets a few different starting points, not random fillers
    starters = False
    # only after two questions (or «ورّني كتب على طول»): a vague request is asked about first (Ibrahim 2026-10-11)
    if ((it["intent"] == "clarify" or skip_questions) and streak >= 2 and not similar_author and not author_only and not topic_phrases
            and not publisher_name and not match_titles(it["mentioned_titles"], all_books)
            and not any(m == "direct" for _, _, m in candidates)):
        picks = starter_picks(pool, cards, researched, language)
        if len(picks) >= 2:
            candidates, starters = picks, True
    # «عندكم الخيميائي؟» and we do not have it: say so; only books chosen for their likeness to it
    # (reference matches below) may follow, never a random alternative (2026-10-06)
    asked_books = match_titles(it["mentioned_titles"], all_books)
    if asked_books and pprefs.get("audience") in AGE_OK:
        # QADFIX14 #3 (QAD 2026-10-23, case 1.3): «وأخته عمرها ٥ تبي قصة عن الأميرات» — a title the visitor never wrote
        # («سندريلا», the model's own guess) does not bring a grown-ups' book («سندريلا سيكريت») to a child: the age check
        # holds for it like for any other book
        _typed = " ".join(normalize(" ".join(user_messages)).split())
        _unsaid = {" ".join(normalize(m).split()) for m in it["mentioned_titles"]} - {
            " ".join(normalize(m).split()) for m in it["mentioned_titles"] if " ".join(normalize(m).split()) in _typed}
        if _unsaid:
            _keep = [b for b in asked_books if age_allowed(cards.get(b["book_id"]), pprefs.get("audience"), pprefs.get("avoid") or (),
                                                           b, pprefs.get("reader_age"))
                     or not any(b["book_id"] in {x["book_id"] for x in match_titles([m], all_books)} for m in _unsaid)]
            if len(_keep) < len(asked_books):
                asked_books = _keep
                it["mentioned_titles"] = [m for m in it["mentioned_titles"] if " ".join(normalize(m).split()) not in _unsaid]
                if not asked_books and it.get("intent") == "about_book":
                    it["intent"] = "recommend"
    if not asked_books:
        # QADFIX14 #8 (QAD 2026-10-23, case 29.1): «كتاب اسمه فيه «فن اللا…»» — a title typed in part, cut with «…» / «...»:
        # the books whose title starts with it (or has it as a whole-word start inside), when they are few
        for _frag in re.findall(r"([^«»\"“”\n.؟?!،,]{3,50}?)\s*(?:…|\.{2,})", user_messages[-1]):
            _hits = []
            for _k in (4, 3, 2):   # «كتاب اسمه فن اللا...»: the end of the phrase is the title's start
                _f = " ".join(normalize(_frag).split()[-_k:])
                if len(_f.split()) < min(_k, 2) or len(_f.replace(" ", "")) < 5:
                    continue
                _hits = [b for b in all_books.values() if is_available(b)
                         and re.search(r"(?:^| )" + re.escape(_f), " ".join(normalize(b.get("title") or "").split()))]
                if 1 <= len(_hits) <= 4:
                    break
            if 1 <= len(_hits) <= 4:
                asked_books = _hits
                it["mentioned_titles"] = [b["title"] for b in _hits][:4]
                if it.get("intent") in ("recommend", "clarify", "smalltalk"):
                    it["intent"] = "about_book"
                break
    # «كم كان سعرها؟ ومتى توصلكم؟» right after «ذهب مع الريح غير متوفر … وبديله «أنشودة المقهى الحزين»»: «ها» is
    # the book the visitor asked about, not the alternative shown (QAD 2026-10-14, case 19; QAFIX m6 came back)
    pronoun_book = None
    _prev_reply = next((c["content"] for c in reversed(conv[:-1]) if c["role"] == "assistant"), "")
    if _PRONOUN_ASK.search(normalize(user_messages[-1])) and "«" not in user_messages[-1] and _prev_reply:
        for _t in re.findall(r"«([^»]{2,80})»[^.؟!\n]{0,80}?(?:غير متوفر|مو متوفر|ما هو متوفر|ما عندنا|not (?:in stock|available))", _prev_reply):
            _hit = [b for b in match_titles([_t], all_books, max_rank=0.5) if not is_available(b)]
            if _hit:
                pronoun_book = _hit[0]
                break
        if not pronoun_book and last_batch:
            # «عندكم مزرعة الحيوان بالإنجليزي؟» → «كم سعرها؟»: the one copy just shown (Animal Farm), not the
            # Arabic edition the interpreter's title finds first (QAD 2026-10-15, case 42.2)
            _lb = [all_books[i] for i in last_batch if i in all_books]
            _ed_prev = wanted_edition(user_messages[-2], language) if len(user_messages) > 1 else None
            if _ed_prev:
                _lb = [b for b in _lb if ((b.get("language") or "ar").strip() or "ar") == _ed_prev] or _lb
            if len(_lb) == 1:
                pronoun_book = _lb[0]
        if pronoun_book:
            asked_books = [pronoun_book]
            it["mentioned_titles"], it["intent"] = [pronoun_book["title"]], "about_book"
    # «The Power of Habit موجود؟»: the interpreter may give the Arabic title; the visitor's own
    # Latin-script words are tried too (QA 2026-10-08, cases 22 and 38)
    for latin in re.findall(r"[A-Za-z][A-Za-z0-9'’:,\- ]{4,}", user_messages[-1]):
        if len(latin.split()) >= 2:
            for b in match_titles([latin.strip()], all_books, max_rank=1):
                if b["book_id"] not in [x["book_id"] for x in asked_books]:
                    asked_books.insert(0, b)
    # Before anything is called «غير متوفر» (QAFIX 2026-10-11: 11 cases said it about a book we have):
    # the title as the visitor typed it in Latin letters ("Do you have Dune?" was looked up as «كثيب»),
    # and the other names the interpreter knows the same book by (its original / English title, another
    # Arabic translation of the title: «الوصايا» = «العهود»).
    other_form = []
    for b in chat_rules.latin_titles(user_messages[-1], all_books):
        if b["book_id"] not in [x["book_id"] for x in asked_books]:
            asked_books.insert(0, b)
            other_form.append(b)
    # only when the title as named found no copy in stock: «العهود» is in stock in Arabic, its English
    # «The Testaments» is not offered first (QAFIX re-run, case 22)
    if (it["mentioned_titles"] or it.get("references")) and not any(is_available(b) for b in asked_books):
        for v in it.get("title_variants") or []:
            # the whole title only: «ديون» (Dune) must not bring a book that merely starts with that word
            for b in match_titles([v], all_books, max_rank=0.5) + chat_rules.series_books(v, all_books)[:5]:
                if b["book_id"] not in [x["book_id"] for x in asked_books]:
                    asked_books.append(b)
                    other_form.append(b)
    # «والجزء الثاني؟»، «وش اللي بعده؟»: the part asked for, from the series of the book named or shown
    # last (series_name + series_position, else the «next part» relation), not from the model's memory of
    # the title (QAFIX 2026-10-11, cases 22 and 28)
    part_wanted, part_book = chat_rules.part_asked(user_messages[-1]), None
    series_anchor, series_rest = None, []
    # QADFIX8 (QAD 2026-10-19, case 8.2): «ابي سلسلة لها أجزاء مؤكدة» asks for series to follow; «لها أجزاء» is not
    # «the other parts of the book shown last» (that made it a question about «مقبرة الكتب المنسية», with no cards)
    _q7_new_series = _q7_series and bool(re.search(r"(?:^| )(?:ابي|ابغى|ابغي|ابغا|اريد|ودي|بدي|رشح|رشحي|رشحلي|عطني|اعطني|اقترح|اقترحي|"
                                                    r"want|recommend|suggest)(?: |$)", " ".join(normalize(user_messages[-1]).split())))
    if _q7_new_series and part_wanted == "all":
        part_wanted = None
    # QADFIX12 #5 (QAD 2026-10-22, cases 3.1–3.3 and 28.1): «Do you have the whole Song of Ice and Fire series?», «عندكم السلسلة
    # كاملة؟» — the series the model names (in Arabic and as published in English) is looked up in our series records in any
    # language, all its parts in order, before anything is called missing; never one word of its name as a subject («الجليد»)
    _ws_books = []
    for _sn in it.get("whole_series") or []:
        for b in chat_rules.series_books(_sn, all_books):
            if b["book_id"] not in [x["book_id"] for x in _ws_books]:
                _ws_books.append(b)
    if _ws_books and not author_only:
        _ws_books.sort(key=lambda b: (b.get("series_position") or 99, not is_available(b), b["book_id"]))
        asked_books = _ws_books[:6]
        candidates, starters = [], False
        it["whole_series_found"] = True
        part_wanted = "all"
    if part_wanted == "all" and not author_only:
        # «هل له أجزاء ثانية؟»: every other part in our records, in stock first; none: said plainly
        _anchors = list(asked_books) + [all_books[i] for i in reversed(shown[-6:]) if i in all_books]
        for a in _anchors:
            if a.get("series_name") or chat_rules.series_others(a, all_books):
                series_anchor, series_rest = a, chat_rules.series_others(a, all_books)
                break
        if series_anchor:
            series_rest = [b for b in series_rest if is_available(b)] + [b for b in series_rest if not is_available(b)]
            asked_books = [series_anchor] + series_rest[:5]
            it["intent"] = "about_book"
        part_wanted = None if series_anchor or not _anchors else part_wanted
    if part_wanted and part_wanted != "all" and not author_only:
        _anchors = list(asked_books) + [all_books[i] for i in reversed(shown[-6:]) if i in all_books]
        for a in _anchors:
            part_book = chat_rules.next_part(a, part_wanted, all_books)
            if part_book:
                break
        if not part_book and part_wanted == "next" and _anchors:
            for r in relations.for_books([_anchors[0]["book_id"]]).get(_anchors[0]["book_id"], []):
                if r.get("relation_type") == "series_next" and r.get("target_book_id") in all_books:
                    part_book = all_books[r["target_book_id"]]
                    break
        if part_book:
            # the part from our records replaces whatever title the interpreter guessed for it («الوصايا»)
            asked_books = [part_book]
            it["intent"] = "about_book"
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
    if not asked_books and not it["mentioned_titles"] and not author_only and not similar_author:
        # «فن الحرب»، «دار نشر فن الحرب»، «عندكم فن الحرب؟»: the message is a stocked title (after a few asking
        # words), even when one of its words is also a topic («الحرب»): that book, not books about war
        # (inbox 2026-10-11 §3.3; audit 2026-10-12 #8)
        exact = exact_title_books(user_messages[-1], all_books)
        if exact:
            asked_books, it["mentioned_titles"], it["intent"] = exact, [exact[0]["title"]], "about_book"
            candidates, starters = [], False
    _frag = chat_rules.named_title(user_messages[-1]) if not asked_books else None
    if _frag:
        # «فيه رواية اسمها الساعة الخامسة شي كذا» (QAD 2026-10-13, case 30)
        if author_only:
            # «رواية لبول أوستر اسمها «حدائق» شي»: only that author's titles, in stock or not, so «حدائق بروكلين»
            # is named and said to be out of stock (QADFIX6, QAD 2026-10-18 case 19.1)
            _his = {i: b for i, b in all_books.items() if b.get("author") in (author_names or ()) or i in author_book_ids}
            guess = match_titles([_frag], _his, max_rank=2.5) if _his else []
        else:
            guess = match_titles([_frag], all_books, max_rank=2.5)
        if guess:
            asked_books, it["mentioned_titles"], it["intent"] = guess, [_frag], "about_book"
            candidates, starters = [], False
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
        if adult_reader and not author_only and match != "asked" and book.get("genre") in CHILD_GENRES:
            return   # a grown-up reader: no children's / YA book from any path (related, shown, likeness …)
        seen.add(book["book_id"])
        facts.append(book_fact(book, cards.get(book["book_id"]), why, language))
        reason_by_id[book["book_id"]] = (why, match)

    # several editions of one asked work (two of the three «فن الحرب» came in as asked books, each listing only the
    # third as «other_editions»): one fact for the work, all its other editions under it, so the answer counts
    # three, not two (library inbox 2026-10-11 §3.3; MGR 2026-10-17)
    _one_per_work = []
    for b in asked_books:
        if any(b["book_id"] in {e["book_id"] for e in same_book_editions(k, all_books)} for k in _one_per_work):
            continue
        _one_per_work.append(b)
    asked_books = _one_per_work
    # QADFIX10 (AGY-20261020-01, case 11.2): a title named only to compare with («أقصر من سر الزعفرانة») stays in facts (its pages)
    # but is not offered as a card
    _cmp_ids = set()
    # QADFIX12 #8 (QAD 2026-10-22, case 5.2; Ibrahim: «زي جو يعني بنفس الاسلوب»): a book named as the model of what is wanted
    # («زي جو ظل الريح»، «بنفس أسلوب X»، "like X") — the model says so (relation similar / same_author), in any wording
    _like = {" ".join(normalize(r.get("title") or "").split()) for r in it.get("references") or []
             if isinstance(r, dict) and r.get("relation") in ("similar", "same_author")}
    _like_ids = set()
    for _mt in it["mentioned_titles"]:
        if compared_title(user_messages[-1], _mt, all_books) or " ".join(normalize(_mt).split()) in _like:
            _cmp_ids |= {b["book_id"] for b in match_titles([_mt], all_books)}
            # QADFIX13 #1 (case 6.1 «similar to 1984 but more recent»): a book the model read as the model of what is wanted
            # stays that even when the message also compares with it («أحدث منها»)
            if " ".join(normalize(_mt).split()) in _like:
                _like_ids |= {b["book_id"] for b in match_titles([_mt], all_books)}
    # QADFIX14 #4 (QAD 2026-10-23, case 2.2): «شعر قديم زي المتنبي أو المعلقات أو ديوان الإمام علي» — books listed as examples
    # of what is wanted (relation example, read by the model) are wanted too: offered, never kept out as a comparison
    _examples = {" ".join(normalize(r.get("title") or "").split()) for r in it.get("references") or []
                 if isinstance(r, dict) and r.get("relation") == "example"}
    _example_ids = {b["book_id"] for _mt in it["mentioned_titles"] if " ".join(normalize(_mt).split()) in _examples
                    for b in match_titles([_mt], all_books)}
    _cmp_ids -= _example_ids
    _like_ids -= _example_ids
    for b in asked_books:
        add(b, "سأل عنه الزائر", "asked")
        if facts and facts[-1]["id"] == b["book_id"] and not (b.get("publisher") or "").strip():
            # «طبعتين … كلتاهما من دار الجمل» when one copy has no publisher recorded (QADFIX4, case 42.1)
            facts[-1]["publisher"] = "not recorded" if language == "en" else "غير مسجّل عندنا"
        # the same title by the same author in other editions we have (publisher, price, language):
        # «وكم سعر كل نسخة؟» gave two prices for three editions (QAD 2026-10-12, case 10)
        if facts and facts[-1]["id"] == b["book_id"]:
            eds = same_book_editions(b, all_books, exclude={x["book_id"] for x in asked_books})
            if eds:
                facts[-1].setdefault("publisher", "not recorded" if language == "en" else "غير مسجّل عندنا")
                facts[-1]["other_editions"] = [edition_fact(e, language) for e in eds[:4]]
                # «طبعتين … كلتاهما من دار الجمل» when one has no publisher recorded (QADFIX4, QAD 2026-10-16 case 42.1)
                for _e in facts[-1]["other_editions"]:
                    _e.setdefault("publisher", "not recorded" if language == "en" else "غير مسجّل عندنا")
        if facts and facts[-1]["id"] == b["book_id"] and _LANGS_ASKED.search(" ".join(normalize(user_messages[-1]).split())):
            # QADFIX6 (QAD 2026-10-18, case 37.1): «كم نسخة من مزرعة الحيوان؟ وبأي لغات؟» — the English «Animal Farm»
            # and the other Arabic title «مزرعة الحيوانات» of the same author are the work's other copies too
            _cand = work_copies_other_titles(b, all_books, exclude={x["book_id"] for x in asked_books}
                                             | {e["id"] for e in facts[-1].get("other_editions") or []})
            if _cand:
                facts[-1]["same_author_maybe_same_work"] = [dict(edition_fact(e, language), title=e.get("title")) for e in _cand[:4]]
    _named_card_ok = set()
    for f in facts:
        if reason_by_id.get(f["id"], ("", ""))[1] == "asked" and f["id"] in all_books:
            _ok, _note = named_age_note(cards.get(f["id"]), pprefs.get("audience"), pprefs.get("avoid") or (),
                                        all_books[f["id"]], pprefs.get("reader_age"), language)
            if _note:
                f["age_note"] = _note
            if _ok and _note:
                _named_card_ok.add(f["id"])
    # a title the visitor asked about is discussed even if unsuitable, but only
    # age-suitable books may be shown as cards (see the id validation below)
    def _cartoon_id(i):
        # a novel made into a cartoon, asked for a child as a cartoon story (QAD 2026-10-15, case 16.2)
        return (prefs.get("adaptation") == "animated" and pprefs.get("audience") == "children"
                and bool(honors.matches_adaptation(i, "animated", prefs.get("adaptation_max_year"))))
    suitable = {b["book_id"] for b in all_books.values()
                if age_allowed(cards.get(b["book_id"]), pprefs.get("audience"), pprefs.get("avoid") or (), b, pprefs.get("reader_age"),
                               _cartoon_id(b["book_id"]))
                and (not prefs.get("subgenres") or matching_subgenres(b, prefs, card=cards.get(b["book_id"])))
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
            in_stock = (any(is_available(b) for b in match_titles([ref["title"]], all_books))
                        or any(is_available(b) for b in other_form))   # here under another form of its title
            # a book we do not have is looked up outside (Wikidata, Open Library, Wikipedia; Goodreads after
            # the answer) and kept for next time; what is found there replaces the model's memory of it,
            # which once named a wrong author for «اخلع حذائك» (Ibrahim 2026-10-11)
            outside = None if in_stock else outside_books.lookup(ref["title"])
            if outside:
                # the model's themes stay unless it named another author (then it was thinking of another book);
                # «عمرو عبد الحميد» and «عمرو عبدالحميد» are the same man
                said, real = normalize(ref.get("author") or ""), normalize(outside.get("author") or "")
                same = (not said or not real or _same_author(said, real)
                        or said.replace(" ", "") == real.replace(" ", ""))
                ref = dict(ref, author=outside.get("author") or "", known=True,
                           themes=ref.get("themes") if same else [], moods=ref.get("moods") if same else [],
                           format=ref.get("format") if same else None)
            found = reference_matches(ref, all_books, cards, suitable, formats) if ref["known"] else []
            if outside:
                text = outside_books.text_for_meaning(outside)
                got = {b["book_id"] for b, _, _ in found}
                near = [i for i in suitable if i not in got and i in all_books and is_available(all_books[i])
                        and normalize(all_books[i]["title"]) != normalize(ref["title"])]
                for bid, _sim in (embeddings.similar_to(text, near, top=4) if text else []):
                    found.append((all_books[bid], (f"Close to «{ref['title']}» in what it is about" if language == "en"
                                                   else f"قريب من «{ref['title']}» في موضوعه"), "reference"))
            if len(found) < 2 and ref.get("known") and not in_stock:
                # a well-known book we do not have: books of the same kind for this reader (QAD 2026-10-14, case 48)
                _kind = reference_kind_matches(ref, all_books, suitable, audience_age(user_messages) if pprefs.get("audience") in ("children", "young_adult") else None,
                                               exclude={b["book_id"] for b, _, _ in found}, language=language)
                found = found + _kind
            for b, why, match in found:
                add(b, why, match)
            ref_notes.append((ref, in_stock, bool(found), outside))
    # «أبغى كتاب يشبه اخلع حذائك» (Ibrahim 2026-10-11): a likeness request never gets random «for variety»
    # fillers; and when the book could not be described or matched and the visitor said nothing else to
    # search by, no book is shown at all and the visitor is asked what they liked in it.
    if ref_notes:
        candidates = [c for c in candidates if c[2] != "filler"]
    other_wishes = any((prefs or {}).get(k) for k in llm.PREFS_SCHEMA["properties"] if k not in ("unsupported_requests", "title_keywords"))
    ref_unmatched_only = (bool(ref_notes) and not any(n[2] for n in ref_notes) and not missing_title
                          and not similar_author and not author_only and not topic_phrases and not publisher_name
                          and not other_wishes)
    if ref_unmatched_only:
        candidates = []
    if publisher_unknown and not topic_phrases and not other_wishes and not asked_books:
        candidates = []   # only a publisher we do not stock was asked for: no book is passed off as theirs
    _ref_dbg = {"refs": len(ref_notes), "found": [n[2] for n in ref_notes], "outside": [bool(n[3]) for n in ref_notes], "missing_title": missing_title, "similar_author": bool(similar_author),
                "author_only": bool(author_only), "topic_phrases": list(topic_phrases or []), "publisher": publisher_name,
                "other_wishes": [k for k in llm.PREFS_SCHEMA["properties"] if (prefs or {}).get(k) and k not in ("unsupported_requests", "title_keywords")],
                "unmatched_only": ref_unmatched_only}
    similar_found, style_source, author_record = False, None, None
    own_shown, own_extra = 0, []
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
        _like = author_style_matches(style, author_name, all_books, cards, allowed, researched)
        _classic = bool(re.search(r"كلاسيك|كلاسيكي|كلاسيكيه|قديم|قديمه|classic", normalize(user_messages[-1])))
        if _classic:
            # «شي كلاسيكي زي روايات جول فيرن»: works first published before 1950 first, not «Inferno» (QADFIX4, QAD
            # 2026-10-16 case 5.2)
            _like = sorted(_like, key=lambda m: not ((first_published(m[0]) or 9999) < 1950))
        for b, why, match in _like:
            add(b, why, match)
            similar_found = True
        if author_record and not several and author_request.get("mode") == "similar" and similar_found and _classic:
            # «شي كلاسيكي زي روايات جول فيرن» asks for classics of that kind, his own among them: they are named too,
            # after the likeness ones (QADFIX4, case 5.2). A plain «كتب تشبه فلان» still never offers his own books.
            for b in [x for x in pool if x["book_id"] in own_ids and x["book_id"] in suitable][:2]:
                add(b, (f"By {author_name} himself, in stock too" if language == "en" else f"من كتب {author_name} نفسه، ومتوفر أيضًا"), "related")
                own_extra.append(b["book_id"])
    # QADFIX13 #1: when the visitor named a book as the model of what he wants, the books linked to it as similar come
    # first and the general matches after them
    _like_first = bool(_like_ids) and not starters
    if not _like_first:
        for b, why, match in candidates:
            add(b, why, match)
    # Related books (card v2 §12.5): alternatives / same author / next part of the first few
    # books, only when in stock and suitable for this reader; each keeps its stated reason.
    # QADFIX13 #1 (AGY-20261022-01 (1), QAD 2026-10-22 case 6.1 «anything similar to 1984 but more recent?»): the editions of
    # one work are folded into one fact (above), so the links of every edition are gathered under it (the «similar» links of
    # «1984» sit on its English and third Arabic edition); when the visitor named the book as the model of what he wants
    # (_like_ids, from the model's reading) the similar books come before the same author's, and «أحدث منه» / "more recent"
    # keeps only books that came out after it
    _fact_ids = [f["id"] for f in facts[:3]]
    _ed_to = {}
    if not starters:
        for _fid in _fact_ids:
            if _fid in all_books:
                for _e in same_book_editions(all_books[_fid], all_books, exclude=set(_fact_ids)):
                    _ed_to.setdefault(_e["book_id"], _fid)
    rel = {} if starters else relations.for_books(_fact_ids + list(_ed_to))  # starters stay different from each other
    for _eid, _fid in _ed_to.items():
        _have = {r["target_book_id"] for r in rel.get(_fid, [])} | set(_ed_to) | set(_fact_ids)
        for r in rel.pop(_eid, []):
            if r["target_book_id"] not in _have:
                rel.setdefault(_fid, []).append(r)
                _have.add(r["target_book_id"])
    _like_work = set(_like_ids) | {e for e, f in _ed_to.items() if f in _like_ids} | \
        {f for e, f in _ed_to.items() if e in _like_ids}
    _newer_than = None
    if _like_work and (_model_order == "newest" or re.search(r"(?:^| )(?:احدث|اجدد)(?: |$)|newer|more recent|recent",
                                                             normalize(user_messages[-1]))):
        _ly = [first_published(all_books[i]) for i in _like_work if i in all_books and first_published(all_books[i])]
        _newer_than = min(_ly) if _ly else None
    for _fid in _fact_ids:
        if _fid in _like_work and rel.get(_fid):
            rel[_fid] = sorted(rel[_fid], key=lambda r: r["relation_type"] != "similar")
    for f in list(facts[:3]):
        listed = []
        for r in rel.get(f["id"], []):
            t = all_books.get(r["target_book_id"])
            if not t or not is_available(t) or t["book_id"] not in strict:
                continue
            if _newer_than and f["id"] in _like_work and (first_published(t) or year_for_new(t) or 0) <= _newer_than:
                continue
            if f["id"] in _like_work and topic_phrases and not _fits_topic(t["book_id"]):
                continue   # «فيها كتب ومكتبات وأسرار زي جو ظل الريح»: the subject asked for comes before the likeness (case 5.2)
            if topic_strict and t["book_id"] not in topic_ids:
                continue   # «تربية النحل»: not «الغثيان» because it shares «يوميات» with the bee book (2026-10-13)
            if order_by and topic_phrases and not topic_strict and not _fits_topic(t["book_id"]):
                continue   # QADFIX10 (case 39.3 replay): «أقصر» on «الهجرة والغربة» — not Kundera's «البطء» because «الجهل» is his
            if pub_years and not pub_missing:
                _y, (_lo, _hi) = year_for_new(t), pub_years
                if _y is None or (_lo and _y < _lo) or (_hi and _y > _hi):
                    continue   # «روايات صدرت مؤخرًا»: not the same author's book of 2015 beside them (2026-10-13)
            label = relations.TYPES_AR.get(r["relation_type"], "")
            listed.append({"id": t["book_id"], "title": t["title"], "relation": label, "reason": r["reason_ar"]})
            if len(listed) <= (4 if f["id"] in _like_work else 2):   # QADFIX13 #1: the like-books are the answer
                matched = matching_subgenres(t, prefs, card=cards.get(t["book_id"]))
                why = ("تصنيفه الفرعي «" + "، ".join(matched) + "» يوافق طلبك" if matched else
                       f"{label} لـ«{f['title']}»: {r['reason_ar']}")
                add(t, why, "related")
            if len(listed) >= 4:
                break
        if listed:
            f["related"] = listed
    if _like_first:
        for b, why, match in candidates:
            add(b, why, match)
    # QADFIX10 (AGY-20261020-01, QAD 2026-10-20 case 23.2): «وأخوها ١١ سنة» after books for his 5-year-old sister — a reader
    # several years older or younger than the one the earlier books were for: those books come back only if made for his age
    _age_now, _age_before = pprefs.get("reader_age"), (audience_age(user_messages[:-1]) if len(user_messages) > 1 else None)
    _age_jump = _age_now is not None and _age_before is not None and abs(_age_now - _age_before) >= 3
    # QADFIX14 #6 (QAD 2026-10-23, case 6.3): «ايه هي! عندكم باقي الأجزاء؟» — when the visitor asks about named books and they
    # are here, the books shown earlier for another reason («الفهد» for an old «مسلسل» wish) are not brought back beside them
    # (the model gave «الفهد»'s series to «صديقتي المذهلة»)
    _asked_here = it.get("intent") == "about_book" and any(_m == "asked" for _w, _m in reason_by_id.values())
    for i in ([] if missing_title or _asked_here or it.get("whole_series_found") else shown[-4:]):
        if topic_strict and not _fits_topic(i):
            continue
        if _age_jump and pprefs.get("audience") == "children" and age_fit(all_books[i], _age_now) != 1:
            continue
        if hard and not all(h(all_books[i]) for h in hard):
            continue   # a book shown before that breaks a wish now in force («لشعراء خليجيين») is not offered again (QADFIX6)
        if age_allowed(cards.get(i), pprefs.get("audience"), pprefs.get("avoid") or (), all_books[i], pprefs.get("reader_age"), _cartoon_id(i)):
            add(all_books[i], "سبق عرضه في المحادثة", "shown")

    guidance = ({
        "recommend": "Recommend 2 to 4 available books from facts, with a brief reason for each from its facts, then ask one question to narrow the choice.",
        "clarify": "The request is still broad: ask one friendly question about mood, time, or a recent favourite. Do not suggest any book yet (show_book_ids empty).",
        "about_book": "Answer about the requested book using only its facts. If unavailable, say so and suggest an available alternative. Do not show new books unless asked.",
        "compare": "Compare the named books using only their facts and help the visitor choose.",
        "smalltalk": "Reply warmly in one sentence, then ask about the mood or type of reading they want.",
        "off_topic": "The request is outside reading: decline briefly and return to books. Do not show books.",
    } if language == "en" else {
        "recommend": "اقترح من 2 إلى 4 كتب متوفرة (available=true) من facts، مع سبب قصير لكل كتاب من صفاته في facts، ثم سؤال واحد يساعد يضيّق الاختيار.",
        "clarify": "الطلب ما زال عامًا: اسأل سؤالًا واحدًا لطيفًا يساعدك تختار (المزاج، الوقت، آخر كتاب عجبه، خفيف ولا عميق، متعة ولا معرفة). لا تقترح أي كتاب الآن (show_book_ids فارغة).",
        "about_book": "أجب عن الكتاب المسؤول عنه من معلوماته في facts فقط. إذا كان غير متوفر قل ذلك واقترح بديلًا متوفرًا. لا تعرض كتبًا جديدة إلا إذا طلب.",
        "compare": "قارن بين الكتب المذكورة من معلوماتها في facts فقط، وساعده يختار.",
        "smalltalk": "رد بلطف في جملة، ثم اسأله عن مزاجه أو نوع القراءة اللي يبغاها.",
        "off_topic": "الكلام خارج القراءة: اعتذر بلطف في جملة وارجع لسؤاله عن الكتب. لا تعرض كتبًا.",
    })[it["intent"]]
    if guidance_extra_en:
        guidance += (" Few Arabic copies fit this wish; the English copies in facts that fit may be offered — say each is in English."
                     if language == "en" else " النسخ العربية المناسبة لهذا الطلب قليلة؛ لك أن تعرض النسخ الإنجليزية المناسبة في facts وتقول إنها بالإنجليزية.")
    # QADFIX5 #4 (QAD 2026-10-17, case 47.1): «هل الطيب صالح سوداني؟» — the author's recorded country goes to the
    # model with an author question, instead of «ما عندي تفاصيل عن جنسيته»
    _arec_name = it.get("author_question") or ((author_request or {}).get("full_name") if author_only else None)
    _arec = authors.lookup(_arec_name) if _arec_name else None
    if _arec and (_arec.get("origin") or "").strip():
        guidance += (f" Our author record: {_arec['name_ar']} — country: {_arec['origin'].strip()}. If asked where the author is from, answer with it."
                     if language == "en" else
                     f" سجل المؤلف عندنا: {_arec['name_ar']} — البلد: {_arec['origin'].strip()}. إذا سُئلت عن جنسيته أو بلده فأجب منه.")
    if it.get("author_question"):
        guidance += (f" The visitor asked a question about the author {it['author_question']} (e.g. what he writes, whether he is a poet):"
                     " answer it first from facts only — say what kind of books we have by him here; if facts do not show what was asked"
                     " (e.g. poetry), say that his books here are of that other kind and that you have nothing showing it. Then show his books."
                     if language == "en" else
                     f" الزائر سأل سؤالًا عن المؤلف {it['author_question']} (مثل: وش يكتب، هل هو شاعر): أجب عنه أولًا من facts فقط —"
                     " قل نوع كتبه اللي عندنا؛ وإذا ما في facts ما يثبت المسؤول عنه (مثل الشعر) فقل إن كتبه عندنا من النوع الآخر وما عندك ما يدل على ذلك."
                     " ثم اعرض كتبه.")
    if it.get("lively_history"):
        # Ibrahim 2026-10-17 (QADFIX5 #7): history that is not boring = lively history books and historical novels
        guidance += (" The visitor wants history that is not boring: offer lively history books from facts and also historical novels"
                     " (a historical novel tells history through a story); say which is which."
                     if language == "en" else
                     " الزائر يبغى تاريخًا غير ممل: اقترح من facts كتب تاريخ بأسلوب ممتع وأيضًا روايات تاريخية"
                     " (الرواية التاريخية تحكي التاريخ بقصة)؛ وقل أيها كتاب تاريخ وأيها رواية.")
    if (streak >= 2 or skip_questions) and it["intent"] in ("clarify", "smalltalk"):
        guidance = ("You have asked enough questions: recommend 2 to 4 available books from facts with a brief reason for each."
                    if language == "en" else "سألت أسئلة كافية: اقترح الآن من 2 إلى 4 كتب متوفرة من facts مع سبب قصير لكل كتاب.")
    if starters:
        guidance = ("The request is open: present the books in facts briefly as different starting points (one short phrase each, from its facts), then ask one friendly question about mood or a recent favourite."
                    if language == "en" else
                    "الطلب مفتوح: اعرض الكتب الموجودة في facts كبدايات متنوعة (جملة قصيرة لكل كتاب من صفاته في facts) وضعها كلها في show_book_ids، ثم اسأل سؤالًا واحدًا لطيفًا عن المزاج أو آخر كتاب عجبه.")
    if topic_nothing and _more_only:
        named = "، ".join(topic_phrases)
        guidance = ((f"We have no other available book about «{named}» beyond the ones already shown: say so plainly in one sentence,"
                     " show no book (show_book_ids empty), and ask one question: a nearby subject, or what they liked in the ones shown.")
                    if language == "en" else
                    f"ما عندنا كتب متوفرة ثانية عن «{named}» غير اللي عرضتها: قل ذلك بصراحة في جملة، ولا تعرض أي كتاب"
                    " (show_book_ids فارغة)، واسأله سؤالًا واحدًا: يبغى موضوعًا قريبًا منه، أو وش اللي عجبه في اللي عرضته.")
    elif topic_nothing:
        named = "، ".join(topic_phrases)
        guidance = ((f"None of our available books is about «{named}» or close to it: say so plainly in one sentence, show no book"
                     " (show_book_ids empty), and ask one question that helps: what they mean by it, or what they would take instead.")
                    if language == "en" else
                    f"ما لقينا بين كتبنا المتوفرة كتابًا عن «{named}» ولا قريبًا منه: قل ذلك بصراحة في جملة، ولا تعرض أي كتاب"
                    " (show_book_ids فارغة)، واسأله سؤالًا واحدًا يوضح وش يقصد بالضبط أو وش يقبل بداله.")
    _months_asked = [p for p in topic_phrases if months_in(p)] if topic_phrases and not topic_nothing else []
    if _months_asked and not _EVENT_WORDS.search(normalize(" ".join(user_messages))):
        _season = _SEASONS[MONTH_SEASON[months_in(_months_asked[0])[0]]]
        guidance += ((f" «{_months_asked[0]}» here is the month itself (no war or event was named): the books in facts mention it"
                      f" or are close to its season ({_season[1]}); say which honestly, never claim a book is set in that month unless its facts say so.")
                     if language == "en" else
                     f" «{_months_asked[0]}» هنا الشهر نفسه (ما ذكر حربًا ولا حدثًا): كتب facts إما تذكره أو قريبة من أجواء {_season[0]}؛"
                     " قل ذلك بصراحة، ولا تقل إن كتابًا تدور أحداثه في هذا الشهر إلا إذا قالت معلوماته ذلك.")
    if missing_title:
        title = it["mentioned_titles"][0]
        found_like = any(n[2] for n in ref_notes)
        if language == "en":
            guidance = (f"The visitor asked for «{title}» and it is not available here: say so plainly first. "
                        + ("Then offer the books in facts, which were chosen for their likeness to it (each reason says how)." if found_like
                           else "Show no books; ask whether they would like something close to it and what they like about it."))
        else:
            guidance = (f"الزائر سأل عن «{title}» وهو غير متوفر عندنا حاليًا: قل ذلك بوضوح أولًا. "
                        + ("ثم اعرض الكتب الموجودة في facts، وهي مختارة لقربها منه (سبب كل كتاب يوضح وجه الشبه)، وضعها في show_book_ids." if found_like
                           else "لا تعرض أي كتاب، واسأله إن كان يبغى شيئًا قريبًا منه ووش اللي يعجبه فيه."))
        # the same work under another translation of its title («بيت آل هيل» / «أشباح هيل هاوس») is not «غير متوفر»
        guidance += (" If one of the books in facts is the very same work by the same author under another wording of its title, and you are sure of it, say we have it under that title instead of saying it is unavailable."
                     if language == "en" else
                     " إذا كان أحد كتب facts هو العمل نفسه للمؤلف نفسه بترجمة أخرى للعنوان، وأنت متأكدة من ذلك، فقولي إنه متوفر عندنا بهذا العنوان بدل «غير متوفر».")
    # the full list of a series' parts from backend/series_parts.json (sourced): «كم جزء سلسلة كينغسبريدج؟»,
    # «وش الجزء الثاني من اسم الريح؟» when we do not stock it (library open questions 15, 17; MGR 2026-10-17)
    _sp_entry, _sp_books = None, []
    if not author_only and chat_rules.part_asked(user_messages[-1]):
        for _a in (([series_anchor] if series_anchor else []) + ([part_book] if part_book else []) + list(asked_books)
                   + [all_books[i] for i in reversed(shown[-6:]) if i in all_books]):
            _sp_entry = series_parts.for_book(_a)
            if _sp_entry:
                _sp_books = [_a] + chat_rules.series_others(_a, all_books)
                break
    _sp_text = ""
    if _sp_entry:
        _sp_name = _sp_entry["names"][0] if language != "en" else (_sp_entry["names"][1] if len(_sp_entry["names"]) > 1 else _sp_entry["names"][0])
        _sp_list = series_parts.describe(_sp_entry, _sp_books, is_available, language)
        _sp_text = ((f" The visitor asks about the parts of the series «{_sp_name}». Its full list, from a checked source ({series_parts.count(_sp_entry)} books): {_sp_list}."
                     " Answer from this list: how many parts there are, which of them we have in stock (they are in facts), and the part asked about by its title and whether we have it."
                     " Name no title that is not in this list.")
                    if language == "en" else
                    (f" الزائر يسأل عن أجزاء سلسلة «{_sp_name}». قائمة أجزائها كلها من مصدر موثّق ({series_parts.count(_sp_entry)} كتب): {_sp_list}."
                     " أجب منها: كم عدد أجزائها، وأي منها متوفر عندنا (موجودة في facts)، والجزء المسؤول عنه باسمه وهل هو متوفر عندنا."
                     " لا تذكر أي عنوان غير الموجود في هذه القائمة، ولا تقل «ما عندي تفاصيل»."))
    if part_book:
        _n = part_book.get("series_position")
        _st = ("available" if is_available(part_book) else "not in stock now") if language == "en" else ("متوفر" if is_available(part_book) else "غير متوفر الآن")
        guidance += ((f" The visitor asked for another part of a series: in our records part {_n} is «{part_book['title']}» (in facts, {_st}). Answer with it; never name a part from your own knowledge.")
                     if language == "en" else
                     f" الزائر سأل عن جزء آخر من سلسلة: الجزء {_n} في سجلاتنا هو «{part_book['title']}» (موجود في facts، {_st}). أجب به، ولا تذكر اسم جزء من معرفتك.")
        guidance += _sp_text
    elif _sp_text:
        guidance += _sp_text
    elif series_anchor:
        _name = series_anchor.get("series_name") or series_anchor["title"]
        _have = [b["title"] for b in series_rest if is_available(b)]
        if ((series_anchor.get("series_name") and chat_rules.title_key(series_anchor["series_name"]) in chat_rules.title_key(user_messages[-1]))
                or it.get("whole_series_found")):
            # QADFIX11 (QAD 2026-10-21, case 8.1): «رباعية نابولي عندكم كلها؟» names the series: its first part counts too
            _have = ([series_anchor["title"]] if is_available(series_anchor) else []) + _have
        if it.get("whole_series_found") and _have:
            # QADFIX12 #5: the whole series asked for — what our records hold is a firm answer, not «I can't confirm»
            _nparts = len([series_anchor] + series_rest)
            guidance += ((f" The visitor asks whether we have the whole series: our records hold {_nparts} of its parts and {len(_have)} are in stock now;"
                          " say plainly which parts we have, with their numbers, and never say you cannot confirm what is in facts.")
                         if language == "en" else
                         f" الزائر يسأل هل عندنا السلسلة كاملة: سجلاتنا فيها {_nparts} من أجزائها والمتوفر الآن {len(_have)}؛"
                         " قل بوضوح أي الأجزاء عندنا بأرقامها، ولا تقل إنك ما تقدر تتأكد مما هو في facts.")
        guidance += (((f" The visitor asks about the other parts of «{_name}»: the ones in our records are in facts ({', '.join(_have)} in stock); name them with their part numbers.")
                      if _have else
                      (f" The visitor asks about the other parts of «{_name}»: none of them is in stock here now. Say that plainly (not «I have no details»), and that a bookseller can check or order them; do not name a title from your own knowledge."))
                     if language == "en" else
                     ((f" الزائر يسأل عن بقية أجزاء «{_name}»: الموجود منها في سجلاتنا في facts (المتوفر: {'، '.join(_have)})؛ اذكرها بأرقام أجزائها.")
                      if _have else
                      (f" الزائر يسأل عن بقية أجزاء «{_name}»: ما عندنا أي جزء ثاني منها متوفر الآن. قل ذلك بوضوح (لا تقل «ما عندي تفاصيل»)، وإن موظف المكتبة يقدر يتأكد أو يطلبها؛ ولا تذكر عنوان جزء من معرفتك.")))
    elif part_wanted and not author_only:
        guidance += (" The visitor asked for another part of a series, and our records do not list one for that book: say you have no record of it here and that a bookseller can check; do not name a title from your own knowledge."
                     if language == "en" else
                     " الزائر سأل عن جزء آخر من سلسلة، وسجلاتنا ما فيها جزء مسجّل لهذا الكتاب: قل إنه ما عندك في السجلات جزء آخر له وإن موظف المكتبة يتأكد، ولا تذكر عنوانًا من معرفتك.")
    for b in other_form:
        if b["book_id"] in seen and (not part_book or b["book_id"] != part_book["book_id"]):
            _lang = (" (a copy in English)" if b.get("language") == "en" else "") if language == "en" else (" (نسخة بالإنجليزية)" if b.get("language") == "en" else "")
            guidance += ((f" The book the visitor named is in our catalog as «{b['title']}»{_lang}, {'available' if is_available(b) else 'not in stock now'}: say that first; do not say we do not have it.")
                         if language == "en" else
                         f" الكتاب اللي سمّاه الزائر موجود في سجلاتنا بعنوان «{b['title']}»{_lang}، وهو {'متوفر' if is_available(b) else 'غير متوفر الآن'}: قل ذلك أولًا، ولا تقل إنه ما عندنا.")
    if pprefs.get("audience") in AGE_OK:
        if pprefs["audience"] == "young_adult":
            # QADFIX12 #7 (Ibrahim 2026-10-22): the books that fit the request, with a plain warning on adult content
            guidance += ((" The reader is a teenager. Recommend from facts the books that fit the request best, whichever shelf they are on;"
                          " for any book whose facts carry a content note (bold scenes, «Not suitable for children», strong language, drugs or"
                          " alcohol, self-harm), say plainly in a short phrase that it has content for adults. Do not mention any other title.")
                         if language == "en" else
                         (" القارئ مراهق: اقترح من facts الكتب الأنسب لطلبه، أيًا كان رفّها؛ وأي كتاب في معلوماته تنبيه محتوى"
                          " (مشاهد جريئة، «لا يناسب الأطفال»، ألفاظ قاسية، مخدرات أو كحول، إيذاء النفس) فنبّه عليه بوضوح في جملة قصيرة"
                          " إن فيه محتوى للكبار. لا تذكر كتابًا آخر من معرفتك."))
        else:
            guidance += ((" The reader is a child. Recommend only from facts; do not mention any other title from your knowledge.") if language == "en" else
                         (" القارئ طفل: اقترح فقط من facts (كلها مفحوصة لعمره). لا تذكر كتابًا آخر من معرفتك."))
    for ref, in_stock, found, outside in ref_notes:
        title = ref["title"]
        about = outside_books.brief(outside) if outside else None
        if about:
            # what we found outside about the visitor's book may be said, with its source; never as our stock
            src = " و".join(about.get("sources") or []) or "مصادر عامة"
            guidance += ((f" About «{title}» (not in our stock) we found outside ({', '.join(about.get('sources') or [])}): "
                          + json.dumps(about, ensure_ascii=False) + ". You may say in one short sentence who wrote it and what kind of book it is,"
                          " naming the source; never present it as available here, and never add anything that is not in this record.")
                         if language == "en" else
                         (f" عن «{title}» (غير موجود عندنا) لقينا من {src}: " + json.dumps(about, ensure_ascii=False)
                          + ". تقدر تقول في جملة قصيرة مين كتبه ووش نوعه وتذكر المصدر (مثل: «حسب Goodreads»)، ولا تقدّمه كأنه متوفر عندنا،"
                          " ولا تضف عنه شيئًا غير موجود في هذا السجل. الكتب المختارة للشبه في facts مع سببها."))
            if not found:
                guidance += (" None of our books is close enough to it: say so briefly and ask what they liked about it."
                             if language == "en" else " ما لقينا بين كتبنا شيئًا قريبًا منه كفاية: قل ذلك باختصار واسأله وش اللي عجبه فيه.")
            continue
        if language == "en":
            guidance += (f" The visitor wants something like «{title}»" + (" (we have it, but without reader details yet)" if in_stock else " (not available here)")
                         + (". Books chosen for that likeness are in facts with the reason; say the likeness is based on what is generally known about that book and do not describe its plot." if found
                            else ". We could not match it to our books: say so briefly and ask what they liked about it."))
        else:
            guidance += (f" الزائر يبغى كتابًا يشبه «{title}»" + ("، وهو موجود عندنا لكن بدون تفاصيل من القرّاء بعد" if in_stock else "، وهو غير متوفر عندنا")
                         + ("؛ الكتب المختارة للشبه موجودة في facts مع سببها. قل إن الشبه حسب ما هو معروف عن الكتاب عمومًا، ولا تصف أحداثه ولا تضف عنه معلومات." if found
                            else "؛ ما قدرنا نربطه بكتبنا: قل ذلك باختصار واسأله وش اللي عجبه فيه."))
    if ref_unmatched_only:
        title = ref_notes[0][0]["title"]
        guidance = (f"The visitor wants a book like «{title}», and we do not know enough about that book to find a similar one among ours. "
                    "Say so honestly in one sentence; do not describe or guess anything about that book; show no books (show_book_ids empty). "
                    "Then ask one question: what did they like in it (its topic? its style? its kind of book?). "
                    "In suggestions give 3 short, concrete answers they could tap, each naming a different kind of book or reading (a genre, a mood or a subject), written in the visitor's voice; never just «its topic» or «its style»."
                    if language == "en" else
                    f"الزائر يبغى كتابًا يشبه «{title}»، وما نعرف عن هذا الكتاب ما يكفي عشان نلقى له شبيهًا بين كتبنا: "
                    "قل ذلك بصراحة في جملة واحدة، ولا تصف الكتاب ولا تخمّن عنه شيئًا، ولا تعرض أي كتاب (show_book_ids فارغة). "
                    "ثم اسأل سؤالًا واحدًا: وش اللي عجبه فيه (موضوعه؟ أسلوبه؟ نوعه؟). "
                    "وفي suggestions ضع 3 إجابات قصيرة ومحددة يقدر يضغطها، كل وحدة تسمّي نوع كتب أو قراءة مختلف (تصنيف أو جو أو موضوع) بصوت الزائر، ولا تكتب «موضوعه» أو «أسلوبه» وحدها.")
    if similar_author and own_extra and not own_shown:
        guidance += (f" After the similar books you may add in one sentence that we also have {author_name}'s own books in facts (their reason says so)."
                     if language == "en" else f" بعد الكتب المشابهة لك أن تقول في جملة إن عندنا أيضًا من كتب {author_name} نفسه ما في facts (سببها يقول ذلك).")
    if similar_author and own_shown:
        guidance += (f" The visitor asked for books by «{author_name}» or similar ones: present his own available books first (their reason in facts says they are his), then the similar ones." if language == "en"
                     else f" الزائر طلب كتبًا لـ«{author_name}» أو مشابهة لها: اعرض كتبه المتوفرة أولًا (سببها في facts أنها من كتبه) ثم الكتب المشابهة.")
        if not similar_found:
            guidance += (" None of our researched books is close enough to his style: say so in one short sentence after his books." if language == "en"
                         else " ما لقينا بين كتبنا المبحوثة شيئًا قريبًا كفاية من أسلوبه: قل ذلك في جملة قصيرة بعد كتبه.")
    if absent_author:
        _known = "، ".join(f"«{t}»" for t in (absent_author.get("known_for") or [])[:3])
        _note = absent_author.get("style_note_ar") or ""
        guidance += ((f" «{absent_author.get('name_latin') or absent_author['name_ar']}» is a well-known author"
                      + (f" ({_note})" if _note else "") + (f", known for {_known}" if _known else "")
                      + ", but we have none of his books in stock now. Say that first in one sentence («we don't have any of his books in stock right now»; you may name one or two of those works), then offer the books in facts as the closest to him; never say we have him.")
                     if language == "en" else
                     (f" «{absent_author['name_ar']}» مؤلف معروف" + (f" ({_note})" if _note else "") + (f"، من أعماله {_known}" if _known else "")
                      + "، لكن ما عندنا من كتبه شيء الآن. قل ذلك أولًا بجملة واحدة (لك أن تذكر عملًا أو عملين من أعماله المذكورة هنا فقط)، ثم اعرض كتب facts على أنها الأقرب له، ولا تقل أبدًا إنه موجود عندنا."))
    if similar_author and (similar_found or not own_shown):
        if language == "en":
            guidance += (f" The visitor wants books similar to the author «{author_name}»."
                         + (" The books chosen for that likeness are in facts, each reason saying where it is close and where it differs. Present the likeness honestly (mood/genre, not identical style) and mention the stated difference" + ("." if own_shown or own_extra else "; do not describe the author's own books.") if similar_found
                            else " None of our researched books is close enough: say so plainly, show no books, and ask what they enjoy in that author's books (e.g. horror, mystery, humour, short fast reads)."))
        else:
            guidance += (f" الزائر يبغى كتبًا تشبه كتابات «{author_name}»."
                         + ("؛ الكتب المختارة للشبه موجودة في facts، وسبب كل كتاب يوضح وين الشبه ووين الاختلاف. قل الشبه بصراحة (في الجو أو النوع، مو نفس الأسلوب تمامًا) واذكر الاختلاف إن وُجد" + ("." if own_shown or own_extra else "، ولا تتكلم عن كتب المؤلف نفسه.") if similar_found
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
        guidance += ((f" The visitor asked about the subject: {named}. The books in facts whose reason says they are about it are the matches: present those first, and say plainly of any other book that it only shares the category."
                      if not topic_strict else
                      f" The visitor asked about the subject: {named}. Show only the books whose facts make clear they are about it; never a book that «only shares the category»: two that fit beat four that scatter. Mind the word's sense: a book whose title or summary uses it in another sense than the visitor's (towers for someone asking about the zodiac) is neither shown nor mentioned.") if language == "en" else
                     (f" الزائر يبغى موضوع: {named}. الكتب في facts التي يذكر سببها أنها عن هذا الموضوع هي المطابقة: قدّمها أولًا، وأي كتاب آخر تعرضه قل بصراحة إنه من نفس التصنيف فقط وليس عن الموضوع."
                      if not topic_strict else
                      f" الزائر يبغى موضوع: {named}. اعرض فقط الكتب اللي توضّح معلوماتها في facts أنها عن هذا الموضوع، ولا تعرض كتابًا «من نفس التصنيف فقط»: كتابان مطابقان أفضل من أربعة مشتتة. وانتبه لمعنى الكلمة: إذا جاءت في عنوان كتاب أو نبذته بمعنى آخر غير اللي يقصده الزائر (مثل «الأبراج» بمعنى المباني لمن يسأل عن الأبراج الفلكية)، فلا تعرضه ولا تذكره."))
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
    # only when the visitor asked for a kind of book (a guide, recipes, learning from scratch): with a plain
    # subject («شيء عن صراع النفس») the model copied the example and opened with «ما عندنا دليل عملي ولا
    # كتاب وصفات» (staff chat 2026-10-10)
    _kind_said = re.search(r"تعلم|اتعلم|نتعلم|خطوات|طريقه|كيف|دليل|وصفات|وصفه|طبخ|الصفر|مبتدئ|عملي|تمارين|تطبيق|"
                           r"guide|how to|learn|recipe|cook|beginner|step|practical|workbook", normalize(user_messages[-1]))
    if (topics_found or topics_mentioned) and _kind_said:
        # «أتعلم الاستثمار من الصفر» with only economics books, «كتب طبخ» with no recipe book (2026-10-07)
        guidance += (" If none of these books is the kind the visitor asked for (for example a practical beginner's guide or a recipe book, when we only have books that discuss the subject), say that plainly first, then offer the closest ones as what they are."
                     if language == "en" else
                     " إذا لم يكن بين هذه الكتب النوع الذي طلبه الزائر فعلًا (مثل دليل عملي للمبتدئ أو كتاب وصفات، وعندنا فقط كتب تتكلم عن الموضوع)، فقل ذلك بصراحة أولًا ثم اعرض الأقرب على حقيقته.")
    unsupported = [u for u in unsupported if u not in topics_found and u not in topics_mentioned and u not in topics_semantic]
    if unsupported:
        guidance += ((" The search cannot reliably apply these wishes: " + ", ".join(unsupported) + ". Do not claim the catalog lacks this data."
                      " If one of them is a subject (what the book should be about) and no book in facts is clearly about it by its summary or subjects, say plainly first that you found no book about it here, then offer the books in facts as what they are, never as being about it.") if language == "en" else
                     " البحث لا يستطيع تطبيق هذه الرغبات بدقة: " + "، ".join(unsupported) + ". لا تقل إن بياناتها غير موجودة في المكتبة."
                     " وإذا كانت إحداها موضوعًا (عن ماذا يكون الكتاب) وما في facts كتاب واضح أنه عنه من نبذته أو موضوعاته، فقل بصراحة أولًا إنك ما لقيت كتابًا عنه هنا، ثم اعرض كتب facts على حقيقتها، لا على أنها عنه.")
    # «وش التصنيفات اللي عندكم؟»، «كم كتاب عندكم؟»، «وش أكثر نوع؟»: the library itself, from the catalog
    # (QAD 2026-10-12, cases 8 and 44: «المتوفر عندي هنا روايات»، «ما عندي تفاصيل عن عدد الكتب»)
    if chat_rules.overview_asked(user_messages[-1]) and order_by != "oldest":
        # first, and said to be the library itself: after «رد بلطف ثم اسأله عن مزاجه» the model answered «ما أقدر
        # أساعدك بمعلومات عامة عن المكتبة» (live check after the 13:49 restart, 2026-10-13)
        guidance = (library_overview(all_books, language).strip()
                    + (" These numbers ARE this library: answer from them first; never say you have no information about the library. "
                       if language == "en" else " هذي أرقام المكتبة نفسها: أجب منها أولًا، ولا تقل أبدًا إنك ما عندك معلومات عن المكتبة. ")
                    + guidance)
    # prizes and screen adaptations asked for (2026-10-08): only what our records hold, said exactly
    honor_wish = honor_labels(prefs)
    if honor_wish and prefs.get("awards") and pool and not author_only:
        # the whole count, so «مين عندكم» / «وش فاز غيره» is not answered as if facts were all we have;
        # won by the book itself, by its author, or only on a list are counted apart (QA2 re-run, case 27)
        _kinds = {"book": set(), "author": set(), "list": set()}
        for b in pool:
            labels_ = honors.matches_award(b["book_id"], prefs["awards"], prefs.get("award_year"),
                                           prefs.get("award_min_year"), prefs.get("award_max_year"), prefs.get("award_results"))
            k = (normalize(b["title"]), normalize(b.get("author")))
            _kinds["book" if any(x.startswith("فاز") for x in labels_) else "author" if any(x.startswith("مؤلفه فاز") for x in labels_) else "list"].add(k)
        _kinds["author"] -= _kinds["book"]
        _kinds["list"] -= _kinds["book"] | _kinds["author"]
        _n_people = len({author_flow.display(b.get("author")) for b in pool})
        if _n_people > 1:
            nb, na, nl = len(_kinds["book"]), len(_kinds["author"]), len(_kinds["list"])
            guidance += ((f" In stock now for this prize wish: {nb} books that won it themselves, {na} more whose author won it, {nl} more that were only shortlisted / longlisted / nominated ({_n_people} authors in all). facts hold only some: when asked who / which / how many / what else, start with the number that fits the question (a «who won» / «which winners» question: the books that won it themselves), then name a few as examples and offer more; never call the books in facts all of them or count only those (QADFIX14 م1).")
                         if language == "en" else
                         f" المتوفر عندنا الآن لطلب الجائزة: {fmt_num(nb)} كتابًا فاز هو نفسه، و{fmt_num(na)} كتابًا فاز مؤلفه، و{fmt_num(nl)} كتابًا وصل للقوائم أو رُشّح فقط (لـ{fmt_num(_n_people)} مؤلفًا). facts فيها بعضها فقط: إذا سأل «مين» أو «أي» أو «كم» أو «وش غيره» فابدأ بالعدد المناسب لسؤاله (سؤال «وش فاز» = الفائزة هي نفسها) ثم اذكر بعضها أمثلة واعرض أن تذكر المزيد؛ لا تقل إن كتب facts هي كلها ولا تعدّها وحدها.")
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
        if absent_author:
            # «نزار قباني»: said as «a well-known author, none of his books now» (guidance above), not as a
            # name missing from our records (2026-10-12)
            missing_authors = [a for a in missing_authors if not (set(authors.key(a).split()) & set(authors.key(absent_author["name_ar"]).split()))
                               and authors.key(a) not in {authors.key(x) for x in authors.names_of(absent_author)}]
        if author_only and author_names and len(people) < 2:
            # the author flow already settled who is meant («تلستوي» -> «ايه» to «تقصد ليو تولستوي؟») and his
            # books are the pool: the spelling the visitor typed is never reported as a missing author
            # (QAFIX 2026-10-11, case 17; the same error came back twice after QA and QA2)
            missing_authors = []
        if missing_authors:
            # each named author is checked on his own: «درويش ونزار» with only Darwish in stock (2026-10-06)
            guidance += (" No available book was found under: " + ", ".join(missing_authors)
                         + ". Say plainly that you found no available books by that author in our current records and that a bookseller can double-check; do not say you lack details.")
    if pub_years:
        lo, hi = pub_years
        _span = (f"{lo}–{hi}" if lo and hi else f"from {lo} on" if lo else f"up to {hi}")
        _span_ar = (f"من {lo} إلى {hi}" if lo and hi else f"من {lo} وبعدها" if lo else f"حتى {hi}")
        if it.get("first_publication"):
            # QADFIX12 #11: the year the work FIRST came out, never our edition's year
            guidance += ((f" The visitor wants works first published {_span} (not new editions): " + (
                            "none available is recorded as first published then; say so plainly, and never present an edition year as the year a work first came out."
                            if pub_missing else "the books in facts first came out in that span (first_published); give that year, never edition_year as the first one."))
                         if language == "en" else
                         (f" الزائر يبغى أعمالًا صدرت أول مرة {_span_ar} (مو طبعات جديدة): " + (
                            "ما عندنا كتاب متوفر مسجّل أنه صدر أول مرة ضمن ذلك؛ قل ذلك بصراحة، ولا تقدّم سنة طبعة على أنها سنة صدور العمل أول مرة."
                            if pub_missing else "كتب facts صدرت أول مرة ضمن ذلك (first_published)؛ اذكر تلك السنة، ولا تقدّم edition_year على أنها سنة الصدور الأول.")))
        else:
            guidance += ((f" The visitor wants books published {_span}: " + ("none available is recorded with such a year; say so and ask what else they would like." if pub_missing
                           else "the books in facts came out in that span (first_published, or edition_year when first_published is missing); mention the year."))
                         if language == "en" else
                         (f" الزائر يبغى كتبًا منشورة {_span_ar}: " + ("ما لقينا كتابًا متوفرًا مسجلة سنة نشره ضمن ذلك؛ قل ذلك واسأله وش يبغى بداله." if pub_missing
                           else "كتب facts صدرت ضمن ذلك (first_published، أو edition_year إذا ما فيه first_published)؛ اذكر السنة.")))
    if edition:
        _ed = {"en": ("English", "الإنجليزية"), "ar": ("Arabic", "العربية")}[edition]
        guidance += ((f" The visitor wants copies in {_ed[0]}: " + ("none is available; say so and ask what else they would like." if edition_missing
                       else "the books in facts are copies in that language."))
                     if language == "en" else
                     (f" الزائر يبغى نسخًا باللغة {_ed[1]}: " + ("ما لقينا نسخة متوفرة بهذه اللغة؛ قل ذلك واسأله وش يبغى بداله." if edition_missing
                       else "كتب facts نسخها بهذه اللغة.")))
    if author_only and language != "en" and not edition and candidates:
        # «وش عندكم لموراكامي؟»: his books in Arabic are shown; when English copies of his are in stock too, the
        # reply ends by offering them (Ibrahim 2026-10-15: «يضيف بعد الجواب انه يوجد نسخ انجليزي. هل تريد ان تلقي نظرة عليها؟»)
        _ba = honors.load()["book_authors"]
        _aids = {a for b, _w, _k in candidates for a in _ba.get(b["book_id"], [])}
        _names = {author_flow.display(b.get("author")) for b, _w, _k in candidates}
        _shown_ids = {b["book_id"] for b, _w, _k in candidates}
        _en_copies = [b for b in all_books.values() if is_available(b) and ((b.get("language") or "ar").strip() or "ar") == "en"
                      and b["book_id"] not in _shown_ids
                      and (set(_ba.get(b["book_id"], [])) & _aids or author_flow.display(b.get("author")) in _names)]
        if _en_copies and all(((b.get("language") or "ar").strip() or "ar") != "en" for b, _w, _k in candidates[:4]):
            guidance += (f" وعندنا بعد {fmt_num(len(_en_copies))} من كتبه بنسخ إنجليزية غير اللي عرضتها"
                         f" ({'، '.join('«' + b['title'] + '»' for b in _en_copies[:4])}). بعد ردك اختم بجملة قصيرة:"
                         " «ويوجد بعد نسخ إنجليزية من كتبه، تبغى تلقي نظرة عليها؟» (بدل سؤالك الأخير).")
    if _EDITION_ASK.search(" ".join(normalize(user_messages[-1]).split())):
        # «عندكم ١٩٨٤؟ وش أحدث طبعة منها؟» (QAD 2026-10-15, case 44): the copies of that one book, in every language
        guidance += (" The visitor asks about the editions of the book he named: count only the copies of that same work in facts"
                     " (every language), give each one's publisher, language and edition_year, and name the newest by edition_year;"
                     " when a year is missing say it is not recorded. Never mention another book here."
                     if language == "en" else
                     " الزائر يسأل عن طبعات الكتاب اللي سمّاه: عدّ فقط نسخ هذا العمل نفسه في facts (بكل اللغات)، واذكر لكل نسخة ناشرها ولغتها"
                     " وسنة طبعتها (edition_year)، وسمِّ الأحدث حسب edition_year؛ وإذا ما فيه سنة فقل إنها غير مسجلة. لا تذكر هنا أي كتاب آخر.")
    if _PLATFORM.search(normalize(" ".join(user_messages[-2:]))):
        # «رواية تحولت لمسلسل على نتفلكس» (QAD 2026-10-15, case 33): which platform showed a film is not stored
        guidance += (" The visitor names a streaming platform: we do not record which platform showed an adaptation."
                     " Offer the adaptations in facts with their year, say plainly that the platform is not recorded, and never claim one was on it."
                     if language == "en" else
                     " الزائر ذكر منصة عرض (مثل نتفلكس): ما نسجّل المنصة التي عُرض عليها العمل. اعرض الأعمال المقتبسة في facts بسنتها،"
                     " وقل بوضوح إن المنصة غير مسجلة عندنا، ولا تدّعِ أن عملًا عُرض عليها.")
    if country:
        _place = country_place(list(country[1]))
        if country_missing:
            guidance += (f" The visitor asked for authors from {_place}: we found no available book by an author recorded as from there. Say so, add that our records of authors' countries are still being completed, and ask what else they would like."
                         if language == "en" else
                         f" طلب الزائر كتّابًا من {_place}: ما لقينا كتابًا متوفرًا لمؤلف مسجّل أنه من هناك. قل ذلك، وأضف أن بيانات بلدان المؤلفين عندنا ما زالت تُستكمل، واسأله وش يبغى بداله.")
        else:
            guidance += (f" The visitor asked for authors from {_place}: the books in facts are by authors recorded as from there."
                         if language == "en" else
                         f" طلب الزائر كتّابًا من {_place}: كتب facts لمؤلفين مسجّلين أنهم من هناك.")
            # QADFIX7 #3 (case 43.2): «اللي مؤلفها سعودي منها» — the books shown before that are not by an author from there
            _keys7 = [normalize(n) for n in country[1]]
            _o7 = book_origins()
            _not7 = [f["title"] for f in facts if str(f.get("why_it_matches") or "") in ("سأل عنه الزائر", "You asked about it.")
                     and not any(k in _o7.get(f["id"], "") for k in _keys7)]
            if _not7:
                guidance += ((" Of the books the visitor points to, none of these is by an author recorded as from " + _place + ": «" + "», «".join(_not7)
                              + "». Say so plainly first, then offer the other books in facts, which are." )
                             if language == "en" else
                             (f" من الكتب اللي يشير لها الزائر، هذه ليست لمؤلفين مسجّلين من {_place}: «" + "»، «".join(_not7)
                              + "». قل ذلك بوضوح أولًا، ثم اعرض بقية كتب facts وهي من هناك (بسنتها إن طلب سنة)."))
    if prefs.get("awards") and prefs.get("award_min_year") and wants_recent(user_messages[-2:]):
        guidance += (f" The visitor wants recent prizes: start with the newest, give each prize's year, and prefer the book's own prize over its author's."
                     if language == "en" else
                     f" الزائر يبغى جوائز حديثة: ابدأ بالأحدث واذكر سنة كل جائزة، وقدّم جائزة الكتاب نفسه على جائزة مؤلفه.")
    if child_theme:
        guidance += (f" The visitor asked for {child_theme} books for a child: we keep no such category, so these are children's books that touch on it; say so in a few words."
                     if language == "en" else
                     f" طلب الزائر كتب {child_theme} لطفل: ما عندنا تصنيف بهذا الاسم للصغار، وهذه كتب أطفال تتناول الموضوع؛ قل ذلك بكلمات قليلة.")
    if (clear_audience or said_for_adult(user_messages)) and not child_said(user_messages[-1]) and earlier_aud:
        guidance += (" The visitor now wants books for an adult, not for the child mentioned earlier: present these books for an adult reader and do not mention the child or the child's age."
                     if language == "en" else
                     " الزائر يبغى الآن كتبًا للكبار وليس للطفل المذكور قبل: قدّم الكتب لقارئ كبير ولا تذكر الطفل ولا عمره.")
    if any(str(w).startswith(("من نفس نوع «", "Same kind of book as «")) for w, _m in reason_by_id.values()):
        guidance += (" Some books in facts were chosen as the same KIND of book as the one the visitor named (their reason says so):"
                     " present them as «from the same kind (fantasy, magic …)», not as similar in story, and say nothing about the named book's plot."
                     if language == "en" else
                     " بعض كتب facts اختيرت لأنها من نفس نوع الكتاب اللي ذكره الزائر (سببها يقول ذلك): قدّمها على أنها «من نفس النوع» (فانتازيا، سحر …)"
                     " لا على أنها تشبه قصته، ولا تذكر شيئًا عن أحداث الكتاب اللي ذكره؛ وإذا كان غير متوفر عندنا فقل ذلك بجملة.")
    if language == "en" and not edition and any((all_books.get(f["id"]) or {}).get("language", "ar") != "en" for f in facts if f["id"] in all_books):
        guidance += (" The visitor writes in English: for each book you present whose copy_language is Arabic, say plainly that our copy is in Arabic."
                     " Prefer the books whose copy is in English.")
    if pronoun_book and is_available(pronoun_book):
        guidance += (f" The visitor's «it» is «{pronoun_book['title']}», the copy just shown (copy_language in facts); answer about that copy."
                     if language == "en" else
                     f" «ها» في سؤال الزائر تعني «{pronoun_book['title']}»، النسخة اللي عرضتها له للتو (لغة نسختها في facts)؛ أجب عنها هي.")
    elif pronoun_book:
        _pp = pronoun_book.get("price")
        guidance += ((f" The visitor's «it» is «{pronoun_book['title']}», the book he asked about, which is NOT in stock now (not the alternative shown before)."
                      + (f" Its recorded price is {_pp:g} SAR (from the last time we had it): say it is that price when available." if _pp else "")
                      + " We have no date for it to arrive: say so, and that a member of staff can check or order it.")
                     if language == "en" else
                     (f" «ها» في سؤال الزائر تعني «{pronoun_book['title']}» اللي سأل عنه، وهو غير متوفر الآن (وليس البديل المعروض قبل)."
                      + (f" سعره المسجّل عندنا {_pp:g} ريال (آخر مرة كان متوفرًا): اذكره كسعره المعتاد." if _pp else "")
                      + " وما عندنا موعد لوصوله: قل ذلك، وإن موظف المكتبة يقدر يتأكد أو يطلبه."))
    if chat_rules.parent_reader(user_messages):
        guidance += (" The reader is the grown-up asking (a parent or a teacher), and children or teenagers are only the subject:"
                     " offer books for adults about that subject (parenting, psychology, education), never children's or young adult books;"
                     " if no book in facts is about it, say so plainly first, then offer the closest as what they are."
                     if language == "en" else
                     " القارئ هو الكبير السائل نفسه (أم أو أب أو معلم)، والأطفال أو المراهقون موضوع الكتاب فقط: اعرض كتبًا للكبار عن هذا الموضوع"
                     " (تربية، علم نفس، تعليم)، ولا تعرض كتب أطفال أو يافعين؛ وإذا ما في facts كتاب عنه فقل ذلك بصراحة أولًا ثم اعرض الأقرب على حقيقته.")
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
        if langs_left:
            _ln_en = ", ".join(LANG_NAMES_EN.get(l, l) for l in langs_left)
            _ln_ar = " و".join("الأدب " + LANG_NAMES.get(l, l).replace("ية", "ي", 1) if LANG_NAMES.get(l, "").endswith("ية") else LANG_NAMES.get(l, l) for l in langs_left)
            guidance += (f" Earlier the visitor asked for {_ln_en} literature; none of those books is about this subject, so these come from other literatures. Say that in one short sentence first, and end by asking whether he would rather stay with {_ln_en} literature."
                         if language == "en" else
                         f" الزائر كان طالب {_ln_ar} قبل؛ ما لقينا منه كتابًا عن هذا الموضوع، فهذه الكتب من آداب أخرى. قل ذلك في جملة قصيرة أولًا، واختم بسؤاله: يفضّل يبقى في {_ln_ar} ولا يناسبه هذا؟")
    en = language == "en"
    if publisher_left:
        guidance += (f" The visitor was looking at books from «{publisher_left}», and none of them fits this request: say that in one short sentence; the books in facts are from other publishers."
                     if en else
                     f" الزائر كان يتصفح إصدارات «{publisher_left}»، وما فيها ما يطابق هذا الطلب: قل ذلك في جملة قصيرة؛ كتب facts من دور نشر أخرى.")
    if publisher_unknown:
        guidance += (f" The visitor asked for the publisher «{publisher_unknown}»: we have no books of theirs in stock now. Say exactly that first; never present a book as theirs, and do not treat the publisher's name as a subject. Then ask what they like to read."
                     if en else
                     f" الزائر سأل عن دار النشر «{publisher_unknown}»: ما عندنا الآن كتب متوفرة من إصداراتها. قل ذلك بوضوح أولًا، ولا تقدّم أي كتاب على أنه منها، ولا تعامل اسم الدار كموضوع. ثم اسأله وش يحب يقرأ.")
    if price_max:
        _n_in = len(pool)
        guidance += ((f" The visitor will pay up to {price_max} SAR: " + (f"every book in facts is within it (price_sar); say each price. {_n_in} available books fit the request at that price." if not price_missing
                       else "nothing in the request is that cheap; say so, and give the price of what you offer."))
                     if en else
                     (f" الزائر حدّد سعرًا لا يتجاوز {fmt_num(price_max)} ريالًا: " + (f"كل كتب facts ضمن هذا الحد (price_sar)؛ اذكر سعر كل كتاب. المتوفر ضمن طلبه بهذا السعر {fmt_num(_n_in)} كتابًا، فلا تقل إنه ما عندنا." if not price_missing
                       else "ما لقينا ضمن طلبه كتابًا بهذا السعر؛ قل ذلك، واذكر سعر ما تعرضه.")))
    if pages_max or pages_min:
        _lim = (f"{pages_max} pages or fewer" if pages_max else f"at least {pages_min} pages") if en else (
            f"لا يتجاوز {fmt_num(pages_max)} صفحة" if pages_max else f"لا يقل عن {fmt_num(pages_min)} صفحة")
        guidance += ((f" The visitor set the length as a number: {_lim}. " + (f"Every book in facts is within it; say each page count (pages_approx). {pages_n} available books fit." if not pages_missing
                       else "Nothing in the request has that many pages recorded; say so."))
                     if en else
                     (f" الزائر حدّد الطول بعدد الصفحات: {_lim}. " + (f"كل كتب facts ضمن هذا الحد؛ اذكر عدد صفحات كل كتاب (pages_approx). المتوفر ضمن طلبه {fmt_num(pages_n)} كتابًا، فلا تقل إنه ما عندنا إلا كتاب أو كتابين." if not pages_missing
                       else "ما لقينا ضمن طلبه كتابًا بهذا العدد من الصفحات المسجلة؛ قل ذلك.")))
    if order_by == "oldest":
        _ol = "، ".join(f"«{b['title']}» ({first_published(b)})" for b in order_list)
        guidance += ((" The visitor asks for the oldest: by the year each work was first published in our records, from the oldest: "
                      + ", ".join(f"«{b['title']}» ({first_published(b)})" for b in order_list) + ". Answer with these titles and years only; name no other year.")
                     if en else f" الزائر يسأل عن الأقدم: حسب سنة صدور العمل أول مرة في سجلاتنا، من الأقدم: {_ol}. أجب بهذه العناوين وسنواتها فقط، ولا تذكر سنة غيرها.")
    if order_by == "cheapest":
        guidance += (f" The visitor asked for the cheapest: facts are sorted from the cheapest within the request, and the cheapest is «{order_first['title']}» at {order_first['price']:g} SAR. Say each price; never call another book the cheapest."
                     if en else
                     f" الزائر سأل عن الأرخص: كتب facts مرتبة من الأرخص ضمن طلبه، وأرخصها «{order_first['title']}» بسعر {order_first['price']:g} ريال. اذكر سعر كل كتاب، ولا تقل عن كتاب آخر إنه الأرخص.")
    elif order_by == "shortest":
        guidance += (f" The visitor asked for the shortest: facts are sorted from the shortest within the request, and the shortest is «{order_first['title']}» ({order_first['page_count']} pages). Present them in that order with their page counts (pages_approx), naming the shortest first even if it was shown before."
                     if en else
                     f" الزائر سأل عن الأقصر: كتب facts مرتبة من الأقصر ضمن طلبه، وأقصرها «{order_first['title']}» ({fmt_num(order_first['page_count'])} صفحة). قدّمها بهذا الترتيب واذكر عدد صفحات كل كتاب (pages_approx)، وابدأ بالأقصر حتى لو سبق عرضه.")
    elif shorter_none:
        guidance += (f" The visitor wants something shorter than the books shown (the shortest had {shorter_none} pages): we have nothing shorter of that kind. Say so plainly; do not offer a book as long or longer."
                     if en else
                     f" الزائر يبغى شيئًا أقصر مما عُرض (أقصرها {fmt_num(shorter_none)} صفحة): ما عندنا أقصر من هذا النوع. قل ذلك بوضوح، ولا تقدّم كتابًا بنفس الطول أو أطول.")
    elif order_by == "shorter":
        guidance += (" The visitor wants something shorter: the books in facts are shorter than the ones shown before, sorted from the shortest; say each page count (pages_approx)."
                     if en else
                     " الزائر يبغى شيئًا أقصر: كتب facts أقصر مما عُرض قبل ومرتبة من الأقصر؛ اذكر عدد صفحات كل كتاب (pages_approx).")
    if order_by in ("shortest", "shorter") and len(order_list) >= 2:
        # QADFIX5 #13 (QAD 2026-10-17, case 12.2): «أقصرها 104، بعدها 241، ثم 157» — the order is spelled out
        guidance += ((" Exact order, shortest first: " + "; ".join(f"«{b['title']}» {b['page_count']} pages" for b in order_list) + ".")
                     if en else (" الترتيب الصحيح من الأقصر: " + "، ".join(f"«{b['title']}» {fmt_num(b['page_count'])} صفحة" for b in order_list) + "."))
    if no_genres:
        # «رواية عن الدولة العثمانية بس مش كتاب تاريخ»: the model read «لا تعرض شيئًا منها» as «show none of
        # facts» and refused the historical novels (QAD 2026-10-13, case 50)
        _hist = "تاريخ" in no_genres
        guidance += ((" The visitor does not want these kinds of book: " + ", ".join(no_genres) + ". Every book in facts is of "
                      "another kind, so they all respect that wish: offer them normally."
                      + (" A historical novel is a novel, not a history book: offer it." if _hist else ""))
                     if en else " الزائر ما يبغى هذه الأنواع: " + "، ".join(no_genres) + ". كل كتب facts من أنواع أخرى، فهي كلها "
                     "تحترم طلبه: اعرضها عادي." + (" الرواية التاريخية رواية وليست كتاب تاريخ: اعرضها ولا تعتذر عنها." if _hist else ""))
    if no_country:
        _np = country_place(no_country)
        guidance += (f" The visitor does not want authors from {_np}: the books in facts are by authors recorded as from other countries."
                     if en else f" الزائر ما يبغى كتّابًا من {_np}: كتب facts لمؤلفين مسجّلين من بلدان أخرى.")
    if topic_title_ids:
        guidance += (" Some books in facts stand on another shelf but their very title is the subject asked for: present those first."
                     if en else " بعض كتب facts من رف آخر لكن عنوانها نفسه عن الموضوع المطلوب: قدّمها أولًا.")
    if any(f.get("publisher") in ("غير مسجّل عندنا", "not recorded") for f in facts if reason_by_id.get(f["id"], ("", ""))[1] == "asked"):
        guidance += (" One copy the visitor asks about has no publisher recorded (publisher: not recorded): say so for that copy, and never give it the other copy's publisher."
                     if en else " نسخة من الكتاب المسؤول عنه ناشرها غير مسجّل عندنا (publisher: «غير مسجّل عندنا»): قل ذلك عن هذه النسخة، ولا تنسبها أبدًا لناشر النسخة الأخرى.")
    _write = chat_rules.write_asked(user_messages[-1])
    if _write:
        guidance += ((" The visitor asks you to write a " + {"poem": "poem", "story": "story"}.get(_write, "text") + " yourself: say kindly in one line that you"
                      " help choose books and do not write texts, then offer books of that kind from facts (poetry for a poem).")
                     if en else
                     (" الزائر يطلب منك تكتبين له " + {"poem": "قصيدة", "story": "قصة"}.get(_write, "نصًا") + " بنفسك: اعتذري بلطف بجملة واحدة (أنتِ تساعدين"
                      " في اختيار الكتب ولا تكتبين نصوصًا)، ثم اقترحي من facts كتبًا من هذا النوع (دواوين شعر للقصيدة)."))
    if any(f.get("same_author_maybe_same_work") for f in facts):
        guidance += (" A book asked about lists same_author_maybe_same_work: copies by the same author under another title or language."
                     " Count only those that ARE the same work (the English «Animal Farm» is «مزرعة الحيوان»; «مزرعة الحيوانات» is another"
                     " translation of it), give each one's language and number of copies, and leave the others out."
                     if en else
                     " في الكتاب المسؤول عنه قائمة same_author_maybe_same_work: نسخ للمؤلف نفسه بعنوان أو لغة أخرى. احسب منها فقط ما هو"
                     " العمل نفسه (Animal Farm الإنجليزية هي «مزرعة الحيوان»، و«مزرعة الحيوانات» ترجمة أخرى له)، واذكر لغة كل نسخة وعدد نسخها،"
                     " واترك الباقي.")
    if it.get("no_age_fit") is not None:
        _a = it["no_age_fit"]
        guidance += ((f" No book in facts is made for a {_a}-year-old in what was asked (language or kind): say that plainly first, then"
                      " give the closest ones with the ages they are for, or offer the same kind in the other language.")
                     if en else
                     (f" ما في facts كتاب مناسب لعمر {fmt_num(_a)} سنوات بالطلب نفسه (اللغة أو النوع): قل ذلك بوضوح أولًا، ثم اعرض الأقرب"
                      " واذكر العمر الذي يناسبه، أو اعرض النوع نفسه باللغة الثانية."))
    if second_look_note:
        guidance += second_look_note
    if q7.get("nopages"):
        guidance += ((f" The book(s) about the subject asked for have no page count recorded: {', '.join(q7['nopages'])}. Say it is the one we have on that subject and that its page count is not recorded; do not say you found no book on the subject."
                      if en else f" الكتاب الموجود عندنا عن الموضوع ما عندي عدد صفحاته: «{'»، «'.join(q7['nopages'])}». قل إنه الموجود عندنا عن الموضوع وإن عدد صفحاته غير مسجل، ولا تقل إنك ما لقيت كتابًا عن الموضوع."))
    if q7.get("series"):
        _sl = "، ".join(f"«{n}» ({k})" for n, k in q7["series"])
        guidance += ((" The visitor wants a series: the facts are series recorded in our catalog with the number of parts in stock (" + _sl + "). Name the series and how many parts we have; do not say you cannot confirm it is a series."
                      if en else f" الزائر يبغى سلسلة: كتب facts من سلاسل مسجلة عندنا، مع عدد أجزائها المتوفرة ({_sl}). اذكر اسم السلسلة وكم جزء عندنا منها، ولا تقل إنك ما تقدر تؤكد أنها سلسلة."))
    if q7.get("women"):
        guidance += ((f" The visitor asks for women authors: our author records say these are by women: {', '.join(q7['women'])}. Offer them as such; any other book in facts is not confirmed as a woman's — say so if you name it."
                      if en else f" الزائر يبغى كتبًا لمؤلفات: سجلات المؤلفين عندنا تقول إن هذه الكتب لنساء: «{'»، «'.join(q7['women'])}». قدّمها على هذا الأساس؛ وأي كتاب آخر في facts غير مؤكد أنه لامرأة، فقل ذلك إن ذكرته."))
    elif women_wish(user_messages[-1]) and not author_only:
        # QADFIX7 #12 (case 49.1): the author's gender is not recorded in our data
        guidance += (" The author's gender is not recorded in our catalog: say so plainly, and do not call an author a woman or a man unless the facts say it."
                     if en else " جنس المؤلف غير مسجل عندنا: قل ذلك بصراحة، ولا تقل عن كاتب إنه امرأة أو رجل إلا إذا كان ذلك في facts.")
    guidance += f" Today's date is {time.strftime('%Y-%m-%d')}."
    if _cmp_ids:
        _ct = "»، «".join(all_books[i]["title"] for i in sorted(_cmp_ids) if i in all_books)
        if _like_ids:
            guidance += ((f" The visitor names «{_ct}» as an example of the style and atmosphere wanted: use its facts for that, offer other"
                          " books in that style, and do not offer it (keep it out of show_book_ids)."
                          if language == "en" else f" الزائر ذكر «{_ct}» كمثال على الأسلوب والأجواء اللي يبغاها: استعمل معلوماته لذلك،"
                          " واقترح كتبًا أخرى بنفس الأسلوب، ولا تقترحه هو (لا تضعه في show_book_ids)."))
        else:
            guidance += ((f" The visitor names «{_ct}» only to compare with it: use its facts for the comparison, but do not offer it (keep it out of show_book_ids)."
                          if language == "en" else f" الزائر ذكر «{_ct}» للمقارنة فقط: استعمل معلوماته للمقارنة ولا تقترحه (لا تضعه في show_book_ids)."))
    if _example_ids & {f["id"] for f in facts}:
        _et = "»، «".join(all_books[i]["title"] for i in sorted(_example_ids) if i in all_books)
        guidance += ((f" The visitor gave «{_et}» as examples of what he wants and would take them: offer them first (in show_book_ids), then others of that kind.")
                     if language == "en" else
                     (f" الزائر ذكر «{_et}» أمثلة على ما يبغاه ويقبلها: اقترحها أولًا (ضعها في show_book_ids) ثم غيرها من نفس النوع."))
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
    valid = {f["id"] for f in facts if f["available"] and f["id"] not in _cmp_ids
             and ((f["id"] in suitable and f["id"] in strict)
                  or ((reason_by_id[f["id"]][1] == "asked" or f["id"] in topic_title_ids)
                      and age_allowed(cards.get(f["id"]), pprefs.get("audience"), pprefs.get("avoid") or (),
                                      all_books.get(f["id"]), pprefs.get("reader_age"), _cartoon_id(f["id"])))
                  or f["id"] in _named_card_ok)}
    ids = []
    for i in resp["show_book_ids"]:
        if i in valid and i not in ids:
            ids.append(i)
    # the visitor named it and the reply talks about it: its card is shown (library trial, 2026-10-08:
    # «أن تقرأ لوليتا في طهران» was described but not shown)
    _said_reply = " " + " ".join(normalize(resp["reply"]).split()) + " "
    _one_asked = it["intent"] == "about_book" and len([b for b in asked_books if is_available(b)]) == 1
    for b in reversed(asked_books):
        # also the one book in stock the visitor asked about by name, even when the reply does not repeat its
        # title («إيه، متأكدة، هذا هو عنوان الرواية»; QAFIX re-run, case 23)
        if (b["book_id"] in valid and b["book_id"] not in ids
                and (" " + " ".join(normalize(b["title"]).split()) + " " in _said_reply or (_one_asked and is_available(b)))):
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
    # the cards and the text agree (QADFIX4, QAD 2026-10-16 cases 21.1, 25.1, 40.2; open since 10-15 m3): a book the
    # reply names from facts gets its card, and when the reply names some of the shown books, a shown book it does
    # not name at all («الحرية» beside «شقة الحرية غير متوفرة») is left out
    # a title counts as named when it stands on its own in the reply: «الجليد» inside «أغنية الجليد والنار» is not
    # Sonallah Ibrahim's novel (QADFIX4 re-run, case 46.1)
    _quoted = [chat_rules.title_key(q) for q in re.findall(r"«([^»]{2,120})»", resp["reply"])]
    _reply_key = " " + chat_rules.title_key(re.sub(r"«[^»]{2,120}»", " | ", resp["reply"])) + " "
    def _named_in_reply(i):
        title = all_books[i].get("title") or ""
        forms = {chat_rules.title_key(title)}
        head = re.split(r"\s*[:：(\[–—-]\s*|\s+\d+\s*$", title)[0]
        if head and head != title:
            forms.add(chat_rules.title_key(head))
        tk = chat_rules.title_key(title)
        if len(tk.split()) >= 4:
            forms.add(" ".join(tk.split()[:3]))
        forms = {f for f in forms if f and len(f) >= 3}
        # QADFIX11 (model replay, case 8.3): «حكاية الطفلة الضائعة» in the reply is not the one-word title «حكاية»
        if any(q == f or (q.startswith(f + " ") and len(f.split()) >= 2) for q in _quoted for f in forms):
            return True
        return any(len(f.split()) >= 2 and (" " + f + " ") in _reply_key for f in forms)
    for f in facts:
        i = f["id"]
        _k = (" ".join(normalize(all_books[i]["title"]).split()), " ".join(normalize(all_books[i].get("author")).split())) if i in all_books else None
        if (len(ids) < 4 and f["available"] and i not in ids and i in all_books and _k not in _seen_books and _named_in_reply(i)
                and age_allowed(cards.get(i), pprefs.get("audience"), pprefs.get("avoid") or (), all_books.get(i),
                                pprefs.get("reader_age"), _cartoon_id(i))):
            ids.append(i)
            _seen_books.add(_k)
    # QADFIX5 #6 (QAD 2026-10-17, case 7.1): a stocked book the reply names in «…» that is not in facts (the model
    # knew «ياسمين والمولود الجديد» but the search had not passed it) gets its card too, when its whole title
    # matches one book in stock and it suits the reader
    if len(ids) < 4 and _quoted:
        _fact_ids = {f["id"] for f in facts}
        _by_key = {}
        for b in all_books.values():
            if is_available(b):
                _by_key.setdefault(chat_rules.title_key(b.get("title") or ""), []).append(b)
        for q in _quoted:
            hits = [b for b in _by_key.get(q, []) if b["book_id"] not in _fact_ids and b["book_id"] not in ids]
            if len(hits) != 1 or len(q) < 4:
                continue
            b = hits[0]
            _k = (" ".join(normalize(b["title"]).split()), " ".join(normalize(b.get("author")).split()))
            if any(chat_rules.title_key(all_books[i].get("title") or "") == q for i in ids if i in all_books):
                continue   # «الجزيرة» already shown as a card from facts: not a second card of it (QADFIX6, case 30.2)
            if _k in _seen_books or not age_allowed(cards.get(b["book_id"]), pprefs.get("audience"), pprefs.get("avoid") or (),
                                                     b, pprefs.get("reader_age"), _cartoon_id(b["book_id"])):
                continue
            ids.append(b["book_id"])
            _seen_books.add(_k)
            reason_by_id[b["book_id"]] = ("ذكرته لك في الرد، وهو متوفر عندنا." if language != "en" else "Named in the reply, and it is in stock.", "direct")
            if len(ids) >= 4:
                break
    if chat_rules.says_not_found(resp["reply"]):
        # «شقة الحرية غير متوفرة» with the card of «الحرية» (a loose match of the title asked for) that the reply never
        # names: that card goes (QADFIX4, QAD 2026-10-16 case 25.1)
        ids = [i for i in ids if not (reason_by_id[i][1] == "asked" and not _named_in_reply(i)
                                      and chat_rules.title_key(all_books[i].get("title") or "") not in
                                      {chat_rules.title_key(t) for t in it["mentioned_titles"]})]
    # still asking (fewer than two questions on a vague request): no book yet, whatever the model chose
    if it["intent"] == "clarify" and streak < 2 and not skip_questions and not asked_books:
        # … except a book the question itself names («عندنا «ذواتا أفنان» من منشورات حياة، تبغى شي مثله؟»): the text and
        # the cards agree (QADFIX6, QAD 2026-10-18 case 22.1; open since 10-15 m3)
        ids = [i for i in ids if _named_in_reply(i)]
    if ref_unmatched_only:  # nothing chosen for likeness: no book, whatever the model chose
        ids = []
    if topic_nothing:       # nothing about the subject asked for: no book; the visitor is asked instead
        # … except a book shown earlier that the reply names again as the closest (QADFIX5 #9, case 36.3: the parent
        # guide named without its card)
        ids = [i for i in ids if i in shown and _named_in_reply(i)]
    if _cmp_ids:
        # QADFIX10 (case 11.2, model replay): the reply names «سر الزعفرانة» to compare — its card is not added back by the
        # «a book the reply names gets its card» step either
        ids = [i for i in ids if i not in _cmp_ids]
    # QADFIX14 ب6 (QAD 2026-10-23, case 42.1): two editions of one work are one card when the visitor did not ask about the
    # book itself (its editions are listed under it in the reply)
    _works, _ids_w = set(), []
    for i in ids:
        if i in all_books:
            _wk = (chat_rules.title_key(all_books[i].get("title") or ""), author_flow.display(all_books[i].get("author")))
            if reason_by_id.get(i, ("", ""))[1] != "asked" and _wk in _works:
                continue
            _works.add(_wk)
        _ids_w.append(i)
    ids = _ids_w
    books = []
    for i in ids[:4]:
        why, match = reason_by_id[i]
        # a book shown earlier that still fits is not «a book you asked about» (2026-10-06)
        reason = (why if match in ("direct", "filler", "related", "reference")
                  else ("Shown to you earlier, and it still fits." if language == "en" else "عرضته لك قبل، وما زال يناسب طلبك.") if match == "shown"
                  else "سألت عنه، وهو متوفر الآن.")
        books.append(dict(all_books[i], reason=reason, match=match))
    if not books and chat_rules.says_not_found(resp["reply"]):
        # QADFIX5 #15 (QAD 2026-10-17, case 16.1): «الحزام غير موجود» with chips about a book («كم سعره؟ وين موقعه؟») —
        # no card means nothing to ask about it; the chips offer the way on instead
        _about_it = re.compile(r"(?:عنه|عنها|سعره|سعرها|موقعه|موقعها|صفحاته|صفحاتها|مؤلفه|مؤلفها|its |it\?|about it|price|where is it)")
        _kept = [c for c in (resp.get("suggestions") or []) if not _about_it.search(normalize(c))]
        resp["suggestions"] = _kept or (["Something similar", "Suggest something to my taste"] if language == "en"
                                         else ["كتب قريبة منه", "رشّحي لي حسب ذوقي"])
    debug["model_book_ids"] = resp["show_book_ids"]
    debug["reference"] = _ref_dbg
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
                                    pub_years if pub_years and not pub_missing else None,
                                    extra=_extra_badges),
            "_debug": debug}


def _sl_hints(message, all_books):
    """Catalog authors who share a name word with the visitor's message (for the second look)."""
    words = set()
    for w in normalize(message).split():
        for v in (w, w[1:] if len(w) > 3 and w[0] in "لبو" else None):
            if v and len(v) >= 3:
                words.add(strip_al(v))
    by_word = {}
    for b in all_books.values():
        if not is_available(b):
            continue
        for person in author_flow._people(author_flow.display(b.get("author") or "")):
            for w in normalize(person).split():
                if strip_al(w) in words:
                    by_word.setdefault(strip_al(w), set()).add(person)
    out = []
    for w, names in by_word.items():
        if len(names) <= 8:   # a common given name («محمد») says nothing
            out += sorted(names)
    return out[:12]


def _sl_find(reading, all_books):
    """Books in stock for one reading of the second look (catalog lookups only, no model)."""
    q, kind, ids = reading["query"], reading["kind"], []
    parts = [p.strip() for p in re.split(r"[،,;/]|\s+(?:او|أو|or)\s+", q) if p.strip()]
    if kind in ("title", "series"):
        for p in parts or [q]:
            for b in match_titles([p], all_books, max_rank=1) + chat_rules.series_books(p, all_books)[:6]:
                ids.append(b["book_id"])
    elif kind == "author":
        for p in parts or [q]:
            rec = authors.lookup(p)
            if rec:
                ids += sorted(authors.book_ids(rec["author_id"]))
            pk = set(normalize(p).split())
            if len(pk) >= 2:
                ids += [b["book_id"] for b in all_books.values()
                        if pk <= set(normalize(author_flow.display(b.get("author") or "")).split())]
    else:
        idx = topics.index(all_books)
        for p in parts[:5]:
            if not topics.searchable([p], idx):
                continue
            ids += [b["book_id"] for b in all_books.values() if is_available(b) and topics.match([p], b["book_id"], idx)[0] > 0]
    out = []
    for i in ids:
        if i in all_books and is_available(all_books[i]) and i not in out:
            out.append(i)
    return out[:12]


def run_chat(messages, active=None, language="ar", clear_audience=False, drop_context=None):
    """One conversation turn. When the first answer says what was asked for is not in stock, the request is
    looked at a second time (second_look.py, QADFIX4: Ibrahim 2026-10-16) before that answer is given.
    A book in stock is never called «غير متوفر» in the answer given (QADFIX6, QAD 2026-10-18 cases 30.2, 46.2, 46.3)."""
    res = _run_chat_looked(messages, active, language, clear_audience, drop_context)
    try:
        if isinstance(res, dict) and res.get("reply"):
            prev_ids = []
            for m in messages[-CHAT_MAX_MESSAGES:] if isinstance(messages, list) else []:
                if isinstance(m, dict) and m.get("role") == "assistant" and isinstance(m.get("book_ids"), list):
                    prev_ids = [i for i in m["book_ids"] if isinstance(i, int)]
            all_books = {b["book_id"]: dict(b) for b in list_books(only_active=False)}
            fixed, changed = chat_rules.fix_false_unavailable(res["reply"], all_books, prev_ids, language)
            if changed:
                res.setdefault("_debug", {})["false_unavailable_fixed"] = res["reply"]
                res["reply"] = fixed
    except Exception as exc:  # noqa: BLE001 - a text check never breaks a turn
        app.logger.warning("unavailable check failed: %s", exc)
    return res


def _run_chat_looked(messages, active=None, language="ar", clear_audience=False, drop_context=None):
    first = _run_chat_once(messages, active, language, clear_audience, drop_context)
    try:
        if not second_look.needed(first, messages):
            return first
        act = active if active is not None else llm.get_active()
        if not act:
            return first
        conv = [{"role": m.get("role"), "content": str(m.get("content") or "")[:600]} for m in messages[-CHAT_MAX_MESSAGES:]
                if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
        last = conv[-1]["content"] if conv and conv[-1]["role"] == "user" else ""
        if not last:
            return first
        all_books = {b["book_id"]: dict(b) for b in list_books(only_active=False)}
        call_first = dict(llm.last_call)
        tried, used = [], None
        _ar = (first.get("_debug") or {}).get("author_request") or {}
        _named = " ".join(normalize(str(_ar.get("name_as_written") or "")).split())
        for r in second_look.readings(conv, first.get("reply") or "", _sl_hints(last, all_books), act, language):
            if r["kind"] == "author" and len(_named.split()) >= 2 and " ".join(normalize(r["query"]).split()) != _named:
                # «ابي كتب أمين معلوف»: a full name the visitor wrote is not read as another author of that family
                tried.append(dict(r, skipped="full name written"))
                continue
            _msg_words = {strip_al(w) for w in normalize(last).split()}
            if (r["kind"] == "author" and not _named
                    and {strip_al(w) for w in normalize(r["query"]).split()} & _msg_words):
                # «هدية لصديقتي تحب الطبخ والسفر» is not a request for «عبد الرحمن سفر»: with no author asked for, a word of
                # the message that happens to be a family name is not read as one (QADFIX4 re-run, case 40.1)
                tried.append(dict(r, skipped="ordinary word, no author asked"))
                continue
            found = _sl_find(r, all_books)
            tried.append(dict(r, found=found[:6]))
            if found and used is None:
                used = r
        dbg = {"readings": tried, "call": dict(llm.last_call)}
        first.setdefault("_debug", {})["second_look"] = dbg
        if used is None:
            return first
        msgs2 = [dict(m) if isinstance(m, dict) else m for m in messages]
        for m in reversed(msgs2):
            if isinstance(m, dict) and m.get("role") == "user":
                m["content"] = used["rewrite"]
                _users = [c["content"] for c in conv if c["role"] == "user"]
                if chat_rules.parent_reader(_users) and not chat_rules.parent_said(used["rewrite"]):
                    # QADFIX5 #9: the second reading keeps who the reader is («للتعامل مع المراهقين» is for the parent);
                    # QADFIX6 (QAD 2026-10-18, case 46.2): also when the parent said it one message before («بنتي ١٤»)
                    m["content"] += (" — for me as a parent" if language == "en" else " — الكتاب لي أنا كولي أمر")
                break
        second = _run_chat_once(msgs2, active, language, clear_audience, drop_context,
                                second_look_note=second_look.note(last, used, first.get("reply") or "", language,
                                                                  [b.get("title") for b in first.get("books") or [] if b.get("title")]))
        if second.get("understanding") == "ai" and second.get("books"):
            second.setdefault("_debug", {})["second_look"] = dict(dbg, used=used, first_reply=first.get("reply"),
                                                                  first_books=[b.get("book_id") for b in first.get("books") or []])
            return second
        dbg["second_without_books"] = True
        return first
    except Exception as exc:  # noqa: BLE001 - the second look never breaks a turn
        app.logger.warning("second look failed: %s", exc)
        first.setdefault("_debug", {})["second_look_error"] = str(exc)[:200]
        return first


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
            # location shown to staff and visitors: typed place, else the section of its genre (never invented)
            "location": sections.location(dict(b))[0], "location_kind": sections.location(dict(b))[1],
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
            "publication_year": b.get("publication_year"), "original_year": b.get("original_year"),
            "origin": origin_label(b),
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


# «اقتراحات الموظفين» (Ibrahim 2026-10-09): general suggestions about the app from staff, logged
@app.route("/api/admin/inbox/staff-ideas", methods=["GET", "POST"])
def admin_staff_ideas():
    user = acting_user() or {}
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        try:
            idea_id, text = staff_ideas.add(data.get("text"), user.get("display_name") or user.get("username"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        auth.audit(user or None, "staff_idea.add", idea_id, {"text": text[:1000]})
        return jsonify({"ok": True, "idea_id": idea_id, "counts": staff_ideas.counts()})
    status = request.args.get("status", "open")
    offset = request.args.get("offset", "0")
    rows, total = staff_ideas.list_rows(status, int(offset) if offset.isdigit() else 0)
    return jsonify({"items": rows, "total": total, "counts": staff_ideas.counts()})


@app.route("/api/admin/inbox/staff-ideas/<int:idea_id>", methods=["POST"])
def admin_staff_idea_update(idea_id):
    data = request.get_json(silent=True) or {}
    user = acting_user() or {}
    try:
        row = staff_ideas.set_status(idea_id, data.get("status"), user.get("display_name"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if row is None:
        return jsonify({"error": "غير موجود"}), 404
    auth.audit(user or None, "staff_idea." + row["status"], idea_id, None)
    return jsonify({"ok": True, "counts": staff_ideas.counts()})


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


# ---------------------------------------------------------------------------
# «اختياراتنا هذا الشهر» (Ibrahim, 2026-10-14): five books on the start page; the automatic ranking
# proposes, staff approve in the admin «اختياراتنا». See backend/picks.py.
# ---------------------------------------------------------------------------

def _pick_book(b, language="ar"):
    if not b:
        return None
    b = with_origin(dict(b), language)
    keep = ("book_id", "title", "author", "publisher", "genre", "cover_url", "hook", "price", "quantity", "origin",
            "location", "location_kind", "page_count", "line", "translator", "original_year", "publication_year")
    out = {k: b.get(k) for k in keep}
    out["line"] = b.get("line") or picks.visitor_line(b)
    return out


@app.route("/api/picks")
def public_picks():
    language = request.args.get("lang") if request.args.get("lang") in ("ar", "en") else "ar"
    month, books = picks.public_books()
    full = {b["book_id"]: dict(b) for b in list_books(only_active=True)} if books else {}
    out = []
    for b in books:
        whole = with_origin(dict(full.get(b["book_id"], b)), language)  # the same fields the chat's book page uses
        for k in ("barcode", "rewaa_sku", "source", "last_synced_at", "research_note", "research_note_ar",
                  "research_candidates", "research_status", "age_note", "keywords"):
            whole.pop(k, None)   # stock and research bookkeeping is not for visitors
        whole["line"] = b["line"]
        out.append(whole)
    return jsonify({"month": month, "books": out})


def _pick_month(value, write=False):
    month = value if picks.valid_month(value) else picks.this_month()
    if write and month not in (picks.this_month(), picks._shift(picks.this_month(), 1)):
        raise picks.PickError("التعديل متاح للشهر الحالي والشهر القادم فقط")
    return month


def _pick_actor():
    user = acting_user() or {}
    return user.get("display_name") or user.get("username")


@app.route("/api/admin/picks")
def admin_picks():
    month = _pick_month(request.args.get("month"))
    view = picks.admin_view(month, draft=month in (picks.this_month(), picks._shift(picks.this_month(), 1)))
    for s in view["slots"]:
        s["book"] = _pick_book(s.get("book"))
    for c in view["candidates"]:
        c["book"] = _pick_book(c["book"])
    view.update(this_month=picks.this_month(), next_month=picks._shift(picks.this_month(), 1),
                live_month=picks.live_month())
    return jsonify(view)


@app.route("/api/admin/picks/search")
def admin_picks_search():
    """Any book in stock, by title or author (for «اختر كتابًا آخر»)."""
    q = normalize(request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"books": []})
    found = [b for b in (dict(r) for r in list_books(only_active=True))
             if q in normalize(b.get("title") or "") or q in normalize(b.get("author") or "")]
    return jsonify({"books": [_pick_book(b) for b in found[:20]]})


@app.route("/api/admin/picks", methods=["POST"])
def admin_picks_action():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "طلب غير صالح"}), 400
    action, by = data.get("action"), _pick_actor()
    try:
        month = _pick_month(data.get("month"), write=True)
        slot = data.get("slot") if isinstance(data.get("slot"), int) and not isinstance(data.get("slot"), bool) else None
        detail = {"month": month, "slot": slot}
        if action == "set":
            book_id = events.safe_id(data.get("book_id"))
            if book_id is None or slot is None:
                raise picks.PickError("اختر كتابًا وخانة")
            book = picks.set_slot(month, slot, book_id, by)
            detail.update(book_id=book_id, title=book["title"])
        elif action == "pin":
            if slot is None or not picks.set_pinned(month, slot, bool(data.get("pinned")), by):
                raise picks.PickError("الخانة فاضية")
            detail["pinned"] = bool(data.get("pinned"))
        elif action == "clear":
            if slot is None or not picks.clear_slot(month, slot, by):
                raise picks.PickError("الخانة فاضية")
        elif action == "move":
            to = data.get("to") if isinstance(data.get("to"), int) and not isinstance(data.get("to"), bool) else None
            picks.move(month, slot, to)
            detail["to"] = to
        elif action == "refresh":
            detail["filled"] = picks.fill(month, by)
        elif action in ("approve", "unapprove"):
            picks.set_approved(month, action == "approve", by)
        else:
            raise picks.PickError("طلب غير صالح")
    except picks.PickError as e:
        return jsonify({"error": str(e)}), 400
    picks.clear_cache()
    auth.audit(acting_user(), "picks." + action, slot, detail)
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


# «ابحث عن غلاف» with choices (Ibrahim 2026-10-13: «ودي ياخذ مجال اكبر ويصير يدور عالنت ويعط اختيارات … والموظف يختار»):
# several sources are searched, the staff member picks one. Only an image offered by the last search for that book
# can be saved through «choose» (the server never downloads an address typed by the browser).
_COVER_CHOICES = {}   # book_id -> (time, {url: source})


@app.route("/api/admin/books/<int:book_id>/cover/search", methods=["POST"])
def admin_cover_search(book_id):
    book = get_book(book_id)
    if book is None:
        return jsonify({"error": "book not found"}), 404
    found = covers.search_candidates(dict(book))
    now = time.time()
    for k in [k for k, (t, _) in _COVER_CHOICES.items() if now - t > 3600]:
        _COVER_CHOICES.pop(k, None)
    _COVER_CHOICES[book_id] = (now, {c["url"]: c["source"] for c in found})
    return jsonify({"ok": True, "candidates": found})


@app.route("/api/admin/books/<int:book_id>/cover/choose", methods=["POST"])
def admin_cover_choose(book_id):
    if get_book(book_id) is None:
        return jsonify({"error": "book not found"}), 404
    url = str((request.get_json(silent=True) or {}).get("url") or "")
    offered = (_COVER_CHOICES.get(book_id) or (0, {}))[1]
    if url not in offered:
        return jsonify({"ok": False, "error": "not offered"}), 400
    try:
        result = covers.save_chosen(book_id, url, offered[url])
    except covers.CoverError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    auth.audit(acting_user(), "cover.choose", book_id, {"from": url, "source": offered[url]})
    return jsonify(dict(result, ok=True))


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


# The library's own book sheet (shelf codes, publisher, category, keywords) — track SHELF, Ibrahim
# 2026-10-11: «ابني طريقة لاعادة استيراده كلما ارسلته المكتبة». Manager only (the /api/admin prefix).
@app.route("/api/admin/library-sheet/upload", methods=["POST"])
def admin_library_sheet_upload():
    import library_sheet
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "اختر ملف"}), 400
    try:
        return jsonify(library_sheet.store_upload(f.filename, f.read()))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/admin/library-sheet/apply", methods=["POST"])
def admin_library_sheet_apply():
    import library_sheet
    data = request.get_json(silent=True) or {}
    user = acting_user() or {}
    try:
        res = library_sheet.apply_upload(str(data.get("upload_id") or ""),
                                         applied_by=user.get("display_name") or user.get("username"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    auth.audit(acting_user(), "library_sheet.apply", None, res)
    return jsonify(res)


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
    try:
        # an update installed while the server was stopped (AUTOUPD): its outcome is read once and logged
        _res = channel.after_start(auth.audit)
        if _res:
            print(("Update installed: " if _res.get("ok") else "Update FAILED and was rolled back: ") + str(_res.get("version")), flush=True)
        channel.start_background()   # the hourly look at the signed channel
    except Exception as exc:  # never block the server on this
        print("update channel failed:", exc, flush=True)
    try:
        inbox_send.start_background(auth.audit)   # INBOXSEND: the weekly / requested sealed inbox file to Ibrahim
    except Exception as exc:  # never block the server on this
        print("inbox send failed:", exc, flush=True)
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
