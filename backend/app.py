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
import visitor_reviews
import shelf_suggestions
import helper_chips
import honors
import inbox_export
import no_data
import series_parts
import open_questions
import staff_notes
import staff_ideas
import library_sheet
import research_queue
import page_extract
import events
import picks
import start_paths
import start_taps
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
                  # «الإعدادات» for staff too (Ibrahim 2026-10-13 «ابغاك تعطي للموظفين صلاحية يدخلون عالاعدادات»): QR,
                  # library sections, audit log, about. Since 2026-10-24 (ADMSTF, Ibrahim: «الموظفون يظهر للمدير والمطور»،
                  # «الذكاء الاصطناعي من صلاحيات المدير») staff accounts and the AI settings (model, keys, usage, test
                  # mode) are manager/developer only. «الاستيراد», «إحصائيات الزوار» stay manager-only.
                  "/api/admin/trial", "/api/admin/sections", "/api/admin/audit", "/api/admin/version",
                  # «أرسل الآن» of «إرسال البلاغات للمطوّر» for every staff member (Ibrahim 2026-10-29 «افتح للموظفين ارسال
                  # البلاغات للمطور»); turning the sending on/off and the upload token stay the manager's (send-inbox/settings)
                  "/api/admin/send-inbox/now",
                  # «موافقة وتحديث» for every staff member too (STAFFUPD, Ibrahim 2026-11-08: the library stayed on 0.9.16 while
                  # 0.9.18 / 0.9.19 waited for a manager's approval; «صلاحية التثبيت للموظفين»). The install still checks the
                  # signature, size and SHA-256, takes a backup and rolls back on any failure; who approved goes to the audit log
                  "/api/admin/channel/install")
# Developer-only writes (ADMSTF 2026-10-24): author spelling merge and restoring a deleted product.
DEV_ONLY_WRITES = ("/api/admin/authors/merge", "/api/admin/books/deleted/")


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
    if not auth.is_manager(user["role"]) and not staff_ok:
        return jsonify({"error": "هذي الصفحة للمدير فقط"}), 403
    if method != "GET" and path.startswith(DEV_ONLY_WRITES) and not auth.is_developer(user["role"]):
        return jsonify({"error": "هذي للمطوّر فقط"}), 403
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
                 "صغيره عمرها", "ابتدائي", "روضه", "قصص اطفال", "للصغار", "الصغار", "لطفلي", "لطفلتي", "child", "children", "kid", "kids", "my little", "little son", "little daughter", "toddler",
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
    for age in chat_rules.kid_ages(normalized_query) + chat_rules.grade_ages(normalized_query):   # «بنتي ١٥» (QAD 2026-10-12, case 38); QADFIX25: «بالصف الثالث»
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
    if (book.get("age_min") is None and book.get("age_max") is None
            and (book.get("genre") or "").strip() in (("أطفال",) if audience == "children" else ("يافعين", "أطفال"))):
        # QADFIX16 (QAD 2026-10-24, case 20.3): a book on the library's children's shelf (genre «أطفال»; «يافعين» too for a
        # teenager) is on that shelf even with no reader card or age range recorded (colouring books, first-word books)
        return True
    if book.get("age_min") is None and book.get("age_max") is None:
        # an edition made for young readers says so in its title: «روبنسون كروزو (للفتيان)» (QAD 2026-10-15, case 16.2)
        # a story the visitor asked for as a cartoon («قصص صارت أفلام كرتون لولدي ٨ سنين») and that was made into
        # an animated film (White Fang, Kidnapped): the cartoon says it suits a child (Ibrahim 2026-10-15, QAD case 16.2)
        # QADFIX23 (QAD 2026-10-28, case 25.3): «شي أنسب لعمر ٦» answered with «الشيخ والبحر» (a cartoon was made of it) and a
        # young readers' edition, with no word on age — neither tells that a book suits a child under 8; from 8 on the reply
        # still says its age is not recorded (guidance below)
        if age is not None and age < 8:
            return False
        return bool(_YOUNG_EDITION.search(normalize(book.get("title") or ""))) or bool(animated)
    if age is not None:
        return age_fit(book, age) > 0
    lo = book.get("age_min") or 0
    return lo <= (10 if audience == "children" else 15)


def unrated_comic_ok(card, book, audience, age):
    """QADFIX22 (QAD 2026-10-27, cases 19.1/19.3): «بنتي ١١ سنة تحب المانجا» — 79 comics in stock, almost all with no age range
    recorded, so the child was told «ما عندنا». A comic with a researched reader card and no age range is offered to a child of
    10 or more when its card flags nothing that blocks a child (checked after this in age_allowed); the reply says its age is not
    recorded and the staff can confirm. Younger children, and comics with no card, still get none unchecked."""
    return bool(audience == "children" and age is not None and age >= 10 and book
                and (book.get("genre") or "").strip() == "قصص مصورة"
                and book.get("age_min") is None and book.get("age_max") is None
                and card and card.get("format") == "comics")


# QADFIX26 #1 (QAD 2026-11-03 case 17.1–17.2: «بنت أخوي في ثاني متوسط، تبي قصة حب خفيفة مافيها شي للكبار» → «هيبتا» with «ما أقدر
# أؤكد خلوّهما من محتوى للكبار», then «في قلبي أنثى عبرية»; QAD 2026-10-25 case 3.2; Ibrahim 2026-11-03 «اصلحها»; his age rule of
# 2026-10-04: an age limit only for sexual content): a young reader (an age, a school year, a child of the family) and a wish for
# a clean book («مافيها شي للكبار»، «مضمون يناسب عمرها»، «تكون نظيفة»، "nothing adult") is strict: only books whose record
# positively fits the reader — the young readers' / children's shelf, or a recorded age range that covers the age — and that
# carry NO sexual-content evidence at any confidence (and no other adult-content flag of the reader from «clear» up). A book
# the reply could only show with «ما أقدر أؤكد خلوّه» is not shown at all; with none left the reply says so (guidance).
CLEAN_YOUNG = "clean_young"
SENSITIVE_LABELS[CLEAN_YOUNG] = "محتوى للكبار"
_CLEAN_WISH = re.compile(
    r"(?:^| )(?:ما ?في(?:ها|ه)|مافي|ما يكون في(?:ها|ه)|ما تكون في(?:ها|ه)|ولا في(?:ها|ه)|بدون|بلا|من غير|خالي(?:ه)? من|خاليه|ليس في(?:ها|ه)"
    r"|لا يكون في(?:ها|ه)|مو في(?:ها|ه)|مب في(?:ها|ه)|تخلو من|يخلو من)"
    r" (?:اي |أي )?(?:شي |شيء |محتوي |مشاهد |لقطات |اشياء |كلام |مواضيع |اشي )?(?:لل|ال)?(?:كبار|بالغين|جريئه|جريء|جريئ|جنسي|جنسيه|خادش|خادشه|اباحي|اباحيه"
    r"|قبلات|غير لائق|غير لائقه|عيب|قله ادب)(?: |$)"
    r"|(?:^| )(?:نظيف|نظيفه|نضيفه|نضيف)(?: |$)"
    r"|(?:^| )(?:مضمون|مضمونه|مناسب|مناسبه|يناسب|تناسب|ينفع|تنفع)(?: لها| له)? (?:ل)?(?:عمرها|عمره|سنها|لعمرها|لعمره)(?: |$)"
    r"|(?:^| )(?:مضمون|مضمونه) (?:يناسب|تناسب|يناسبها|تناسبها|يناسبه|تناسبه|لعمرها|لعمره)(?: |$)"
    r"|clean (?:book|novel|story|read|romance)|(?:^| )clean(?: |$)|nothing (?:adult|explicit|inappropriate)|no (?:adult|explicit|sexual|mature)(?: |$)"
    r"|age[- ]appropriate|appropriate for (?:her|his|their) age|suitable for (?:her|his|their) age")


def clean_wish(text):
    """«مافيها شي للكبار»، «أبي شي مضمون يناسب عمرها»، «تكون نظيفة»، "a clean romance, nothing adult": content for grown-ups is
    refused for the reader (QADFIX26 #1). Read only for a young reader (see _run_chat_once)."""
    t = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split()).lower()
    return bool(t and _CLEAN_WISH.search(t))


def clean_young_ok(card, book, audience, age=None):
    """QADFIX26 #1: the record positively fits this young reader and carries no sexual-content evidence."""
    if card and (("sensitive_content", "sexual_content") in (card.get("confidence") or {})
                 or "sexual_content" in (card.get("severity") or {})):
        return False
    if book and age is not None and age_fit(book, age) < 0:
        return False
    shelf = bool(card and card.get("format") in AGE_OK[audience]) or bool(
        book and (book.get("genre") or "").strip() in (("أطفال",) if audience == "children" else ("يافعين", "أطفال")))
    if not shelf and not (book and age is not None and age_fit(book, age) > 0):
        return False
    return not (card and any(_warning_blocks(card, audience, c) for c in set(AGE_BLOCKING[audience]) | SEVERE_ONLY))


def clean_young_wish(pprefs, user_messages):
    """QADFIX26 #1, the one rule for every path that shows books (the model's answer, and the keyword answer and its «books
    like X» without the model — AUDFIX, audit 2026-11-04 P2): a young reader and a clean book asked for in any message
    («مافيها شي للكبار») or sexual content refused -> CLEAN_YOUNG joins pprefs["avoid"] (strict, clean_young_ok) and the
    reader's age is kept. Changes pprefs in place; True when the rule holds."""
    if not (pprefs.get("audience") in AGE_OK
            and (any(clean_wish(t) for t in user_messages) or "sexual_content" in (pprefs.get("avoid") or ()))):
        return False
    pprefs["avoid"] = set(pprefs.get("avoid") or ()) | {CLEAN_YOUNG}
    if pprefs.get("reader_age") is None:
        pprefs["reader_age"] = audience_age(user_messages)
    return True


def clean_young_shelf(all_books, shown, cards, audience, age, language="ar"):
    """QADFIX26 #1: nothing fits both the request and a clean book for this young reader -> the young readers' (children's)
    shelf instead: its books in stock, not shown yet, that pass the same strict check, Arabic copies first."""
    shelf = ("أطفال",) if audience == "children" else ("يافعين",)
    alt = [b for b in all_books.values() if is_available(b) and b["book_id"] not in shown
           and (b.get("genre") or "").strip() in shelf and clean_young_ok(cards.get(b["book_id"]), b, audience, age)]
    alt.sort(key=lambda b: ((b.get("language") or "ar") == "en" and language != "en", -(b.get("goodreads_rating") or 0), b["book_id"]))
    return alt


CLEAN_YOUNG_SHELF_REASON = {"en": "From our young readers' shelf, nothing for adults in its record (not the rest of the request)",
                            "ar": "من رف اليافعين، وما في سجله تنبيه لمحتوى للكبار (ليس على باقي الطلب)"}


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
    if CLEAN_YOUNG in avoid and audience in AGE_OK and not clean_young_ok(card, book, audience, age):
        return False   # QADFIX26 #1: a young reader and «مافيها شي للكبار»
    if audience == "young_adult":
        # QADFIX12 #7 (QAD 2026-10-22, cases 47.2–47.3; Ibrahim 2026-10-22: «ما ابغى الجواب يكون مفلتر … الجواب يضم الكتب اللي تناسب
        # الطلب … مع تحذير للروايات الي فيها محتوى للكبار»): a teenager gets every book that fits the request — the young readers'
        # shelf first (score_books) — and the reply warns about adult content from the book's facts; only content the visitor
        # asked to avoid keeps a book out (QADFIX26 #1: and a clean book asked for, above)
        return True
    if audience in AGE_OK:
        if ((not card or card.get("format") not in AGE_OK[audience]) and not _age_range_ok(book, audience, age, animated)
                and not unrated_comic_ok(card, book, audience, age)):
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
    CLEAN_YOUNG: "adult content",   # QADFIX26 #1
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
    if ({"beginner"} & set(pprefs["reader_level"]) or {"habit_start"} & set(pprefs["use_cases"])
            or "simple_style" in (pprefs.get("qualities") or ())) and not tier_of("reader_level", "beginner"):
        # QADFIX18 (inbox 2026-10-24): «سرد بسيط / لغة سهلة» is the same signs — easy language, a direct style
        # «كتب لقارئ مبتدئ»: few books carry a reader level, so the readers' other signs count — easy
        # language, no prior knowledge needed, a light or quick read, a short book; and specialist,
        # philosophical or experimental books step back (staff chat 2026-10-11)
        easy = [("language_difficulty", "very_easy", "لغته سهلة جدًا"), ("language_difficulty", "easy", "لغته سهلة"),
                ("style_complexity", "direct", "أسلوبه مباشر"),
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
        made = honors.matches_adaptation(book["book_id"], prefs["adaptation"], prefs.get("adaptation_max_year"), prefs.get("adaptation_origin"),
                                         prefs.get("adaptation_min_year"))
        if made:
            score += 4
            reasons.append("تحوّل إلى " + made[0])
    if prefs["genres"] and book.get("genre") in prefs["genres"]:
        score += 2
        reasons.append(f"من تصنيف «{book['genre']}» كما طلبت")
    elif prefs["genres"] and _genre_in_title(book, prefs["genres"]):
        # QADFIX26 (QAD 2026-11-01 case 44): its title names the field asked for — above the shelf's other books
        score += 3
        reasons.append("عنوانه عن «" + next(g for g in _GENRE_TITLE26 if g in prefs["genres"]) + "»")
    if prefs.get("manga") and (book.get("genre") or "").strip() == "قصص مصورة":
        # QADFIX22 (QAD 2026-10-27, case 19.1): «تحب المانجا» — the comics before the children's shelf the model added,
        # the Japanese ones (manga) first
        score += 3 + (2 if (book.get("original_language") or "") == "ja" else 0)
        if (book.get("original_language") or "") == "ja":
            reasons.append("مانجا مترجمة عن اليابانية")
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
    # QADFIX30 #5 (QAD 2026-11-07 case 17.2, grave): «وشي واقعي مو خيال؟» for a 13-year-old → «السيد الرئيس» and «حكايات من ضيعة
    # الأرامل», «الروايتان تمزجان الواقع بعناصر خيالية»: magical realism is not the realism asked for, and it is the fantastic refused
    def _hit(w, v):
        wv = words(v)
        if key == "subgenres" and w and w <= {"واقعيه", "واقعي", "realism", "realistic"} and wv & {"سحريه", "magical", "magic"}:
            return False
        if (key == "subgenres_exclude" and w and w <= {"خيال", "خيالي", "خياليه", "فانتازيا", "فنتازيا", "fantasy"}   # not «خيال علمي»
                and wv & {"سحريه", "magical"} and wv & {"واقعيه", "realism"}):
            return True
        return bool(w) and w <= wv
    hits = [v for v in own if isinstance(v, str) and any(_hit(w, v) for w in wanted)]
    if (not hits and key == "subgenres" and any(w and w <= _CLASSIC34 for w in wanted)
            and (book.get("genre") or "").strip() in _CLASSIC_FORMS34 and (first_published(book) or 9999) < 1960):
        # QADFIX34 #4 (QAD 2026-11-11 case 8, medium): "List five classic novels you carry in English editions" → «Here are two» (only two
        # English copies carry the label «كلاسيكيات»), then two more, the five at the third message — while «Pride and Prejudice», «Jane
        # Eyre», «Great Expectations», «Dracula» are in stock: a novel, a play or a poem first published before 1960 is a classic too
        hits = ["كلاسيكيات"]
    return hits


_CLASSIC34 = {"كلاسيكيات", "كلاسيكي", "كلاسيكيه", "كلاسيك", "classic", "classics", "classical"}
_CLASSIC_FORMS34 = {"رواية", "مسرحية", "شعر", "قصص قصيرة"}


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
                       r"|(?:بدون|بلا|من غير|مو|ما فيه|ماتكون|ما تكون|لا)\s+(?:\S+\s+)?(?:دم|دماء|دموي|دمويه|عنف|عنيف|عنيفه|قتل)"
                       # QADFIX32 #3 (QAD 2026-11-09 case 3.1, medium): «بس أهلها ما يبون شي فيه دم أو قتل» — refused with a verb of wishing
                       r"|(?:^| )(?:ما|مو|مب|مش) ?(?:يبون|يبي|تبي|ابي|نبي|ابغي|ابغا|يبغون|تبغي|يبغي|نبغي|يحبون|تحب|يحب|احب|نحب|يريدون|اريد"
                       r"|تريد|يرضون|يسمحون|يفضلون|ودهم|ودي|ودها)\s+(?:\S+\s+){0,3}(?:دم|دماء|دموي|دمويه|عنف|عنيف|عنيفه|قتل)(?: |$)")
# QADFIX32 #3: blood or killing refused for a child or a teenager — a book whose record says horror is out too (Ibrahim's QADFIX12 #7 keeps
# every book that fits for a teenager, but content the visitor asked to avoid keeps a book out; the round's 3.2 «لا ما تبي رعب» said it)
_BLOOD32 = re.compile(r"(?:^| )(?:دم|دماء|دموي|دمويه|قتل|قتال|جثث|ذبح|gore|gory|blood|bloody|killing|killings|murder|murders)(?: |$)")
_GORE_WORD = re.compile(r"دم|دماء|دموي|عنف|عنيف|قتل|gore|gory|blood|violen")
# QADFIX32 #4 (QAD 2026-11-09 case 5.2, medium): «عالمة امرأة»، «عالمة عربية»، «عالم كيميائي»، «قصة حياة عالمة» — a scientist with a word
# that only says who («امرأة», «عربي», a field «كيميائي / فيزيائية»); QADFIX31 read the bare word only
_SCI_FORMS32 = {"عالم": "m", "علماء": "m", "عالمه": "f", "عالمات": "f"}
_SCI_QUAL32 = {normalize(w) for w in "امرأة امراه امرأه نساء نسائية سيدة سيده انثى عربي عربية عربيه عرب مسلم مسلمة مسلمه مسلمين "
               "عربيات مسلمات مشهورات نسوية "
               "مشهور مشهورة مشهوره كبير كبيرة كبيره قصة قصه حياة حياه سيرة سيره".split()}


def title_words_books32(keywords, all_books):
    """QADFIX32 #5: ["حصار", "غزالة"] -> the books in stock whose title holds every one of these words, «ال» or not («حصار الغزالة
    واو»); two words or more, at most four books (more is no remembered title)."""
    kw = {strip_al(w) for k in keywords for w in chat_rules.title_key(normalize(k)).split()
          if len(strip_al(w)) >= 3 and w not in chat_rules._T_STOP}
    if len(kw) < 2:
        return []
    hits = [b for b in all_books.values() if is_available(b) and b.get("active", 1)
            and kw <= {strip_al(w) for w in chat_rules.title_key(normalize(b.get("title") or "")).split()}]
    return sorted(hits, key=lambda b: b["book_id"])[:4] if len(hits) <= 4 else []


_FIVE32 = re.compile(r"(?:^| )(?:ال)?(?:خمس|خمسه|خمسة)(?: |$)|(?<![\d٠-٩])[5٥](?![\d٠-٩])|(?:^| )five(?: |$)")


def _head34(title):
    """«فرانكشتاين في بغداد : Frankenstion in Baghdad» -> «فرانكشتاين في بغداد»; «ترمي بشرر…» -> «ترمي بشرر»."""
    t = re.split(r"\s*[:：(\[]\s*", str(title or ""))[0]
    return re.sub(r"[…\.]+$", "", t).strip() or str(title or "")


def list_all_missing34(reply, items, all_books, language="ar"):
    """QADFIX34 #3 (QAD 2026-11-11 case 3.2): the winners of the whole list ([(book_id, year)]) that the reply does not name."""
    key = " " + chat_rules.title_key(reply or "") + " "
    out, seen = [], set()
    for i, y in items:
        b = all_books.get(i)
        if not b:
            continue
        h = chat_rules.title_key(_head34(b.get("title")))
        if not h or h in seen:
            continue
        seen.add(h)
        if (" " + h + " ") not in key:
            out.append((i, y))
    return out


_ORD35 = r"(?:ال)?(?:اول|أول|الأول|ثاني|ثالث|رابع|خامس|سادس|سابع|ثامن|تاسع|عاشر|اخير|أخير|باقي|بقية|بقيه|الباقي)\S*"
_LIST_NO_DETAILS35 = re.compile(
    r"(?:^|(?<=[.!؟?]))\s*[^.!؟?]*?(?:ما عندي|ما عندنا|ليس عندي|لا تتوفر لدي|ما أقدر|ما اقدر|ما تتوفر|ما لقيت)\s+(?:الآن\s+|حاليًا\s+|حاليا\s+)?"
    r"(?:تفاصيل|معلومات|بيانات|اسم|أذكر|اذكر)[^.!؟?]*?" + _ORD35 + r"[^.!؟?]*[.!]"
    r"|(?:^|(?<=[.!?]))\s*[^.!?]*?(?:I don't|I do not|I can't|I cannot)\s+(?:have|give|name|find)\s+[^.!?]*?(?:details|information|name)[^.!?]*?"
    r"(?:seventh|eighth|ninth|tenth|last|remaining|rest|other one|others)[^.!?]*[.!]")
_LIST_SOME35 = re.compile(r"(?:و)?(?:أقدر|اقدر|أستطيع|استطيع|ممكن) (?:أذكر|اذكر|أسمّي|أسمي|اسمي) لك (?:منها|بعضها|منهم|بعضهم)\s*:"
                          r"|(?:I can|I could) (?:name|list|mention) (?:some|a few) of them\s*:")


def list_all_completed34(reply, missing, all_books, language="ar"):
    names = [(f"«{_head34(all_books[i]['title'])}» ({y})" if language == "en" else f"«{_head34(all_books[i]['title'])}» ({fmt_num(y)})")
             if y else f"«{_head34(all_books[i]['title'])}»" for i, y in missing]
    add = (("Also in stock: " + ", ".join(names) + ".") if language == "en" else ("وعندنا كذلك: " + "، ".join(names) + "."))
    # QADFIX35 (QAD 2026-11-12 case 3.1, medium): «… أقدر أذكر لك منها: … ما عندي تفاصيل السابع الآن، وموظف المكتبة يقدر يساعدك. وعندنا كذلك:
    # «الفن الهندي»» — once the list is completed here, the model's «I can name some of them» and «no details on the seventh» contradict it:
    # that sentence goes and «some of them» becomes «they are»
    reply = _LIST_NO_DETAILS35.sub("", reply or "")
    reply = _LIST_SOME35.sub(lambda m: "They are:" if m.group(0)[:1].isascii() else "وهي:", reply)
    reply = re.sub(r"\s{2,}", " ", reply)
    parts = re.split(r"(?<=[.!؟?])\s+", (reply or "").strip())
    if len(parts) > 1 and parts[-1].rstrip().endswith(("؟", "?")):
        return " ".join(parts[:-1] + [add, parts[-1]])
    return (reply or "").strip() + " " + add


def five_list32(facts, all_books, quoted, reply, prior=()):
    """QADFIX32b (Ibrahim «ب», QAD 2026-11-09 case 31.1): the reply names a complete list of exactly five works in stock from facts
    (each quoted by its whole title; two copies of one work count once) and says «five», or they are the very five works of the
    cards shown last (replay 31.2 «أيها أقصر؟» ordering the five) -> their ids (one per work), else []."""
    qs, works = set(quoted), {}
    # QADFIX33 (QAD 2026-11-10 case 10.1, minor): "List them all please" → "We have five parts… 1. A Game of Thrones ⏎ 2. A Clash of
    # Kings …" (an English reply lists the titles one per numbered line, not quoted): four cards, «A Dance With Dragons» left out. A
    # line of a list that is a whole title counts as that title named
    # (replay: the list may also run on one line, «1. A Game of Thrones; 2. A Clash of Kings; …»)
    for _ln33 in re.split(r"[\n;؛]|[.!?؟](?=\s|$)|(?<![\d٠-٩])(?=[\d٠-٩]+[.)]\s)", str(reply or "")):
        _t33 = re.sub(r"^\s*(?:and |و)?(?:[\d٠-٩]+\s*[.)\-–:]|[-•*·])\s*", "", _ln33).strip(" .,،;؛*")
        if _t33:
            qs.add(chat_rules.title_key(_t33))
    for f in facts:
        b = all_books.get(f["id"])
        if b and f.get("available") and chat_rules.title_key(b.get("title") or "") in qs:
            works.setdefault((chat_rules.title_key(b.get("title") or ""), author_flow.display(b.get("author"))), f["id"])
    _wk = lambda b: (chat_rules.title_key(b.get("title") or ""), author_flow.display(b.get("author")))
    _prior = {_wk(all_books[i]) for i in prior or () if i in all_books}
    if len(works) != 5 or not (_FIVE32.search(" ".join(normalize(reply or "").lower().split())) or _prior == set(works)):
        return []
    return sorted(works.values())


def kid_shelf32(book, card):
    """QADFIX32b: a book of the children's shelf (genre «أطفال», or a card of children's format)."""
    return (book.get("genre") or "").strip() == "أطفال" or (card or {}).get("format") == "children"


# QADFIX33 #1 / #2 (QAD 2026-11-10 cases 5.1, 25.1–25.2, grave): a young reader's kind of story, read from the wish («سحر»، «تنانين»،
# «جرائم»، «غموض») and found in a young-shelf book's own record (title, subgenres, keywords, subjects, summary): the young readers'
# shelf has hardly any subgenres recorded, so «جرائم / غموض» matched none of «One of Us Is Lying» (keywords Mystery, Murder), «لغز
# اللافندر», «لغز عملية السلمندر», and «سحر» none of «التنين الأبيض» (keywords تنين، فنتازيا) or «مومو» (فانتازيا)
_YOUNG_KIND33 = {
    "fantasy": re.compile(r"سحر|ساحر|مسحور|فانتازيا|فنتازيا|خيالي|تنين|تنانين|اسطور|fantasy|magic|wizard|witch|dragon"),
    "mystery": re.compile(r"لغز|الغاز|غموض|غامض|جريم|جرائم|جرايم|بوليسي|محقق|القاتل|mystery|mysteries|murder|detective|whodunit|crime"),
}
# in the book's record, whole words only («بيلجريم» is no «جريمة»)
_YOUNG_PRE33 = r"(?:^| )(?:[وبلفك]|ال|وال|بال|فال|كال|لل)?"
_YOUNG_KIND33_REC = {
    "fantasy": re.compile(_YOUNG_PRE33 + r"(?:سحر\S*|ساحر\S*|مسحور\S*|فانتازيا|فنتازيا|تنين\S*|تنانين)(?= |$)"
                          r"|\b(?:fantasy|magic\w*|wizards?|witch\w*|dragons?)\b"),
    "mystery": re.compile(_YOUNG_PRE33 + r"(?:لغز\S*|الغاز|غموض|غامض\S*|جريم\S*|جرائم|جرايم|بوليسي\S*|محقق\S*|قاتل|القاتل)(?= |$)"
                          r"|\b(?:myster\w*|murder\w*|detectives?|whodunit|crimes?)\b"),
}
# QADFIX33 #1: a young-shelf book whose own summary speaks of the content the visitor refuses («فيه استغلال وعنف») is not offered for it
_AVOID_TEXT33 = {"violence": re.compile(r"(?:^| )(?:[وبل]|ال|وال|بال)?(?:عنف|عنفا|عنيف|عنيفه|عنيفا|قتل|يقتل|تقتل|مقتل|مقتله|دماء|دموي|دمويه|ذبح|سفاح)(?= |$)"
                                        r"|\b(?:violen\w*|murder\w*|kill\w*|blood\w*|gore)\b")}


def young_kinds33(phrases):
    """«سحر»، «تنانين»، «جرائم»، «غموض» (the wish's subgenres and subjects) -> {"fantasy"} / {"mystery"} (QADFIX33)."""
    t = " ".join(normalize(" ".join(str(p or "") for p in phrases)).split()).lower()
    return {k for k, rx in _YOUNG_KIND33.items() if rx.search(t)}


_YOUNG_WEAK33 = re.compile(r"^(?:ال)?(?:غامض|غامضه|غامضا|غموض|الغامض|الغامضه|غامضين|غامضون)$")


def young_kind_word33(book, kinds):
    """-> (the first word of the book's own record that shows it is of one of those kinds («تنين», "Mystery", «لغز»), its strength:
    0 in the title / subgenres / keywords / subjects, 1 in the summary, 2 only «غامض / غموض» in the summary, and how many such words
    the record has), or None (QADFIX33)."""
    best, count = None, 0
    for k in ("title", "subgenres", "keywords", "subjects", "summary"):
        txt = _rec_text33(book.get(k))
        for kind in sorted(kinds):
            for m in _YOUNG_KIND33_REC[kind].finditer(txt):
                w = m.group(0).strip()
                count += 1
                rank = 0 if k != "summary" else (2 if _YOUNG_WEAK33.match(w) else 1)
                if best is None or rank < best[1]:
                    best = (w, rank)
    return (best[0], best[1], count) if best else None


def _json_words33(v):
    if isinstance(v, str) and v.strip().startswith("["):
        try:
            return " ".join(str(x) for x in json.loads(v))
        except ValueError:
            return v
    return str(v or "")


def _rec_text33(v):
    """A record field as plain normalized words (punctuation out, lower case)."""
    return " ".join(normalize(re.sub(r"[^\w\s]", " ", _json_words33(v))).split()).lower()


def avoided_in_text33(book, avoid):
    """QADFIX33 #1: the book's own title or summary speaks of a content the visitor refuses (violence: «عنف»، «قتل»، «دماء»)."""
    txt = _rec_text33(book.get("title")) + " " + _rec_text33(book.get("summary")) + " " + _rec_text33(book.get("hook"))
    return any(_AVOID_TEXT33[a].search(txt) for a in avoid if a in _AVOID_TEXT33)


# QADFIX33 #2 (case 25.1): «تحليل الأدلة»، «كشف القاتل»، «حل الألغاز» are the crime story itself, never a subject of their own: «تحليل»
# found «الجين الأناني: تحليل كتاب…» and «نسب قبيلة بني خالد… دراسة تحليلية»
_CRIME_SIDE33 = {normalize(w) for w in (
    "تحليل وتحليل جمع تتبع كشف فك حل وحل البحث عن معرفة معرفه اكتشاف الأدلة الادلة الادله والادله ادلة ادله الدليل دليل القرائن "
    "قرائن الجاني القاتل قاتل الجريمة الجريمه جريمة جريمه الجرائم جرائم الجرايم جرايم الغامضة الغامضه غامضة غامضه الغامض غامض "
    "اللغز لغز الألغاز الالغاز ألغاز الغاز المحقق محقق البوليسي بوليسي البوليسية البوليسيه الغموض غموض").split()}
_CRIME_CORE33 = re.compile(r"ادله|دليل|قرائن|جاني|قاتل|جريم|جرائم|جرايم|محقق|بوليسي|غموض|غامض|لغز|الغاز")


def crime_side33(phrase):
    """«تحليل الأدلة» -> True; «تحليل البيانات»، «تحليل» alone -> False (QADFIX33 #2)."""
    ws = normalize(phrase or "").split()
    return bool(ws) and all(w in _CRIME_SIDE33 for w in ws) and bool(_CRIME_CORE33.search(" ".join(ws)))


_NOBEL_Q33 = re.compile(r"نوبل|nobel")   # QADFIX33 (case 8.1)


def own_win_year33(book_id, keys):
    """QADFIX33 #3: the latest year the book ITSELF won one of these prizes (its author's prizes do not count), or 0."""
    d = honors.load()
    want_any = "any" in (keys or ())
    years = [honors._int_year(r.get("year")) or 0 for r in d["book"].get(book_id, [])
             if r.get("result") == "winner" and honors._wanted(r, list(keys or ()), want_any)]
    return max(years) if years else 0


def own_win_year35(book_id, keys):
    """QADFIX35 #1 (case 28): the EARLIEST year the book itself won one of these prizes, or 0 — for «أقدم فائزة»."""
    d = honors.load()
    want_any = "any" in (keys or ())
    years = [honors._int_year(r.get("year")) or 0 for r in d["book"].get(book_id, [])
             if r.get("result") == "winner" and honors._wanted(r, list(keys or ()), want_any)]
    years = [y for y in years if y]
    return min(years) if years else 0


def age_covers32(book, age):
    """QADFIX32b: the book's RECORDED age range covers the reader — «12+» for 13 yes; «13+» for 12 no; «7+» for 12 no (an open
    range covers up to four years past its start, as age_fit reads «1+»); no recorded age no."""
    lo, hi = book.get("age_min"), book.get("age_max")
    if age is None or lo is None or lo > age:
        return False
    return age <= hi if hi is not None else age - lo < 5


def scientist_phrase32(phrase):
    """«عالمة امرأة» -> "f", «عالم كيميائي» -> "m", «عالم الموضة» -> None (QADFIX32 #4)."""
    raw = normalize(phrase or "").split()
    ws = [strip_al(w) for w in raw]
    core = [w for w in ws if w not in _SCI_QUAL32]
    if not core or core[0] not in _SCI_FORMS32 or len(core) > 2:
        return None
    if core[0] == "عالم" and len(ws) > 1 and any(w.startswith("ال") and strip_al(w) == "عالم" for w in raw):
        return None   # «العالم العربي»، «العالم الإسلامي»: the world
    if len(core) == 2 and not re.search(r"(?:ائي|ائيه|ائيات|ائيين|يائي|ولوجي|ولوجيه)$", core[1]):
        return None   # «عالم الموضة»، «عالم الأدب»: a world, not a scientist; a field word («كيميائي»، «بيولوجية») only
    fem = _SCI_FORMS32[core[0]] == "f" or any(w in {normalize(x) for x in ("امرأة", "امراه", "امرأه", "نساء", "نسائية", "سيدة", "سيده", "انثى")}
                                               for w in ws)
    return "f" if fem else "m"


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
# QADFIX22 (case 40.1): a refused war / politics / killing, said plainly after a negation
_REFUSED_SUBJECT_NAME = {normalize("حروب"): normalize("الحرب"), normalize("حرب"): normalize("الحرب"),
                         normalize("سياسة"): normalize("السياسة"), normalize("قتل"): normalize("القتل"), "war": normalize("الحرب"),
                         "wars": normalize("الحرب"), "politics": normalize("السياسة")}
_REFUSED_SUBJECT = re.compile(
    r" و?(?:ما ?فيها|ما ?فيه|ولا فيها|ولا فيه|بدون|بلا|من غير|مو عن|مب عن|مش عن|ما تكون عن|ما يكون عن|ولا عن|without|no)"
    r" (?:اي |أي |any )?(?:ال)?(" + "|".join(sorted(_REFUSED_SUBJECT_NAME, key=len, reverse=True)) + r") ")
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


# QADFIX30 #4 (QAD 2026-11-07 case 45.1, grave): «بناء العادات» cut to its rarest word «بناء» brought «لدى بناء سور الصين» and «نظرية جديدة
# في بناء الأهرامات»: in «بناء / تكوين / تغيير / ترك … + X» the subject is X
_HEADS30 = {normalize(w) for w in ("بناء تكوين تطوير تحسين تغيير تعديل صناعة صناعه اكتساب كسب ترك كسر التخلص تنمية تنميه تعزيز تقوية "
                                   "تقويه زيادة زياده رفع إدارة اداره ادارة تنظيم ضبط تعلم تعليم فن أسرار اسرار مهارات طرق طريقة طريقه كيفية "
                                   "كيفيه").split()}


def head_object30(phrase):
    """«بناء العادات» -> «العادات»; «تطوير الذات» -> «الذات»; «الحرب الباردة» -> None (QADFIX30 #4)."""
    ws = " ".join(normalize(phrase or "").split()).split()
    if len(ws) >= 2 and (ws[0] in _HEADS30 or strip_al(ws[0]) in _HEADS30) and len(ws[1]) >= 3:
        return " ".join(ws[1:])
    return None


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
_SERIES_REFUSED = re.compile(r"(?:^| )(?:(?:not|no|without|non|مو|مب|مش|بدون|بلا|غير|ليس|ليست|ما ابي|ماابي|ما تكون|مو تكون)\s+(?:\S+\s+){0,3}?"
                             r"(?:سلسله|سلاسل|السلسله|السلاسل|اجزاء|ثلاثيه|series|trilogy)|standalone|stand-alone|stand alone|مستقله|منفرده|منفصله)(?: |$)")
# QADFIX7 #12 (QAD 2026-10-19, case 49): «شعر لشاعرات خليجيات» is not an author named «مجموعة شاعرات»
_GENERIC_AUTHOR = {normalize(w) for w in "مجموعه مجموعة شاعرات شاعره شاعرة شعراء الشعراء كاتبات كاتبه كاتبة كتاب كتّاب الكتاب "
                   "مؤلفين مؤلفات مؤلفه مؤلفة نساء نسائي نسائيه امراه امرأة خليجيات خليجيه خليجية سعوديات عربيات عربيه "
                   "عدة عده مختلفين متنوع women woman female poets writers authors various".split()}
# QADFIX31 #2 (QAD 2026-11-08 cases 6.1–6.2, grave): «عدّ لي الأديبات العربيات اللي ترشحن لقصيرة جائزة الرواية العربية» → «ما أقدر أعدّ لك
# كل الأديبات» while 8 shortlisted novels by women are in stock: «أديبات / أديبة / المبدعات / القاصّات» are women writers too (not the verbs
# «ترشحن / وصلن»: in the dialect they also follow the novels themselves)
_PEOPLE_WOMEN = re.compile(r"(?:^| )(?:ال|لل|ل|و)?(?:شاعرات|شاعره|كاتبات|كاتبه|مؤلفات|روائيات|روائيه|امراه|نساء|نسائي|نسائيه"
                           r"|اديبات|اديبه|مبدعات|قاصات|قاصه|سيدات الادب"
                           # QADFIX27 #1 (QAD 2026-11-04 case 8.1): «رواية مؤلفتها نفسها حازت البوكر العربية»
                           r"|مؤلفتها|كاتبتها|مؤلفته|كاتبته)(?: |$)"
                           r"|\b(?:women|woman|female)\b")
# QADFIX21 (QAD 2026-10-26, case 45.2): in a book's TITLE only a plural or a word for women says «by / about women»; the singular
# «الروائية / الشعرية / الكاتبة» there is an adjective («الأعمال الروائية» of يوسف إدريس, «الآلة الكاتبة»)
_TITLE_WOMEN = re.compile(r"(?:^| )(?:ال|لل|ل|و)?(?:شاعرات|كاتبات|مؤلفات|روائيات|اديبات|مبدعات|امراه|نساء|نسائي|نسائيه)(?: |$)"   # QADFIX31 #2
                          r"|\b(?:women|woman|female)\b")
# QADFIX11 (QAD 2026-10-21, case 33.2; Ibrahim «صححهم»): «شي ثاني مو لنفس الكاتبة» points back at the author shown, it is
# not a wish for women authors: «نفس / غير / بدل + الكاتبة» and a bare definite «الكاتبة» are references
_WOMEN_REF = re.compile(r"(?:^| )(?:مو |مب |ماهو |مش )?(?:نفس|لنفس|بنفس|غير|بدل|بدال)\s+(?:ال)?(?:كاتبه|مؤلفه|روائيه|شاعره)(?= |$)"
                        r"|(?:^| )ال(?:كاتبه|مؤلفه|روائيه|شاعره)(?= |$)|same (?:author|writer)")


def women_wish(text, titles=()):
    """A wish for books by women («روايات لروائيات سعوديات»), not a reference back to the author shown."""
    t = " " + " ".join(normalize(text or "").split()) + " "
    # QADFIX35 (QAD 2026-11-12 case 26.1, medium): «ابغى كتاب نساء جسورات من طهران» (out of stock) → a crime series and Arendt «for a woman
    # author», while Kamali's two other novels and «سووشون» were among the candidates: «نساء» inside the title asked for is no wish
    for x in titles or ():
        k = " ".join(normalize(str(x or "")).split())
        if len(k) >= 4:
            t = t.replace(" " + k + " ", "  ")
    return bool(_PEOPLE_WOMEN.search(" ".join(_WOMEN_REF.sub(" ", t).split())))


def women_award_counts(all_books, prefs, genres=None):
    """QADFIX27 #1 (QAD 2026-11-04 case 8, 2026-11-05 case 1): the prize wish in force over the whole stock, among the books our
    author records give to a woman (book_author_gender). One copy per work. -> {"won": titles that won it themselves,
    "author_won": titles whose woman author won it for another book, "listed": titles only on a list, "others_won": the authors
    of the other winners}."""
    g = book_author_gender()
    out = {"won": [], "author_won": [], "listed": [], "others_won": []}
    seen = set()
    for b in sorted(all_books.values(), key=lambda b: b["book_id"]):
        if not is_available(b) or (genres and (b.get("genre") or "").strip() not in genres):
            continue
        labels = honors.matches_award(b["book_id"], prefs.get("awards") or [], prefs.get("award_year"), prefs.get("award_min_year"),
                                      prefs.get("award_max_year"), prefs.get("award_results"))
        if not labels:
            continue
        k = (chat_rules.title_key(b.get("title") or ""), author_flow.display(b.get("author")))
        if k in seen:
            continue
        seen.add(k)
        kind = ("won" if any(x.startswith("فاز") for x in labels) else
                "author_won" if any(x.startswith("مؤلفه فاز") for x in labels) else "listed")
        if g.get(b["book_id"]) == "f":
            out[kind].append(f"«{b['title']}» ({author_flow.display(b.get('author'))})")
            if kind == "won":
                out.setdefault("won_ids", []).append(b["book_id"])   # QADFIX28 #1
            if kind in ("listed", "author_won"):
                # QADFIX30 m6 (QAD 2026-11-07 cases 5.2–5.3): the list the book itself reached — longlisted only, or shortlisted (also a
                # book whose author won for another one: «ملكوت هذه الأرض» of هدى بركات was longlisted itself)
                _own30 = [normalize(x) for x in labels if not x.startswith("مؤلفه")]
                _lst30 = ("short_listed" if any("القائمه القصيره" in x for x in _own30) else
                          "long_only" if any("القائمه الطويله" in x for x in _own30) else None)
                if _lst30:
                    out.setdefault(_lst30, []).append(f"«{b['title']}» ({author_flow.display(b.get('author'))})")
                    out.setdefault(_lst30 + "_ids", []).append(b["book_id"])
        elif kind == "won":
            a = author_flow.display(b.get("author"))
            _key = "others_won" if g.get(b["book_id"]) == "m" else "unknown_won"
            if a and a not in out.setdefault(_key, []):
                out[_key].append(a)
    return out


def drop_unknown_adult30b(scored, on_shelf, content_known, young_book):
    """QADFIX30b (Ibrahim 2026-11-07 «ب», on QADFIX30's question about QAD 2026-11-07 cases 17.2–17.3): a young reader who refuses
    violence (or other content) — when two or more books of the young readers' shelf fit (score > 0), the adult books whose record
    holds no content data are left out entirely, as QADFIX26 does for «مافيها شي للكبار». Fewer than two: nothing is left out
    (QADFIX30's ordering only). -> (kept entries, set of the book ids left out)."""
    if sum(1 for s, _r, b in scored if s > 0 and on_shelf(b)) < 2:
        return scored, set()
    out = {b["book_id"] for _s, _r, b in scored if not on_shelf(b) and not young_book(b) and not content_known(b)}
    return [x for x in scored if x[2]["book_id"] not in out], out


def longlist_only30(user_messages):
    """QADFIX30 m6 (QAD 2026-11-07 cases 5.2–5.3): «سمّ لي اللي وصلت الطويلة بس وما دخلت القصيرة»، «كم وحدة بالضبط وصلت الطويلة بس؟»
    -> True: the books that reached the longlist only (said in the last two messages)."""
    t = " " + " ".join(normalize(re.sub(r"[؟?!.،,«»]", " ", " ".join(user_messages[-2:]))).split()) + " "
    return bool(re.search(r"(?:^| )(?:لل|ل|ال|بال)?قائمه (?:ال)?طويله |(?:^| )(?:وصلت|دخلت|وصل|دخل|وصلن) (?:ل|لل)?(?:ال)?طويله | الطويله (?:بس|فقط|وبس) "
                          r"|longlist|long list", t)) and bool(re.search(
        r" (?:بس|فقط|وبس|بالذات|only|just) | (?:ما|ولا|مو|لم|بدون|دون) (?:دخلت|وصلت|تدخل|تصل|وصل|دخل|ال)?\s?(?:ال)?(?:قائمه )?(?:ال)?قصيره"
        r"| not (?:on )?the shortlist| not shortlisted", t))


# QADFIX27 #3 (QAD 2026-11-05 case 3.1, 2026-11-04 case 50): one volume that holds the parts of a series («ثلاثية غرناطة»: غرناطة،
# مريمة، الرحيل)
_OMNIBUS27 = re.compile(r" (?:ال)?(?:ثلاثيه|رباعيه|خماسيه|سداسيه) | (?:ال)?اعمال (?:ال)?(?:كامله|روائيه|قصصيه) | (?:ال)?مجموعه (?:ال)?كامله "
                        r"| (?:trilogy|omnibus) | complete (?:works|novels) ")


def _words27(text):
    return " " + " ".join(normalize(re.sub(r"[^\w\s]", " ", str(text or ""))).split()) + " "


def omnibus_for(mentioned, message, part_wanted, all_books):
    """-> (the omnibus in stock, [the parts the visitor named that it holds]) or (None, []). A part counts when it is named in the
    omnibus's title or summary and is no book of its own in stock; or the visitor names the omnibus itself and asks for a part."""
    cands = [b for b in all_books.values() if is_available(b) and _OMNIBUS27.search(_words27(b.get("title")))]
    if not cands:
        return None, []
    own = {}
    for b in all_books.values():
        if is_available(b):
            own.setdefault(chat_rules.title_key(b.get("title") or ""), set()).add(b["book_id"])
    msg = _words27(message)
    # only when the message speaks of parts or of a series («أول جزء»، «اللي بعدها»، «ثلاثية»): «عندكم رواية الحب؟» is not this
    if not (part_wanted or re.search(r" (?:ال)?(?:جزء|اجزاء|ثلاثيه|رباعيه|خماسيه|سلسله|مجلد|بعدها|بعده|تكملتها|تكملته|الثاني|الثانيه|الثالث"
                                     r"|الثالثه|part|parts|series|trilogy|sequel|volume) ", msg)):
        return None, []
    best = None
    for b in cands:
        text = _words27((b.get("title") or "") + " " + (b.get("summary") or ""))
        held, alone = [], False
        for m in mentioned or []:
            mw = _words27(m).split()
            if not mw or len("".join(mw)) < 4:
                continue
            if re.search(r" [وبلف]?" + " ".join(re.escape(w) for w in mw) + " ", text):
                held.append(m)
                if not (own.get(chat_rules.title_key(m), set()) - {b["book_id"]}):
                    alone = True   # «مريمة»: named as a book, and no book of ours has that title
        _self = " ".join(_words27(b.get("title")).split())
        _named_self = len(_self) >= 7 and (" " + _self + " ") in msg
        if (held and alone) or (part_wanted and (_named_self or any(chat_rules.title_key(m) == chat_rules.title_key(b.get("title") or "")
                                                                     for m in mentioned or []))):
            k = (len(held), -b["book_id"])
            if best is None or k > best[0]:
                best = (k, b, held)
    return (best[1], best[2]) if best else (None, [])


_OMNIBUS_PARTS29 = {}


def omnibus_part29(mentioned, message, all_books):
    """QADFIX29 #9 (QAD 2026-11-06 case 3.1): «عندكم «الغرف الموصدة» لبول أوستر؟» → «مو متوفر» and other novels of his — «ثلاثية
    نيويورك» (4114) holds it, but its title and summary do not name the parts. backend/omnibus_parts.json lists them with a source.
    -> (the volume, [the part named]) or None. A one-word part («أشباح») counts only with the volume's author named."""
    if "v" not in _OMNIBUS_PARTS29:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "omnibus_parts.json"), encoding="utf-8") as fh:
                _OMNIBUS_PARTS29["v"] = json.load(fh).get("volumes") or []
        except (OSError, ValueError):
            _OMNIBUS_PARTS29["v"] = []
    msg = _words27(message)
    own = {}
    for b in all_books.values():
        if is_available(b):
            own.setdefault(chat_rules.title_key(b.get("title") or ""), set()).add(b["book_id"])
    for v in _OMNIBUS_PARTS29["v"]:
        b = all_books.get(v.get("book_id"))
        if not b or not is_available(b):
            continue
        _aw = [w for w in normalize(author_flow.display(b.get("author")) or "").split() if len(w) >= 4]
        _author_said = any((" " + w + " ") in msg or (" " + strip_al(w) + " ") in msg for w in _aw)
        keys = {chat_rules.title_key(p): p for p in v.get("parts") or []}
        for m in mentioned or []:
            k = chat_rules.title_key(m)
            if k in keys and not (own.get(k, set()) - {b["book_id"]}) and (len(k.split()) >= 2 or _author_said):
                return b, [m]
    return None


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


_ADAPT_AFTER = re.compile(r"(?:بعد|من بعد|after|since) (?:سنه |عام |the )?((?:19|20)\d\d|الالفين|الفين|الالفينات|2000s)")


def prefs_from_ai(ai, normalized):
    """Map cleaned AI preferences to the internal ranking structures."""
    prefs = {"genres": set(ai["genres"]), "length": ai["length"],
             "tokens": ai_tokens(ai), "normalized": normalized,
             "authors": [a for a in ai.get("authors", []) if not generic_author(a)], "subgenres": ai.get("subgenres", []),
             "original_languages": set(ai.get("original_languages") or ()), "translated": ai.get("translated"),
             # prizes and screen adaptations (2026-10-08, backend/honors.py)
             "awards": list(ai.get("awards") or []), "award_year": ai.get("award_year"), "adaptation": ai.get("adaptation"),
             # QADFIX19 (QAD 2026-10-25, case 38): «بدون سحر أو خيال» — kinds of story refused, read by the model
             "subgenres_exclude": list(ai.get("subgenres_exclude") or [])}
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
        # QADFIX22 (QAD 2026-10-27, case 37.2): «أفلام بعد الألفين» — the year is the adaptation's, kept on book_adaptations.year;
        # and «أفلام / مسلسلات» is the adaptation wish itself, never a subject searched in the summaries
        _ay = _ADAPT_AFTER.search(normalized or "")
        if _ay:
            _y = _ay.group(1)
            prefs["adaptation_min_year"] = 2000 if not _y.isdigit() else int(_y)
        _screen_w = {normalize(w) for w in "فيلم فلم افلام أفلام فلام مسلسل مسلسلات سينما السينما الافلام المسلسلات film films movie movies series".split()}
        unsupported = [u for u in unsupported if not (set(normalize(u).split()) <= _screen_w | {normalize("بعد"), normalize("الالفين")})]
    # QADFIX22 (QAD 2026-10-27, case 19.1): «بنتي ١١ سنة تحب المانجا» — «مانجا» is the comics shelf (Japanese comics first by
    # their facts), not a kind no card carries: kept as a subgenre it was relaxed into a subject searched in the summaries,
    # found nowhere, and the child was told «ما عندنا»
    _manga = {normalize(w) for w in "مانجا مانغا المانجا المانغا manga mangas".split()}
    if any(set(normalize(s).split()) & _manga for s in prefs["subgenres"]) or set((normalized or "").split()) & _manga:
        prefs["subgenres"] = [s for s in prefs["subgenres"] if not (set(normalize(s).split()) & _manga)]
        prefs["genres"].add("قصص مصورة")
        prefs["manga"] = True   # comics first, Japanese ones before the rest (score_books)
    unsupported = [u for u in unsupported if not (set(normalize(u).split()) <= _manga | {normalize("يابانيه"), normalize("اليابانيه")})]
    if prefs["original_languages"]:
        # «أدب ياباني» = Japanese literature of any kind, not the catalog genre «أدب» (2026-10-03)
        prefs["genres"].discard("أدب")
    prefs["subgenres"], prefs["soft_subgenres"], moods = split_mood_subgenres(prefs["subgenres"], ai["moods_include"])
    moods -= set(ai["moods_exclude"])
    pprefs = {"moods": moods, "pace": set(ai["pace"]),
              "weight": set(ai["weight_include"]), "use_cases": set(ai["use_cases"]),
              "reader_level": set(ai["reader_level"]),
              "qualities": set(ai.get("qualities") or []),
              "audience": ai.get("audience") or detect_audience(normalized),
              "avoid": set(ai.get("avoid_content") or [])}
    exclusions = {"moods": set(ai["moods_exclude"]), "weight": set(ai["weight_exclude"])}
    if (not prefs["original_languages"] and not prefs.get("translated")
            and re.search(r"\barabic (?:novel|novels|story|stories|literature|poetry|fiction|novella)\b", normalized or "")
            and not re.search(r"in arabic|arabic (?:translation|copy|copies|edition|version)|translated|translation", normalized or "")):
        # QADFIX25 (full replay of QAD 2026-10-31, case 14.1; HOMEFOLD observation 2026-10-30): "a short Arabic novel" is a novel
        # written in Arabic — the interpreter sometimes left it out and «رفيقي» (Gorky, translated) came
        prefs["original_languages"] = {"ar"}
    if women_wish(normalized):
        # QADFIX25 (QAD 2026-10-31, case 2.1, grave): «كاتبة من السعودية اخذت البوكر العربية» — «كاتبة» is the women-authors wish
        # (read from the author records below), never a subject: as one it emptied the candidates («ما لقينا») while Raja Alem's
        # «باهبل» (IPAF shortlist 2024) was in stock
        unsupported = [u for u in unsupported if not _PEOPLE_WOMEN.search(" " + " ".join(normalize(u).split()) + " ")]
    if era_said(normalized):
        # QADFIX25 (case 49.1, grave): «الأدب الإنجليزي الفيكتوري» — the era is a range of first-publication years (years_said),
        # not a subject searched in the summaries (it found nothing and ~40 Victorian books in stock were never offered)
        unsupported = [u for u in unsupported if not era_said(u) and not _ERA_WORD.search(" " + " ".join(normalize(u).split()) + " ")]
    if pprefs.get("audience") in ("children", "young_adult") and "سيرة نبوية" in prefs["genres"]:
        # QADFIX25 (case 43.1, grave): the Prophet's life for a child is on the children's religious shelf («سلسلة قصص نبي الرحمة»،
        # «قصص الأنبياء»), not the academic studies catalogued «سيرة نبوية» / «تاريخ»
        prefs["genres"] = (set(prefs["genres"]) - {"سيرة نبوية"}) | {"دين"}
    return prefs, pprefs, exclusions, unsupported


_MYSTERY_TITLE = re.compile(r" (?:ال)?(?:لغز|ألغاز|الغاز|محقق|المحقق|المحققه|المحققون|التحري|المخبر) |mystery|detective")


def score_books(books, prefs, pprefs, cards):
    """Phase 1 rules + profile matches; returns [(score, reasons, book)] best first."""
    has_profile_prefs = any((pprefs["moods"], pprefs["pace"], pprefs["weight"],
                             pprefs["use_cases"], pprefs["reader_level"], pprefs.get("audience"), pprefs.get("qualities")))
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
        if "original" in (pprefs.get("qualities") or ()) and honors.load()["book"].get(book["book_id"]):
            # QADFIX18 (inbox 2026-10-24, «رواية غير مبتذلة»): we hold no measure of «مبتذل»; a book's own prize or prize list
            # (from our records, with a source) is the sign we have that it stood out — said as that, never as «غير مبتذلة»
            score += 2
            reasons.append(honors.reason_label(honors.load()["book"][book["book_id"]][0]))
        if pprefs["moods"] & {"mysterious", "suspenseful"} and _MYSTERY_TITLE.search(" " + normalize(book.get("title") or "") + " "):
            # «لغز المدرسة»، «لغز كرة القدم» for a girl who wants mystery: children's books often have no mood on their
            # card, the title says it (QADFIX4, QAD 2026-10-16 case 12)
            score += 2
            reasons.append("عنوانه يدل على لغز يُحل")
        scored.append((score, reasons, book))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def reason_text(reasons, language="ar"):
    # CHATFIX31 (1a), Ibrahim 2026-11-01 «ابدا» (the chat review's «reasons» step): the card line names what in the request the
    # book meets, most specific first (subject, «مثل X», prize, kind, mood, length, price), and the bare shelf («من تصنيف «رواية»
    # كما طلبت») only when nothing more specific is there; two reasons at most, one short line
    reasons = chat_rules.card_reasons(list(dict.fromkeys(reasons)))[:2]
    if reasons:
        if language == "en":
            return "Matches what you asked for: " + "; ".join(localize_reason(r) for r in reasons) + "."
        return "اخترناه لأنه " + "، و".join(chat_rules.short_reason(r) for r in reasons) + "."
    # Matched only through Medium-tier profile values: ranking signal,
    # never quoted as a guarantee (schema §4/§7).
    return ("It may be close to what you described, based on early reader impressions that have not been reviewed yet."
            if language == "en" else "قد يكون قريبًا من وصفك حسب انطباعات قرّاء أولية لم تُراجع بعد.")


# CHATFIX31 (1a), the replay of 2026-11-01: «اخترناه لأنه من تصنيف «رواية» كما طلبت.» stayed on most cards (no more specific
# label: the lightness the visitor asked for is only an early impression, the length and the origin are filters). A chat card
# whose line says only that (or only its price, or only «قد يكون قريبًا…») shows the book's own short line instead (books.hook,
# written for visitors: «مغامرة عبثية مضحكة تبدأ من داخل خزانة.»); in English, its kind, original language and length
_WEAK_REASON_AR = re.compile(r"^(?:اخترناه لأنه (?:من تصنيف «[^»]+» كما طلبت|سعره [^،.]+ ريال)|"
                             r"قد يكون قريبًا من وصفك حسب انطباعات قرّاء أولية لم تُراجع بعد)\.$")
_IMPRESSION_EN = "It may be close to what you described, based on early reader impressions that have not been reviewed yet."


def card_line(reason, book, language="ar"):
    r = str(reason or "")
    if language == "en":
        rest = r.removeprefix("Matches what you asked for: ").removesuffix(".")
        if r != _IMPRESSION_EN and not (r.startswith("Matches what you asked for: ")
                                         and rest.lower() in {str(v).lower() for v in GENRE_LABELS_EN.values()} | {"the category you named"}):
            return r
        parts = [GENRE_LABELS_EN.get(book.get("genre") or "", "")]
        _ol = (book.get("original_language") or "").strip()
        if _ol in LANG_NAMES_EN:
            parts.append("originally " + LANG_NAMES_EN[_ol])
        if book.get("page_count"):
            parts.append(f"{book['page_count']} pages")
        parts = [p for p in parts if p]
        return (parts[0][:1].upper() + parts[0][1:] + "".join(", " + p for p in parts[1:]) + ".") if len(parts) >= 2 else r
    if not _WEAK_REASON_AR.match(r):
        return r
    hook = " ".join(str(book.get("hook") or "").split())
    if not hook or len(hook) > 140:
        return r
    return hook if hook.endswith((".", "؟", "!", "…", "»")) else hook + "."


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


# QADFIX17 (QAD 2026-10-24, case 35.1): prizes given only to novels — a book that won one is a novel for «روايات فازت بـ…»,
# whatever shelf it is filed on («حياة متعجلة», Goncourt 2022, on the autobiography shelf)
NOVEL_PRIZES = {"goncourt", "booker", "ipaf", "intl_booker"}


def _novel_prize_ok(b, prefs, genres):
    aw = [a for a in (prefs.get("awards") or ()) if a in NOVEL_PRIZES]
    if "رواية" not in (genres or ()) or not aw:
        return False
    return any(x.startswith("فاز") for x in honors.matches_award(b["book_id"], aw, prefs.get("award_year"), prefs.get("award_min_year"),
                                                                 prefs.get("award_max_year"), prefs.get("award_results")))


def refusals_ok(b, card, prefs, exclusions):
    """What the visitor refused in words — moods/weight («مو حزين»), kinds of story («بدون واقعية سحرية», QADFIX19 case 38).
    Held by meets_constraints and by every exception path that eases the other wishes (the kind of a named work, a subject in
    a title, the nearest prize books of a country), so no exception brings back a refused kind (AUDFIX, Codex audit 2026-10-25)."""
    if (exclusions["moods"] or exclusions["weight"]) and excluded_by_card(card, exclusions):
        return False
    if prefs.get("subgenres_exclude") and matching_subgenres(b, prefs, key="subgenres_exclude", card=card):
        return False   # QADFIX19 (case 38): «بدون سحر» — not «نصب الدير التذكاري» (magical realism)
    return True


# QADFIX26 (QAD 2026-11-03 case 33): a wish for non-fiction («كفاية خيال»، «مو روايات أصلًا»، «كتاب واقعي»، «من الواقع»)
_NONFIC26 = re.compile(r" (?:كفايه|كفايا|مليت من|زهقت من|طفشت من|تعبت من|شبعت من) (?:ال)?(?:خيال|روايات|قصص)(?! علمي)(?: |$)"
                       r"| (?:مو|مب|مش|ما ابي|ما ابغي|ما ابغى|بدون) (?:ال)?(?:روايات|روايه)(?: اصلا)?(?: |$)"
                       r"| (?:كتاب|كتب|شي) (?:من )?(?:ال)?(?:واقع|واقعي|واقعيه)(?: |$)| (?:غير روائي|غير روائيه|non ?-?fiction|nonfiction) ")


# QADFIX26 (QAD 2026-11-01 case 44, 2026-11-02 case 14.1): «مدخل ميسّر إلى علم الاجتماع» — the shelf «علم اجتماع» holds 6 books
# and «النظريات المعاصرة في علم الاجتماع» / «موسوعة علم الاجتماع» sit on «فكر»: a non-fiction book whose title names the field
# asked for is of that field, whatever its shelf
_GENRE_TITLE26 = {"علم اجتماع": re.compile(r"(?:^| )[لبو]?(?:ال)?علم (?:ال)?اجتماع(?: |$)|sociolog"),
                  "علم نفس": re.compile(r"(?:^| )[لبو]?(?:ال)?علم (?:ال)?نفس(?: |$)|psycholog")}


def _genre_in_title(b, genres):
    if (b.get("genre") or "").strip() in _Q7_FICTION:
        return False
    t = " ".join(normalize(b.get("title") or "").split()).lower()
    return any(rx.search(t) for g, rx in _GENRE_TITLE26.items() if g in genres)


def meets_constraints(b, card, prefs, pprefs, exclusions, genres, form=True):
    """One book against every stated constraint of the visitor. The single test used by constrain()
    and by every other path that can put a book in front of the visitor (related books, books shown
    earlier, reference matches), so no path can break a stated wish (audit 2026-10-02/03).
    form=False (AUDFIX 2026-10-25, QADFIX18b × QADFIX19): a book of the kind of a work the visitor named («من نوع ألف ليلة
    وليلة») is not held to the form the model guessed from that work (shelf, wanted subgenre, the no-genre children's
    default); every other wish and every refusal the visitor stated («بدون واقعية سحرية», moods, length, prizes ...)
    still applies."""
    _cartoon = (prefs.get("adaptation") == "animated" and pprefs.get("audience") == "children"
                and bool(honors.matches_adaptation(b["book_id"], "animated", prefs.get("adaptation_max_year"))))
    if not age_allowed(card, pprefs.get("audience"), pprefs.get("avoid") or (), b, pprefs.get("reader_age"), _cartoon):
        return False
    if form and not genres and pprefs.get("audience") not in AGE_OK and card and card.get("format") == "children":
        return False  # a request that names neither a genre nor a child gets no children's books
    if not refusals_ok(b, card, prefs, exclusions):
        return False
    if form and genres and b.get("genre") not in genres and not _genre_in_title(b, genres) and not (
            # «قصص صارت أفلام كرتون لولدي»: the child's shelves the interpreter names do not hide a novel made into
            # a cartoon («ناب أبيض», «المخطوف») (QAD 2026-10-15, case 16.2)
            _cartoon and set(genres) <= {"أطفال", "قصص مصورة", "قصص قصيرة"}) and not _novel_prize_ok(b, prefs, genres):
        return False
    if form and prefs.get("subgenres") and not matching_subgenres(b, prefs, card=card):
        return False
    if prefs.get("length") == "short" and is_long(b, card):
        return False
    if prefs.get("awards") and not honors.matches_award(b["book_id"], prefs["awards"], prefs.get("award_year"),
                                                       prefs.get("award_min_year"), prefs.get("award_max_year"), prefs.get("award_results")):
        return False
    if prefs.get("adaptation") and not honors.matches_adaptation(b["book_id"], prefs["adaptation"], prefs.get("adaptation_max_year"),
                                                                 prefs.get("adaptation_origin"), prefs.get("adaptation_min_year")):
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
            # CHATFIX31: «صادرة من ٢٠٢٣» — the visitor sees the year filter is on and can remove it (✕)
            add("pubyear", "صادرة " + (f"من {fmt_num(lo)} إلى {fmt_num(hi)}" if lo and hi else f"من {fmt_num(lo)}" if lo else f"حتى {fmt_num(hi)}"))
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
        out.append({"film": "تحوّل إلى فيلم", "series": "تحوّل إلى مسلسل", "animated": "تحوّل إلى رسوم متحركة",
                    "tv": "تحوّل إلى فيلم تلفزيوني أو مسلسل"}.get(prefs["adaptation"], "تحوّل إلى فيلم أو مسلسل")
                   + (f" قديم (حتى {prefs['adaptation_max_year']})" if prefs.get("adaptation_max_year") else "")
                   + (f" سنة {prefs['adaptation_min_year']} أو بعدها" if prefs.get("adaptation_min_year") else ""))
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


@app.route("/api/start-tap", methods=["POST"])
def start_tap():
    """«من أين يبدأ الزوار» (STARTTAP, Ibrahim 2026-11-01 «تمام اضف العداد»): one tap on one of the three start
    choices (chat / reading / shelf), sent by navigator.sendBeacon. Only a count per day, choice and language is
    stored (backend/start_taps.py). Not counted: test mode, a browser signed in to the admin page, and taps past
    the per-address limit (kept in memory only)."""
    data = request.get_json(silent=True, force=True)   # a beacon may arrive as text/plain
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    choice = start_taps.clean_choice(data.get("choice"))
    if choice is None:
        return jsonify({"ok": False, "error": "unknown choice"}), 400
    if not start_taps.allow(request.remote_addr):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    if testlog.enabled() or (session.get("uid") and current_user() is not None):
        return "", 204
    start_taps.record(choice, data.get("lang"))
    return "", 204


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


# ---------------------------------------------------------------------------
# «ماذا تقرأ الآن؟» (Ibrahim 2026-10-27, from a visitor's proposal): a visitor reviews a book of the library (stars,
# quick traits, optional words and first name; shown after staff approve), or suggests a book for the shelves
# (one we have is shown to them; one we do not have is looked up outside and, once the visitor confirms it, kept
# as a candidate book). See backend/visitor_reviews.py and backend/shelf_suggestions.py.
# ---------------------------------------------------------------------------
_VISITOR_HIDDEN = ("barcode", "rewaa_sku", "source", "last_synced_at", "research_note", "research_note_ar",
                   "research_candidates", "research_status", "age_note", "keywords")


def _visitor_book(book, language="ar"):
    b = with_origin(dict(book), language)
    for k in _VISITOR_HIDDEN:
        b.pop(k, None)   # stock and research bookkeeping is not for visitors
    b["available"] = is_available(book)
    return b


def _catalog_books():
    """Every book of the catalogue a visitor may name (in stock or not); hidden products are left out."""
    return {b["book_id"]: dict(b) for b in list_books(only_active=False) if b["active"]}


@app.route("/api/reviews/search")
def review_search():
    """Books of the library by title or author, in stock or not (Ibrahim: «لا يشترط ان الكمية متوفرة»); one edition
    per work, the edition in stock first."""
    language = request.args.get("lang") if request.args.get("lang") in ("ar", "en") else "ar"
    q = " ".join(normalize(request.args.get("q") or "").split())[:80]
    if len(q) < 2:
        return jsonify({"books": []})
    books = _catalog_books()
    qk = chat_rules.title_key(q)
    scored = []
    for b in books.values():
        tk = chat_rules.title_key(b.get("title") or "")
        au = " ".join(normalize(b.get("author") or "").split())
        if tk == qk:
            rank = 0
        elif tk.startswith(qk):
            rank = 1
        elif qk in tk:
            rank = 2
        elif q in au:
            rank = 3
        else:
            continue
        scored.append((rank, not is_available(b), len(tk), b["title"], b))
    scored.sort(key=lambda x: x[:4])
    out, seen = [], set()
    for *_, b in scored:
        k = visitor_reviews.work_key(b)
        if k in seen:
            continue
        seen.add(k)
        out.append(_visitor_book(b, language))
        if len(out) >= 8:
            break
    return jsonify({"books": out})


@app.route("/api/reviews", methods=["POST"])
def review_add():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    if not limits.allow_note(request.remote_addr):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    book_id = clean_id(data.get("book_id"))
    book = get_book(book_id) if book_id is not None else None
    if book is None or not book["active"]:
        return jsonify({"ok": False, "error": "unknown book"}), 400
    if visitor_reviews.clean_stars(data.get("stars")) is None:
        return jsonify({"ok": False, "error": "stars required"}), 400
    text = visitor_reviews.clean_text(data.get("text"))
    processed = None
    if text:
        active = active_within_budget()
        if active:
            processed = visitor_reviews.process(text, active)
    rid = visitor_reviews.add(dict(book), data.get("stars"), data.get("tags"), text, data.get("first_name"),
                              data.get("language"), processed)
    return jsonify({"ok": True, "review_id": rid}), 201


@app.route("/api/reviews/book/<int:book_id>")
def review_for_book(book_id):
    language = request.args.get("lang") if request.args.get("lang") in ("ar", "en") else "ar"
    book = get_book(book_id)
    # AUDFIX 2026-10-27 evening (Codex P2): a hidden book (active = 0) has no public review page, as an unknown one
    if book is None or not book["active"]:
        return jsonify({"error": "not found"}), 404
    return jsonify(visitor_reviews.summary(dict(book), language) or {"count": 0})


@app.route("/api/reviews/status")
def review_status():
    return jsonify({"favorites_ready": visitor_reviews.ready()})


@app.route("/api/shelf/check", methods=["POST"])
def shelf_check():
    """A book a visitor wants on the shelves: ours in stock (shown), ours out of stock, or looked up outside."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400
    if not limits.allow_note(request.remote_addr):
        return jsonify({"error": "rate_limited"}), 429
    language = data.get("language") if data.get("language") in ("ar", "en") else "ar"
    title = " ".join(visitor_notes.clean_text(data.get("title")).split())[:120]
    author = " ".join(visitor_notes.clean_text(data.get("author")).split())[:80]
    if len(normalize(title).strip()) < 2:
        return jsonify({"error": "title required"}), 400
    books = _catalog_books()
    same = [b for b in match_titles([title], books) if outside_books.same_title(title, b.get("title") or "")]
    if author and same:
        # a title we hold by another author is another book
        same = [b for b in same if outside_books.same_author(author, b.get("author") or "") or not (b.get("author") or "").strip()] or []
    if same:
        here = [b for b in same if is_available(b)]
        if here:
            return jsonify({"result": "available", "books": [_visitor_book(b, language) for b in here[:2]]})
        return jsonify({"result": "out_of_stock", "book": _visitor_book(same[0], language)})
    # Goodreads asked together with Wikidata and Open Library, and a title remembered as not found tried again: the
    # visitor is waiting for this one book (VREVFIX 2026-10-27: «الأرض بعد طوفان نوح» was on Goodreads but not found)
    found = outside_books.lookup(title, budget=10.0, background=False, goodreads_too=True)
    if found and found.get("status") == "found":
        if author and found.get("author") and not outside_books.same_author(author, found["author"]):
            found = None   # shown anyway would ask the visitor about another book
    if found and found.get("status") == "found":
        brief = outside_books.brief(found, language) or {}
        return jsonify({"result": "found_outside", "candidate": {
            "key": found["key"], "title": brief.get("title"), "author": brief.get("author"), "year": brief.get("year"),
            "summary": (brief.get("summary") or "")[:300] or None, "genres": brief.get("genres") or [],
            "sources": brief.get("sources") or []}})
    return jsonify({"result": "not_found"})


@app.route("/api/shelf/suggest", methods=["POST"])
def shelf_suggest():
    """The visitor confirmed: «نعم هذا هو الكتاب» (an outside lookup) or «أبغاه يتوفر» (ours, out of stock)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    if not limits.allow_note(request.remote_addr):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    asked = " ".join(visitor_notes.clean_text(data.get("asked_as")).split())[:120]
    note = data.get("note")
    if data.get("key"):
        record = shelf_suggestions.outside_record(data.get("key"))
        if record is None:
            return jsonify({"ok": False, "error": "unknown book"}), 400
        cid, new = shelf_suggestions.add_outside(record, asked, note)
        return jsonify({"ok": True, "candidate_id": cid, "new": new}), 201
    book_id = clean_id(data.get("book_id"))
    book = get_book(book_id) if book_id is not None else None
    if book is None or not book["active"]:
        return jsonify({"ok": False, "error": "unknown book"}), 400
    if is_available(dict(book)):
        return jsonify({"ok": False, "error": "available"}), 400   # it is on the shelf: nothing to suggest
    cid, new = shelf_suggestions.add_catalog(dict(book), asked, note)
    return jsonify({"ok": True, "candidate_id": cid, "new": new}), 201


@app.route("/api/admin/inbox/reviews")
def admin_reviews():
    status = request.args.get("status", "open")
    offset = request.args.get("offset", "0")
    offset = int(offset) if offset.isdigit() else 0
    items = visitor_reviews.list_reviews(status, visitor_reviews.PAGE, offset)
    for it in items:
        if it.get("cover_url") and not covers.has_local_cover(it):
            it["cover_url"] = None
    total = visitor_reviews.total(status)
    return jsonify({"counts": visitor_reviews.counts(), "items": items, "total": total, "offset": offset,
                    "has_more": offset + len(items) < total, "approvable": visitor_reviews.approvable()})


@app.route("/api/admin/inbox/reviews/approve-all", methods=["POST"])
def admin_reviews_approve_all():
    """«اعتماد الكل» (Ibrahim 2026-10-27): every waiting review the model check flagged nothing in."""
    user = acting_user()
    out = visitor_reviews.approve_all(user["display_name"] if user else None)
    if out["approved"]:
        auth.audit(user, "review.approved_all", None, {"count": len(out["approved"]), "ids": out["approved"][:200],
                                                      "left_flagged": out["left_flagged"]})
    return jsonify({"ok": True, "approved": len(out["approved"]), "left_flagged": out["left_flagged"],
                    "counts": visitor_reviews.counts()})


@app.route("/api/admin/inbox/reviews/<int:review_id>", methods=["POST"])
def admin_review_update(review_id):
    data = request.get_json(silent=True) or {}
    user = acting_user()
    try:
        row = visitor_reviews.set_status(review_id, data.get("status"), user["display_name"] if user else None,
                                         hide_text=data.get("hide_text") is True)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if row is None:
        return jsonify({"error": "غير موجود"}), 404
    auth.audit(user, "review." + row["status"], review_id, {"book_id": row["book_id"], "hide_text": bool(row["hide_text"])})
    return jsonify({"ok": True, "counts": visitor_reviews.counts()})


@app.route("/api/admin/inbox/candidates")
def admin_candidates():
    status = request.args.get("status", "open")
    offset = request.args.get("offset", "0")
    offset = int(offset) if offset.isdigit() else 0
    items = shelf_suggestions.list_candidates(status, shelf_suggestions.PAGE, offset)
    total = shelf_suggestions.total(status)
    return jsonify({"counts": shelf_suggestions.counts(), "items": items, "total": total, "offset": offset,
                    "has_more": offset + len(items) < total})


@app.route("/api/admin/inbox/candidates/<int:candidate_id>", methods=["POST"])
def admin_candidate_update(candidate_id):
    data = request.get_json(silent=True) or {}
    user = acting_user()
    try:
        row = shelf_suggestions.set_status(candidate_id, data.get("status"), user["display_name"] if user else None)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if row is None:
        return jsonify({"error": "غير موجود"}), 404
    auth.audit(user, "candidate." + row["status"], candidate_id, {"title": row["title"]})
    return jsonify({"ok": True, "counts": shelf_suggestions.counts()})


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
                        ideas_open=staff_ideas.counts()["open"],
                        # «ماذا تقرأ الآن؟» (2026-10-27): reviews waiting for approval, open candidate books
                        reviews_open=visitor_reviews.counts()["pending"], candidates_open=shelf_suggestions.counts()["open"]))


# «التنبيهات» and «ملاحظات من المطور» (QADFIX4, Ibrahim 2026-10-16): what needs a look now, in one box, and the
# developer's notes that staff mark done. Under /api/admin/inbox so staff accounts reach them too.
def _latest(conn, sql, args=()):
    try:
        r = conn.execute(sql, args).fetchone()
        return (r[0] or 0, r[1]) if r else (0, None)
    except sqlite3.Error:
        return 0, None


_MONTHS_AR = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")


def _picks_alert():
    """PICKALERT (Ibrahim 2026-11-01 «اضف التنبيه المقترح»): this month's «اختياراتنا» waits for a staff approval
    (until then visitors keep seeing the last approved month) -> one alert row that opens «تنظيم منار» → «اختياراتنا».
    «latest» is the first day of the month, so the row shows «جديد» once per month on each device."""
    month = picks.this_month()
    name = _MONTHS_AR[int(month[5:7]) - 1]
    try:
        waiting = picks.approval(month) is None
    except Exception:  # noqa: BLE001 — picks tables missing on an old database: no alert
        waiting = False
    label = (f"«اختياراتنا» لشهر {name} تنتظر الاعتماد — الزوار يشوفون آخر شهر معتمد" if waiting
             else f"«اختياراتنا» لشهر {name} معتمدة")
    return ("picks", label, "organize:picks", (1, f"{month}-01T00:00:00+03:00") if waiting else (0, None))


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
            ("reviews", "مراجعات القرّاء بانتظار الموافقة", "inbox:reviews",
             _latest(conn, "SELECT COUNT(*), MAX(created_at) FROM visitor_reviews WHERE status = 'pending'")),
            ("candidates", "كتب رشّحها الزوار للرفوف", "inbox:candidates",
             _latest(conn, "SELECT COUNT(*), MAX(last_at) FROM candidate_books WHERE status = 'open'")),
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
            # «كتب تحتاج تأكيد هويتها» removed (ADMSTF, Ibrahim 2026-10-24); the coloured section stays in the book card
            _picks_alert(),
        ]
    finally:
        conn.close()
    items = [{"key": k, "label": label, "go": go, "open": n, "latest": latest} for k, label, go, (n, latest) in rows]
    return jsonify({"items": items, "attention": sum(i["open"] for i in items if i["key"] not in ("confirm",))})


# «تحديثات منار» (AUTOUPD, Ibrahim 2026-10-16 «approve»): updates and messages Ibrahim sends through the signed
# channel (backend/channel.py). Everyone sees the state and may check now; since 2026-11-08 (STAFFUPD, Ibrahim «صلاحية
# التثبيت للموظفين») every signed-in account — staff, manager, developer — may approve an install (/api/admin/channel/install
# is in STAFF_PREFIXES).
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
# every week and when he asks (backend/inbox_send.py). Everyone sees the state and can «أرسل الآن» (2026-10-29); only the
# manager turns it on or off and saves the upload token (/api/admin/send-inbox/settings is outside STAFF_PREFIXES).
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
            clean["translator"] = str(data["translator"] or "").strip()[:250] or None   # room for several, «،» between
        if "goodreads_url" in data:
            # GRURL (Ibrahim 2026-10-30 «كيف اقدر اضيف الرابط للكتاب؟»): only a Goodreads book page, kept in one form
            raw = str(data["goodreads_url"] or "").strip()
            m = re.search(r"goodreads\.com/(?:[a-z]{2}/)?book/show/(\d+)", raw)
            if raw and not m:
                raise ValueError
            clean["goodreads_url"] = f"https://www.goodreads.com/book/show/{m.group(1)}" if m else None
            clean["goodreads_url_source"] = "staff" if m else None
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
            # BKREV (Ibrahim 2026-10-29): «R02» typed alone is saved as «روايات · الرف R02»
            clean["shelf_location"] = library_sheet.normalize_location(str(data["shelf_location"]))[:300]
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
    # GRURL (Ibrahim 2026-10-30 «الرابط ذا خله للمطور بس هو اللي يضيفه»): only the developer sets the Goodreads link
    if "goodreads_url" in data and not auth.is_developer((acting_user() or {}).get("role")):
        return jsonify({"ok": False, "error": "رابط Goodreads يضيفه المطوّر فقط"}), 403
    fields = _validate_book_fields(data)
    if fields is None:
        return jsonify({"ok": False, "error": "invalid value"}), 400
    # DEVSEARCH (Ibrahim 2026-10-29): the page the developer took the data from is its source — the summary's and the age's
    # source, and written with the change in the audit (every value its source)
    _src = str(data.get("source_url") or "").strip()[:500]
    if _src and not re.match(r"^https?://[^\s]+$", _src):
        return jsonify({"ok": False, "error": "invalid value"}), 400
    if _src and fields.get("summary"):
        fields["summary_source"] = _src
    if _src and (fields.get("age_min") is not None or fields.get("age_max") is not None):
        fields["age_source"] = _src
    if fields.get("goodreads_url_source") == "staff":     # GRURL: who pasted the link
        fields["goodreads_url_source"] = f"developer: {_who() or '?'}"
    before = dict(get_book(book_id))
    if "goodreads_url" in fields and fields["goodreads_url"] != before.get("goodreads_url"):
        # AUDIT 2026-10-31 P2: the stored rating was read on the old page; another page's rating is not this one's
        fields.update(goodreads_rating=None, goodreads_ratings_count=None, goodreads_rating_source=None, goodreads_rating_at=None)
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
    # several authors or translators are typed in one field, separated by «،» (Ibrahim 2026-10-29 «اذا الكتاب له اكثر من مترجم
    # كيف يقدر الموظف يضيف المترجمين؟»); a translator change relinks too (it waited for the next start before)
    if any(k in fields and fields[k] != before.get(k) for k in ("author", "translator")):
        try:
            authors.sync()  # links the book to the right author record(s)
        except Exception:
            app.logger.exception("authors sync after a staff edit failed")
    _changes = {k: [before.get(k), v] for k, v in fields.items() if before.get(k) != v}
    if _src and _changes:
        _changes["source"] = _src
    if _changes:   # AGY-20261029-E2: a save that changed nothing writes no empty «تعديل بيانات كتاب» row
        auth.audit(acting_user(), "book.update", book_id, _changes)
    _queue_research(book_id, list(_changes))
    return jsonify({"ok": True})


def _who():
    u = acting_user() or {}
    return u.get("display_name") or u.get("username")


def _queue_research(book_id, fields):
    """BKREV (Ibrahim 2026-10-29: «لما اعدل بيانات كتاب ابغاها تنحط في قائمة مستقلة يعيد البحث عنها بناءا على البيانات
    المضافة. مثلا لو اضفت الغلاف او الدار»): a change of what identifies the book puts it on «بانتظار إعادة البحث»."""
    try:
        research_queue.add(book_id, fields, _who())
    except Exception:
        app.logger.exception("research queue")


# BKREV (Ibrahim 2026-10-29: «لما نراجع كتاب في المكتبة ونعدل في البيانات كيف خلاص نأكد انها تم مراجعتها عشان نصفي اللي
# تحتاج مراجعة»): «تمت مراجعة الكتاب» in the book card. The reviewer had the book in hand, so: every value on its card that
# no one confirmed yet is confirmed in their name (human_verified — what «✓ تأكيد» does one by one), the card is recomputed
# («معتمد»), and «يحتاج تأكيد هوية» becomes «تم التعرف» with a note naming them. The book then leaves «تحتاج مراجعة».
# Written in the audit with what was changed, so «إلغاء المراجعة» puts back exactly that.
def _reviewed_map():
    """book_id -> {"at", "by"} of its standing review (the last book.reviewed not followed by book.unreviewed)."""
    conn = get_connection()
    try:
        out = {}
        for r in conn.execute("SELECT at, username, action, target, details FROM audit_log "
                              "WHERE action IN ('book.reviewed', 'book.unreviewed') ORDER BY log_id"):
            if not str(r["target"] or "").isdigit():
                continue
            if r["action"] == "book.reviewed":
                d = {}
                try:
                    d = json.loads(r["details"] or "{}")
                except ValueError:
                    pass
                out[int(r["target"])] = {"at": r["at"], "by": d.get("by") or r["username"]}
            else:
                out.pop(int(r["target"]), None)
        return out
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


@app.route("/api/admin/books/<int:book_id>/reviewed", methods=["POST"])
def admin_book_reviewed(book_id):
    book = get_book(book_id)
    if book is None:
        return jsonify({"error": "book not found"}), 404
    if (request.get_json(silent=True) or {}).get("confirm") is not True:
        return jsonify({"ok": False, "error": "يلزم التأكيد"}), 400
    who = _who() or "موظف"
    now = auth._now()
    # the reader-impression values (the card's «صفات القرّاء») are shown to the developer only, so only a developer's review
    # confirms them; a staff review is about the book in hand: its identity and the data they corrected (Ibrahim 2026-10-29
    # «مو محدد وش الشيء المطلوب اراجعه» → the block lists what to check)
    dev = auth.is_developer((acting_user() or {}).get("role"))
    conn = get_connection()
    try:
        ids = [] if not dev else [r[0] for r in conn.execute(
            """SELECT evidence_id FROM attribute_evidence WHERE book_id = ? AND COALESCE(rejected, 0) = 0
                   AND source_site <> 'derived' AND attribute_value <> 'unknown' AND COALESCE(human_verified, 0) = 0""",
            (book_id,))]
        if ids:
            conn.executemany("UPDATE attribute_evidence SET human_verified = 1, verified_by = ?, verified_at = ? WHERE evidence_id = ?",
                             [(who, now, i) for i in ids])
        card_review.recompute_card(conn, book_id)
        rs_before = book["research_status"]
        note_before = book["research_note_ar"] if "research_note_ar" in book.keys() else None
        if rs_before == "needs_confirmation":
            conn.execute("UPDATE books SET research_status = 'matched', research_note_ar = ? WHERE book_id = ?",
                         (f"أكّد {who} هوية الكتاب عند مراجعته في المكتبة ({now[:10]})", book_id))
        conn.commit()
    finally:
        conn.close()
    auth.audit(acting_user(), "book.reviewed", book_id, {"by": who, "evidence_ids": ids, "research_status": rs_before,
                                                          "research_note_ar": note_before})
    return jsonify({"ok": True, "confirmed": len(ids), "identity_confirmed": rs_before == "needs_confirmation"})


@app.route("/api/admin/books/<int:book_id>/reviewed", methods=["DELETE"])
def admin_book_unreviewed(book_id):
    if get_book(book_id) is None:
        return jsonify({"error": "book not found"}), 404
    conn = get_connection()
    try:
        last = conn.execute("SELECT action, details FROM audit_log WHERE target = ? AND action IN ('book.reviewed', 'book.unreviewed') "
                            "ORDER BY log_id DESC LIMIT 1", (str(book_id),)).fetchone()
        if not last or last["action"] != "book.reviewed":
            return jsonify({"ok": False, "error": "الكتاب غير مُعلَّم كمراجَع"}), 400
        d = json.loads(last["details"] or "{}")
        ids = [int(i) for i in d.get("evidence_ids") or []]
        if ids:   # only the values this review confirmed (a value confirmed one by one before stays confirmed)
            conn.executemany("UPDATE attribute_evidence SET human_verified = 0, verified_by = NULL, verified_at = NULL "
                             "WHERE evidence_id = ? AND book_id = ?", [(i, book_id) for i in ids])
        card_review.recompute_card(conn, book_id)
        if d.get("research_status") == "needs_confirmation":
            conn.execute("UPDATE books SET research_status = 'needs_confirmation', research_note_ar = ? WHERE book_id = ?",
                         (d.get("research_note_ar"), book_id))
        conn.commit()
    finally:
        conn.close()
    auth.audit(acting_user(), "book.unreviewed", book_id, {"evidence_ids": ids})
    return jsonify({"ok": True})


# «اجلب من الرابط» (DEVFETCH, Ibrahim 2026-10-29: «انا ابغى ابحث يدويا يعني يشوف المصدر وياخذ البيانات»): the developer pastes
# the page he found; it is read (backend/page_extract.py) and what it says is returned to be ticked and saved through the
# usual PATCH with that page as the source. Nothing is written here. A cover found there is offered to «cover/choose».
@app.route("/api/admin/books/<int:book_id>/from-page", methods=["POST"])
def admin_book_from_page(book_id):
    book = get_book(book_id)
    if book is None:
        return jsonify({"error": "book not found"}), 404
    # open to every staff member since 2026-10-29 (Ibrahim «افتحها للموظفين كلهم»)
    url = str((request.get_json(silent=True) or {}).get("url") or "").strip()[:500]
    try:
        out = page_extract.extract(url)
    except page_extract.PageError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if out.get("cover"):
        now = time.time()
        prev = (_COVER_CHOICES.get(book_id) or (now, {}))[1]
        _COVER_CHOICES[book_id] = (now, dict(prev, **{out["cover"]: "web_images"}))
    current = {k: book[k] for k in page_extract.FIELDS}
    return jsonify(dict(out, ok=True, current=current))


# «نبذة بالعربي من وصف الطبعة الأصلية» (DEVFETCH, Ibrahim 2026-10-29: «لو كتاب مافي اي نبذة عنه في المواقع العربية. بينما هو
# اصلا مترجم. ابغى النبذة تأخذ من النسخة الاصلية وتترجم للعربي»): the description read from the page the developer pasted
# (Goodreads / the publisher of the original) is turned by the active model into a short Arabic نبذة in our own words —
# not a word-for-word translation of the publisher's text — shown for him to edit and save with that page as its source.
SUMMARY_FROM_TEXT_SYSTEM = (
    "أنت تكتب «نبذة» عربية قصيرة لكتاب في فهرس مكتبة. يصلك وصف الكتاب كما هو في صفحة طبعته الأصلية (غالبًا بلغة غير العربية). "
    "اكتب نبذة بالعربية الفصحى السهلة، من جملتين إلى أربع جمل، لا تزيد على ٥٥٠ حرفًا، بكلماتك أنت لا ترجمة حرفية للوصف. "
    "قل عمّ يدور الكتاب: موضوعه أو حكايته وشخصيته الرئيسية وأين ومتى إن ذُكر، دون كشف النهاية. "
    "لا تضف أي معلومة ليست في الوصف، ولا مديحًا ولا عبارات تسويق، ولا أسعارًا ولا جوائز ولا آراء قرّاء، ولا تذكر اسم الموقع. "
    "اكتب أسماء الأعلام كما تُكتب عادة بالعربية، واستعمل عنوان الكتاب العربي إن ذكرته. "
    "أعد كائن JSON فيه المفتاح summary فقط.")


@app.route("/api/admin/books/<int:book_id>/summary-from-text", methods=["POST"])
def admin_book_summary_from_text(book_id):
    book = get_book(book_id)
    if book is None:
        return jsonify({"error": "book not found"}), 404
    # open to every staff member since 2026-10-29 (Ibrahim «افتحها للموظفين كلهم»)
    text = re.sub(r"\s+", " ", str((request.get_json(silent=True) or {}).get("text") or "")).strip()[:3000]
    if len(text) < 40:
        return jsonify({"ok": False, "error": "الوصف قصير جدًا"}), 400
    user = (f"الكتاب بالعربية: «{book['title']}» — {book['author']}" + (f"، ترجمة {book['translator']}" if book["translator"] else "")
            + f"\n\nوصف الطبعة الأصلية:\n{text}")
    try:
        out = llm.complete_json(SUMMARY_FROM_TEXT_SYSTEM, [{"role": "user", "content": user}],
                                {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
                                max_tokens=600)
    except Exception as e:  # noqa: BLE001 - shown to the developer as one line
        return jsonify({"ok": False, "error": f"النموذج ما رد الآن ({str(e)[:120]})"}), 503
    summary = re.sub(r"\s+", " ", str((out or {}).get("summary") or "")).strip()
    if not summary:
        return jsonify({"ok": False, "error": "النموذج ما كتب نبذة"}), 503
    return jsonify({"ok": True, "summary": summary[:700], "model": llm.last_call.get("model")})


@app.route("/api/admin/books/<int:book_id>/research-done", methods=["POST"])
def admin_book_research_done(book_id):
    """Developer: the book on «بانتظار إعادة البحث» was researched again («تمت إعادة البحث»)."""
    if get_book(book_id) is None:
        return jsonify({"error": "book not found"}), 404
    if not auth.is_developer((acting_user() or {}).get("role")):
        return jsonify({"ok": False, "error": "للمطور فقط"}), 403
    note = (request.get_json(silent=True) or {}).get("note")
    n = research_queue.set_done(book_id, _who(), note)
    auth.audit(acting_user(), "book.research_done", book_id, {"closed": n, "note": note})
    return jsonify({"ok": True, "closed": n})


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


def oldest_year_label(book, en=False):
    """QADFIX16 (QAD 2026-10-24, case 4.2): the year «الأقدم» goes by — first published, else our edition's year said as
    such (the latest the work can have come out)."""
    y = first_published(book)
    if y:
        return str(y)
    return (f"our edition {edition_year(book)}; its first year is not recorded" if en
            else f"طبعتنا {edition_year(book)}، وسنة صدوره الأول غير مسجلة")


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


def named_author_books(names, all_books, message, limit=8):
    """QADFIX22 (QAD 2026-10-27, case 2.1): the in-stock books of the authors the visitor named, for a book asked by its
    author and its subject («رواية ديفيد معلوف اللي تحكي عن حياة الشاعر أوفيد») whose title we could not find by name.
    The books whose title or summary share the most words with the message come first -> list of books."""
    found = {}
    for n in names:
        if not isinstance(n, str) or not n.strip():
            continue
        rec = authors.lookup(n)
        ids = authors.book_ids(rec["author_id"]) if rec else set()
        spell = [n] + (authors.names_of(rec) if rec else [])
        for b in all_books.values():
            if not is_available(b) or b["book_id"] in found:
                continue
            if b["book_id"] in ids or any(author_matches(s, b.get("author")) for s in spell):
                found[b["book_id"]] = b
    words = {w for w in normalize(message).split() if len(w) > 2}
    def _overlap(b):
        text = set(normalize(" ".join([b.get("title") or "", b.get("summary") or ""])).split())
        return len(words & text)
    return sorted(found.values(), key=lambda b: (-_overlap(b), b["book_id"]))[:limit]


_RULED_OUT = re.compile(r"(?:^| )(?:غير|عدا|سوي|سوى|بدون|الا|ماعدا|except|besides|other than|apart from)\s+(?:(?:روايه|كتاب|رواية|the|book)\s+)?(.+)")


def visitor_ruled_out(book_ids, all_books, user_texts):
    """QADFIX23 (case 16): the books among `book_ids` whose title the visitor ruled out in words («غير مية عام من العزله»،
    «ما عدا الحب في زمن الكوليرا»، "anything except Dune") -> set of ids."""
    tails = []
    for t in user_texts:
        k = " " + chat_rules.title_key(normalize(t)) + " "
        for m in _RULED_OUT.finditer(k):
            tails.append(" " + m.group(1).strip() + " ")
    out = set()
    if not tails:
        return out
    for i in set(book_ids):
        b = all_books.get(i)
        if not b:
            continue
        tk = chat_rules.title_key(normalize(b.get("title") or ""))
        head = chat_rules.title_key(normalize(re.split(r"\s*[:：(\[–—-]\s*", b.get("title") or "")[0]))
        for f in {tk, head}:
            if f and len(f) >= 4 and (len(f.split()) >= 2 or len(f) >= 6) and any(tl.startswith(" " + f + " ") or (" " + f + " ") in tl[:len(f) + 12] for tl in tails):
                out.add(i)
                break
    return out


# QADFIX24 (QAD 2026-10-30, case 8.2, grave): «طيب انسى الفيلم، ابي بس قصة تناسب عمره ٤» — «انسى / خلك من / بدون / مو لازم X»
# lifts the wish X said before (the screen adaptation, a prize), like «غير X» rules out a title: the cartoon wish stayed and
# only «أليس في بلاد العجائب» (7–11) was left, so «ما عندنا قصة لعمر ٤» was said with «سمكة قوس قزح» (4–8) in stock
_FORGET_WISH = re.compile(r"(?:^| )(?:انسي|انسى|انساك من|خلك من|خليك من|خلنا من|خلينا من|بلاش|بدون|بلا|مو لازم|مب لازم|مش لازم"
                          r"|ما يهم|مايهم|مو شرط|مب شرط|مش شرط|لا تهتم ب|لا تهتمين ب|forget(?: about)?|never mind|no need for|skip"
                          r"|doesn.t (?:need|have) to (?:be|have)(?: a)?)\s+(?:ال|the |a |an )?(\S+)")
_FORGET_KIND = (("adaptation", re.compile(r"فلم|فيلم|افلام|مسلسل|كرتون|كارتون|انمي|انيمي|رسوم|film|movie|series|show|cartoon|anime")),
                ("awards", re.compile(r"جائز|جوائز|بوكر|نوبل|prize|award")))


def forgotten_wishes(text):
    """QADFIX24: the earlier wishes the latest message lifts («انسى الفيلم»، «مو لازم جائزة»، "forget the movie") -> set of
    "adaptation" / "awards"."""
    out = set()
    for m in _FORGET_WISH.finditer(" " + " ".join(normalize(text or "").split()) + " "):
        for kind, rx in _FORGET_KIND:
            if rx.search(m.group(1)):
                out.add(kind)
    return out


# QADFIX24 (case 6.2): an order asked over all of an author's books («أقصر رواية له عندكم»، «أقصر شي كتبه»، "his shortest")
_AUTHOR_WIDE = re.compile(r" (?:له|لها|لهم) (?:عندكم|عندك|كلها|كلهم|كله|في المكتبه)(?= )| (?:من )?(?:كتبه|كتبها|رواياته|رواياتها|اعماله|اعمالها"
                          r"|مؤلفاته|مؤلفاتها)(?= )| (?:كتبه|كتبها|الفه|الفها)(?= )| (?:of|by) (?:his|her|him) | his (?:books|novels|works) "
                          r"| her (?:books|novels|works) ")


# QADFIX24 (cases 49.1, 45.1): a part number written in the title only («(ج 2)»، «ج3»، «الجزء الثاني»، "Vol. 2")
_TITLE_PART = re.compile(r"[\s(\[]+(?:ج|جزء|الجزء|vol\.?|volume|part|book)\s*[.:]?\s*(\d{1,2}|الاول|الأول|الثاني|الثالث|الرابع|الخامس)\s*[)\]]?\s*$",
                         re.I)
_PART_WORDS = {"الاول": 1, "الأول": 1, "الثاني": 2, "الثالث": 3, "الرابع": 4, "الخامس": 5}


def title_part(title):
    """-> (the title without its part, the part number) or None: «روايتي لروايتي (ج 2)» -> («روايتي لروايتي», 2)."""
    t = str(title or "").strip()
    m = _TITLE_PART.search(t)
    if not m or m.start() == 0:
        return None
    n = m.group(1)
    num = int(n) if n.isdigit() else _PART_WORDS.get(n)
    base = chat_rules.title_key(normalize(t[:m.start()].strip(" -–—:،,")))
    return (base, num) if num and base else None


# QADFIX33 (QAD 2026-11-10 cases 1.1–1.2, medium): «جهينة كلها موجودة عندكم؟» → «ثلاثية جهينة … كاملة: الجزء الأول، والجزء الثاني،
# والجزء الثالث» with no card, then «ابدأ بجهينة ج1» with no card either (the reply names the parts by their number, not by the whole
# title «جهينة ج1»): a part of the series asked is named when the reply names the series and that part's number
_ORD33 = {1: r"(?:ال)?اول|ج ?1|الجزء 1|جزء 1|first|part 1|book 1|volume 1", 2: r"(?:ال)?ثاني|ج ?2|الجزء 2|جزء 2|second|part 2|book 2|volume 2",
          3: r"(?:ال)?ثالث|ج ?3|الجزء 3|جزء 3|third|part 3|book 3|volume 3", 4: r"(?:ال)?رابع|ج ?4|الجزء 4|جزء 4|fourth|part 4|book 4|volume 4",
          5: r"(?:ال)?خامس|ج ?5|الجزء 5|جزء 5|fifth|part 5|book 5|volume 5", 6: r"(?:ال)?سادس|ج ?6|الجزء 6|جزء 6|sixth|part 6|book 6"}


def series_parts_named33(reply, books):
    """-> the ids of the books (parts of one series asked about) whose part the reply names by its number («الجزء الأول»، «ج1»،
    «والجزء الثالث») while it names the series itself («جهينة»، «ثلاثية جهينة»), or []."""
    t = " " + " ".join(normalize(chat_rules.num_canon(re.sub(r"[«»\"“”()\[\]،,.:؛;!?؟]", " ", reply or ""))).split()).lower() + " "
    parts = {}
    for b in books:
        pos = b.get("series_position") if isinstance(b.get("series_position"), (int, float)) else None
        tp = title_part(b.get("title"))
        name = (b.get("series_name") or "").strip() or (tp[0] if tp else "")
        if pos is None and tp:
            pos = tp[1]
        if name and pos and int(pos) == pos and int(pos) in _ORD33:
            parts.setdefault(" ".join(normalize(name).split()), []).append((int(pos), b["book_id"]))
    out = []
    for name, ps in parts.items():
        words = [strip_al(w) for w in name.split() if len(strip_al(w)) >= 3 and w not in ("سلسله", "ثلاثيه", "رباعيه", "series")]
        if not words or not all(re.search(r" (?:[وبلفك]|ال|وال|بال|لل)?" + re.escape(w) + r"(?: |$)", t) for w in words):
            continue
        for pos, i in ps:
            if re.search(r"(?:^| )(?:[وب]|ال|وال|بال)?(?:" + _ORD33[pos] + r")(?: |$)", t):
                out.append(i)
    return out


# QADFIX24 (replay of case 3.1): books for toddlers by their form, named in the title
_TODDLER_FORM = re.compile(r"طي اللوحات|اللوحات القابله للطي|كتب? قماش|القماشيه|المس واشعر|لمس وتحسس|كتاب الحمام|lift.the.flap|board book|touch and feel")


_COMICS_SAID = re.compile(r"كوميكس|كومكس|كومك|مانجا|مانغا|قصص مصوره|قصه مصوره|روايه مصوره|روايات مصوره|comic|manga|graphic novel")


def title_author_said(title, message, all_books):
    """QADFIX23 (case 37): the author the visitor gave with a title — «المسخ لكافكا»، «المسخ حق فرانز كافكا»، «Metamorphosis by
    Kafka» -> the author's name as written (one or two words) when it names a writer we know or stock, else None."""
    t = " ".join(normalize(title or "").split())
    m = " " + " ".join(normalize(message or "").split()) + " "
    if not t or (" " + t + " ") not in m:
        return None
    after = m.split(" " + t + " ", 1)[1]
    g = re.match(r"(?:(?:حق|حقت|تبع|للكاتب|للمؤلف|by|from)\s+|ل(?=\S{3,}))(\S+)(?:\s+(\S+))?", after)
    if not g:
        return None
    first, second = g.group(1), g.group(2)
    if g.group(0).startswith("ل" + first) and chat_rules.lam_is_word("ل" + first):
        # QADFIX30 #4 (QAD 2026-11-07 case 45.1, grave): «بس مو العادات الذرية لأني قريته» → «لـاني» (آني إرنو) — «لأني» is «because I»
        return None
    stocked = {b.get("author") for b in all_books.values() if is_available(b) and b.get("author")}
    for cand in ([first + " " + second] if second else []) + [first]:
        if len(cand) >= 3 and (authors.lookup(cand) or any(author_matches(cand, a) for a in stocked)):
            return cand
    return None


_ABOUT_LIST = re.compile(r"(?:^| )(?:عن|حول|about|on)\s+((?:\S+)(?:\s+(?:او|و|ولا|or|and)\s+\S+){0,3})")
_title_word_count = {"n": None, "words": {}}


_first_names_cache = {"n": None, "names": frozenset()}
_NOT_FIRST_NAMES = {normalize(w) for w in "مجموعه مجموعة د دكتور الدكتور الشيخ الامام الإمام غير unknown".split()}


def _author_first_names(all_books):
    """CHATFIX30 follow-up: the first words of our authors' names (catalog strings and author records), normalized —
    «هنري»، «روي»، «علي» -> frozenset; cached while the catalog size stays the same."""
    if _first_names_cache["n"] != len(all_books):
        names = set()
        people = set()
        for b in all_books.values():
            people.update(author_flow._people(author_flow.display(b.get("author") or "")))
        try:
            conn = get_connection()
            try:
                people.update(r[0] for r in conn.execute("SELECT name_ar FROM authors") if r[0])
            finally:
                conn.close()
        except sqlite3.OperationalError:
            pass
        for p in people:
            ws = normalize(p).split()
            if len(ws) >= 2 and ws[0] not in _NOT_FIRST_NAMES:
                names.add(ws[0])
        _first_names_cache.update(n=len(all_books), names=frozenset(names))
    return _first_names_cache["names"]


def _title_word_count_of(word, all_books):
    """How many available titles carry this word (without «ال») — a family name is in few or none."""
    title_subject_names("", all_books)   # fills _title_word_count for this catalog
    return _title_word_count["words"].get(strip_al(normalize(word).strip()), 0)


_TSN_GENERIC = {strip_al(normalize(w)) for w in "كتاب كتب كتابا روايه رواية روايات قصه قصص ديوان مجموعه سلسله book novel".split()}


def title_subject_names(message, all_books, most=8):
    """QADFIX23 (case 2.2): the words after «عن» (one or a few joined by «او / و») that stand in the titles of 1 to `most` books in
    stock — a philosopher, a person, a rare subject («هيغل»، «هوبز») -> list of words as written."""
    avail = [b for b in all_books.values() if is_available(b)]
    if _title_word_count["n"] != len(avail):
        cnt = {}
        for b in avail:
            for w in {strip_al(x) for x in normalize(b.get("title") or "").split()}:
                cnt[w] = cnt.get(w, 0) + 1
        _title_word_count.update(n=len(avail), words=cnt)
    out = []
    for m in _ABOUT_LIST.finditer(" " + " ".join(normalize(message or "").split()) + " "):
        for w in re.split(r"\s+(?:او|و|ولا|or|and)\s+", m.group(1)):
            w = w.strip()
            k = strip_al(w)
            # CHATFIX30: «ايش تعرف عن كتاب زمان الملائكة؟» — «كتاب» after «عن» names no subject (it was logged as a wish)
            if (len(k) >= 4 and 1 <= _title_word_count["words"].get(k, 0) <= most and w not in out
                    and k not in _TSN_GENERIC):
                out.append(w)
    return out


_THALATH_READERS = re.compile(r"(?:بعض\s+)?(?:قر[ّ]?اء|زو[ّ]?ار)\s+(?:في\s+)?(?:مكتبة\s+ثلاث|مكتبتنا|المكتبة)|Thalath(?:'s)?\s+(?:library\s+)?readers|readers\s+(?:at|of)\s+(?:the\s+)?Thalath(?:\s+library)?",
                              re.IGNORECASE)
_NO_CLAIM = re.compile(r"ما عندي|ما عندهم|ما فيه|لا يوجد|ليس لدي|لم يقيّم|ما قيّم|no rating|not rated|haven't rated|no review", re.IGNORECASE)


def thalath_readers_unclaimed(reply):
    """QADFIX23: a sentence that credits «قرّاء مكتبة ثلاث» with an impression, when no approved review of theirs is in facts, says
    «بعض القرّاء» instead; a sentence saying they have NOT rated it stays."""
    parts = re.split(r"(?<=[.!؟?؛])(\s+)", reply or "")
    out = []
    for p in parts:
        if _THALATH_READERS.search(p) and not _NO_CLAIM.search(p):
            p = _THALATH_READERS.sub(lambda m: "some readers" if re.match(r"[A-Za-z]", m.group(0)) else "بعض القرّاء", p)
        out.append(p)
    return "".join(out)


def _q24_name_forms(ask):
    """The spellings of the person asked about (as written, full, aliases, and our record's names)."""
    names = [ask.get("name_as_written"), ask.get("full_name")] + list(ask.get("aliases") or [])
    rec = None
    for n in names:
        if isinstance(n, str) and n.strip():
            rec = authors.lookup(n)
            if rec:
                break
    if rec:
        names += authors.names_of(rec)
    return [n for n in names if isinstance(n, str) and n.strip()]


def name_said_now(ask, message):
    """QADFIX24 (case 42.2): the person is named in this message (any spelling, by a word of 3+ letters other than «ابو / ابن …»),
    or pointed at («كتاب عنه / عنها»)."""
    m = " " + " ".join(normalize(message or "").split()) + " "
    if re.search(r" (?:عنه|عنها|حوله|حولها|about (?:him|her)) ", m):
        return True
    words = {strip_al(w) for w in m.split()}
    for n in _q24_name_forms(ask):
        toks = {strip_al(w) for w in normalize(n).split() if len(strip_al(w)) >= 3} - {"ابو", "ابن", "بن", "بنت", "عبد", "ام"}
        if toks and toks & words:
            return True
    return False


_LIKENESS_LEAD = re.compile(r"(?:^| )(?:مثل|زي|زيّ|شبه|يشبه|تشبه|نفس|على طريقه|علي طريقه|باسلوب|بأسلوب|اسلوب|ستايل|like|similar to|in the style of"
                            r"|style of|the likes of)\s+(?:\S+\s+){0,2}$")


def name_as_likeness(ask, message):
    """QADFIX24 (case 42.1): the person is named as the likeness of the books wanted («مثل أجاثا كريستي»، "like Agatha Christie")."""
    m = " ".join(normalize(message or "").split())
    for n in _q24_name_forms(ask):
        toks = [strip_al(w) for w in normalize(n).split() if len(strip_al(w)) >= 3]
        for t in toks:
            for w in m.split():
                if strip_al(w) == t:
                    before = m.split(" " + w, 1)[0] if (" " + w) in m else ""
                    if _LIKENESS_LEAD.search(before + " "):
                        return True
    return False


def books_about_person(author_request, all_books, limit=6):
    """QADFIX23 (QAD 2026-10-28, case 30, grave): «ابوي يعشق المتنبي وابي اهديه شي» — a writer we hold no book of (his
    record exists, nothing of his is in stock), whose name is in the TITLE of books in stock («مع المتنبي»، «في أثر المتنبي»،
    «رباط المتنبي»): those books are about him, the answer — never «تقصد «المتنبي»؟».
    -> (the name as the records hold it, [books]) or (None, [])."""
    rec = known_absent_author(author_request, all_books)
    if not rec:
        return None, []
    spellings = [n for n in authors.names_of(rec) + [author_request.get("name_as_written"), author_request.get("full_name")]
                 if isinstance(n, str) and n.strip()]
    # the word that names him alone (the last word of the stored Arabic name: «المتنبي» of «أبو الطيب المتنبي»): «الطيب» in
    # «محمد الطيب الأنصاري» is another man (QADFIX23 replay, case 30.2)
    _core = [strip_al(w) for w in normalize(rec.get("name_ar") or "").split() if len(strip_al(w)) >= 3]
    _core = _core[-1] if _core else None
    sets = []
    for n in spellings:
        toks = {strip_al(w) for w in normalize(n).split() if len(strip_al(w)) >= 3}
        toks -= {"ابو", "ابن", "بن", "بنت", "عبد", "ام"}
        if toks and toks not in sets and (_core is None or _core in toks):
            sets.append(toks)
    if not sets:
        return None, []
    hits = []
    for b in all_books.values():
        if not is_available(b):
            continue
        title = {strip_al(w) for w in normalize(b.get("title") or "").split()}
        if any(t <= title for t in sets):
            hits.append(b)
    hits.sort(key=lambda b: b["book_id"])
    return (rec["name_ar"], hits[:limit]) if hits else (None, [])


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


# AUDFIX 2026-11-05 (Codex audit P2): work_copies_other_titles offers books that MAY be the same work (any copy of the author in
# another language); the language-copy swap of QADFIX28 took the same author record + the same first year as proof, so a synthetic
# Arabic guide on farming gave way to an unrelated English murder novel of that author and year, and kept the old reason. The same
# work is now proven by a work-level record only: the same title, the same work record behind the first year (a Wikidata work,
# an Open Library work), the same Goodreads book, or an entry of backend/work_links.json (sourced).
_WORK_REF = re.compile(r"(wikidata\.org/(?:wiki|entity)/Q\d+|openlibrary\.org/works/OL\d+W)", re.I)
_GR_BOOK = re.compile(r"goodreads\.com/book/show/(\d+)", re.I)
_WORK_LINKS = {"titles": None}


def _work_ids(text):
    return {m.lower() for m in _WORK_REF.findall(str(text or ""))}


def _work_links():
    """{title_key: {work record, ...}} from backend/work_links.json, read once (an entry's «work» is one record or a list)."""
    if _WORK_LINKS["titles"] is None:
        m = {}
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "work_links.json"), encoding="utf-8") as fh:
                for w in json.load(fh).get("works") or []:
                    ids = _work_ids(" ".join(w["work"]) if isinstance(w.get("work"), list) else w.get("work"))
                    for t in w.get("titles") or []:
                        if chat_rules.title_key(t) and ids:
                            m.setdefault(chat_rules.title_key(t), set()).update(ids)
        except (OSError, ValueError, KeyError, TypeError):
            m = {}
        _WORK_LINKS["titles"] = m
    return _WORK_LINKS["titles"]


def work_records(book):
    """The work-level records of a copy: the Wikidata / Open Library work behind its first year, and its entry in
    work_links.json. An edition-level reference (an ISBN page, a library record, a Wikipedia page) is no work record."""
    return _work_ids(book.get("original_year_ref")) | _work_links().get(chat_rules.title_key(book.get("title") or ""), set())


def same_work_copy(book, other, ba=None):
    """True only when «other» is proven to be a copy of the very same work as «book» (a translation or another edition), never
    for the same author and first year alone (AUDFIX 2026-11-05): a shared author record, no clash of first year, original
    language or part number (both titles numbered differently), and one work-level sign — the same title, a shared work record
    (work_records), or the same Goodreads book."""
    if not book or not other or book.get("book_id") == other.get("book_id"):
        return False
    ba = book_author_map() if ba is None else ba
    if not (set(ba.get(book.get("book_id"), [])) & set(ba.get(other.get("book_id"), []))):
        return False
    for k in ("original_year", "original_language"):
        a, b = book.get(k), other.get(k)
        if a not in (None, "") and b not in (None, "") and str(a).strip().lower() != str(b).strip().lower():
            return False
    ta, tb = chat_rules.title_key(book.get("title") or ""), chat_rules.title_key(other.get("title") or "")
    na, nb = re.findall(r"\d+", ta), re.findall(r"\d+", tb)
    if na and nb and na != nb:
        return False
    if ta and ta == tb:
        return True
    if work_records(book) & work_records(other):
        return True
    ga = _GR_BOOK.search(str(book.get("goodreads_url") or ""))
    gb = _GR_BOOK.search(str(other.get("goodreads_url") or ""))
    return bool(ga and gb and ga.group(1) == gb.group(1))


def edition_fact(book, language="ar"):
    e = {"id": book["book_id"], "publisher": (book.get("publisher") or "").strip() or None,
         "price_sar": book.get("price"), "pages_approx": book.get("page_count"),
         "copies": book.get("quantity") or 0, "edition_year": edition_year(book)}
    if book.get("language") in ("en", "ar"):
        e["copy_language"] = {"en": "English", "ar": "Arabic"}[book["language"]] if language == "en" else \
            {"en": "الإنجليزية", "ar": "العربية"}[book["language"]]
    return {k: v for k, v in e.items() if v not in (None, "")}


def _copy_reason1106(why, old, new):
    """AUDFIX 2026-11-06 (Codex audit P2): a reason written for one copy («سعره ٢٠ ريال», «عدد صفحاته ١٠٠», «100 pages»)
    quotes the other copy's price and page count when that copy takes its place; a figure the new copy lacks is dropped."""
    if not why:
        return why
    out = str(why)
    op, np_ = old.get("price"), new.get("price")
    if op:
        out = out.replace(f"سعره {op:g} ريال", f"سعره {np_:g} ريال" if np_ else "")
        out = out.replace(f"{op:g} SAR", f"{np_:g} SAR" if np_ else "")
    og, ng = old.get("page_count"), new.get("page_count")
    if og:
        out = out.replace(f"عدد صفحاته {fmt_num(og)}", f"عدد صفحاته {fmt_num(ng)}" if ng else "")
        out = out.replace(f"{og} pages", f"{ng} pages" if ng else "")
    out = re.sub(r"\s*[،,]\s*(?=[،,]|$)", "", out).strip(" ،,")
    return out


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
    if language == "en" and re.search(r"[؀-ۿ]", book.get("author") or ""):
        # QADFIX28: the author's name in Latin letters from the author records, for an English reply
        _al28 = book_author_latin().get(book["book_id"])
        if _al28:
            fact["author_latin"] = _al28
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
    # «قرّاء مكتبة ثلاث» (Ibrahim 2026-10-27): what this library's own visitors said after reading it (reviews staff approved)
    _tr = visitor_reviews.summary(book, language, quotes=2)
    if _tr:
        fact["thalath_readers"] = {"stars_out_of_5": _tr["avg"], "readers": _tr["count"],
                                   "said": [f"{t['label']} ({t['n']})" for t in _tr["tags"][:4]],
                                   "quotes": [r["text"][:200] for r in _tr["reviews"]]}
    # CHATFIX30c (1): the readers' average on Goodreads (GRRATE, the page it was read on is books.goodreads_rating_source),
    # given only with enough ratings to mean something; the only rating Manar may speak of besides «قرّاء مكتبة ثلاث»
    if book.get("goodreads_rating") and (book.get("goodreads_ratings_count") or 0) >= chat_rules.RATING_MIN_COUNT:
        fact["goodreads_rating"] = {"average_out_of_5": round(float(book["goodreads_rating"]), 1),
                                    "ratings": int(book["goodreads_ratings_count"]), "site": "Goodreads"}
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


# QADFIX30 m8 (QAD 2026-11-07 case 42.1, medium): "Locus award winners عندكم؟ بالعربي او انجليزي ما تفرق" → «٣ ضمن طلبك» (the Arabic
# copies only) while 10 winners are in stock: both languages named with «ما تفرق / أي وحدة / either» is no copy-language wish
_ANY_LANG30 = re.compile(r" (?:بال)?(?:عربي|عربيه|العربي|العربيه)\s+(?:او|ولا|و|أو|or|and)\s+(?:بال|ال)?(?:انجليزي|انجليزيه|انقليزي|انقليزيه)"
                         r"(?= ).*(?:ما تفرق|ماتفرق|ما يفرق|مايفرق|ما فرق|مو مهم|مب مهم|ما يهم|مايهم|عادي|كلها|كله|اي وحده|اي واحد|either|any|doesn t matter)"
                         r"| (?:بال)?(?:انجليزي|انجليزيه|انقليزي)\s+(?:او|ولا|و|or|and)\s+(?:بال|ال)?(?:عربي|عربيه)(?= ).*(?:ما تفرق|ماتفرق|ما يفرق|مو مهم|ما يهم|عادي|either|any)"
                         r"| (?:arabic or english|english or arabic|either language|any language|اي لغه|ايه لغه|اللغه ما تفرق|اللغه مو مهمه|اللغه ما تهم) ")


def wanted_edition(text, language="ar"):
    """-> "en" / "ar" / None. In the English chat «English novels» means books in English."""
    words = normalize(text).split()
    if _ANY_LANG30.search(" " + " ".join(words) + " "):
        return None   # QADFIX30 m8 (QAD 2026-11-07 case 42.1): «بالعربي او انجليزي ما تفرق» — either language
    # «رواية انجليزية مترجمة» names the original language: only an explicit copy wish («بالانجليزي، نسخة انجليزية») counts then
    translated = any(w.startswith(("مترجم", "translat")) for w in words)
    # QADFIX35 (replay of QAD 2026-11-12 case 24.2): «أبيها بالعربي مو بالإنجليزي» was read as English (the first language listed
    # wins): a language said right after «مو / مش / مب / لا / غير / بدون / not / no» is refused, the other one said is the wish
    _neg35 = {"مو", "مب", "مش", "موب", "ماهو", "لا", "غير", "بدون", "not", "no"}
    _refused35 = set()
    for code, phrases in _EDITION.items():
        for p in phrases:
            i = keyword_prefs.find_phrase(words, p)
            if i >= 1 and words[i - 1] in _neg35:
                _refused35.add(code)
    for k, w in enumerate(words[1:], 1):   # («no English copies», «مو انجليزي»: the language word alone)
        if words[k - 1] in _neg35:
            if w in ("english", "انجليزي", "انجليزيه", "انقليزي", "انقليزيه", "الانجليزي", "الانجليزيه"):
                _refused35.add("en")
            elif w in ("arabic", "عربي", "عربيه", "العربي", "العربيه"):
                _refused35.add("ar")
    for code, phrases in _EDITION.items():
        if code in _refused35:
            continue
        if translated:
            phrases = [p for p in phrases if p.startswith(("بال", "باللغه", "نسخه", "نسخته", "طبعه", "in ", "arabic ", "english edition", "english version"))]
        if any(keyword_prefs.find_phrase(words, p) >= 0 for p in phrases):
            return code
    if language == "en" and "english" in words and "en" not in _refused35:
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
# QADFIX34 #6 (QAD 2026-11-11 case 34.1, medium): «روايات طالعة هالسنة او السنة اللي قبلها» — the participle «طالعة / نازلة» says it too
_RELEASE_VERB = re.compile(r"(?:^| )(?:صدر|صدرت|صادر|صادره|الصادره|نشر|نشرت|منشور|المنشوره|طلع|طلعت|نزل|نزلت|اصدار|اصدارات"
                           r"|طالع|طالعه|الطالعه|نازل|نازله|النازله"
                           r"|published|released|came out|out)(?: |$)")


_WHO_WON = re.compile(r"(?:^| )و?(?:مين|من|منو|مين اللي|وش|ايش|اي كتاب|اي روايه)\s*(?:اللي )?(?:فاز|فازت|فايز|حصل|اخذ|ربح)"
                      r"|(?:^| )(?:الفائز|الفايز|الفائزه|الفائزين|winner|winners|who won|which book won)(?: |$)")


# «روايات انكتبت في الستينات» = 1960–1969 (QAD 2026-10-15, case 43: the decade went to unsupported_requests)
_DECADES = {"عشرين": 1920, "ثلاثين": 1930, "اربعين": 1940, "خمسين": 1950, "ستين": 1960, "سبعين": 1970,
            "ثمانين": 1980, "تسعين": 1990}
_DECADE_RX = re.compile(r"(?:^| )[وبل]?(?:ال)?(عشرين|ثلاثين|اربعين|خمسين|ستين|سبعين|ثمانين|تسعين)(?:ات|يات)(?: |$)"
                        r"|(?:^| )(?:the )?(twenties|thirties|forties|fifties|sixties|seventies|eighties|nineties)(?: |$)"
                        r"|(?:^| )(1[89][0-9]0)(?:s|ات|يات)(?: |$)")
_DECADES_EN = {"twenties": 1920, "thirties": 1930, "forties": 1940, "fifties": 1950, "sixties": 1960, "seventies": 1970,
               "eighties": 1980, "nineties": 1990}


# QADFIX25 (QAD 2026-10-31, case 49.1, grave): «الأدب الفيكتوري»، «القرن التاسع عشر» are first-publication years
_ERA_WORD = re.compile(r"(?:^| )(?:ال)?(?:فيكتوري|فكتوري|فيكتوريه|فكتوريه)(?: |$)|victorian")
_CENTURY = re.compile(r"(?:^| )(?:ال)?قرن (?:ال)?(ثامن عشر|تاسع عشر|عشرين|العشرين|سابع عشر)(?: |$)|(?:^| )(17|18|19)(?:th)? century")
_CENTURY_START = {"سابع عشر": 1601, "ثامن عشر": 1701, "تاسع عشر": 1801, "عشرين": 1901, "العشرين": 1901,
                  "17": 1601, "18": 1701, "19": 1801}


def era_said(text):
    """«الأدب الفيكتوري» -> (1837, 1901); «القرن التاسع عشر» -> (1801, 1900); else None."""
    t = " ".join(normalize(text or "").split())
    if _ERA_WORD.search(" " + t + " "):
        return 1837, 1901
    m = _CENTURY.search(" " + t + " ")
    if m:
        start = _CENTURY_START[m.group(1) or m.group(2)]
        return start, start + 99
    return None


def decade_said(text):
    """«في الستينات»، «الستينيات»، "the sixties", «1960s» -> (1960, 1969); else None."""
    m = _DECADE_RX.search(" ".join(normalize(text).split()))
    if not m:
        return None
    start = _DECADES.get(m.group(1) or "") or _DECADES_EN.get(m.group(2) or "") or int(m.group(3))
    return start, start + 9


def decade_only29(phrase):
    """QADFIX29 #4 (QAD 2026-11-06 case 10.2, grave): «أحداث تقع في الستينات» → «الستينات» — a wish that is a decade said with story
    words is that decade (it was cut to its rarest word «تقع» and the decade was lost)."""
    t = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", phrase or "")).split())
    m = _DECADE_RX.search(t)
    if not m or not decade_said(t):
        return phrase
    rest = [w for w in (t[:m.start()] + " " + t[m.end():]).split()
            if w not in {"احداث", "الاحداث", "احداثها", "احداثه", "تقع", "يقع", "تدور", "يدور", "تصير", "يصير", "تجري", "يجري", "في", "ب",
                         "خلال", "ايام", "وقت", "زمن", "فتره", "set", "in", "the", "during", "story", "events", "takes", "place"}]
    return phrase if rest else m.group(0).strip()


_DECADE_WORD29 = {v: k for k, v in _DECADES.items()}
_DECADE_EN29 = {v: k for k, v in _DECADES_EN.items()}


def decade_name29(start, language="ar"):
    if language == "en":
        return f"the {start}s"
    w = _DECADE_WORD29.get(start)
    return f"ال{w}ات" if w else f"{start}s"


def decade_fit29(b, start):
    """QADFIX29 #4 (QAD 2026-11-06 cases 10.1–10.2, grave): the book's own text names the decade the story is asked to take place in —
    «في القاهرة الستينيات» (the summary of «قالت ضحى»), «1965», "the sixties". «رجل ستيني» (a man of sixty) is not the decade."""
    t = " " + " ".join(normalize(re.sub(r"[^\w\s]", " ", " ".join(str(b.get(k) or "") for k in
                                                               ("summary", "hook", "setting", "subjects", "keywords")))).split()) + " "
    w = _DECADE_WORD29.get(start)
    if w and re.search(r" [وبلفك]?(?:ال)?" + w + r"(?:ات|يات) ", t):
        return True
    en = _DECADE_EN29.get(start)
    if en and re.search(r" (?:the )?" + en + " ", t):
        return True
    return bool(re.search(r"(?<!\d)" + str(start // 10) + r"\d(?:s|ات|يات)?(?!\d)", t))


# QADFIX29 #1 (QAD 2026-11-06 case 2.1, grave): «آخر سلاطين بني الأحمر ويوم تسليم قصر الحمراء» → «ما لقيت… ولا كتابًا قريبًا» while
# «ثلاثية غرناطة» and «غرناطة (آخر الأيام)» are in stock: the dynasty and the palace were cut to «بنو» / «تسليم». The names of the last
# kingdom of al-Andalus are that kingdom's city
_PLACE_ALIAS29 = [(re.compile(r" (?:بنو|بني|ابناء|ملوك|سلاطين|دوله) (?:ال)?احمر | (?:ال)?نصريين | (?:ال)?نصريون | (?:ال)?دوله (?:ال)?نصريه "
                              r"| (?:بنو|بني) نصر | قصر (?:ال)?حمرا[ءا]? | (?:ال)?حمرا[ءا]? (?:في|ب) غرناطه | (?:ابو|ابي) عبد ?الله (?:ال)?صغير "
                              r"| nasrids? | alhambra | boabdil "), "غرناطة"),
                  # QADFIX29 #3 (QAD 2026-11-06 case 8.2, grave): «الحيوانات البحرية» needs both words and no children's book has
                  # «حيوانات» beside the sea («سمكة قوس قزح»: «أسماك المحيط») — the sea animals are the sea («البحر» finds the ocean too)
                  (re.compile(r" (?:ال)?(?:حيوانات|كائنات|احياء|مخلوقات) (?:ال)?(?:بحريه|البحر|المحيط|المحيطات) | (?:ال)?حياه (?:ال)?بحريه "
                              r"| (?:عالم|اعماق) (?:ال)?(?:بحار|البحر|المحيط|المحيطات) | sea (?:animals|creatures|life) "
                              r"| marine (?:animals|life|creatures) | ocean (?:animals|life|creatures) "), "البحر")]


def place_alias29(phrase):
    t = " " + " ".join(normalize(re.sub(r"[؟?!.،,«»]", " ", phrase or "")).split()) + " "
    for rx, to in _PLACE_ALIAS29:
        if rx.search(t):
            return to
    return phrase


# «وش الروايات اللي وصلت القائمة القصيرة للبوكر العربية؟»: shortlisted ones (and the winners, which were on
# it), not the longlist or a prize of the author (QAD 2026-10-15, case 29.1); «الفائزة» = winners only
_SHORTLIST_RX = re.compile(r"(?:ال)?قائمه (?:ال)?قصيره|shortlist|short list")
_LONGLIST_RX = re.compile(r"(?:ال)?قائمه (?:ال)?طويله|longlist|long list")
# QADFIX25 (QAD 2026-10-31, case 2.1): «كاتبة … اخذت البوكر العربية» — «أخذت / حصلت على / نالت» a prize is winning it
# QADFIX26 (QAD 2026-11-02 case 13.1): «كتبتها امرأة وفازت هي نفسها بالبوكر العربية» — «وفازت» with its «و»
_WINNER_RX = re.compile(r"(?:^| )و?(?:ال)?(?:فائزه|فايزه|فائزين|فايزين|فازت|فاز|اخذت|اخذ|حصلت|حصل|نالت|نال)(?: |$)|winners?(?: |$)|(?:^| )won(?: |$)")


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
        # QADFIX34 #6: «هالسنة او السنة اللي قبلها» / "this year or last year" is the two years
        if (_LAST_YEAR.search(t) or re.search(r"(?:^| )(?:او|و|ولا) (?:ال)?(?:سنه|عام) (?:اللي|الي|الذي)? ?(?:قبلها|قبله|قبل|فاتت|فات|راحت|راح)(?: |$)"
                                              r"|(?:^| )or (?:the year before|the one before)", t)):
            return y - 1, y
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
        # QADFIX28 (QAD 2026-11-05 case 45.2): «أقدم من كذا، قبل الثلاثينات» is before 1930, not 1930–1939 («الكتابين صدروا بعد
        # الثلاثينات» about 1931 and 1934); «بعد الستينات» is from 1970
        _m28 = _DECADE_RX.search(t)
        _pre28 = t[:_m28.start()].split()[-2:] if _m28 else []
        if _pre28 and (_pre28[-1] in ("قبل", "before", "pre") or _pre28[-2:] == ["prior", "to"] or _pre28[-2:] == ["earlier", "than"]
                       or (_pre28[-1] == "the" and len(_pre28) > 1 and _pre28[-2] == "before")):
            return None, d[0] - 1
        if _pre28 and (_pre28[-1] in ("بعد", "after") or (_pre28[-1] == "the" and len(_pre28) > 1 and _pre28[-2] == "after")):
            return d[1] + 1, None
        return d
    e = era_said(t)
    if e:
        return e
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

# QADFIX28 (QAD 2026-11-05 case 20.2): toddlers' formats named in a title
_TODDLER28 = re.compile(r" (?:كلماتي (?:ال)?اولي|كتابي (?:ال)?اول|(?:ال)?دمي|دمي (?:ال)?اصابع|اكتشف (?:ال)?مفاجاه|طي (?:ال)?لوحات"
                        r"|ملصقات فوم|المس و|ارفع (?:ال)?غطاء|my first|first words|lift the flap|board book|touch and feel)")

# QADFIX28 (QAD 2026-11-05 case 30.1): words that are never a subject on their own ("your" of "on your shelves")
_FUNCTION28 = {normalize(w) for w in (
    "your yours you our ours my mine the a an any some anything something here there on in at of for with from to have has had got "
    "shelf shelves shop store library stock books book novels novel please do does is are it its this that these those "
    "عندكم عندك عندنا رفوفكم رفوف الرفوف مكتبتكم المكتبه هنا فيه في من على عن لي لك هذا هذي هذه ذلك").split()}

# QADFIX28 #2 (QAD 2026-11-05 case 5.1): a war event said with a place — the books about that war there come first
_WAR28 = re.compile(r"(?:^| )[وبلفك]?(?:ال)?(?:حرب|حروب|الحروب|قصف|اقتتال|قتال|نزوح|نازحين|تهجير|لجوء|لاجئ|لاجئين|لاجئه|ملاجئ|ملجا"
                    r"|حصار|غارات|غاره|قنابل|ميليشيات|ميليشيا|مليشيات|اجتياح|مجزره|مجازر|war|wars|bombing|bombings|shelling|shelled"
                    r"|siege|refugees?|militias?|displaced|displacement)(?:ها|هم)?(?: |$)")
_PLACES28 = {"بيروت": "لبنان", "صيدا": "لبنان", "دمشق": "سوريا", "حلب": "سوريا", "حمص": "سوريا", "حماه": "سوريا",
             "بغداد": "العراق", "الموصل": "العراق", "البصره": "العراق", "القدس": "فلسطين", "غزه": "فلسطين", "يافا": "فلسطين",
             "حيفا": "فلسطين", "جنين": "فلسطين", "صنعاء": "اليمن", "عدن": "اليمن", "الخرطوم": "السودان", "دارفور": "السودان",
             "سراييفو": "البوسنه", "كابل": "افغانستان", "beirut": "لبنان", "damascus": "سوريا", "aleppo": "سوريا",
             "baghdad": "العراق", "gaza": "فلسطين", "sarajevo": "البوسنه", "kabul": "افغانستان"}
_COUNTRY28 = {"لبنان": ("لبنان", "لبناني"), "سوريا": ("سوريا", "سوريه", "سوري"), "العراق": ("العراق", "عراقي"),
              "فلسطين": ("فلسطين", "فلسطيني"), "اليمن": ("اليمن", "يمني"), "السودان": ("السودان", "سوداني"),
              "ليبيا": ("ليبيا", "ليبي"), "الجزائر": ("الجزائر", "جزائري"), "البوسنه": ("البوسنه", "بوسني"),
              "افغانستان": ("افغانستان", "افغاني"), "الكويت": ("الكويت", "كويتي")}
_COUNTRY28_EN = {"lebanon": "لبنان", "lebanese": "لبنان", "syria": "سوريا", "syrian": "سوريا", "iraq": "العراق", "iraqi": "العراق",
                 "palestine": "فلسطين", "palestinian": "فلسطين", "yemen": "اليمن", "sudan": "السودان", "bosnia": "البوسنه"}


def _words28(text):
    return " " + " ".join(normalize(re.sub(r"[^\w\s]", " ", str(text or ""))).split()) + " "


def war_place28(message, phrases=()):
    """«أيام القصف في بيروت الثمانينات» -> {"place": "بيروت", "city": "بيروت", "country": "لبنان"}; None without a war word
    and a known place (a city of the list, or a country) in the message or its subject phrases."""
    t = _words28(message)
    if not _WAR28.search(t):
        return None
    words = (t + " " + " ".join(_words28(p) for p in phrases or ())).split()
    for w in words:
        w0 = strip_al(w) if w not in _PLACES28 else w
        for cand in (w, w0, "ال" + w0):
            if cand in _PLACES28:
                return {"place": cand, "city": cand, "country": _PLACES28[cand]}
    for w in words:
        for c, forms in _COUNTRY28.items():
            if any(strip_al(w).startswith(strip_al(f)) for f in forms) and len(strip_al(w)) <= len(strip_al(forms[-1])) + 3:
                return {"place": c, "city": None, "country": c}
        if w in _COUNTRY28_EN:
            return {"place": _COUNTRY28_EN[w], "city": None, "country": _COUNTRY28_EN[w]}
    return None


# a war named as another one («يخوض الأصغر حرب القرم» in a novel of 19th-century Beirut) is not the war asked about
_OTHER_WAR28 = re.compile(r" (?:ال)?(?:حرب|حروب) (?:ال)?(?:قرم|عالميه|خليج|اكتوبر|حزيران|الايام السته|فيتنام|استقلال|طرواده|البسوس"
                          r"|نابليون|الفرنجه|الصليبيه)(?: (?:ال)?(?:اولي|الاولي|ثانيه|الثانيه))?")


def war_place_fit28(book, wp, origins=None):
    """2: its title / summary / hook / subjects name a war and the country (in the text, or as the author's country);
    1: a war only in its keywords with the country, or a war with the city only; 0 else."""
    def _txt(keys):
        return _OTHER_WAR28.sub(" ", _words28(" ".join(str(book.get(k) or "") for k in keys)))
    main, kw = _txt(("title", "summary", "hook", "subjects", "setting")), _txt(("keywords",))
    forms = _COUNTRY28.get(wp["country"], (wp["country"],))
    def _country(t):
        return (any(re.search(r" [وبلفك]?(?:ال)?" + re.escape(strip_al(f)), t) for f in forms)
                or wp["country"] in (origins or {}).get(book["book_id"], ""))
    def _city(t):
        return bool(wp.get("city")) and bool(re.search(r" [وبلفك]?" + re.escape(wp["city"]) + " ", t))
    if wp.get("decade") and wp["decade"] >= 1900 and _OLD_CENTURY28.search(main):
        return 0   # «بيروت مدينة العالم» (the 19th century) for a war in the 1980s
    if _WAR28.search(main):
        return 2 if _country(main + kw) else 1 if _city(main + kw) else 0
    if _WAR28.search(kw) and (_country(main + kw) or _city(main + kw)):
        return 1
    return 0


_OLD_CENTURY28 = re.compile(r" (?:ال)?قرن (?:ال)?(?:تاسع عشر|ثامن عشر|سابع عشر|سادس عشر|خامس عشر|رابع عشر|ثالث عشر|التاسع عشر|الثامن عشر)"
                            r"| (?:ال)?(?:عثماني|العثمانيه|عثمانيه|المماليك|مملوكي|الصليبيين) | 1[0-8]\d\d ")


def war_place_close28(book, wp, message):
    """Among the books on the war there: 2 for the city named in its text, 1 more for the visitor's own war word («القصف»)."""
    main = _words28(" ".join(str(book.get(k) or "") for k in ("title", "summary", "hook", "subjects", "setting")))
    n = 2 if wp.get("city") and re.search(r" [وبلفك]?" + re.escape(wp["city"]) + " ", main) else 0
    for m in _WAR28.finditer(_words28(message)):
        w = m.group(0).strip()
        forms = {strip_al(w), strip_al(w[1:])} if w[:1] in "وبلفك" else {strip_al(w)}
        if any(len(f) >= 3 and re.search(r" [وبلفك]?(?:ال)?" + re.escape(f), main) for f in forms):
            n += 1
            break
    return n


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
                             r"|(?:طالع|طالعه|الطالعه|نازل|نازله)(?: \S+)? (?:مؤخرا|موخرا|حديثا|جديد|جديده|هالسنه|هذي السنه|السنه هذي|هذا العام)"
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
                      r"|(^| )(قديم|قديمه|القديمه|كلاسيك|كلاسيكي|كلاسيكيه)( |$)|any year|not new|doesn.?t have to be new|older|classic"
                      # CHATFIX31: «ما يهم السنة»، «بغض النظر عن سنة النشر»، «أي تاريخ»
                      r"|(ما|مو|مب) (يهم|مهم|تفرق|فارقه) (ال)?(سنه|تاريخ|النشر|متي)|بغض النظر عن (ال)?(سنه|تاريخ)|(^| )اي تاريخ( |$)")


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
    # CHATFIX31 (Ibrahim 2026-11-01, reverting the CHATFIX30c restriction): «على حسب، يمكن يبغى يضيف ويحدد اكثر مو انه يغير
    # الكتاب. المفروض يظل عالسياق الا اذا هو الغى السياق او بدأ محادثة جديدة». The «جديدة» wish stays in the conversation's
    # context, through new subjects too, until the visitor takes it back in words («مو لازم جديدة»، «أي سنة»، «حتى القديمة»:
    # _NOT_NEW), removes its badge («صادرة من ٢٠٢٣», drop_context «pubyear») or starts a new chat
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
        if len(phrase) >= 4 and honors.name_exists(phrase) and not honors.name_is_generic(phrase):
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


# QADFIX20 (QAD 2026-10-25, case 3.2): «بدون أي مشاهد للكبار، تكون نظيفة» for a girl of 14 — «للكبار» under a negation names
# content to keep out, not a grown-up reader; the self words («لي أنا»، «لنفسي») are never read that way
_ADULT_READER_WORDS = {"للكبار", "الكبار", "كبار", "adult", "adults", "grown up", "grownups"}


# QADFIX34 #1 (QAD 2026-11-11 case 17.2, grave): "Something less adult please, I'm only 15" → «For an adult reader, Range…»: «less /
# too / أقل / مو كثير» before «adult» asks for content less grown-up, never for a grown-up reader
_LESS_ADULT34 = {"less", "too", "not", "non", "اقل", "زياده", "مره"}


def _adult_word_said(words, w):
    i = keyword_prefs.find_phrase(words, w)
    return i >= 0 and not (w in _ADULT_READER_WORDS and (_audience_negated(words, i)
                                                          or any(x in _LESS_ADULT34 for x in words[max(0, i - 2):i])))


def adult_said(text):
    words = normalize(text).split()
    return (any(_adult_word_said(words, w) for w in _ADULT_SAID)
            or chat_rules.parent_said(text)   # «لي أنا كأم» (QAD 2026-10-12, case 38)
            or any(age >= 18 for age in _ages(text)))   # «فوق ١٨ سنة»


def said_for_adult(user_messages):
    """The latest message asks for a grown-up reader: «للكبار»، «فوق ١٨»، or the visitor himself
    («وأنا ابي رواية رومانسية لي» after two children, QAFIX 2026-10-11 case 12) unless he is the young
    reader himself («أنا بثاني متوسط»)."""
    last = user_messages[-1] if user_messages else ""
    # QADFIX34 #1: "I'm only 15" in the same message: the visitor is the young reader, whatever word stands beside it
    if chat_rules.minor_self([last]) and not chat_rules.parent_reader(user_messages):
        return False
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
        # QADFIX24 (QAD 2026-10-30, case 7.1): «بنت بأول ابتدائي» is 6, not the 9 the model read: a school year said in the latest
        # message (one child only) gives the age when the model's reading is far from it
        # (the latest message that names a school year; an age said in a message after it is the newer reading)
        _g24 = []
        for _i24 in range(len(user_messages or []) - 1, -1, -1):
            _g24 = chat_rules.grade_ages(user_messages[_i24])
            if _g24:
                # a later message about a person or an age («ولأختهم الصغيرة سنة ونص؟») is another reading: the model's
                if any(re.search(r"بنت|ولد|ابن|اخت|اخو|طفل|حفيد|صغير|كبير|عمر|سنه|سنين|سنوات|عام|ابتدائي|متوسط|ثانوي|روضه|daughter|son|kid|child|year",
                                 " ".join(normalize(t).split())) for t in user_messages[_i24 + 1:]):
                    _g24 = []
                break
        if len(_g24) == 1 and abs(int(_model_reader_age.age) - _g24[0]) > 1:
            return _g24[0]
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
# QADFIX35 (case 7.1): a book on one medium — screens, phones, social media, games, the internet
_SCREEN35 = re.compile(r"(?:^| )(?:ال)?(?:رقمي|رقميه|شاشات|شاشه|هواتف|هاتف|جوال|جوالات|انترنت|الانترنت|العاب الفيديو|الالعاب الالكترونيه|ايباد)(?: |$)"
                       r"|وسائل التواصل|مواقع التواصل|السوشال|السوشيال|screen|screens|smartphone|social media|digital|internet|gaming")
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
    # QADFIX20 (QAD 2026-10-25, case 22.3; AGY-20261025-01 a): «قصة لولدي عمره ٦» → «وأنا أبي كتاب طبخ لي» → «قلت كتاب طبخ!
    # وصفات أكل» — the visitor became the reader in the second message; the child said before that does not come back while
    # the later messages do not name a child again
    if not child_said(user_messages[-1]):
        _adult_at = max((k for k in range(len(user_messages) - 1) if said_for_adult(user_messages[:k + 1])), default=-1)
        _child_at = max((k for k in range(len(user_messages) - 1)
                         if detect_audience(normalize(user_messages[k])) or child_said(user_messages[k])), default=-1)
        if _adult_at > _child_at:
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


# QADFIX22 (QAD 2026-10-27, case 12.1): «عندكم شي من نشر التنوير عن الفلسفة؟» — «نشر / إصدار» names a publisher too, so a
# one-word stocked name after it («التنوير»، «نابو») is the publisher, not «دار» left as a subject
_PUB_TRIGGERS = {normalize(w) for w in "دار الدار منشورات مطبوعات إصدارات الإصدارات اصدارات ناشر الناشر نشر إصدار اصدار".split()}
# QADFIX23 (case 2.1): verbs that say a name is a publisher («مسكلياني تنشر…»)
_PUB_VERBS = {normalize(w) for w in "تنشر ينشر نشرت نشروا تطبع يطبع طبعت تصدر يصدر أصدرت اصدرت".split()}
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
    # QADFIX23 (QAD 2026-10-28, case 2.1): «مسكلياني تنشر روايات حلوة، عندكم منها شي؟» — a publisher's own name (no title uses it)
    # with a publishing verb in the message is the publisher asked for, without «دار / من»
    if any(w in _PUB_VERBS or w[1:] in _PUB_VERBS for w in words if w):
        for w in words:
            for cand in {w, w[1:] if len(w) > 4 and w[0] in "وبل" else w}:
                if _pub_distinct(publisher_key(cand), all_books):
                    said.append(cand)
    for phrase in said:
        k = publisher_key(phrase)
        if k in names:
            return k, names[k]
    # QADFIX21 (QAD 2026-10-26, case 25): «كتب المركز القومي للترجمة عن التاريخ» — a stocked publisher whose name is two
    # words or more and has no «دار» («المركز القومي للترجمة») is named by its whole name, in the wishes or the message
    flat = " " + " ".join(words) + " "
    for phrase in list(unsupported) + [None]:
        if phrase is None:
            hits = [k for k in names if len(k.split()) >= 2 and " " + k + " " in flat]
            if hits:
                k = max(hits, key=len)
                return k, names[k]
            continue
        k = publisher_key(phrase)
        if k in names and len(k.split()) >= 2:
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
# QADFIX26 (QAD 2026-11-03 case 4; 11-02 13.1): «حائزة البوكر العربية» (هدى بركات's record) says a woman as plainly as «كاتبة»
_NOTE_F = re.compile(r"^(?:كاتبه|شاعره|روائيه|اديبه|مؤلفه|باحثه|قاصه|صحفيه|ناشطه|مترجمه|اكاديميه|طبيبه|معلمه|ممثله|رساميه|رسامه|فنانه"
                     r"|عالمه|استاذه|ناقده|اقتصاديه|مستشرقه|مؤرخه|سياسيه|فيلسوفه|مفكره|صاحبه"   # AUTHGENDER 11-05: «رسامه» typo fixed, missing roles
                     r"|حائزه|حايزه|فائزه|فايزه|حاصله)(?: |$)")
_NOTE_M = re.compile(r"^(?:كاتب|شاعر|روائي|اديب|مؤلف|باحث|قاص|صحفي|ناشط|مترجم|اكاديمي|طبيب|معلم|ناظم|مفكر|فيلسوف|مؤرخ|ممثل|رسام|فنان"
                     r"|عالم|استاذ|ناقد|اقتصادي|مستشرق|سياسي|صاحب"
                     r"|حائز|حايز|فائز|فايز|حاصل)(?: |$)")
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


def author_gender(gender, note):
    """GENDERFIELD (Ibrahim 2026-11-05): the author record's own field authors.gender ('f' / 'm') first; the style
    note's first word (note_gender) only when the field is empty. Any other value in the field counts as unknown."""
    g = (gender or "").strip().lower()
    if g in ("f", "m"):
        return g
    return note_gender(note) if not g else None


_latin28_cache = {"map": None, "at": 0}


def book_author_latin():
    """QADFIX28 (QAD 2026-11-04 cases 14.2, 30.1; 2026-11-05 cases 14.2, 23, 50.1): {book_id: the authors' names in Latin letters}
    from the author records, when every author of the book has one; cached 2 minutes. An English reply names the author so,
    not «أصدقائي by إيمانويل بوف»."""
    if _latin28_cache["map"] is not None and time.time() - _latin28_cache["at"] < 120:
        return _latin28_cache["map"]
    seen = {}
    conn = get_connection()
    try:
        for r in conn.execute("SELECT ba.book_id, a.name_latin FROM book_authors ba JOIN authors a USING(author_id) "
                              "WHERE ba.role='author' ORDER BY ba.rowid"):
            seen.setdefault(r[0], []).append((r[1] or "").strip())
    except sqlite3.Error:
        seen = {}
    finally:
        conn.close()
    out = {i: " and ".join(dict.fromkeys(n)) for i, n in seen.items() if n and all(n) and not any(re.search(r"[؀-ۿ]", x) for x in n)}
    _latin28_cache.update(at=time.time(), map=out)
    return out


def book_author_gender():
    """{book_id: "f" | "m"} from its author records (every author of the book agrees), cached 2 minutes: the field
    authors.gender first, the style note's first word as a fallback (author_gender, GENDERFIELD 2026-11-05)."""
    if _gender_cache["map"] is not None and time.time() - _gender_cache["at"] < 120:
        return _gender_cache["map"]
    seen = {}
    conn = get_connection()
    try:
        try:
            rows = conn.execute("SELECT ba.book_id, a.gender, a.style_note_ar FROM book_authors ba JOIN authors a USING(author_id) "
                                "WHERE ba.role='author'").fetchall()
        except sqlite3.OperationalError:   # a database from before the field (init_db adds it): the notes only
            rows = [(r[0], None, r[1]) for r in conn.execute(
                "SELECT ba.book_id, a.style_note_ar FROM book_authors ba JOIN authors a USING(author_id) WHERE ba.role='author'")]
        for book_id, gender, note in rows:
            seen.setdefault(book_id, set()).add(author_gender(gender, note))
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


def _refused_author30(span, author_request, all_books):
    """QADFIX30 #3: the author named in a refused span («لمحفوظ»، «نجيب محفوظ لو») -> every spelling of him to leave out, or [].
    The author asked for before in any word of his name; a catalog author by his full name; a family name alone only with «لـ»
    («لمحفوظ») and when it points at three authors at most. «لأني»، «لبنتي» are words, never «لـ» + a name."""
    words = (span or "").split()
    if not words:
        return []
    ar = author_request or {}
    req = [n for n in [ar.get("full_name"), ar.get("name_as_written")] + list(ar.get("aliases") or []) if isinstance(n, str) and n.strip()]
    stocked = {b.get("author") for b in all_books.values() if is_available(b) and b.get("author")}
    lam = words[0].startswith("ل") and len(words[0]) >= 4 and not chat_rules.lam_is_word(words[0])
    for size in (3, 2, 1):
        if len(words) < size:
            continue
        forms = ([" ".join([words[0][1:]] + words[1:size])] if lam else []) + [" ".join(words[:size])]
        for f in forms:
            if len(f.replace(" ", "")) < 3 or chat_rules.lam_is_word(f):
                continue
            if req and any(author_matches(f, n) for n in req):
                return list(dict.fromkeys(req))
            rec = authors.lookup(f, spelling=False)
            if rec and authors.book_ids(rec["author_id"]) & {b["book_id"] for b in all_books.values() if is_available(b)}:
                return list(dict.fromkeys(n for n in authors.names_of(rec) if n))
            if size >= 2 or (lam and f is forms[0] and len(f) >= 4):
                hits = {author_flow.display(a) for a in stocked if author_matches(f, a)}
                if 1 <= len(hits) <= 3:
                    return sorted(hits)
    return []


def refused_authors30(user_messages, author_request, all_books):
    """QADFIX30 #3 (QAD 2026-11-07 case 25.2, grave): «شي ثاني بس مو لمحفوظ» -> ["نجيب محفوظ", "Naguib Mahfouz", …]: the authors the
    visitor refused, kept through the conversation until a later message names him again without refusing him."""
    out = []
    for k, text in enumerate(user_messages):
        for span in chat_rules.negated_spans(text):
            names = _refused_author30(span, author_request, all_books)
            if names:
                out.append((k, names))
    keep = []
    for k, names in out:
        later = [t for t in user_messages[k + 1:]]
        if any(name_said_now({"name_as_written": n, "full_name": n, "aliases": []}, t)
               and not any(_refused_author30(s, None, all_books) for s in chat_rules.negated_spans(t)) for n in names[:1] for t in later):
            continue
        keep += [n for n in names if n not in keep]
    return keep


def exact_title_books(message, all_books):
    """Available books whose whole title is the message, once a few asking words are taken off its start
    («دار نشر فن الحرب» -> «فن الحرب»). Titles of two words at least; every edition, the most copies first."""
    words = normalize(message).split()
    while words and words[0] in _TITLE_ASKING:
        words = words[1:]
    want = " ".join(words)
    _form = {normalize(w) for w in "قصص قصة قصه كتب كتاب روايات رواية روايه شعر ديوان مقالات حكايات قصائد".split()}
    if len(words) < 2 or (keyword_prefs.understood_anything(keyword_prefs.understand([want]))
                          and all(w in _form or keyword_prefs.understood_anything(keyword_prefs.understand([w])) for w in words)):
        return []   # «قصص قصيرة»، «رواية رومانسية»: a wish in reading words, even if a book bears that title
    # INBOXFIX 2026-10-29: «عندكم الجريمة والعقاب» is the title — one reading word in it («الجريمة») no longer hides it
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
        # QADFIX21 (QAD 2026-10-26, case 16.3): «ابن سينا أو الطريق إلى أصفهان» is a whole title: the name of a person inside it
        # («ابن سينا», also an author of ours) does not turn the rest into a search among his books
        _whole = chat_rules.title_key(nm)
        _same = [b for b in all_books.values() if chat_rules.title_key(b["title"]) == _whole] if _whole else []
        if _same:
            _same.sort(key=lambda b: not is_available(b))
            if not any(is_available(b) for b in _same[:2]):
                # QADFIX35 (QAD 2026-11-12 cases 24.1–24.2, minor): «اللص والكلاب» (4195, out of stock) → «العربية غير متوفرة» and two other
                # novels, while «اللص والكلاب (رواية مصورة)» (1882, Arabic) is in stock: the same work in another form, its title before «(» or
                # «:» the one asked, comes with it
                _form35 = [b for b in all_books.values() if b not in _same and is_available(b) and re.search(r"[(:：]", b.get("title") or "")
                           and chat_rules.title_key(normalize(_head34(b.get("title")))) == _whole]
                _same = _same[:1] + _form35[:1] + _same[1:]
            for b in _same[:2]:
                if b["book_id"] not in [x["book_id"] for x in found]:
                    found.append(b)
            continue
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
                # INBOX1112 (inbox 2026-11-11 §3, item 11 «Wings of Fire»): read as «أجنحة النار», it was taken for «النار» (4071) and the
                # reply was «not available» with nothing else — a one-word title that is only a word inside the longer title asked for is
                # not that book (as QADFIX11 for a one-word title at its start), so the missing-title path looks for it and its likes
                rank = 3.5 if (len(nt.split()) == 1 and len(nm.split()) >= 2 and (" " + nt + " ") in (" " + nm + " ")) else 2
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


_FB_ASK_WORDS = {authors.key(w) for w in (
    "ابي ابغى ابغا ابغي اريد بدي ودي عندك عندكم فيه في هل كتب كتاب روايات رواية مؤلفات اعمال أعمال توصيات توصية "
    "مشابهة مشابهه تشبه يشبه زي مثل نفس لكتب لكتابات كتابات اقترح رشح رشحي الدكتور دكتور د الكاتب الكاتبة للكاتب "
    "او أو و ولا تبع حق").split()}


def fallback_author(message, all_books):
    """With the model down (INBOXFIX 2026-10-29: «كتب لنوال السعداوي»، «مصطفى حجازي» got unrelated books by single
    words): an author named in the message -> (display name, his available books) or None. A stocked author's full
    name in the message, else an author record for 1–4 of its words (a «ل» before the name dropped; one long word
    may be one letter off a stored one-word name: «دوستوفيسكي»)."""
    msg = authors.key(message)
    if not msg:
        return None
    words = msg.split()
    padded = " " + msg + " "
    stocked = {}
    for b in all_books.values():
        d = author_flow.display(b.get("author"))
        k = authors.key(d)
        if len(k.split()) >= 2:
            stocked.setdefault(k, (d, []))[1].append(b)
    best = None
    for k, (d, books) in stocked.items():
        if (" " + k + " ") in padded or (" ل" + k + " ") in padded:
            if best is None or len(k) > len(best[0]):
                best = (k, d, books)
    if best:
        avail = [b for b in best[2] if is_available(b)]
        return best[1], sorted(avail, key=lambda b: -(b.get("quantity") or 0))
    rec = None
    for n in (4, 3, 2, 1):
        for i in range(len(words) - n + 1):
            gram = words[i:i + n]
            if all(w in _FB_ASK_WORDS for w in gram) or gram[0] in _FB_ASK_WORDS or gram[-1] in _FB_ASK_WORDS:
                continue
            tries = [" ".join(gram)]
            if gram[0].startswith("ل") and len(gram[0]) > 3:
                tries.append(" ".join([gram[0][1:]] + gram[1:]))
            for t in tries:
                if n == 1 and len(t) < 5:
                    continue
                r = authors.lookup(t, spelling=(n > 1))
                if r:
                    rec = r
                    break
            if rec:
                break
        if rec:
            break
    if rec is None:
        try:
            conn = get_connection()
            try:
                idx = authors._index(conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            idx = {}
        one = {k: a for k, a in idx.items() if " " not in k and len(k) >= 7}
        for w in words:
            for t in ([w, w[1:]] if w.startswith("ل") else [w]):
                if len(t) < 7 or t in _FB_ASK_WORDS:
                    continue
                # one letter off, or two in a long name (a swapped pair: «دوستوفيسكي» / «دوستويفسكي»)
                near = {a for k, a in one.items() if abs(len(k) - len(t)) <= 1
                        and author_flow._lev(k, t) <= (2 if len(t) >= 9 else 1)}
                if len(near) == 1:
                    rec = authors.lookup_id(next(iter(near))) if hasattr(authors, "lookup_id") else None
                    if rec is None:
                        conn = get_connection()
                        try:
                            row = conn.execute("SELECT name_ar FROM authors WHERE author_id = ?", (next(iter(near)),)).fetchone()
                        finally:
                            conn.close()
                        rec = authors.lookup(row[0]) if row else None
                    break
            if rec:
                break
    if rec is None:
        return None
    ids = authors.book_ids(rec["author_id"])
    avail = [all_books[i] for i in ids if i in all_books and is_available(all_books[i])]
    return rec["name_ar"], sorted(avail, key=lambda b: -(b.get("quantity") or 0))


# FALLBACKSIM (library log 2026-11-01 13:15, model down → «النظام البديل»): «قرأت مؤخرا كل صيف تلاه. ابغاه شيء مقارب له» got one
# card, «كل صيف تلاه» itself, and «هذا الكتاب اللي قرأته. انا ابغى كتب مشابهه له» after it got «ما لقيت … كتابًا بهذا الوصف».
# Without the model, a wish for books like a book — a title named with a likeness word («مثل / زي / شبه / مشابه / مقارب / قريب
# من …»، «قرأت X وابغى …»، "something like X"), or the book(s) just shown («مشابه له»، «مثله»، «زيه»، "similar to this") — is
# answered with books in stock close to it, as the model path does for a book of ours (its «similar» relations, then the
# nearest stored meanings, embeddings.similar_to_book), never with the book itself, another edition of it or another part
# of its series (unless the parts are asked about). No reference found -> the keyword answer as before.
_FB_LIKE_AR = re.compile(
    r"(?:^| )(?:[وف]?(?:مثل|مثله|مثلها|زي|زيه|زيها|شبه|يشبه|تشبه|يشبهه|يشبهها|تشبهه|تشبهها|شبيه|شبيهه|مشابه|مشابهه|مشابهين"
    r"|مشابهات|متشابه|متشابهه|مقارب|مقاربه|مقاربين|مقاربات|قريب|قريبه|قريبين|قريبات)"
    r"|على غرار|علي غرار|نفس (?:ال)?(?:جو|نوع|اسلوب|طابع|نمط|فكره|ستايل))(?: |$)")
_FB_LIKE_EN = re.compile(r"\b(?:similar|resembl\w*|in the vein of|along the lines of|the likes of)\b"
                         r"|\b(?:something|anything|books?|novels?|stories|ones?|stuff|more|reads?|titles?)\s+like\b|^like\b")
_FB_LIKE_IT = re.compile(
    r"(?:^| )(?:[وف]?(?:مثله|مثلها|زيه|زيها|يشبهه|يشبهها|تشبهه|تشبهها|يشبهونه)"
    r"|(?:شبيه|شبيهه|مشابه|مشابهه|مشابهين|مشابهات|مقارب|مقاربه|مقاربين|قريب|قريبه|قريبين)\s+(?:له|لها|لهم|منه|منها|منهم|لهذا|لهذي"
    r"|لهذه|لذا|من هذا|من هذي|من هذه)"
    r"|(?:مثل|زي|شبه|يشبه|تشبه)\s+(?:هذا|هذي|هذه|ذا|ذي|هاذا|هاذي|ذاك)"
    r"|نفس (?:ال)?(?:جو|نوع|اسلوب|طابع|نمط|فكره))(?: |$)"
    r"|(?:^| )(?:مشابهه|مشابهين|مشابهات|مقاربه|مقاربين|شبيهه)$"
    r"|\b(?:similar|like|resembling|close)\s+(?:to\s+)?(?:this|that|it|these|those|them)\b|\bsomething similar\b"
    r"|\bsimilar (?:ones|books|novels|reads|titles)\b")
_FB_READ_WANT = re.compile(r"(?:^| )(?:قرات|قريت|خلصت|انهيت|i read|i just read|i finished|i loved)(?: |$)")
_FB_WANT = re.compile(r"(?:^| )(?:ابغي|ابي|ابغا|ابا|ابغاه|ودي|رشح|رشحي|اقترح|اقترحي|عطني|عطيني|recommend|suggest|want|looking for)(?: |$)")
_FB_AUTHOR_WISH = re.compile(r"(?:^| )(?:ال|لل|ل)?(?:مؤلف|مولف|كاتب|كاتبه|مؤلفه|مولفه)(?: |$)|\b(?:author|writer)\b")
_FB_FORM_WORDS = {normalize(w) for w in "قصص قصة قصه كتب كتاب روايات رواية روايه شعر ديوان مقالات حكايات قصائد".split()}
_fb_title_cache = {"sig": None, "keys": []}


def _fb_title_keys(all_books):
    """[(title key, [book ids])] of every book (with and without its subtitle), the longest key first; cached per catalog.
    AUDFIX (audit 2026-11-01 P3): the memory is keyed on a digest of every (book id, title), not on the number of books — a
    staff title edit keeps the count, and the new title went unknown (the old one still matched) until a restart."""
    sig = hashlib.sha1("\x1f".join(f"{i}\x1e{all_books[i].get('title') or ''}" for i in sorted(all_books)).encode("utf-8")).hexdigest()
    if _fb_title_cache["sig"] != sig:
        keys = {}
        for b in all_books.values():
            t = str(b.get("title") or "")
            for k in {chat_rules.title_key(normalize(t)), chat_rules.title_key(normalize(t.split(":")[0]))}:
                if k:
                    keys.setdefault(k, []).append(b["book_id"])
        _fb_title_cache.update(sig=sig, keys=sorted(keys.items(), key=lambda kv: -len(kv[0])))
    return _fb_title_cache["keys"]


def _fb_reading_words(k):
    """«رواية رومانسية»، «قصص قصيرة»: a wish in reading words, never taken for a title."""
    und = keyword_prefs.understand
    return (keyword_prefs.understood_anything(und([k]))
            and all(w in _FB_FORM_WORDS or keyword_prefs.understood_anything(und([w])) for w in k.split()))


def fallback_like_reference(message, all_books, last_ids=()):
    """FALLBACKSIM: the book(s) the visitor wants books like -> (reference books, how) or None.
    how = "named" (its title in the message) or "shown" (the books of the last turn, pointed at: «مشابه له»، "similar to this")."""
    m = " ".join(normalize(message or "").split())
    if not m or _FB_AUTHOR_WISH.search(m):
        return None   # «من نفس المؤلف»: the author's books, not likeness (the author paths answer it)
    mk = " " + chat_rules.title_key(m) + " "
    for k, ids in _fb_title_keys(all_books):
        words = k.split()
        if len(k) < 4 or (" " + k + " ") not in mk:
            continue
        rest = mk.replace(" " + k + " ", " | ", 1)
        before = rest.split(" | ", 1)[0] + " "
        lead = bool(_LIKENESS_LEAD.search(before) or re.search(r"(?:^| )(?:like|to|of)\s+$", before))
        if len(words) == 1 and not (len(k) >= 5 and lead):
            continue   # one word is a title only right after «مثل / like»
        if len(words) >= 2 and len(k) < 6:
            continue
        if _fb_reading_words(k):
            continue
        rest = " ".join(rest.replace("|", " ").split())
        cue = (_FB_LIKE_AR.search(rest) or _FB_LIKE_EN.search(rest) or lead
               or (_FB_READ_WANT.search(rest) and _FB_WANT.search(rest)))
        if not cue:
            return None
        refs = [all_books[i] for i in ids if i in all_books]
        refs.sort(key=lambda b: (b["book_id"] not in set(last_ids or ()), not is_available(b), b["book_id"]))
        return (refs, "named") if refs else None
    if last_ids and _FB_LIKE_IT.search(m):
        refs = [all_books[i] for i in last_ids if i in all_books]
        return (refs[:3], "shown") if refs else None
    return None


# the kind of book: the reference's in a likeness wish (an earlier turn's wish of this kind gives way to it)
_FB_KIND_KEYS = ("genres", "moods_include", "pace", "weight_include", "use_cases", "title_keywords", "authors",
                 "unsupported_requests", "awards", "award_year", "adaptation")


def fallback_similar_conditions(pool, refs, messages, cards, language="ar", info=None):
    """AUDFIX (audit 2026-11-01 P2): the visitor's conditions for «books like X» without the model, read from the whole
    conversation as the keyword answer reads them -> (the books of pool that keep them, their labels). «ابغى رواية قصيرة» ->
    a book shown -> «كتب قريبة منه» brought four 350-page books: only the latest message was read.
    Kept from every message in order (later ones win; «عادي طويل» / «مو مهم السعر» lift them): the length and a page limit,
    the reader (age, refused content; the parent reading for themself as in the keyword answer), refused moods and weights,
    the original language / translated wish and the price limit. The kind of book is the reference's: a genre, prize, screen,
    mood or author wish of an earlier turn would only empty the answer, so it gives way to the likeness — a genre stays when
    the reference is of it, and a wish said beside the likeness in the latest message («مثله بس رواية») applies as before.
    AUDFIX (audit 2026-11-04 P2): a young reader and a clean book asked for («لبنت أخوي عمرها ١٣ … مافيها شي للكبار» -> a book
    shown -> «كتب قريبة منه») is held to the strict rule of the model's answer (clean_young_wish); info (a dict) then gets
    "clean_young": (audience, reader age)."""
    def _strip(t):
        t = normalize(t or "")
        for b in refs:
            for k in {normalize(b.get("title") or ""), normalize(str(b.get("title") or "").split(":")[0])}:
                if k.strip():
                    t = t.replace(k, " ")
        return " ".join(t.split())
    en = language == "en"
    texts = [_strip(t) for t in messages]
    last = local_understand(texts[-1:])
    und = local_understand(texts)
    ref_genres = {b.get("genre") for b in refs}
    for k in _FB_KIND_KEYS:
        if k in und:
            und[k] = last.get(k) if k != "genres" or last.get("genres") else [g for g in und["genres"] if g in ref_genres]
    labels = []
    if keyword_prefs.understood_anything(und):
        p, pp, ex, _ = prefs_from_ai(und, " ".join(texts))
        if not last.get("genres"):
            p["genres"] = {g for g in p.get("genres") or () if g in ref_genres}
        if chat_rules.parent_reader(messages) or (adult_said(messages[-1]) and not child_said(messages[-1])):
            p = dict(p, genres=set(p.get("genres") or ()) - CHILD_GENRES)
            pp = dict(pp, audience=None)
        if clean_young_wish(pp, messages) and info is not None:
            info["clean_young"] = (pp["audience"], pp.get("reader_age"))
        # with no genre stated, the form is the reference's (already held by the pool), never the no-genre default
        genres = wanted_genres(p, pp)
        form = bool(p.get("genres"))
        pool = [b for b in pool if meets_constraints(b, cards.get(b["book_id"]), p, pp, ex, genres, form=form)]
        labels = constrain([], p, pp, ex, cards)[1]
    price_max, _cheapest = chat_rules.price_wish(messages)
    if price_max:
        pool = [b for b in pool if b.get("price") and 0 < b["price"] <= price_max]
        labels.append(f"price up to {price_max} SAR" if en else f"بسعر حتى {fmt_num(price_max)} ريال")
    pages_max, pages_min = chat_rules.page_limit(messages)
    if pages_max or pages_min:
        pool = [b for b in pool if (b.get("page_count") or 0) > 0 and (not pages_max or b["page_count"] <= pages_max)
                and (not pages_min or b["page_count"] >= pages_min)]
        if en:
            labels.append(f"up to {pages_max} pages" if pages_max and not pages_min else
                          f"at least {pages_min} pages" if not pages_max else f"{pages_min}–{pages_max} pages")
        else:
            labels.append(f"حتى {fmt_num(pages_max)} صفحة" if pages_max and not pages_min else
                          f"{fmt_num(pages_min)} صفحة أو أكثر" if not pages_max else f"بين {fmt_num(pages_min)} و{fmt_num(pages_max)} صفحة")
    return pool, labels


def fallback_similar_books(refs, all_books, shown, cards, message, language="ar", limit=4, history=(), info=None):
    """FALLBACKSIM: in-stock books like the reference books -> [(book, reason, match)], at most one per author and per series.
    Never a reference itself, another edition of it, or (unless the visitor asks about parts) another part of its series.
    history: the visitor's earlier messages, whose conditions still hold (AUDFIX, audit 2026-11-01 P2); info (a dict) gets the
    labels of the conditions in force."""
    en = language == "en"
    ref_ids = {b["book_id"] for b in refs}
    for b in list(refs):
        ref_ids |= {e["book_id"] for e in same_book_editions(b, all_books)}
    like_ids = set(ref_ids)   # the work itself: its relations and its stored meaning are the likeness
    for b in list(refs):
        # … and never the same work under another title or language («Love in the time of cholera» for «الحب في زمن الكوليرا»)
        ref_ids |= {e["book_id"] for e in work_copies_other_titles(b, all_books)}

    def _k(s):
        return chat_rules.title_key(normalize(s or ""))
    ref_keys = {_k(all_books[i].get("title")) for i in ref_ids if i in all_books}
    series_ids, series_keys = set(), set()
    if not chat_rules.part_asked(message):
        for b in refs:
            series_ids |= {x["book_id"] for x in chat_rules.series_others(b, all_books)}
            series_keys |= {k for k in (_k(b.get("series_name")), (title_part(b.get("title")) or ("",))[0], _k(b.get("title"))) if k}
    ref0 = refs[0]
    form = chat_rules.book_is_fiction(ref0)
    child = ref0.get("genre") in CHILD_GENRES

    def _ok(b):
        if not b or b["book_id"] in ref_ids or b["book_id"] in shown or b["book_id"] in series_ids or not is_available(b):
            return False
        if _k(b.get("title")) in ref_keys:
            return False
        if series_keys and (_k(b.get("series_name")) in series_keys or (title_part(b.get("title")) or ("",))[0] in series_keys):
            return False
        return chat_rules.same_form_as(form, b) and (b.get("genre") in CHILD_GENRES) == child
    pool = [b for b in all_books.values() if _ok(b)]
    # a wish said beside the likeness («شي مثله بس أقصر») and the conditions of the earlier messages («ابغى رواية قصيرة»
    # before «كتب قريبة منه»; AUDFIX, audit 2026-11-01 P2), read without the reference's title
    pool, _labels = fallback_similar_conditions(pool, refs, list(history or ()) + [message], cards, language, info)
    if info is not None:
        info["labels"] = _labels
    allowed = {b["book_id"] for b in pool}
    t0 = ref0.get("title") or ""
    found, seen = [], set()
    authors_seen, series_seen = set(), set()

    def _add(bid, why, match):
        b = all_books.get(bid)
        if not b or bid in seen or bid not in allowed:
            return
        ak = " ".join(normalize(b.get("author") or "").split())
        sk = _k(b.get("series_name")) or (title_part(b.get("title")) or ("",))[0]
        if (ak and ak in authors_seen) or (sk and sk in series_seen):
            return
        seen.add(bid)
        authors_seen.add(ak) if ak else None
        series_seen.add(sk) if sk else None
        found.append((b, why, match))
    rel = relations.for_books(sorted(like_ids))
    rows = sorted((r for rs in rel.values() for r in rs), key=lambda r: -(r.get("confidence") or 0))
    for r in rows:
        if r["relation_type"] == "similar" and len(found) < limit:
            _add(r["target_book_id"], (f"Similar to «{t0}»" if en else f"مشابه لـ«{t0}»: {r.get('reason_ar') or ''}".rstrip(": ")),
                 "related")
    if len(found) < limit:
        for bid, _s in embeddings.similar_to_book(sorted(like_ids), sorted(allowed), top=limit * 4):
            if len(found) >= limit:
                break
            _add(bid, (f"Close to «{t0}» in what it is about" if en else f"قريب من «{t0}» في موضوعه"), "reference")
    for r in rows:
        if r["relation_type"] == "same_topic" and len(found) < limit:
            _add(r["target_book_id"], (f"A subject close to «{t0}»" if en else f"موضوعه قريب من «{t0}»"), "related")
    return found[:limit]


def fallback_similar_answer(user_messages, all_books, shown, cards, language="ar", last_ids=()):
    """FALLBACKSIM: the whole keyword answer to «books like X» -> dict, or None when no reference book is found."""
    if not user_messages:
        return None
    got = fallback_like_reference(user_messages[-1], all_books, last_ids)
    if not got:
        return None
    refs, how = got
    en = language == "en"
    info = {}
    found = fallback_similar_books(refs, all_books, set(shown), cards, user_messages[-1], language,
                                   history=user_messages[:-1], info=info)
    labels = info.get("labels") or []
    several = how == "shown" and len(refs) > 1
    t = refs[0].get("title") or ""
    shelf = []
    if not found and info.get("clean_young"):
        # AUDFIX (audit 2026-11-04 P2): nothing close to it is clean for this young reader — said first, then the young readers'
        # shelf offered instead, as the model's answer does (QADFIX26 #1)
        _aud, _age = info["clean_young"]
        shelf = [(b, CLEAN_YOUNG_SHELF_REASON["en" if en else "ar"], "related")
                 for b in clean_young_shelf(all_books, set(shown) | {b["book_id"] for b in refs}, cards, _aud, _age, language)[:4]]
    if shelf:
        reply = ((f"I could not find an available book close to «{t}» that fits the reader's age with nothing for adults. "
                  "These books from our young readers' shelf have nothing for adults in their record (they are not necessarily "
                  "like it). Tap a book to see its details.") if en else
                 (f"ما لقيت الآن بين الكتب المتوفرة كتابًا قريبًا من «{t}» يناسب عمر القارئ وما فيه شي للكبار. هذي كتب من رف اليافعين "
                  "ما في سجلها تنبيه لمحتوى للكبار (مو بالضرورة قريبة منه). اضغط على أي كتاب تشوف تفاصيله."))
        found = shelf
    elif found:
        reply = (("These books are close to the ones I showed you and available now." if several
                  else f"These books are close to «{t}» and available now.") + " Tap a book to see its details." if en else
                 ("هذي كتب قريبة من الكتب اللي عرضتها لك ومتوفرة الآن." if several else f"هذي كتب قريبة من «{t}» ومتوفرة الآن.")
                 + " اضغط على أي كتاب تشوف تفاصيله.")
    elif labels:
        # AUDFIX (audit 2026-11-01 P2): nothing close keeps the visitor's conditions — say which, as the keyword answer does
        reply = ((f"I could not find an available book close to «{t}» that matches: "
                  + ", ".join(localize_reason(x) for x in labels) + ". Would you like to relax one condition?") if en else
                 (f"ما لقيت الآن بين الكتب المتوفرة كتابًا قريبًا من «{t}» يطابق: " + "، ".join(labels) + ". تحب نخفف أحد الشروط؟"))
    else:
        reply = ((f"I could not find an available book close to «{t}» right now. Tell me what you liked about it (its mood, its "
                  "subject, its style?) and I will look, or ask the library staff.") if en else
                 (f"ما لقيت الآن بين الكتب المتوفرة كتابًا قريبًا من «{t}». قل لي وش عجبك فيه (أجواؤه؟ موضوعه؟ أسلوبه؟) "
                  "وأدوّر لك، أو اسأل موظف المكتبة."))
    return {"reply": reply,
            "books": [with_origin(dict(b, reason=why, match=match), language) for b, why, match in found],
            "suggestions": (["Something lighter", "A novel", "Show me others"] if en else ["شي أخف", "رواية", "ورّني غيرها"]),
            "understanding": "keywords",
            "_debug": {"mode": "keywords", "why": "similar to a book", "reference": [b["book_id"] for b in refs], "how": how,
                       "found": [b["book_id"] for b, _, _ in found], "conditions": labels,
                       "clean_young": bool(info.get("clean_young")), "clean_young_shelf": [b["book_id"] for b, _, _ in shelf]}}


def chat_keyword_fallback(user_messages, all_books, shown, cards, language="ar", last_ids=()):
    """Safe answer without a model: keyword understanding of the visitor's messages, read in
    order (later messages override earlier ones), with the same constraints as the AI path."""
    _sim = fallback_similar_answer(user_messages, all_books, shown, cards, language, last_ids)
    if _sim:
        return _sim
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
    _fa = fallback_author(user_messages[-1], all_books) if user_messages else None
    if _fa:
        en = language == "en"
        _name, _theirs = _fa
        if _theirs:
            reply = (f"These books by «{_name}» are available now. Tap a book to see its details." if en
                     else f"هذي كتب «{_name}» المتوفرة عندنا الآن. اضغط على أي كتاب تشوف تفاصيله.")
            books = [with_origin(dict(b, reason=("By " if en else "من كتب ") + _name, match="direct"), language)
                     for b in _theirs if b["book_id"] not in shown][:4] or [with_origin(dict(b, reason=("By " if en else "من كتب ") + _name, match="direct"), language) for b in _theirs[:4]]
        else:
            reply = (f"We have none of «{_name}»'s books in stock right now. Tell me what you like in their writing (a novel? ideas? a memoir?) and I will look, or ask the library staff."
                     if en else f"ما عندنا من كتب «{_name}» شيء متوفر الآن. قل لي وش يعجبك في كتاباته (رواية؟ فكر؟ سيرة؟) وأدوّر لك، أو اسأل موظف المكتبة يتأكد لك.")
            books = []
        return {"reply": reply, "books": books,
                "suggestions": (["A novel", "Something light", "Show me others"] if en else ["رواية", "شي خفيف", "ورّني غيرها"]),
                "understanding": "keywords", "_debug": {"mode": "keywords", "why": "author", "author": _name}}
    prefs, pprefs, exclusions, _ = prefs_from_ai(understood, normalize(" ".join(user_messages)))
    pool = [b for b in all_books.values() if is_available(b) and b["book_id"] not in shown]
    if user_messages and (chat_rules.parent_reader(user_messages)
                          or (adult_said(user_messages[-1]) and not child_said(user_messages[-1]))):
        # «… للمراهقين، لي أنا كأم» with the model down: the reader is the parent, as in run_chat
        # (audit 2026-10-12 evening #1: only YA books were offered to the mother here)
        prefs = dict(prefs, genres=set(prefs.get("genres") or ()) - CHILD_GENRES)
        pprefs = dict(pprefs, audience=None)
        pool = [b for b in pool if b.get("genre") not in CHILD_GENRES]
    clean_young_wish(pprefs, user_messages)   # QADFIX26 #1 here too (AUDFIX, audit 2026-11-04 P2)
    pool, labels = constrain(pool, prefs, pprefs, exclusions, cards)
    scored = score_books(pool, prefs, pprefs, cards)
    direct = [(r, b) for s, r, b in scored if s > 0]
    # INBOXFIX 2026-10-29 (library inbox 12:08): with nothing understood in reading words, books chosen by one word of
    # the message («عندكم حيونة الإنسان؟» -> «دفاع عن الإنسان») are no answer: when the message has two words or more,
    # a book must carry two of them in its title, subjects or author; none -> say so plainly and ask
    _kw = [strip_al(w) for w in (understood.get("title_keywords") or []) if authors.key(w) not in _FB_ASK_WORDS]
    _nothing = not keyword_prefs.understood_anything(understood)
    if _nothing and len(set(_kw)) >= 2:
        def _carries(b):
            have = {strip_al(w) for w in normalize(" ".join(str(b.get(k) or "") for k in ("title", "subjects", "author"))).split()}
            return sum(1 for w in set(_kw) if w in have) >= 2
        direct = [(r, b) for r, b in direct if _carries(b)]
    direct = direct[:4]
    books = [dict(b, reason=reason_text(r, language), match="direct") for r, b in direct]
    if not direct and _nothing and _kw:
        books = []
        reply = ("I could not find an available book matching those words, and I could not pin down your request right now. Tell me the kind of book "
                 "or the mood you want (a novel, something light, a thriller…), or ask the library staff." if language == "en" else
                 "ما لقيت بين الكتب المتوفرة كتابًا بهذا الوصف، وما قدرت أفهم طلبك بدقة الحين. قل لي نوع الكتاب أو المزاج اللي تبغاه "
                 "(رواية، شي خفيف، تشويق…)، أو اسأل موظف المكتبة.")
    elif direct:
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


def outside_subject_matches(words, ref, all_books, pool, exclude=(), language="ar", limit=4):
    """CHATFIX30 (1): books in `pool` whose own title, keywords or subgenres carry the subject words of a missing book's
    outside tags (chat_rules.outside_subject_words: «Film» -> سينما، أفلام، فيلم، سيناريو) — never a word of the missing
    book's title, never a shelf name alone («فنون وسينما» holds embroidery too) -> [(book, why, "reference")]."""
    if not words:
        return []
    scored = []
    for bid in pool:
        b = all_books.get(bid)
        if not b or bid in exclude or not is_available(b) or normalize(b["title"]) == normalize(ref["title"]):
            continue
        have = set(normalize(b.get("title") or "").split())
        for t in _json_list(b.get("keywords")) + _json_list(b.get("subgenres")):
            if isinstance(t, str):
                have |= set(normalize(t).split())
        hit = sorted(words & {strip_al(w) for w in have})
        if hit:
            scored.append((len(hit), b, hit))
    scored.sort(key=lambda x: (-x[0], x[1]["book_id"]))
    return [(b, (f"Close to «{ref['title']}» in its subject: " + ", ".join(hit)) if language == "en" else
             (f"قريب من «{ref['title']}» في موضوعه: " + "، ".join(hit)), "reference") for _s, b, hit in scored[:limit]]


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


def _run_chat_once(messages, active=None, language="ar", clear_audience=False, drop_context=None, second_look_note=None,
                   register_msgs=None):
    """Core conversation turn. Raises ValueError for malformed input. run_chat() below may call it a second time."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages required")
    chat_rules.set_model_parent(False)   # QADFIX12 #3: set again below from this turn's interpretation
    set_model_reader_age(None, None)      # QADFIX14 #2: likewise
    all_books = {b["book_id"]: dict(b) for b in list_books(only_active=False)}
    conv, shown, streak, last_batch = [], [], 0, []
    last_reply_ids = []   # the books of the assistant's last turn (empty when it showed none)
    for m in messages[-CHAT_MAX_MESSAGES:]:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), str(m.get("content", "")).strip()[:600]
        if role not in ("user", "assistant") or not content:
            continue
        if role == "assistant":
            raw_ids = m.get("book_ids") if isinstance(m.get("book_ids"), list) else []
            ids = [int(i) for i in raw_ids if str(i).isdigit() and int(i) in all_books]
            last_reply_ids = ids
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
    # CHATFIX30c (2), conversation c-mvvbiykv: «فاجئيني بكتب جديدة» (the start button, or typed) got «تبغى المفاجأة رواية، ولا
    # كتاب غير روائي؟» — a surprise asked for is a request for books now
    if chat_rules.surprise_asked(user_messages[-1]):
        skip_questions = True
    if skip_questions:
        streak = max(streak, 2)
    cards = get_profile_cards()

    active = active if active is not None else llm.get_active()
    # a tapped start button: Manar asks that path's first question before any book (Ibrahim 2026-10-11)
    _opening = start_paths.opener(user_messages, language)
    if _opening and chat_rules.surprise_asked(user_messages[0]):
        _opening = None   # CHATFIX30c (2): «فاجئيني بكتب جديدة» brings books at once (Ibrahim «ابدا الاصلاح كله», 2026-10-30)
    if _opening:
        return {"reply": _opening[0], "books": [], "suggestions": list(_opening[1]), "understanding": "ai",
                "provider": active[0] if active else None, "model": active[1] if active else None,
                "_debug": {"mode": "start_path", "path": user_messages[0]}}
    # «رشّحي لي تفضيلات قرّاء مكتبة ثلاث» tapped (Ibrahim 2026-10-27): the books this library's readers rated highest
    if helper_chips.is_favorites(user_messages[-1]):
        return _favorites_answer(all_books, shown, language, active)
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
    # INBOXFIX2 (library inbox 2026-10-29, conversation 8.7, Ibrahim «هذا خطأ ما ابغاه يتكرر»): «ظل الريح» -> its card ->
    # «رشّحي لي كتابًا» was answered with «وش الجو اللي ودك فيه؟». A bare «recommend me a book» right after a turn about one
    # book asks for a book like it
    _like_last = None
    if (len(user_messages) >= 2 and len(last_reply_ids) == 1 and conv[-2]["role"] == "assistant"
            and chat_rules.bare_recommend(user_messages[-1])):
        _t = _like_last = all_books[last_reply_ids[0]]["title"]
        conv[-1] = {"role": "user", "content": (f"Recommend me another book like «{_t}»" if language == "en" else f"رشّح لي كتابًا ثانيًا يشبه «{_t}»")}
        user_messages[-1] = conv[-1]["content"]
        user_text = " ".join(user_messages)
    it = (chat.interpret(conv, active, language) if language == "en" else chat.interpret(conv, active)) if active else None
    if it is not None and streak < 2 and start_paths.is_undecided(user_messages[-1]):
        it["intent"] = "clarify"   # «ما أدري وش يحب»: one more question first (Ibrahim 2026-10-11)
    if it is None:
        out = chat_keyword_fallback(user_messages, all_books, set(shown), cards, language, last_ids=last_reply_ids)
        if active:
            out["_debug"].update(failed_stage="interpret", call=dict(chat.last_failure.get("call") or {}))
        return out

    interpret_call = dict(llm.last_call)
    # INBOXFIX2 (library inbox 2026-10-29, conversation 8.7): «ظل الريح» alone got «تقصد رواية «ظل الريح» لكارلوس زافون؟ وش
    # الجو اللي ودك فيه؟» although it is in stock in two editions (the model read the bare title as unclear; 1 time in 5 in the
    # replay). A message that is a stocked title is a question about that book, never a question back
    _exact_said = exact_title_books(user_messages[-1], all_books)
    if _exact_said and it.get("intent") in ("clarify", "smalltalk", "off_topic"):
        it["intent"] = "about_book"
        if not it.get("mentioned_titles"):
            it["mentioned_titles"] = [_exact_said[0]["title"]]
    # QADFIX12 #6 (QAD 2026-10-22, case 32): a visitor who writes in English, asks for English or does not read Arabic is
    # answered in English on every path, whatever the page language (the model reads it, in any wording)
    if language != "en" and it.get("reply_language") == "en":
        language = "en"
    # the same asked in the visitor's own words («وش أكثر كتب عجبت قرّاءكم؟»): the model reads it (2026-10-27)
    # QADFIX26 (QAD 2026-11-01 case 15.1, grave): «وش اعلى رواية لإليف شافاق تقييم عندكم؟» was answered «لسّا ما تجمّعت آراء قرّاء
    # مكتبة ثلاث» with no book — an author or a title named, or «تقييم» with no word for this library's readers, is the ordinary
    # rating question (stored Goodreads ratings, CHATFIX30c), not our readers' favourites
    if it.get("thalath_favorites"):
        _lf26 = " " + " ".join(normalize(re.sub(r"[؟?!.،,]", " ", user_messages[-1])).split()) + " "
        _named26 = bool(it.get("mentioned_titles") or (it.get("prefs") or {}).get("authors")
                        or (it.get("author_request") or {}).get("mode") not in (None, "", "none"))
        _readers26 = re.search(r" (?:ال|لل)?(?:قراء|قرائ|قراي|زوار|زبائن|زباين|عملاء|عملا|رواد|متابعين)(?:كم|نا|ك)? "
                               r"| (?:your|the library's|thalath) (?:readers|visitors|customers)| readers ", _lf26)
        if _named26 or (not _readers26 and re.search(r"تقييم|تقيم|مقيم|rating|rated", _lf26)):
            it["thalath_favorites"] = False
            it["favorites_bypassed26"] = True
    if it.get("thalath_favorites"):
        return _favorites_answer(all_books, shown, language, active)
    # QADFIX12 #3 (QAD 2026-10-22, case 13.1; QADFIX5 #9 and QADFIX9 ج4 came back): «كتاب لي أنا عن تربية طفل عمره سنتين» —
    # the model says the reader is the visitor, an adult; the child's age is what the book is about. Every parent-reader
    # check of this turn (chat_rules.parent_reader) honours it, not only the phrasings in its word lists.
    # QADFIX16 (QAD 2026-10-24, cases 22.3, 41.2): the visitor reading the book himself is a parent reader only when the book
    # is about raising or dealing with a child (the model reads it: about_raising_child); «وأنا بعد أبي شي عن كرة القدم لي»
    # and «تطوير ذات لي أنا، مو تربية» keep their own subject, and no word list overrides the model then
    _about_child = it.get("about_raising_child", True) is not False
    _model_parent = bool(it.get("reader_is_visitor")) and (it.get("prefs") or {}).get("audience") == "adult" and _about_child
    chat_rules.set_model_parent(_model_parent, not_parent=bool(it.get("reader_is_visitor")) and not _about_child)
    if it.get("avoid_author_countries"):
        # QADFIX16 (case 41.1): a country the visitor refused is never a country wished for
        _avc = {normalize(c) for c in it["avoid_author_countries"]}
        it["author_countries"] = [c for c in it.get("author_countries") or [] if normalize(c) not in _avc]
    if not _model_parent:
        set_model_reader_age(user_messages, it.get("reader_age"))
    # QADFIX21 (QAD 2026-10-26, case 14.1): «صباح الورد يا منار» — a greeting that opens with a greeting word and says nothing
    # else (no question, no ask) is a greeting, even when its words are also a book's title («صباح الورد» of Mahfouz)
    _gw = [w for w in normalize(user_messages[-1]).split() if w not in {"يا", "منار", "manar"}]
    _greeting_only = bool(_gw and _gw[0] in keyword_prefs.GREETING_WORDS and len(_gw) <= 3
                          and not re.search(r"[؟?]", user_messages[-1]) and not shown)
    if _greeting_only:
        it["mentioned_titles"] = []
        if it.get("intent") in ("about_book", "recommend", "compare"):
            it["intent"] = "smalltalk"
    author_request = it.get("author_request", {})
    if it.get("intent") in ("off_topic", "smalltalk") and (author_request or {}).get("mode") in (None, "", "none"):
        # a question about a catalog author is a reading question: «هل تركي بن فهد آل سعود شاعر؟» -> what we have by
        # him and what his books are (library inbox 2026-10-11 §3.8; MGR 2026-10-17)
        _named_author = catalog_author_in(user_messages[-1], all_books)
        if _named_author:
            author_request = dict(author_request or {}, name_as_written=_named_author["name_ar"],
                                  full_name=_named_author["name_ar"], aliases=[], mode="works")
            it["author_request"], it["intent"], it["author_question"] = author_request, "recommend", _named_author["name_ar"]
        elif find_publisher([], user_messages[-1], all_books):
            # QADFIX24 (replay of QAD 2026-10-30, case 5.1): «المتوسط تنشر شعر؟» read as off topic («ما عندي تفاصيل عن إصدارات
            # دار المتوسط»): a question about a publisher we stock is a reading question
            it["intent"] = "recommend"
    # QADFIX33 (QAD 2026-11-10 case 8.1, minor): «عبدالرحمن منيف أخذ جائزة نوبل؟» was read as off topic with «books like Munif»: a
    # question whether a writer won the Nobel is a reading question about that writer (the Nobel rows answer it), not a wish for
    # books like his
    if _NOBEL_Q33.search(normalize(user_messages[-1]).lower()) and it.get("intent") in ("off_topic", "smalltalk"):
        it["intent"] = "recommend"
        if ((author_request or {}).get("mode") in ("similar", "both")
                and not re.search(r"(?:^| )(?:يشبه|تشبه|مثل|زي|شبه|نفس اسلوب|like|similar)(?: |$)", " ".join(normalize(user_messages[-1]).split()).lower())):
            author_request = dict(author_request, mode="works")
            it["author_request"] = author_request
        it["nobel_question33"] = True
    # CHATFIX30 follow-up (MGR replay 2026-10-30): «هنري ترويا» / «روي ياكوبسن» alone, read by the model as «clarify» with no
    # author request, got «تقصد تبغى كتابًا لهنري ترويا؟ وش النوع…». A message that is only a person's name (chat_rules.
    # looks_like_name) and names no book is a request for that author: the author flow answers it (his books, «تقصد …؟» for a
    # catalog author of that family name, or «ما عندنا الآن كتب لـX…» logged as a missing author)
    if (len(user_messages) == 1 and it.get("intent") in ("clarify", "smalltalk", "off_topic", "recommend")
            and (author_request or {}).get("mode") in (None, "", "none") and not it.get("mentioned_titles")
            and not _exact_said and not _greeting_only
            and chat_rules.looks_like_name(user_messages[-1], _author_first_names(all_books))
            and _title_word_count_of(user_messages[-1].split()[-1], all_books) <= 2):
        _bn = " ".join(user_messages[-1].strip(" ؟?!.،,").split())
        author_request = dict(author_request or {}, name_as_written=_bn, full_name=_bn, mode="works", bare_name=True,
                              aliases=list((author_request or {}).get("aliases") or []))
        it["author_request"], it["intent"] = author_request, "recommend"
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
    if (author_request.get("mode") == "list" and not str(author_request.get("name_as_written") or "").strip()
            and it.get("author_countries")):
        # QADFIX16 (QAD 2026-10-24, case 8.3): "Do you have any Iranian novelists?" names a country, not a name to list: the
        # books of authors from there, not «What name should I search for?»
        author_request = dict(author_request, mode="none")
        it["author_request"] = author_request
        if it.get("intent") in ("clarify", "smalltalk", "off_topic"):
            it["intent"] = "recommend"
    _pr = it.get("prefs") if isinstance(it.get("prefs"), dict) else {}
    if (str(author_request.get("name_as_written") or "").strip() and author_request.get("mode") in (None, "", "none")
            and (_pr.get("adaptation") or _pr.get("awards")) and not it.get("mentioned_titles")
            and authors.lookup(str(author_request.get("full_name") or author_request.get("name_as_written"))) is not None):
        # QADFIX21 (QAD 2026-10-26, case 7.1): «مسلسل نتفليكس مأخوذ من رواية لماركيز، الرواية عندكم؟» — the author named is the
        # author of the book asked for: his books carry the series wish, not every adapted novel of anyone
        author_request = dict(author_request, mode="works")
        it["author_request"] = author_request
    # QADFIX23 (QAD 2026-10-28, case 30, grave): a writer with no book of his in stock whose name titles books we have
    # («المتنبي» → «مع المتنبي») — the books about him are the answer, no «تقصد …؟» about the same name
    _about_name, _about_books = None, []
    _pr0 = it.get("prefs") if isinstance(it.get("prefs"), dict) else {}
    _about_asks = []
    if author_request.get("mode") in ("works", "both", "similar") and str(author_request.get("name_as_written") or author_request.get("full_name") or "").strip():
        _about_asks.append(author_request)
    # «ايه المتنبي الشاعر… كتاب عنه أو ديوانه» (case 30.2): the name read as an author wish or a subject, not as an author request
    _about_asks += [{"name_as_written": n, "full_name": n, "aliases": []} for n in list(_pr0.get("authors") or []) + list(_pr0.get("unsupported_requests") or [])
                    if isinstance(n, str) and 1 <= len(n.split()) <= 4 and authors.lookup(n)]
    # QADFIX24 (QAD 2026-10-30, case 42.2, grave): "Do you have any mysteries in English?" after "Something like Agatha Christie"
    # — the name carried from the message before (a style the visitor likes, not a person to read about) turned the answer
    # into two Arabic books ABOUT Christie and «no English copy», with Inferno in English in stock. Books about a person only
    # for a name said in THIS message, and never for a name given as a likeness («مثل / زي / like X»)
    _about_asks = [a for a in _about_asks if name_said_now(a, user_messages[-1]) and not name_as_likeness(a, user_messages[-1])]
    for _ask in _about_asks:
        _about_name, _about_books = books_about_person(_ask, all_books)
        if _about_books:
            break
    if _about_books:
        _drop = {authors.key(n) for n in [_ask.get("name_as_written"), _ask.get("full_name"), _about_name] if isinstance(n, str)}
        if _ask is author_request:
            author_request = dict(author_request, mode="none")
            it["author_request"] = author_request
        if isinstance(it.get("prefs"), dict):
            it["prefs"]["authors"] = [a for a in it["prefs"].get("authors") or [] if authors.key(a) not in _drop]
            it["prefs"]["unsupported_requests"] = [u for u in it["prefs"].get("unsupported_requests") or [] if authors.key(u) not in _drop]
        if it.get("intent") in ("clarify", "smalltalk", "off_topic", "author"):
            it["intent"] = "recommend"
    # QADFIX23 (QAD 2026-10-29, case 2.2, grave): «ابي فلسفة بحتة، مثلا عن هيغل او هوبز» — names after «عن / مثل عن» that only a
    # few titles in stock carry («هيغل الأول في سياقه»، «العقل والحرية في فلسفة هوبز السياسية») are subjects to look for in the
    # titles, though the interpreter kept only «الفلسفة»
    if isinstance(it.get("prefs"), dict) and author_request.get("mode") in (None, "", "none"):
        for _w in title_subject_names(user_messages[-1], all_books):
            if _w not in (it["prefs"].get("unsupported_requests") or []):
                it["prefs"].setdefault("unsupported_requests", []).append(_w)
    if _ANY_LANG30.search(" " + " ".join(normalize(user_messages[-1]).split()) + " "):
        it["copy_language"] = None   # QADFIX30 m8 (QAD 2026-11-07 case 42.1): «بالعربي او انجليزي ما تفرق»
    # QADFIX30 #4 (QAD 2026-11-07 case 45.1, grave): «ابي كتاب عن بناء العادات بس مو العادات الذرية لأني قريته» — the title ruled out
    # was taken as the book asked for. A title the visitor refuses in this message is a rejected reference (QADFIX15: never a card),
    # its copies are left out of the request below, and it is no longer a title asked about
    _refused30 = [t for t in it.get("mentioned_titles") or [] if chat_rules.title_refused(t, user_messages[-1])]
    if _refused30:
        it["mentioned_titles"] = [t for t in it["mentioned_titles"] if t not in _refused30]
        _rv30 = list(it.get("title_variants") or []) if not it["mentioned_titles"] else []
        it["refused_ids30"] = sorted({b["book_id"] for t in _refused30 + _rv30 for b in match_titles([t], all_books, max_rank=1)})
        if not it["mentioned_titles"]:
            it["title_variants"] = []
        it["references"] = ([r for r in it.get("references") or [] if not (isinstance(r, dict) and r.get("title") in _refused30)]
                            + [{"title": t, "author": "", "format": None, "moods": [], "themes": [], "subgenres": [], "known": False,
                                "relation": "rejected"} for t in _refused30])
        if isinstance(it.get("prefs"), dict):
            it["prefs"]["unsupported_requests"] = [u for u in it["prefs"].get("unsupported_requests") or []
                                                   if " ".join(normalize(u).split()) not in {" ".join(normalize(t).split()) for t in _refused30}]
        if it.get("intent") in ("about_book", "compare", "clarify"):
            it["intent"] = "recommend"
    # QADFIX30 #3 (QAD 2026-11-07 case 25.2, grave): «ابي رواية صارت مسلسل» → محفوظ → «شي ثاني بس مو لمحفوظ» — the interpreter kept
    # محفوظ as the author asked for (mode works) and put «ليس من تأليف نجيب محفوظ» in unsupported_requests, so the reply was «كلها لنجيب
    # محفوظ… ويوجد بعد نسخ إنجليزية من كتبه». A named author the visitor refuses is an author left out (below), never the request
    avoid_authors30 = refused_authors30(user_messages, author_request, all_books) if "noauthor" not in drops else []
    if avoid_authors30:
        def _is_refused30(n):
            return isinstance(n, str) and n.strip() and any(author_matches(n, a) or author_matches(a, n) for a in avoid_authors30)
        if author_request.get("mode") not in (None, "", "none") and (
                _is_refused30(author_request.get("full_name")) or _is_refused30(author_request.get("name_as_written"))):
            author_request = dict(author_request, mode="none")
            it["author_request"] = author_request
            it["wants_new_options"] = True
        if isinstance(it.get("prefs"), dict):
            it["prefs"]["authors"] = [a for a in it["prefs"].get("authors") or [] if not _is_refused30(a)]
            _rk30 = {w for a in avoid_authors30 for w in normalize(a).split() if len(w) >= 4}
            it["prefs"]["unsupported_requests"] = [u for u in it["prefs"].get("unsupported_requests") or []
                                                   if not (_rk30 & set(normalize(u).split()))]
        if it.get("intent") in ("clarify", "smalltalk", "about_book"):
            it["intent"] = "recommend"
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
    # CHATFIX31 (1d), HOMEFOLD's live check 2026-10-30: "a light Arabic novel, under 200 pages" got «الرقة» (Foenkinos, translated)
    # — the model read «Arabic» as the copy's language. In English, an «Arabic novel / book / literature / author» is a work
    # first written in Arabic; only «in Arabic», "Arabic edition / version / translation / copy" is about the copy
    if chat_rules.arabic_origin_asked(user_messages[-1]) and not any(str(k).split(":")[0] == "lang" for k in drops):
        prefs["original_languages"] = set(prefs.get("original_languages") or ()) | {"ar"}
        if it.get("copy_language") == "ar":
            it["copy_language"] = None
    # QADFIX33 (QAD 2026-11-10 case 40.1, minor): «جدي يحب سير الصحابة» (genres «دين») → «تذكرة الأولياء» (Sufi saints) while «الوعد الحق»
    # (keywords «صحابة», the biography shelf) is in stock: a life story asked for («سير / سيرة / تراجم») keeps the biography shelf beside
    # the field read
    if (prefs.get("genres") and "سيرة ذاتية" not in prefs["genres"] and not author_only
            and set(prefs["genres"]) & {"دين", "تاريخ"}
            and re.search(r"(?:^| )[وب]?(?:ال)?(?:سير|سيره|تراجم|ترجمه حياه|biography|biographies|lives)(?: |$)",
                          " ".join(normalize(re.sub(r"[؟?!.،,]", " ", " ".join(user_messages[-2:]))).split()).lower())):
        prefs["genres"] = set(prefs["genres"]) | {"سيرة ذاتية"}
        it["bio_shelf33"] = True
    if pprefs.get("audience") == "young_adult" and "يافعين" in prefs["genres"] and prefs.get("subgenres"):
        # QADFIX16 (QAD 2026-10-24, case 24.2): a teenager gets every book that fits the kind asked for (QADFIX12 #7, Ibrahim
        # 2026-10-22), the young readers' shelf first: «يافعين» is that shelf, not a wish that hides the Arabic dystopias
        prefs["genres"] = {g for g in prefs["genres"] if g != "يافعين"}
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
    # QADFIX15 (Codex/AGY audit 2026-10-23, case 36.2 "Which of those is the shortest?"): «الأقصر» in any wording (the model's
    # order_by too) is an order among the books the request allows, not the wish «a short book»: read as length=short it
    # dropped every shown book over 220 pages, and the cards no longer matched the answer
    if (prefs.get("length") == "short"
            and (chat_rules.shortest_asked(user_messages[-1]) or (it or {}).get("order_by") == "shortest")
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
    # QADFIX21 (QAD 2026-10-26, case 50.1): «كتب عن تربية المراهقين؟ ولدي صار عنيد» — a book ABOUT raising a child or a teenager
    # (the model reads it) is read by the grown-up asking: the teenagers are its subject, never the reader's badge
    _aud_prefs = dict(it["prefs"], audience=None) if it.get("about_raising_child") is True else it["prefs"]
    pprefs["audience"] = chat_audience(user_messages, _aud_prefs, author_only or bool(author_names), clear_audience)
    if it.get("about_raising_child") is True and pprefs["audience"] in ("children", "young_adult") and not chat_rules.minor_self(user_messages):
        pprefs["audience"] = None
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
    # QADFIX20 (QAD 2026-10-25, case 15.1): "burnt out from work, want something calm and slow, nothing dark" — a mood, a pace
    # and a thing to keep out already say what to read: books now, not «what kind of calm?»; a vague wish («ما أدري وش يحب»,
    # «أي كتاب») still gets its question (Ibrahim 2026-10-11)
    _wish_kinds = sum(bool(x) for x in (prefs.get("genres"), prefs.get("subgenres"), prefs.get("length"), pprefs.get("moods"),
                                        pprefs.get("use_cases"), pprefs.get("weight"), pprefs.get("pace"), unsupported))
    if it["intent"] == "clarify" and _wish_kinds >= 2 and not start_paths.is_undecided(user_messages[-1]):
        it["intent"] = "recommend"
    # CHATFIX30 (5), Ibrahim 2026-10-30: «اقتراح ان منار تجاوب اولا قبل تقترح المفروض يكون كذا اذا كان السؤال ما يتضمن اقتراح.
    # وغالبا يكون السؤال الاول وانا طلبت هالشيء انه ما يقترح على طول من اول سؤال اذا كان السؤال ما يوضح الاقتراح بشكل قريب. يعني
    # سؤال عام بشكل كبير ويحتاج سؤال ثاني» (his rule of 2026-10-11, OWN1: questions before suggestions unless the request is clear;
    # it rested on the model's «clarify», and «فلسفة» or «السعادة» alone were read as «recommend» and got three books at once).
    # A visitor's FIRST message that names at most one bare subject and nothing else to choose by (chat_rules.general_first_message)
    # is answered with one short question; no book on that turn
    # CHATFIX30b (3), Ibrahim 2026-10-30: «التلوين» / «كتب تلوين» got «تقصد كتب عن التلوين، ولا كتب تلوين…؟» or «الحرب والتربنتين»
    # (a novel about a painter) while 2093 «مجموعة التلوين الكبيرة» and 3877 «كتاب تلوين حروف وحيوانات» are in stock: with no reader
    # named the reader was taken for a grown-up, and a request that names neither a genre nor a child keeps the children's shelf out
    # (meets_constraints). A colouring / activity book is a children's book: its reader is a child unless the visitor says
    # «للكبار»; and the child's age is not asked first («ابي كتاب تلوين لبنتي»): such books suit young children broadly
    _activity_now = chat_rules.activity_wish(user_messages[-1])
    if _activity_now and not pprefs.get("audience"):
        pprefs["audience"] = "children"
        if isinstance(it.get("prefs"), dict):
            it["prefs"]["audience"] = "children"
    if _activity_now and it["intent"] in ("clarify", "smalltalk"):
        it["intent"] = "recommend"
    _q30_general = bool(
        len(user_messages) == 1 and not shown and not skip_questions and it["intent"] in ("recommend", "clarify")
        and not it.get("mentioned_titles") and not it.get("references") and not it.get("thalath_favorites")
        and (author_request or {}).get("mode") in (None, "", "none") and not it.get("author_countries")
        and not it.get("order_by") and not prefs.get("awards") and not prefs.get("adaptation") and not prefs.get("length")
        and not prefs.get("original_languages") and not prefs.get("translated") and not pprefs.get("audience")
        and not _exact_said and chat_rules.general_first_message(user_messages[-1])
        # CHATFIX30 follow-up: an author's name alone («ماركيز» with a record of ours) is never «general»
        and not authors.lookup(user_messages[-1].strip(" ؟?!.،,"), spelling=False))
    if _q30_general:
        it["intent"] = "clarify"
    elif (len(user_messages) == 1 and it.get("intent") == "clarify" and pprefs.get("audience") in ("children", "young_adult")
          and not skip_questions and re.search(r"(?:^| )(?:تحب|يحب|حب|مجنونه|مجنون|مهووسه|مهووس|تموت علي|يموت علي|تعشق|يعشق|متعلقه ب|متعلق ب"
                                               r"|loves|likes|is into|crazy about|obsessed with)\s+\S+",
                                               " ".join(normalize(user_messages[-1]).split()))):
        # QADFIX24 (QAD 2026-10-30, case 7.1): «عندي بنت بأول ابتدائي مجنونة بالقطط، وتقرا انجليزي أكثر من عربي» names the child,
        # what she loves and the language: books for her, not «وش ودّها تحس وهي تقرأ؟» first
        it["intent"] = "recommend"
        if not unsupported:
            _lv24 = re.search(r"(?:^| )(?:تحب|يحب|حب|مجنونه|مجنون|مهووسه|مهووس|تموت علي|يموت علي|تعشق|يعشق|متعلقه|متعلق)\s+(\S+)",
                              " ".join(normalize(re.sub(r"[؟?!.,،]", " ", user_messages[-1])).split()))
            if _lv24:
                _w24 = re.sub(r"^(?:بال|بـ|ب|في)", "", _lv24.group(1))
                _w24 = _w24 if _w24.startswith("ال") else "ال" + _w24
                if topics.searchable([_w24], topics.index(all_books)):
                    unsupported = unsupported + [_w24]
    pprefs["reader_age"] = audience_age(user_messages) if pprefs["audience"] in ("children", "young_adult") else None
    _lastq = " ".join(normalize(user_messages[-1]).split())
    if (re.search(r"(?:^| )(?:ال)?تاريخ(?: |$)", _lastq) and not re.search(r"روايه|روايات|سيره|سير |مذكرات|novel|biograph", _lastq)
            and ("سيرة ذاتية" in prefs["genres"] or _LIVELY.search(_lastq))):
        # QADFIX5 #7 (QAD 2026-10-17, case 3.1): «تاريخ؟ بس مو ممل» is history told in a lively way, not biographies
        prefs["genres"] = (set(prefs["genres"]) - {"سيرة ذاتية"}) | {"تاريخ", "رواية"}
        # Ibrahim 2026-10-17: a historical novel is also history that is not boring — offer it too
        prefs["soft_subgenres"] = list(dict.fromkeys(list(prefs.get("soft_subgenres") or []) + ["رواية تاريخية"]))
        it["lively_history"] = True
    if re.search(r"(?:^| )(?:ال)?(?:ملحمي|ملحميه|ملحمه|ملاحم)(?: |$)|(?:^| )epics?(?: |$)", _lastq):
        # QADFIX32 (QAD 2026-11-09 case 15.1, minor): «ملحمي» (one word) → «طبل الصفيح» and «ستالين: سيرة سياسية» (a long life of Stalin):
        # an epic is a kind of story — the books our records call «ملحمة / رواية ملحمية / فانتازيا ملحمية» rank first
        prefs["soft_subgenres"] = list(dict.fromkeys(list(prefs.get("soft_subgenres") or []) + ["ملحمة", "ملحمية"]))
        it["epic32"] = True
    if re.search(r"(?:^| )(?:ال)?(?:روايه|روايات) (?:ال)?تاريخي(?:ه)?(?: |$)|historical novel", _lastq) and "تاريخ" in prefs["genres"]:
        # QADFIX6 (replay of QAD 2026-10-18 case 34.1): «رواية تاريخية» is a novel, not history books beside novels
        prefs["genres"] = (set(prefs["genres"]) - {"تاريخ"}) | {"رواية"}
        if not prefs.get("subgenres"):
            prefs["subgenres"] = ["تاريخية"]
    if _GORE_NOT.search(_lastq):
        # QADFIX5 #8 (QAD 2026-10-17, case 35): «nothing gory / بدون دم» is content to avoid, never a subject
        pprefs["avoid"] = set(pprefs.get("avoid") or ()) | {"violence"}
        unsupported = [u for u in unsupported if not _GORE_WORD.search(" ".join(normalize(u).split()))]
        if pprefs.get("audience") in ("children", "young_adult") and _BLOOD32.search(_lastq):
            # QADFIX32 #3 (QAD 2026-11-09 case 3.1, medium): «بنت أختي ١٣ … ما يبون شي فيه دم أو قتل» → «أمير الضباب» alone (record: horror)
            pprefs["avoid"] = set(pprefs["avoid"]) | {"horror"}
            it["blood_out32"] = True
    elif pprefs.get("audience") in ("children", "young_adult") and any(
            _GORE_NOT.search(_t33) and _BLOOD32.search(_t33)
            for _t33 in (" ".join(normalize(m).split()) for m in user_messages[-3:-1])):
        # QADFIX33 #1 (QAD 2026-11-10 case 5.2, grave): «بس أمه ما تبي … قتل ولا دم» said in the message before, then «أبي شي من رف
        # اليافعين أو الأطفال فيه تنين» → «أمير الضباب» / «أضواء سبتمبر» (dark fantasy, record: horror) first: the blood refused for a
        # child or a teenager holds in the next messages too
        pprefs["avoid"] = set(pprefs.get("avoid") or ()) | {"violence", "horror"}
        it["blood_out33"] = True
    if _NONFIC26.search(" " + _lastq + " "):
        # QADFIX26 (QAD 2026-11-03 cases 33.1–33.2): «كفاية خيال، أبي كتاب من الواقع ينفعني» / «مو روايات أصلًا، أبي كتاب واقعي» —
        # not novels at all: «خيال» there is fiction (not fantasy only), «واقعي / من الواقع» is non-fiction (not «الواقعية
        # السحرية», not the title word «الواقع»)
        prefs["genres"] = set(prefs["genres"]) - _Q7_FICTION or {"تطوير ذاتي", "علم نفس", "سيرة ذاتية", "فكر", "تاريخ", "علوم"}
        prefs["subgenres"] = [s for s in prefs.get("subgenres") or [] if "واقع" not in normalize(s)]
        unsupported = [u for u in unsupported if not re.fullmatch(r"(?:من )?(?:ال)?واقع(?:ي|يه)?", " ".join(normalize(u).split()))]
        it["nonfiction26"] = True
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
    # QADFIX23 (QAD 2026-10-28, case 20): «ابغى عزازيل، الرواية اللي اخذت البوكر» then «وش الأقرب لها عندكم؟» — the prize describes the
    # book asked for (not in stock), it is no wish for the books offered in its place: «سنة موت ريكاردو ريس» and «هيباتيا» were
    # dropped for not being prize winners and only a question was left
    _lm = " " + " ".join(normalize(user_messages[-1]).split()) + " "
    _award_now = re.search(r"بوكر|جايز|جائز|فاز|فايز|نوبل|كتارا|زايد|غونكور|بوليتزر|booker|prize|award|pulitzer|goncourt", _lm)
    _closest_now = re.search(r" (?:ال)?(?:اقرب|يشبه|تشبه|يشبهها|تشبهها|مثلها|مثله|زيها|زيه|قريب منها|قريبه منها|قريب منه|بديل|بديلها)"
                             r"|similar|closest|nearest|something like", _lm)
    _award_of_title = re.search(r"(?:اللي|الي|التي|الذي)\s+(?:\S+\s+)?(?:اخذت|اخذ|فازت|فاز|حصلت|نالت|ربحت)", _lm) and it.get("mentioned_titles")
    if prefs.get("awards") and ((_closest_now and not _award_now) or (_award_of_title and it.get("intent") == "about_book")):
        q7["award_of_missing_title"] = list(prefs["awards"])
        prefs["awards"] = []
        for _k in ("award_year", "award_min_year", "award_max_year", "award_results"):
            prefs.pop(_k, None)
    # QADFIX33 #3 (QAD 2026-11-10 cases 33.1–33.2, grave): «الغونكور، وش أحدث رواية فائزة فيها عندكم؟» → «حديثة (من ٢٠٢١)» and «عندنا
    # كتابان» while seven winners are in stock; then «لا أبيها كلها» kept that span. «أحدث / آخر فائزة / latest» is an order by the year of
    # the prize (newest_award33, below), not a span of recent years; a span is read only from «حديثًا / مؤخرًا / جديدة / السنوات الأخيرة /
    # recently», and «كلها / كل الفائزة» lifts one carried from the message before
    _list_all33 = bool(prefs.get("awards")) and chat_rules.list_all_asked33(user_messages[-1])
    _newest33 = bool(prefs.get("awards")) and (chat_rules.newest_asked33(user_messages[-1]) or it.get("order_by") == "newest")
    _span33 = any(chat_rules.recent_filter_said33(t) for t in user_messages[-2:])
    if _list_all33 and not chat_rules.recent_filter_said33(user_messages[-1]):
        _span33 = False
    if _newest33:
        it["newest_award33"] = True
    # QADFIX35 #1 (QAD 2026-11-12 cases 28.1–28.3, grave; the mirror of QADFIX33 #3): «عندكم كتب فازت بجائزة غونكور؟ ابي أقدمها» → the
    # oldest order (QADFIX9) ran over the whole stock by first publication («منطق الطير»، «الفردوس»…; no card left), then «من أقدمها العاشق
    # (١٩٨٤)» and «العاشق طبعتنا ٢٠١٧» while «بؤساء بلفيل» won in 1975. «أقدم / أقدمها / أول فائزة / oldest» on a prize is the order of the
    # year each book itself won it, oldest first, over every winner in stock (oldest_award35, below); «يعني ما عندكم ولا وحدة فازت؟» right
    # after keeps it
    _lm35 = normalize(user_messages[-1])
    # («أيهم أقدم؟ / أقدمها» right after some books are shown, the prize not named again: among those, QADFIX14 م4, as before)
    _among35 = bool(last_batch) and chat_rules.among_shown_said(user_messages[-1]) and not chat_rules._PRIZE_WORD35.search(_lm35)
    if (prefs.get("awards") and not _newest33 and not _among35 and not _EDITION_ASK.search(_lm35)
            and (chat_rules.oldest_asked35(user_messages[-1]) or it.get("order_by") == "oldest"
                 or chat_rules.oldest_prize_kept35(user_messages))):
        it["oldest_award35"] = True
    # QADFIX35 (case 26.1): the titles the visitor names, whose words («نساء جسورات من طهران») are no wish of their own
    _ttl35 = tuple(it.get("mentioned_titles") or ()) + tuple((it.get("prefs") or {}).get("title_keywords") or ()) + tuple(prefs.get("title_keywords") or ())
    if _list_all33:
        it["list_all33"] = True
    elif prefs.get("awards") and len(user_messages) >= 2 and chat_rules.list_all_asked33(user_messages[-2]):
        # QADFIX35 (QAD 2026-11-12 case 3.2, medium): «وش السابع اللي قلت ما عندك تفاصيله؟» right after the whole list — the list given the
        # turn before is still what he is asking about: its winners stay in facts, and none of them is called a mistake
        it["list_all_prev35"] = True
    if prefs.get("awards") and not prefs.get("award_year") and wants_recent(user_messages[-2:]) and _span33:
        prefs["award_min_year"] = time.localtime().tm_year - RECENT_YEARS
    unsupported = [u for u in unsupported if not only_recency(u)]
    # QADFIX33 #2 (QAD 2026-11-10 case 25.1, grave): «تحب الجرايم الغامضة وتحليل الأدلة» — «تحليل الأدلة» is the crime story itself: never
    # a subject of its own («تحليل» found «الجين الأناني: تحليل كتاب…»); with no crime or mystery kind read, it is «جريمة» (a subject the
    # search reads as crime / mystery / «لغز», topics.py)
    if any(crime_side33(u) for u in unsupported):
        _cr33 = bool(young_kinds33(list(prefs.get("subgenres") or [])) & {"mystery"})
        unsupported = list(dict.fromkeys(x for u in unsupported for x in ([] if _cr33 and crime_side33(u) else
                                                                          ["جريمة"] if crime_side33(u) else [u])))
        it["crime_side33"] = True
    # QADFIX16 (QAD 2026-10-24, case 41.2): a negated wish («غير أمريكي»، «مو تربية») is never a subject to search for
    unsupported = [u for u in unsupported if not re.match(r"(?:غير|مو|مب|مش|ليس|بدون|بلا|not|non|no)(?: |-)",
                                                          " ".join(normalize(u).split()))]
    # a prize stored under «other» asked for by its name («جائزة الملك فيصل») (QA2 2026-10-09)
    _prize = prize_named(user_messages[-1]) if set(prefs.get("awards") or ["any"]) <= {"any"} else None
    if _prize:
        prefs["awards"] = [honors.named(_prize)]
        unsupported = [u for u in unsupported if not (set(normalize(u).split()) & set(normalize(_prize).split()))]
    # a prize stored under «other» written out in full, though one of its words is a prize of its own:
    # «غونكور للرواية الأولى» is not the main Goncourt (QAD 2026-10-12, case 16)
    # QADFIX19 (QAD 2026-10-25, case 4.1): «روايات لكتاب من المغرب أو الجزائر أو تونس» is not «جائزة المغرب للكتاب» — a prize
    # name in the message counts only when the model read a prize wish in it
    _sub = honors.other_name_in(user_messages[-1]) if prefs.get("awards") else None
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
    # QADFIX27 #11 (QAD 2026-11-04 case 11.1: «قصة تدور بين ملاجئ بيروت وقت الاقتتال في السبعينات» → «ما لقيت… ولا قريبًا»; 2026-11-05
    # case 5.1: «عائلة تنزح… أيام القصف في بيروت الثمانينات»): a decade said as the time the story takes place in («تدور / وقت / أيام /
    # زمن … في السبعينات») is its setting, not the years our books came out — it stays a subject, never a publication-year filter
    _yst27 = " " + " ".join(normalize(re.sub(r"[؟?!.،,]", " ", _ysrc or "")).split()) + " "
    # QADFIX28 (replay of QAD 2026-11-05 case 5.1): the second look's rewording «… بسبب القصف في بيروت بالثمانينات» lost «أيام» and the
    # decade became a publication-year filter («حكاية زهرة» 1980، «أفراح القبة» 1981): a decade said with a war event is the story's time
    if ((_lo or _hi) and decade_said(_ysrc) and not prefs.get("awards") and not _RELEASE_VERB.search(_yst27)
            and (re.search(r" (?:تدور|يدور|تصير|يصير|تجري|يجري|احداثها|احداثه|وقت|ايام|زمن|خلال|فتره|set in|takes place|during) ",
                           _yst27) or _WAR28.search(_yst27))):
        it["decade_setting27"] = [_lo, _hi]
        _lo = _hi = None
    if (_lo or _hi) and era_said(_ysrc) and not prefs.get("awards"):
        # QADFIX25 (case 49.1): an era is the years the work was written, never the year of our edition
        it["first_publication"] = True
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
    # QADFIX27 #8 (QAD 2026-11-04 case 40.1): «نسخة فرنسية» is the language of a copy asked for, never a subject searched by its words
    unsupported = [u for u in unsupported if not re.search(r"نسخه|نسخ|طبعه|edition|copy|version", normalize(user_messages[-1])) or not re.fullmatch(
        r"(?:(?:ال)?(?:نسخه|طبعه|ترجمه|النسخه|الطبعه) )?(?:بال|ال|ب)?(?:فرنسي|فرنسيه|الماني|المانيه|اسباني|اسبانيه|تركي|تركيه|روسي|روسيه"
        r"|ايطالي|ايطاليه|french|german|spanish|turkish|russian|italian)(?: (?:نسخه|طبعه|edition|copy))?|(?:french|german|spanish) (?:edition|copy|version)",
        " ".join(normalize(u).split()))]
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
            and it["intent"] in ("recommend", "clarify") and not _activity_now):   # CHATFIX30b (3): colouring books, no age first
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
    # QADFIX23 (QAD 2026-10-28, case 44.2): «ابني ٨ سنين…» then «لا هو يحب الكوميكس مو القصص» — the child's age is known and the
    # latest message speaks of him (هو / يحب), not of the visitor: no «للطفل ولا للكبار؟»
    _lm44 = " " + " ".join(normalize(user_messages[-1]).split()) + " "
    if (audience_age(user_messages) is not None
            and re.search(r" (?:هو|هي|يحب|تحب|يبي|تبي|يبغي|تبغي|يبغا|تبغا|يفضل|تفضل|يموت في|تموت في|he|she|likes|loves) ", _lm44)
            and not re.search(r" (?:انا|لي|لنفسي|نفسي|me|myself|for me) ", _lm44)):
        asked_before = True
        if earlier_aud in ("children", "young_adult") and not pprefs.get("audience"):
            # … and the books are for him: his shelf and his age (QADFIX23 replay: «مترو» and «الإلياذة» were offered to the boy of 8)
            pprefs["audience"] = earlier_aud
            it["prefs"]["audience"] = earlier_aud
            if pprefs.get("reader_age") is None:
                pprefs["reader_age"] = audience_age(user_messages)
    # QADFIX26 (QAD 2026-11-01 cases 4.2, 3.1; 2026-11-02 cases 2.2, 22.2; Ibrahim 2026-11-03 «اصلحها»): «ولدي في الصف الأول
    # ابتدائي يبي قصص الأنبياء» → «فيه قصة عن سيدنا يوسف بالذات؟»; «بنت عمرها ثمان سنين» → «وعن النبي موسى فيه شي يناسبها؟» —
    # the child's age is known and the latest message points at the child («يناسبها»، «يناسبه») or goes on with the kind of book
    # already asked for the child («قصص الأنبياء» → «قصة يوسف»), with no other reader named (no «لي / أنا / للكبار»): the books
    # are for that child, not «للطفل ولا للكبار؟». «بنتي عمرها ٨» → «عندكم كتب تاريخ؟» is still asked (Ibrahim 2026-10-08).
    _age26 = audience_age(user_messages)
    _ref26 = re.search(r" (?:يناسبها|يناسبه|تناسبها|تناسبه|يناسبهم|تناسبهم|يناسب عمرها|يناسب عمره|لعمرها|لعمره|يفيدها|يفيده|تحبه|يحبه"
                       r"|suits her|suits him|for her age|for his age) ", _lm44)
    _theme26 = bool(shared) and shared[0] in CHILD_THEMES and any(
        (detect_audience(normalize(t)) or kid_mentioned([t]) or child_said(t))
        and set(normalize(t).split()) & set(CHILD_THEMES[shared[0]].split()) for t in user_messages[:-1])
    if (shared and not asked_before and _age26 is not None and _age26 <= 17 and not clear_audience and (_ref26 or _theme26)
            and not re.search(r" (?:انا|لي|لنفسي|نفسي|me|myself|for me) ", _lm44) and not said_for_adult(user_messages)):
        asked_before = True
        it["audience_carried26"] = _age26
        if pprefs.get("audience") not in ("children", "young_adult"):
            pprefs["audience"] = "children" if _age26 <= 12 else "young_adult"
            it["prefs"]["audience"] = pprefs["audience"]
        if pprefs.get("reader_age") is None:
            pprefs["reader_age"] = _age26
    # QADFIX27 #7 (QAD 2026-11-05 case 18.3; QADFIX26 #2 came back): «ولدي صاير يسأل عن البراكين والزلازل» → «عمره ٨» → «طيب وعن
    # الفضاء؟» was answered «تبي كتب علوم للطفل ولا للكبار؟» — a short follow-up that only changes the subject («وعن الفضاء؟»،
    # «طيب وعن الديناصورات؟»), after the child's age is known and the child was the reader, names no other reader: still the child
    if (shared and not asked_before and _age26 is not None and _age26 <= 17 and not clear_audience
            and len(_lm44.split()) <= 5
            and re.match(r" (?:(?:طيب|طب|زين|اوكي|ok|okay|حلو|تمام) )?(?:و ?عن|وش عن|شي عن|وكتب عن|وفيه عن|وبعد عن|and about|what about|how about) ", _lm44)
            and not re.search(r" (?:انا|لي|لنفسي|نفسي|me|myself|for me) ", _lm44) and not said_for_adult(user_messages)
            and any(kid_mentioned([t]) or child_said(t) or detect_audience(normalize(t)) for t in user_messages[:-1])):
        asked_before = True
        it["audience_carried27"] = _age26
        if pprefs.get("audience") not in ("children", "young_adult"):
            pprefs["audience"] = "children" if _age26 <= 12 else "young_adult"
            it["prefs"]["audience"] = pprefs["audience"]
        if pprefs.get("reader_age") is None:
            pprefs["reader_age"] = _age26
    # QADFIX31 #4 (QAD 2026-11-08 cases 44.2–44.3, medium): "book for a 10 year old who hates reading" → two books for him → "something
    # with pictures, like comics" was answered "Are you looking for these books for the child or for adults?", then "For the child, I
    # already told you he's 10". A short refinement of the request just answered with books for that child («شي فيه صور»، "something
    # with pictures"), naming no other reader, is still for the child
    if (shared and not asked_before and _age26 is not None and _age26 <= 17 and not clear_audience and last_reply_ids
            and len(_lm44.split()) <= 7 and earlier_aud in ("children", "young_adult")
            # («كتب تاريخ؟» after a child is still asked: Ibrahim 2026-10-08 — only «شي … / something …», a refinement)
            and re.match(r" (?:(?:طيب|طب|زين|اوكي|ok|okay|حلو|تمام|then|maybe|hmm) )?(?:(?:ابي|ابغي|ابغا|بس|او|ولا) )?(?:شي|شيء|اشياء"
                         r"|something|anything) ", _lm44)
            and not re.search(r" (?:انا|لي|لنفسي|نفسي|me|myself|for me|for adults|للكبار|adult) ", _lm44) and not said_for_adult(user_messages)):
        asked_before = True
        it["audience_carried31"] = _age26
        if pprefs.get("audience") not in ("children", "young_adult"):
            pprefs["audience"] = "children" if _age26 <= 12 else "young_adult"
            it["prefs"]["audience"] = pprefs["audience"]
        if pprefs.get("reader_age") is None:
            pprefs["reader_age"] = _age26
    # QADFIX26 #1 (QAD 2026-11-03 case 17, 2026-10-25 case 3.2): a young reader and a clean book asked for — strict (clean_young_ok)
    if not clear_audience and clean_young_wish(pprefs, user_messages):
        it["clean_young"] = True
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
    # QADFIX26 (QAD 2026-11-03 case 6.3, grave; QADFIX7/8/10 class): «روايات تدور في الأندلس» → «شي ثاني» → «أقصر منها» — the
    # subject of the last message that said one carries on while only follow-ups («شي ثاني»، «أقصر») came after it; it is
    # never «stale» then (the shorter books were picked from the whole stock, «موضوعها مختلف عن الأندلس»)
    _real26 = max((k for k, t in enumerate(user_messages) if not follow_up_only(t)), default=-1)
    _keep26 = (_said_in([user_messages[_real26]]) if 0 <= _real26 < len(user_messages) - 1 and len(user_messages) > 1
               else set())
    def _stale(phrase):
        words = topics.content_words(phrase)
        if words and _keep26 and all(f & _keep26 for f in words):
            return False
        return bool(words) and all(f & _older for f in words) and not any(f & _recent for f in words)
    unsupported = [u for u in unsupported if not _stale(u)]
    # QADFIX26 (QAD 2026-11-02 cases 22.2–22.3): «ولدي تسع سنين وما يحب إلا الكورة» → «طيب شي علمي يناسبه» — a new kind of book
    # asked for after a hobby the child loves: the hobby is not the subject of the science books («ما لقيت كتابًا علميًا عن الكورة»
    # with a health booklet); only a subject the visitor stated as a liking («يحب / تحب / مهووس») and does not say again is dropped
    # (a field of knowledge only: «بنتي تحب الخيول» → «عندكم قصص؟» still wants horse stories)
    _gnow26 = (set(local_understand([user_messages[-1]]).get("genres") or ()) - _Q7_FICTION - {"أدب"}
               if len(user_messages) > 1 else set())
    if _gnow26 and not (_gnow26 & set(local_understand(user_messages[:-1]).get("genres") or ())):
        _last26 = _said_in([user_messages[-1]])
        def _liked26(u):
            words = topics.content_words(u)
            if not words or any(f & _last26 for f in words):
                return False
            src26 = [t for t in user_messages[:-1] if all(f & _said_in([t]) for f in words)]
            # the interpreter's own words for it («كرة القدم» for «الكورة والملاعب»): the earlier messages that said something
            src26 = src26 or [t for t in user_messages[:-1] if not follow_up_only(t)]
            return bool(src26) and all(re.search(r"(?:^| )(?:و?ما )?(?:يحب|تحب|يحبون|يموت في|تموت في|مجنون|مجنونه|مهووس|مهووسه|يعشق|تعشق"
                                                 r"|متعلق|متعلقه|loves|likes|is into|crazy about|obsessed with)(?: |$)",
                                                 " ".join(normalize(t).split())) for t in src26)
        if any(_liked26(u) for u in unsupported):
            it["hobby_dropped26"] = [u for u in unsupported if _liked26(u)]
            unsupported = [u for u in unsupported if not _liked26(u)]
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
        # QADFIX28 (QAD 2026-11-05 case 36.1): «قصة مبهجة، بعيد عن الموت والفقد» — «بعيد عن» is what he does not want, never the subject
        if _m and re.search(r"(?:^| )(?:بعيد|بعيده|بعيدا|بعيدين|ابعد|بعيدة|بعيدًا) عن ", _last[:_m.start() + 4] + " "):
            _m = None
        if _m and topics.searchable([_m.group(1).strip()], topics.index(all_books)):
            unsupported = [_m.group(1).strip()]
    # wishes from earlier messages that the latest message did not repeat: dropped when they leave
    # nothing for a new subject or a publisher the visitor just named (library trial, 2026-10-08)
    latest = local_understand([user_messages[-1]])
    # QADFIX12 (replay of QAD 2026-10-22 case 22.2): «شي واقعي عن المدرسة والصداقات» — a realistic story is a novel (the model
    # read it so); the subject searched in the whole stock keeps that kind, not «تاريخ المدرسة النصرية بغرناطة»
    _story_now = {"رواية"} if re.search(r"(?:^| )(?:شي|شيء|قصه|قصص|روايه|روايات|حكايه|حكايات)? ?واقعي(?:ه|ة)?(?: |$)|realistic",
                                        " ".join(normalize(user_messages[-1]).split())) else set()
    # QADFIX24 (QAD 2026-10-30, case 27.2, grave): «طيب بثينة العيسى عندكم لها روايات؟» right after «روائية كويتية فازت بجائزة؟»
    # kept the prize wish of the message before: her two novels in stock were filtered out and «ما عندي كتب مطابقة» was said.
    # An earlier prize or screen wish is opened like the other wishes unless this very message asks for it again
    _q24_last = " ".join(normalize(user_messages[-1]).split())
    _q24_award_now = bool(re.search(r"جائز|جوائز|بوكر|نوبل|غونكور|كتارا|فاز|فائز|حائز|\bprize|\baward|\bwon\b|\bwinn", _q24_last))
    _q24_screen_now = bool(re.search(r"فلم|فيلم|افلام|مسلسل|انمي|انيمي|كرتون|كارتون|رسوم متحرك|\bfilm|\bmovie|\bseries|\banime|\bcartoon|\btv\b",
                                     _q24_last))
    def _open_prefs(p):
        out = dict(p, genres=set(p["genres"]) & (set(latest["genres"]) | _story_now), subgenres=[], soft_subgenres=[],
                   length=p["length"] if latest["length"] else None,
                   original_languages=p["original_languages"] if latest["original_languages"] else set(),
                   translated=p.get("translated") if latest["translated"] else None)
        if not _q24_award_now:
            out.update(awards=[], award_year=None, award_min_year=None, award_max_year=None)
        if not _q24_screen_now:
            out.update(adaptation=None, adaptation_max_year=None, adaptation_min_year=None, adaptation_origin=None)
        return out
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
    # QADFIX24 (case 8.2): «انسى الفيلم» lifts the screen wish (and «مو لازم جائزة» the prize wish) said before
    _q24_forget = forgotten_wishes(user_messages[-1])
    if "adaptation" in _q24_forget and prefs.get("adaptation"):
        prefs.update(adaptation=None, adaptation_max_year=None, adaptation_min_year=None, adaptation_origin=None)
    if "awards" in _q24_forget and prefs.get("awards"):
        prefs.update(awards=[], award_year=None, award_min_year=None, award_max_year=None)
    if (pprefs.get("audience") == "children" and (pprefs.get("reader_age") or 99) <= 8 and "قصص مصورة" in (prefs.get("genres") or ())
            and not any(_COMICS_SAID.search(" ".join(normalize(t).split())) for t in user_messages)):
        # QADFIX24 (case 8.2): «قصة تناسب عمره ٤» is a picture book on the children's shelf, not the comics shelf («قصص مصورة»)
        prefs["genres"] = (set(prefs["genres"]) - {"قصص مصورة"}) | {"أطفال"}
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
            # CHATFIX30 (3): … or the name the visitor wrote is the model's full name in another spelling («روي ياكوبسون» /
            # «روي ياكوبسن»; author_flow.resolve answers at once, no «تقصد …؟»)
            if _full and (any(re.search(r"[A-Za-z]{3}", str(a or "")) for a in author_request.get("aliases") or [])
                          or author_request.get("spelling_of") or author_request.get("bare_name")):
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
        # QADFIX28 (QAD 2026-11-04 case 11.3, left by QADFIX27): «أقصر شوي» after two novels on the Lebanese civil war — the shorter
        # ones on that war («رحلة غاندي الصغير» 208) had been shown two turns before, so the shortest books of the shop («الشاب» 48)
        # came instead: a plain «shorter» keeps the books shown before the last answer (the reply says they were shown)
        _keep_old28 = (_keep_np and chat_rules.shortest_asked(user_messages[-1]) == "shorter"
                       and set(last_batch or ()) and len(user_messages) > 2)
        pool = [b for b in pool if b["book_id"] not in shown or (_keep_np and not b.get("page_count"))
                or (_keep_old28 and b["book_id"] not in set(last_batch))]
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
            # QADFIX25 (QAD 2026-10-31, case 4, grave): «ولدي ١٥ سنة مهووس بالفلك» — a teenager also gets the field's general books
            # («أنت والكون»، «عن هذا الكون الفسيح»): with the pool cut to the children's shelves, every book left was for 3–12 and the
            # age check (QADFIX24) had nothing older to keep
            _ra25 = audience_age(user_messages)
            if pprefs["audience"] == "young_adult" and (_ra25 is None or _ra25 >= 13):
                themed = themed + [b for b in pool if b not in themed and (b.get("genre") or "").strip() == child_theme]
            pool = themed
            prefs = dict(prefs, genres=set(prefs["genres"]) - {child_theme})
    pool_before = pool
    # QADFIX27 #5 (QAD 2026-11-05 case 29.1, grave): «عطني شي حاصل على جائزة زايد في فرع الآداب» → «ما لقيت…» while 871, 986 and 3813
    # won it in that branch: «الآداب» was read as the shelf «أدب» and emptied the prize pool. A branch named with a prize is the
    # prize's own category (book_awards.category), never a shelf
    _branch27 = None
    if prefs.get("awards") and not author_only:
        _branch27 = next((b for b in (chat_rules.award_branch(t) for t in reversed(user_messages[-2:])) if b), None)
    if _branch27:
        _bw27 = {strip_al(w) for w in normalize(_branch27).split()}
        _bw27 |= {"ادب"} if _bw27 & {"اداب", "ادب"} else set()
        prefs = dict(prefs, genres={g for g in (prefs.get("genres") or ()) if not ({strip_al(w) for w in normalize(g).split()} & _bw27)})
        unsupported = [u for u in unsupported if not chat_rules.award_branch(u) and not ({strip_al(w) for w in normalize(u).split()} <= _bw27)]
        _bp27 = [b for b in pool if honors.in_category(b["book_id"], prefs["awards"], _branch27)]
        if _bp27:
            pool = _bp27
            it["award_branch27"] = _branch27
        else:
            it["award_branch27_none"] = _branch27
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
    if it.get("award_branch27"):
        hard.append(lambda b, _k=tuple(prefs.get("awards") or ()), _br=_branch27: honors.in_category(b["book_id"], list(_k), _br))
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
    _q24_pub_kind = set()
    _q24_lastw = set(" ".join(normalize(re.sub(r"[؟?!.,،]", " ", _last_msg)).split()).split())
    _q24_said_kind = set(latest.get("genres") or ()) or {
        g for g in set((it.get("prefs") or {}).get("genres") or ()) if g not in CHILD_GENRES
        and {normalize(w) for w in g.split()} <= ({strip_al(w) for w in _q24_lastw} | _q24_lastw)}
    if found_pub and _q24_said_kind:
        latest = dict(latest, genres=sorted(_q24_said_kind))   # «لا ابي شعر تحديدًا» was read as no kind by the word reader
        # QADFIX24 (QAD 2026-10-30, cases 5.1, 5.2): «المتوسط تنشر شعر؟» / «ابي شعر تحديدًا من المتوسط» — the kind asked of the
        # publisher in this message is a firm wish (the model sometimes read none): «ما عندنا شعر من المتوسط» is said, never
        # «إيه، تنشر كتبًا أدبية» or «لقيت لك من إصداراتها» beside an essay and a novel
        _q24_pub_kind = set(latest["genres"])
        prefs["genres"] = set(prefs.get("genres") or ()) | _q24_pub_kind
    _pub_key27 = None
    if found_pub:
        key, publisher_name = found_pub
        _pub_key27 = key
        pub_pool = [b for b in pool_before if publisher_key(b.get("publisher")) == key]
        # QADFIX27 #6 (QAD 2026-11-05 case 7, 2026-11-04 case 27.1): «كم كتاب لها هنا؟» — «عدد الكتب» is the count asked, not a subject
        unsupported = [u for u in unsupported if not re.fullmatch(r"(?:ال)?عدد (?:ال)?(?:كتب|كتاب|اصدارات|الاصدارات|عناوين)(?: .*)?",
                                                                    " ".join(normalize(u).split()))]
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
        if _q24_pub_kind and not any((b.get("genre") or "").strip() in _q24_pub_kind for b in p_pool):
            p_pool = []   # QADFIX24 (case 5.1): none of that kind from this publisher — never its other books instead
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
            if _q24_pub_kind and not any((b.get("genre") or "").strip() in _q24_pub_kind for b in p_pool):
                p_pool = []   # QADFIX24 (case 5.1)
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
    country_has_books, country_near = [], []
    if not author_only and not genre_as_topic and "country" not in drops:
        country = wished_country(user_messages[-1]) or (
            wished_country(user_messages[-2]) if len(user_messages) > 1 and (len(user_messages[-1].split()) <= 4
                                                                       or _narrow_followup(user_messages[-1])) else None)
    if (country and country[0] in _ARAB_NATIONS and "ar" in (it["prefs"].get("original_languages") or [])
            and not it.get("author_countries")):
        # QADFIX17 (QAD 2026-10-24, case 41.3): «لكاتب عربي أو سعودي» — the model read any Arab author (original Arabic, no
        # one country): the Saudi adjective in the message does not narrow it to Saudi authors
        country = None
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
            # QADFIX18 (inbox 2026-10-24, «روايات حاصلة على جوائز» after «كتّاب كويتيين»): books by authors from there are in
            # stock, only none fits the rest of the wish (a prize) — not «no author recorded as from there»
            country_has_books = [b for b in (c_pool or [b for b in pool_before if _from_there(b)]) if is_available(b)]
            if country_has_books and prefs.get("awards"):
                # the nearest of them for a prize wish: the ones on a list or nominated, said as such (never «فاز»)
                _named_aw = [k for k in prefs["awards"] if k != "any"]
                for b in country_has_books:
                    _rows = [r for r in honors.load()["book"].get(b["book_id"], []) if not _named_aw or r.get("award_key") in _named_aw]
                    if _rows:
                        country_near.append((b, honors.reason_label(_rows[0])))
                country_near = country_near[:2]
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
        # QADFIX16 (QAD 2026-10-24, case 41): «بس مو أمريكي» in any wording — the countries the model read as refused
        no_country += [n for n in it.get("avoid_author_countries") or [] if n not in no_country]
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
    # QADFIX30 #3 (QAD 2026-11-07 case 25.2, grave): «بس مو لمحفوظ» — none of the refused author's books, in any language
    if avoid_authors30 and not author_only:
        def _not_by30(b, _n=tuple(avoid_authors30)):
            a = b.get("author") or ""
            return not any(author_matches(n, a) for n in _n)
        a_pool = [b for b in pool if _not_by30(b)]
        if not a_pool:
            a_all = [b for b in pool_before if all(h(b) for h in hard) and _not_by30(b)]
            a_pool, _l, _p, _ = constrain_relaxing(a_all, prefs, pprefs, exclusions, cards)
            if a_pool:
                labels, prefs = _l, _p
        if a_pool:
            pool = a_pool
            hard.append(_not_by30)
        else:
            avoid_authors30 = []
    # QADFIX30 #4 (QAD 2026-11-07 case 45.1, grave): «بس مو العادات الذرية لأني قريته» — never the book he read, in any of its copies
    if it.get("refused_ids30"):
        def _not_refused30(b, _ids=frozenset(it["refused_ids30"])):
            return b["book_id"] not in _ids
        pool = [b for b in pool if _not_refused30(b)] or pool
        hard.append(_not_refused30)
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
        # QADFIX24 (replay of QAD 2026-10-30 case 7.1): «تقرا انجليزي أكثر من عربي» is a preference, not a limit — with no copy in
        # that language on the subject asked for («القطط») and one in the other language («حكايات القط الشقي»), the subject wins
        # and the reply says no copy in that language is about it (before: «ما لقينا كتابًا عن القطط»)
        _q24_soft = bool(re.search(r"(?:اكثر|افضل) من (?:ال)?(?:عربي|انجليزي)|(?:^| )(?:يفضل|تفضل|نفضل|افضل|ياليت|يا ليت|لو امكن|اذا امكن|ان امكن)(?: |$)"
                                   r"|preferabl|if possible|mostly|more than arabic|rather than arabic",
                                   " ".join(normalize(" ".join(user_messages[-2:])).split())))
        if e_pool and _q24_soft and unsupported:
            _ti24 = topics.index(all_books)
            if (not any(topics.match(unsupported, b["book_id"], _ti24)[0] for b in e_pool)
                    and any(topics.match(unsupported, b["book_id"], _ti24)[0] for b in pool)):
                q7["edition_soft"] = (edition, list(unsupported))
                e_pool = []
        if e_pool:
            pool = e_pool
            hard.append(lambda b, _e=edition: ((b.get("language") or "ar").strip() or "ar") == _e)
        elif q7.get("edition_soft"):
            edition = None   # a preference only: the copies in the other language on the subject are offered
        else:
            # QADFIX24 (replay of QAD 2026-10-30, case 25.2): «وش عندكم لدان براون بالانجليزي؟» right after «Inferno» and «The Lost
            # Symbol» were shown — the new-options pool had left them out and «ما لقينا نسخة بالإنجليزية» was said: the copies in
            # that language just shown, by the author asked for, are the answer
            _e_auth = [a for a in (prefs.get("authors") or []) if str(a or "").strip()]
            _e_shown = [all_books[i] for i in shown if i in all_books and is_available(all_books[i])
                        and ((all_books[i].get("language") or "ar").strip() or "ar") == edition and all(h(all_books[i]) for h in hard)
                        and (author_only or (_e_auth and any(author_matches(a, all_books[i].get("author")) for a in _e_auth)))]
            if _e_shown:
                pool = _e_shown
                hard.append(lambda b, _e=edition: ((b.get("language") or "ar").strip() or "ar") == _e)
            else:
                edition_missing = True
    # the publication year («after 2020»، «صدر حديثًا»): the work's first publication, or our edition's
    # year when that is unknown (QA2 2026-10-09; original year ORIGYR 2026-10-11)
    pub_missing = False
    if pub_years and not author_only:
        lo, hi = pub_years
        if not it.get("first_publication") and lo and lo >= time.localtime().tm_year - NEW_RELEASE_YEARS:
            # QADFIX34 #6 (QAD 2026-11-11 case 34.1, medium): «روايات طالعة هالسنة او اللي قبلها» → «المكتبة الجوالة» (a 1917 work, our
            # edition 2026) while 56 novels first published in 2025–2026 are in stock: for the last few years, when works whose first
            # publication is recorded in those years are in stock, a reprint whose own first year is unknown is not one of them
            def _fp34(b):
                y = first_published(b)
                return bool(y) and (not lo or y >= lo) and (not hi or y <= hi)
            if sum(1 for b in pool_before if is_available(b) and _fp34(b) and all(h(b) for h in hard)) >= 2:
                it["first_publication"] = True
                it["first_publication34"] = True
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
        else:
            # CHATFIX31 (3), Ibrahim 2026-11-01 («المفروض يظل عالسياق الا اذا هو الغى السياق»): an earlier «جديدة» this request
            # cannot meet is no longer let go quietly: it stays (its badge too), and the reply says none of these is that new
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
    if not (pages_max or pages_min) and "pages" not in drops:
        # QADFIX30 #2 (QAD 2026-11-07 case 2.1, grave): «ما يقل عن ١٠٠ صفحة ولا يزيد عن ٢٠٠» — in whatever words the visitor said it, the
        # interpreter wrote it as «من ١٠٠ إلى ٢٠٠ صفحة» in unsupported_requests: that is the page limit too
        _pu30 = [u for u in unsupported if chat_rules.is_pages_phrase(u)]
        if _pu30:
            pages_max, pages_min = chat_rules.page_limit(_pu30)
    if chat_rules.absolute_asked(_last_msg):
        # QADFIX29 #2 (QAD 2026-11-06 case 4.2, grave): «وش أقصر رواية عندكم أصلاً؟» after «بين ٨٠ و١٥٠ صفحة» — only a limit said now
        pages_max, pages_min = chat_rules.page_limit([_last_msg])
        it["absolute29"] = True
    # QADFIX27 #4 (QAD 2026-11-05 case 15): «أطول منها شوي» after books shown — longer than the longest of them
    _longest27 = chat_rules.longest_asked(_last_msg)
    if not _longest27 and it.get("order_by") == "longest" and not chat_rules.shortest_asked(_last_msg):
        # QADFIX32 #2 (QAD 2026-11-09 case 1.1, grave): «أضخم كتاب تاريخ» — the longest the model reads in any wording counts too (as
        # QADFIX12 #4 did for «shortest»; Ibrahim 2026-10-22: «المفروض النموذج يفهم هالشيء»); the word list stays a second reader
        _longest27 = "longest"
    if _longest27 == "longer" and last_batch and not pages_min:
        _lbp27 = [all_books[i].get("page_count") or 0 for i in last_batch if i in all_books]
        if max(_lbp27 or [0]) > 0:
            pages_min = max(_lbp27) + 1
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
    _twk32 = list(it["prefs"].get("title_keywords") or [])
    _lm32 = " ".join(normalize(re.sub(r"[؟?!.،,«»\"]", " ", _last_msg)).split())
    if re.search(r"(?:^| )(?:عنوان|عنوانها|عنوانه|العنوان|اسم|اسمها|اسمه|الاسم|title|called|named)(?: |$)", _lm32):
        # «ما أذكر عنوانها، فيها كلمة حرير وكلمة غزالة»: the words the visitor quotes from the title, whatever the model kept of them
        _twk32 += re.findall(r"(?:^| )[وب]?(?:كلمه|كلمة|word) (\S+)", _lm32)
    # (a title the model named that is no whole title in stock does not stop it: replay «… بالضبط» → mentioned «مختلف»)
    _tw32 = (title_words_books32(_twk32, all_books)
             if not author_only and not any(match_titles([m], all_books, max_rank=0.5) for m in it["mentioned_titles"] or []) else [])
    if _tw32:
        # QADFIX32 #5 (QAD 2026-11-09 case 28.1, medium): «ابحث عن رواية نسيت اسمها كامل بس فيه حصار وغزالة» → a clarifying question while
        # «حصار الغزالة واو» is in stock: the words of a title the visitor remembers, ALL found in a title in stock (with or without «ال»),
        # answer the request — those books first, not a question
        it["title_words32"] = [b["book_id"] for b in _tw32]
        if it["intent"] in ("clarify", "smalltalk"):
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
    if avoid_authors30 and not author_only:
        _extra_badges.append(("noauthor", ("not by " if language == "en" else "بدون كتب ") + avoid_authors30[0]))   # QADFIX30 #3
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
    if reader_age is not None and (pprefs.get("audience") == "young_adult" or reader_age >= 9):
        # (also a child of 9 or more: «سلسلة طي اللوحات… المطار» (3+) was offered to a boy of 11 who asks about Ibn Battuta)
        # QADFIX24 (QAD 2026-10-30, case 46.3): a teenager's books are not cut by age (QADFIX12 #7), but books recorded for small
        # children are not hers: «سلسلة الكَوْن» (3+) was offered to a 13-year-old who likes science
        def _q24_too_young(b):
            lo, hi = b.get("age_min"), b.get("age_max")
            return ((hi is not None and hi < reader_age - 2) or (hi is None and lo is not None and lo <= reader_age - 8)
                    # a toddler's book by its very form, age not recorded («طي اللوحات»، «كتب قماش»، lift-the-flap, board book)
                    or (lo is None and hi is None and bool(_TODDLER_FORM.search(normalize(b.get("title") or "")))))
        _q24_teen = [b for b in pool if not _q24_too_young(b)]
        if _q24_teen:
            pool = _q24_teen
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
    # QADFIX30 #5 (QAD 2026-11-07 cases 17.2–17.3, grave): «هي عمرها ١٣ وما تحب العنف، ابي شي من رف اليافعين» → «كافكا على الشاطئ» and
    # «الوصفة الغريبة» (adult novels with no content record) and «ما عندي تفاصيل… ولا عن كتب رف اليافعين», while 61 books stand on that
    # shelf. The young readers' shelf named, or a reader of 12–15, gets that shelf first (QADFIX12 #7 keeps the rest after it; a novel
    # asked for counts the shelf's novels); with a content to avoid, a book off that shelf whose record holds no content data is never
    # ranked as if it were known to be free of it: it comes last (and the guidance says it is not known)
    _yas30 = chat_rules.ya_shelf_said(" ".join(user_messages[-2:]))
    # QADFIX34 #8 (QAD 2026-11-11 case 1.2, replay): «عندكم شي ثاني غير كيكي؟» for a girl of 13 → «اسم الريح» (an adult epic with violence):
    # a title named only as the one already shown, to ask for another («غير X / شي ثاني / something other than X»), keeps the young path
    _shown_t34 = {chat_rules.title_key(all_books[i]["title"]) for i in shown if i in all_books}
    _other34 = bool(it["mentioned_titles"]) and bool(re.search(r"(?:^| )(?:غير|ثاني|ثانيه|other|else|besides|apart)(?: |$)",
                                                               normalize(_last_msg))) and all(
        any(chat_rules.title_key(m) and (chat_rules.title_key(m) in t or t.startswith(chat_rules.title_key(m))) for t in _shown_t34)
        for m in it["mentioned_titles"])
    _ya30 = (pprefs.get("audience") == "young_adult" and not author_only and (not it["mentioned_titles"] or _other34)
             and not (chat_rules.shortest_asked(_last_msg) or chat_rules.dearest_asked(_last_msg) or it.get("order_by"))
             # QADFIX30b: a young reader whose age is not said counts too («audience young», Ibrahim «ب» 2026-11-07)
             # QADFIX33 #2 (QAD 2026-11-10 case 25.1, grave): «بنت أخوي ١٦ سنة تحب الجرايم الغامضة» — a reader of 16 or 17 is a teenager
             # too (the interpreter's young_adult); the young readers' shelf comes first for him as for 12–15
             and (_yas30 or reader_age is None or 12 <= reader_age <= 17))
    ya_added30, ya_unknown30 = [], []
    kid_added32 = []   # QADFIX32b
    young_fit33 = []   # QADFIX33 #1 / #2
    _ya_pred30b, _ya_fit31 = None, []   # QADFIX31 #3
    if _ya30:
        def _ya_shelf30(b):
            return (b.get("genre") or "").strip() == "يافعين" or (cards.get(b["book_id"]) or {}).get("format") == "young_adult"
        def _content_known30(b):
            c = cards.get(b["book_id"]) or {}
            return bool(c.get("sensitive_content") or any(k[0] == "sensitive_content" for k in (c.get("confidence") or {})))
        _avoid30 = tuple(sorted(pprefs.get("avoid") or ()))
        _real30 = any(re.search(r"واقعي|realis", normalize(s)) and not re.search(r"سحري|magic", normalize(s))
                      for s in prefs.get("subgenres") or ())
        if _yas30 or sum(1 for x in scored if x[0] > 0 and _ya_shelf30(x[2])) < 2:
            _in30 = {x[2]["book_id"] for x in scored}
            for b in sorted(all_books.values(), key=lambda b: b["book_id"]):
                if (b["book_id"] in _in30 or b["book_id"] in shown or not is_available(b) or not b.get("active", 1) or not _ya_shelf30(b)
                        or not all(h(b) for h in hard)):
                    continue
                _c30 = cards.get(b["book_id"])
                if not age_allowed(_c30, "young_adult", _avoid30, b, reader_age):
                    continue
                if avoided_in_text33(b, _avoid30):
                    continue   # QADFIX33 #1: its own summary speaks of the content refused («فيها قتل ودماء»)
                if prefs.get("subgenres_exclude") and matching_subgenres(b, prefs, key="subgenres_exclude", card=_c30):
                    continue
                if _real30 and re.search(r"فانتازيا|فنتازيا|خيال|رعب|خوارق|سحر|ساحر|تنين|تنانين|اشباح|fantasy|horror|science fiction|magic|dragon",
                                         normalize(" ".join(_json_list(b.get("subgenres"))) + " " + (b.get("title") or "") + " "
                                                   + (b.get("summary") or "") + " " + (b.get("hook") or ""))):
                    continue   # «رواية واقعية» for a boy of 14: not «أمير الضباب» (dark fantasy) from the shelf
                if reader_age is not None and age_fit(b, reader_age) < 0:
                    continue
                ya_added30.append((0.5, ["from the young readers' shelf" if language == "en" else "من رف اليافعين"], b))
        # four of them, so that a book of the shelf that fits the wish itself keeps its place in facts: the reader's age range, the
        # visitor's language and a summary first
        ya_added30 = sorted(ya_added30, key=lambda x: (-(age_fit(x[2], reader_age) if reader_age is not None else 0),
                                                         ((x[2].get("language") or "ar") == "en") != (language == "en"),
                                                         not (x[2].get("summary") or "").strip(), x[2]["book_id"]))[:4]
        # QADFIX32b (Ibrahim 2026-11-09 14:0x on QADFIX32's question 1: «أ»; QAD 2026-11-09 case 3.1): «بنت أختي ١٣ … السحر والعوالم
        # الخيالية» — «مومو» (children's shelf, 12+, content none) came only on the next turn: for a reader of 12–15, a children's-shelf
        # book whose recorded age covers him and that fits the wish comes too, right after the young readers' shelf books; the age,
        # the refusals, the content to avoid and the QADFIX30b rule hold as for any book
        kid_added32 = []
        if reader_age is not None and 12 <= reader_age <= 15:
            _in32 = {x[2]["book_id"]: x[0] for x in scored}
            for b in sorted(all_books.values(), key=lambda b: b["book_id"]):
                _c32 = cards.get(b["book_id"])
                if (b["book_id"] in shown or not is_available(b) or not b.get("active", 1) or _ya_shelf30(b)
                        or not kid_shelf32(b, _c32) or not age_covers32(b, reader_age) or not all(h(b) for h in hard)
                        or not age_allowed(_c32, "young_adult", _avoid30, b, reader_age) or not refusals_ok(b, _c32, prefs, exclusions)
                        or avoided_in_text33(b, _avoid30)):   # QADFIX33 #1
                    continue
                _fits32 = (_in32.get(b["book_id"], 0) > 0 or (prefs.get("subgenres") and matching_subgenres(b, prefs, card=_c32))
                           or (prefs.get("soft_subgenres") and matching_subgenres(b, prefs, "soft_subgenres", card=_c32)))
                if not _fits32 or (_real30 and re.search(r"فانتازيا|فنتازيا|خيال|سحر|fantasy|magic", normalize(" ".join(_json_list(b.get("subgenres")))))):
                    continue
                kid_added32.append(([("from the children's shelf, its recorded age fits the reader" if language == "en"
                                      else "من رف الأطفال، وعمره المسجّل يناسب القارئ")], b))
            kid_added32 = kid_added32[:3]
            if kid_added32:
                it["kid_shelf32"] = [b["book_id"] for _r, b in kid_added32]
        _kid32 = {b["book_id"] for _r, b in kid_added32}
        # by score, so that the sorts below keep it: the shelf's books that fit the wish, then the shelf's other books, then the
        # children's shelf books that cover the reader's age (QADFIX32b), then the rest
        _top30 = max([x[0] for x in scored] + [1])
        scored = sorted([((s + 2 * _top30 + 2) if s > 0 and _ya_shelf30(b) else
                          (_top30 + 0.5) if b["book_id"] in _kid32 else
                          (max(s - 3, 0.1) if s > 0 and _avoid30 and not _content_known30(b) else s),
                          ([x for x, _b in kid_added32 if _b["book_id"] == b["book_id"]][0] + r) if b["book_id"] in _kid32 else r, b)
                         for s, r, b in scored]
                        + [(_top30 + 1, r, b) for s, r, b in ya_added30]
                        + [(_top30 + 0.5, r, b) for r, b in kid_added32 if b["book_id"] not in {x[2]["book_id"] for x in scored}],
                        key=lambda x: x[0], reverse=True)
        if _avoid30 and any(avoided_in_text33(x[2], _avoid30) for x in scored):
            # QADFIX33 #1 (case 5.1): «ما تبي يكون فيها قتل ولا دم» — for a young reader, a book whose own summary speaks of killing or
            # blood is left out, whatever its record of content says (the record may hold nothing)
            _txt33 = {x[2]["book_id"] for x in scored if avoided_in_text33(x[2], _avoid30)}
            scored = [x for x in scored if x[2]["book_id"] not in _txt33]
            it["avoid_text33"] = sorted(_txt33)
        # QADFIX33 #1 / #2 (QAD 2026-11-10 cases 5.1, 25.1–25.2, grave): «ولدي ١٤ … قصص التنانين والسحر، بس أمه ما تبي … قتل ولا دم» →
        # «المعلم ومرغريتا» (adult, a beheading) and «حكواتي الليل»; «بنت أخوي ١٦ تحب الجرايم الغامضة» → a tribal genealogy study. The
        # young readers' shelf books (and the children's shelf books whose recorded age covers the reader, QADFIX32b) whose own record
        # shows the kind of story wished — fantasy / magic / dragons, or a mystery / crime — come first, before any adult book; the age,
        # the content to avoid, the refusals and the hard wishes hold as for any book
        _kinds33 = young_kinds33(list(prefs.get("subgenres") or []) + list(prefs.get("soft_subgenres") or []) + list(unsupported)
                                 + list(relaxed_subgenres or []))
        if _kinds33 and not _real30:
            _yf33 = []
            for b in sorted(all_books.values(), key=lambda b: b["book_id"]):
                _c33 = cards.get(b["book_id"])
                _kid33 = not _ya_shelf30(b) and kid_shelf32(b, _c33)
                if (b["book_id"] in shown or not is_available(b) or not b.get("active", 1) or not (_ya_shelf30(b) or _kid33)
                        or (_kid33 and not age_covers32(b, reader_age)) or not all(h(b) for h in hard)
                        or not age_allowed(_c33, "young_adult", _avoid30, b, reader_age) or not refusals_ok(b, _c33, prefs, exclusions)
                        or avoided_in_text33(b, _avoid30) or (reader_age is not None and age_fit(b, reader_age) < 0)):
                    continue
                _w33 = young_kind_word33(b, _kinds33)
                if _w33:
                    _yf33.append(((["from the children's shelf, its recorded age fits the reader" if language == "en"
                                    else "من رف الأطفال، وعمره المسجّل يناسب القارئ"] if _kid33 else
                                   ["from the young readers' shelf" if language == "en" else "من رف اليافعين"])
                                  + [(f"its record says «{_w33[0]}»" if language == "en" else f"سجلّه يذكر «{_w33[0]}»")], b, _w33[1:]))
            # a summary that only says «غامض» counts when nothing clearer is there
            if sum(1 for x in _yf33 if x[2][0] < 2) >= 2:
                _yf33 = [x for x in _yf33 if x[2][0] < 2]
            # the visitor's language first, then where the record says it (title / keywords before the summary) and how often, the young
            # readers' shelf, the reader's age range, a summary; at most six
            _yf33.sort(key=lambda x: (((x[1].get("language") or "ar") == "en") != (language == "en"), x[2][0], -min(x[2][1], 3),
                                      not _ya_shelf30(x[1]), -(age_fit(x[1], reader_age) if reader_age is not None else 0),
                                      not (x[1].get("summary") or "").strip(), x[1]["book_id"]))
            young_fit33 = [(r, b) for r, b, _k in _yf33[:6]]
            if young_fit33:
                _yid33 = {b["book_id"] for _r, b in young_fit33}
                _top33 = max([x[0] for x in scored] + [1])
                scored = ([(_top33 + 1 + 0.01 * (len(young_fit33) - n), r, b) for n, (r, b) in enumerate(young_fit33)]
                          + [x for x in scored if x[2]["book_id"] not in _yid33])
                # they live through the subject-only filter and the final card check like the shelf additions (QADFIX30 #5)
                ya_added30 = [x for x in ya_added30 if x[2]["book_id"] not in _yid33] + [(0.5, r, b) for r, b in young_fit33]
                it["young_fit33"] = [b["book_id"] for _r, b in young_fit33]
        if _avoid30:
            # QADFIX30b (Ibrahim 2026-11-07 «ب», answering QADFIX30's question): with two or more books of the young readers' shelf that
            # fit, an adult book with no content record is left out entirely (as QADFIX26 does for «مافيها شي للكبار»); with fewer,
            # ordering only, and the reply says its content is not known
            _young30b = (lambda b: (b.get("genre") or "").strip() in CHILD_GENRES
                         or (cards.get(b["book_id"]) or {}).get("format") in ("children", "young_adult"))
            # QADFIX33q3 (Ibrahim 2026-11-12 on QADFIX33's question 3: «اكتفي بترتيب الكتب»): a reader of 16 or 17 (brought into the
            # young path by QADFIX33 #2) keeps the ordering only — adult books with no content record are not left out for him; the
            # leaving out stays for 12–15, an unsaid age, and the young readers' shelf named
            _rule30b = _yas30 or reader_age is None or reader_age <= 15
            if _rule30b:
                scored, _out30b = drop_unknown_adult30b(scored, _ya_shelf30, _content_known30, _young30b)
            else:
                _out30b = set()
            if _out30b:
                it["ya_out30b"] = sorted(_out30b)
            if _rule30b and sum(1 for s, _r, b in scored if s > 0 and _ya_shelf30(b)) >= 2:
                # QADFIX31 #3 (QAD 2026-11-08 case 5.1, medium): «الجوالون» and «رحلات الطرشجي الحلوجي» (adult, no content record) came back
                # through the subject search («الرحلات / الاستكشاف») after the others like them were left out: the rule is kept as a test
                # applied again after every path that adds books, and in the final check
                _ya_pred30b = (lambda b, _s=_ya_shelf30, _k=_content_known30, _y=_young30b: not _s(b) and not _y(b) and not _k(b))
                _ya_fit31 = [(s, r if any("رف اليافعين" in str(x) or "young readers" in str(x) for x in r)
                              else [("from the young readers' shelf" if language == "en" else "من رف اليافعين")] + r, b)
                             for s, r, b in scored if s > 0 and _ya_shelf30(b)]
            ya_unknown30 = [x[2]["title"] for x in scored[:6] if not _ya_shelf30(x[2]) and not _content_known30(x[2])]
    if language == "en" and not edition:
        # the English chat: books in English higher — a visitor writing in English was given two Arabic novels
        # without a word about their language (QAD 2026-10-14, case 41.1); an Arabic copy that fits much better
        # can still come, and the guidance has the model say its language
        scored = sorted(((s + (3 if (b.get("language") or "ar") == "en" else 0), r, b) for s, r, b in scored),
                        key=lambda x: x[0], reverse=True)
    if prefs.get("length") == "short" and scored:
        # QADFIX32 (QAD 2026-11-09 case 20.1, minor): "a short mystery I can finish on a flight" → «Inferno» first (no page count, length
        # unknown; the novel is long) before «Chronicle of a Death Foretold» (120 pages): when good fits known to be short exist, a book
        # whose length we do not know comes right after them
        def _unk32(b):
            c = cards.get(b["book_id"])
            return (not b.get("page_count") and not _medium_or_better(c, "perceived_length", "short")
                    and not _medium_or_better(c, "perceived_length", "very_short"))
        _top32 = max(x[0] for x in scored)
        _known32 = [x[0] for x in scored if x[0] > 0 and x[0] >= 0.5 * _top32 and not _unk32(x[2])]
        if _known32:
            _floor32 = min(_known32)
            scored = sorted(((min(s, _floor32 - 0.01) if s > _floor32 and _unk32(b) else s, r, b) for s, r, b in scored),
                            key=lambda x: x[0], reverse=True)
    if country and not country_missing:
        _place = country_place(list(country[1]))
        # QADFIX24 (QAD 2026-10-30, case 40.1): «شنو عندكم من الأدب العماني؟» left out Jokha Alharthi («سيدات القمر», International
        # Booker 2019): a prize the book (or its author) won puts it a little higher among the country's books
        _hd24 = honors.load()
        def _won24(b):
            # a small lift: it orders books that fit about as well, never a poorer fit over a better one
            if any(r.get("result") == "winner" for r in _hd24["book"].get(b["book_id"], [])):
                return 0.6
            return 0.3 if any(r.get("result") == "winner" for a in _hd24["book_authors"].get(b["book_id"], [])
                            for r in _hd24["author"].get(a, [])) else 0
        scored = sorted(((s + 1 + _won24(b), [f"كاتبه من {_place}"] + r, b) for s, r, b in scored), key=lambda x: x[0], reverse=True)
    if child_theme:
        scored = sorted(((s + 1 + theme_score.get(b["book_id"], 0),
                          ([f"كتاب للصغار فيه {child_theme}"] if b["book_id"] in theme_score else []) + r, b)
                         for s, r, b in scored), key=lambda x: x[0], reverse=True)
    # A subject the visitor named («عن الأندلس»، «أترك التسويف»): looked up in title, subjects and
    # summary; a book that covers it ranks first and says so (2026-10-06).
    topic_index = topics.index(all_books)
    # a shelf label nothing in stock carries («أدب السجون») is still a subject to look for (2026-10-07)
    unsupported = unsupported + [x for x in relaxed_subgenres if x not in unsupported]
    # a subject the visitor does NOT want («ولا فيها موت») is never searched for (2026-10-07)
    _said = normalize(user_text).split()
    if _refused30:
        # QADFIX30 #4 (case 45.1): the words of the refused title («مو العادات الذرية») are not a refused subject («بناء العادات»)
        _s30 = " " + " ".join(_said) + " "
        for _t30 in _refused30:
            _s30 = _s30.replace(" " + " ".join(normalize(_t30).split()) + " ", " ")
        _said = _s30.split()
    _neg_spans = []
    for _seg in re.split(r"[،,.؟?!؛;\n]", user_messages[-1] if user_messages else ""):
        _sg = " ".join(normalize(_seg).split())
        for _t30 in _refused30:
            # QADFIX30 #4 (case 45.1): «بس مو العادات الذرية» refuses that book, not the subject «بناء العادات» said before it
            _sg = (" " + _sg + " ").replace(" " + " ".join(normalize(_t30).split()) + " ", " ").strip()
        for _m in _NEG_LEAD.finditer(_sg):
            _span = []
            for _n, _w in enumerate(_m.group(1).split()):
                if _w in _NEG_STOP and _n > 0:
                    break   # QADFIX12 #1: «مو عن الكورة» — «عن» right after the negation leads to what is rejected
                if _w not in _NEG_STOP:
                    _span.append(_w)
            _neg_spans.append({f for w in _span for f in topics.stems(_hz(w))})
    # QADFIX22 (QAD 2026-10-27, case 40.1): «رواية بدون نهاية حزينة وما فيها حروب» — the model kept the sad ending and dropped
    # the war: a refused war, politics or killing said plainly after a negation is kept even when the model misses it
    _neg_sub = _REFUSED_SUBJECT.search(" " + " ".join(normalize(user_messages[-1]).split()) + " ") if user_messages else None
    if _neg_sub:
        _ns = _REFUSED_SUBJECT_NAME[_neg_sub.group(1)]
        if not any(_ns in normalize(a) or normalize(a) in _ns for a in it.get("avoid_topics") or []):
            it["avoid_topics"] = list(it.get("avoid_topics") or []) + [_ns]
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
    if (pprefs.get("audience") not in ("children", "young_adult") and _BEDTIME.search(" ".join(normalize(user_messages[-1]).split()))
            and not _SLEEP_PROBLEM.search(" " + " ".join(normalize(user_messages[-1]).split()) + " ")):
        # QADFIX31 (QAD 2026-11-08 case 13.1, minor): «مضغوط من الدوامات وابي شي يريّح راسي قبل النوم» → «نوم هانئ» and «فن الاستلقاء»: for a
        # grown-up reader «قبل النوم» is when he reads, not what the book is about (a sleep problem said plainly still is)
        unsupported = [u for u in unsupported if not (_BEDTIME.search(" ".join(normalize(u).split()))
                                                      or " ".join(normalize(u).split()) in ("النوم", "نوم"))]
    unsupported = _routine_topics(unsupported)
    if (not unsupported and _SLEEP_PROBLEM.search(" " + " ".join(normalize(user_messages[-1]).split()) + " ")
            and not _BEDTIME.search(" ".join(normalize(user_messages[-1]).split()))):
        unsupported = ["النوم"]   # QADFIX11 (QAD 2026-10-21, case 7.1): «عندي أرق وما أعرف أنام»
    _q7_series = (not it["mentioned_titles"] and not author_only
                  and bool(_SERIES_WISH.search(" " + " ".join(normalize(user_messages[-1]).split()) + " "))
                  # QADFIX23 (QAD 2026-10-29, case 30.1, grave): "Hugo winners that are standalone, not part of a series" — a
                  # series refused is no series wish (the prize wish was lost to a list of series)
                  and not _SERIES_REFUSED.search(" " + " ".join(normalize(user_messages[-1]).split()) + " "))
    if _q7_series:
        unsupported = [u for u in unsupported if not _SERIES_WISH.search(" " + " ".join(normalize(u).split()) + " ")
                       and not re.search(r"مسلسل|اتابع|نتابع|متابعه", normalize(u))]
    # CHATFIX30 (library inbox 2026-10-29, §8.1 and §8.2): «ابحث عن كتاب تشريح الفيلم» — the title the visitor named came back
    # as a subject too («كتاب تشريح الفيلم»), was cut to its rarest word «تشريح» and brought «العين: كاميرا الجسم المذهلة» and
    # «بيولوجيا السلوك الإجرامي»; «ايش تعرف عن كتاب زمان الملائكة؟» logged «كتاب زمان الملائكه» as a subject not found. A title
    # named is that book (found, or missing and answered by likeness), never a subject searched by its words; a subject said
    # after «عن» («كتب عن الحرب والسلام») stays one
    _named_titles = list(it.get("mentioned_titles") or []) + [r.get("title") for r in it.get("references") or []
                                                              if isinstance(r, dict) and r.get("title")]
    _title_phrases = [u for u in unsupported if _named_titles and chat_rules.phrase_is_title(u, _named_titles)
                      and not re.search(r"(?:^| )(?:عن|حول|about) (?!(?:ال)?(?:كتاب|روايه|book|novel) )" + re.escape(chat_rules.phrase_title_key(u)),
                                        chat_rules.title_key(user_messages[-1]))]
    if _title_phrases:
        unsupported = [u for u in unsupported if u not in _title_phrases]
    # CHATFIX30c (1): «تقييمها عالي» is a wish for the readers' rating (books.goodreads_rating), never a subject searched by its
    # words (library inbox §3: «تقييم مرتفع» logged as a wish we cannot search)
    _rating_wish = chat_rules.high_rating_wished(user_messages[-1]) or any(chat_rules.rating_phrase(u) for u in unsupported)
    unsupported = [u for u in unsupported if not chat_rules.rating_phrase(u)]
    # QADFIX20 (QAD 2026-10-25, case 33.1; AGY-20261025-01 c): «مجموعات قصصية فازت بجائزة الملتقى للقصة القصيرة» — a prize the
    # visitor names that our records do not hold is a prize wish, not a subject: never cut to a word of its name («الملتقى») and
    # matched against titles («ملتقى البحرين»); it stays among the wishes the search cannot apply
    _prize_phrases = [u for u in unsupported if prefs.get("awards")
                      and any(w in topics.AWARD_WORDS or strip_al(w) in topics.AWARD_WORDS for w in normalize(u).split())]
    # QADFIX29 #1 / #4 (QAD 2026-11-06 cases 2.1 and 10.2, grave): «بني الأحمر» / «قصر الحمراء» are «غرناطة»; «أحداث تقع في الستينات» is
    # «الستينات»
    unsupported = list(dict.fromkeys(decade_only29(place_alias29(u)) for u in unsupported))
    # QADFIX29 #5 (QAD 2026-11-06 case 1.2): «القائمة القصيرة» is the prize result asked (award_results), never a subject: it was searched
    # for, matched no book and «ما لقينا كتابًا متوفرًا له صلة بالجائزة» was said with eight shortlisted novels by women in stock
    if prefs.get("awards"):
        unsupported = [u for u in unsupported if not (_SHORTLIST_RX.search(" ".join(normalize(u).split()))
                                                      or _LONGLIST_RX.search(" ".join(normalize(u).split())))]
    # QADFIX30 m6 (QAD 2026-11-07 case 5.3): «كم وحدة بالضبط؟» → «عدد الروايات» searched as a subject: a count asked is never one
    unsupported = [u for u in unsupported if not re.fullmatch(r"(?:ال)?عدد (?:ال)?(?:كتب|كتاب|روايات|روايه|قصص|اصدارات|عناوين|كاتبات|مؤلفات)(?: .*)?",
                                                              " ".join(normalize(u).split()))]
    # QADFIX29 #2 (QAD 2026-11-06 case 4.1, grave): a number of pages is a page limit, never a subject («صفحه»)
    unsupported = [u for u in unsupported if not (chat_rules.is_pages_phrase(u) and re.search(r"\d", chat_rules.num_canon(u)))]
    # QADFIX27 #2 (QAD 2026-11-04 case 16.2): «الذكاء الاصطناعي» and «الذكاء» both came back — the one word of a longer subject
    # asked for is not a subject of its own («تفاني المشتبه به X… تتناول الذكاء»)
    _uw27 = {u: [w for w in normalize(u).split() if len(w) >= 3] for u in unsupported}
    unsupported = [u for u in unsupported if not (_uw27[u] and any(
        v != u and len(_uw27[v]) > len(_uw27[u]) and set(_uw27[u]) < set(_uw27[v]) for v in unsupported))]
    # QADFIX30 #4 (QAD 2026-11-07 case 45.1, grave): «بناء العادات» — the habits themselves are the subject too («معجزة الصباح: العادات
    # الـ6»), so the thing built / changed / kept is searched beside the whole phrase
    unsupported = list(dict.fromkeys(x for u in unsupported for x in ([u] + ([head_object30(u)] if head_object30(u) else []))))
    # QADFIX31 #5 (QAD 2026-11-08 case 46.1, medium): «أبحث عن سيرة ذاتية لعالِم عربي» → «العلماء» matched a Meccan scholar's study and
    # «محمد رسول الحرية» (the Prophet's life), while «عصر العلم» (subjects «سيرة أحمد زويل العلمية») is in stock: with a life story asked,
    # «عالِم / العلماء» is also a scientist's life — «سيرة علمية» and «العلم» are searched beside it
    _bio31 = bool({"سيرة ذاتية", "مذكرات"} & set(prefs.get("genres") or ()) or re.search(r"(?:^| )(?:سيره|السيره|مذكرات|biography|memoir)(?: |$)",
                                                                                         " ".join(normalize(_last_msg).split())))
    if _bio31 and any(strip_al(" ".join(normalize(u).split())) in ("عالم", "علماء", "عالمه", "عالمات", "عالم عربي", "علماء عرب")
                      or scientist_phrase32(u) for u in unsupported):
        unsupported = list(dict.fromkeys(unsupported + ["سيرة علمية", "العلم"]))
        it["scientist_bio31"] = True
        if any(scientist_phrase32(u) == "f" for u in unsupported):
            # QADFIX32 #4 (QAD 2026-11-09 case 5.2, medium): «فيه سيرة عالمة امرأة؟» (unsupported «عالمة امرأة») → «فرويد في زمانه» while
            # «التلميذة الخالدة: مدام كوري» is in stock: a woman scientist's life — searched in the feminine (the subject search reads it
            # by meaning too)
            unsupported = list(dict.fromkeys(unsupported + ["عالمة", "سيرة عالمة"]))
            it["woman_scientist32"] = True
    if "شعر" in set(prefs.get("genres") or ()) and any("شعبي" in normalize(u) for u in unsupported + list(prefs.get("subgenres") or [])):
        # QADFIX31 (QAD 2026-11-08 case 36.1, minor): «الشعر الشعبي» → «الأعمال الشعرية» of فاروق جويدة (فصيح) while «مخطوطة العسافي
        # (ديوان شعر - نبطي)» is in stock: Gulf popular poetry is «نبطي» in our titles and summaries
        unsupported = list(dict.fromkeys(unsupported + ["نبطي"]))
    # QADFIX28 (QAD 2026-11-05 case 30.1): "Any Pulitzer winners on your shelves?" → «your» as a subject («None of our available books
    # is about «your»: show no book») and «17 books…» with no card — a wish made only of function words is no wish
    unsupported = [u for u in unsupported if not (normalize(u).split() and all(w in _FUNCTION28 for w in normalize(u).split()))]
    topic_phrases = topics.searchable([u for u in unsupported if u not in _prize_phrases], topic_index)
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
        # QADFIX30 #4 (QAD 2026-11-07 case 45.1, grave): «بناء العادات» was cut to «بناء» («لدى بناء سور الصين»، «بناء الأهرامات»): the
        # thing built, kept, changed… is the subject, never the verbal noun in front of it
        core = [w for w in ((head_object30(p) or topics.core_word(p, topic_index)) for p in topic_phrases if not no_cut(p)) if w
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
            c = None if no_cut(p) else (head_object30(p) or topics.core_word(p, topic_index))   # QADFIX30 #4
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
        # QADFIX27 #2 (QAD 2026-11-04 case 7.1, grave; 11-01 32 and 11-03 36.1 class; 11-04 16.2): «قصة تاريخية تصير في غرناطة أيام بني
        # الأحمر» → «ما لقيت…» while «غرناطة (آخر الأيام)» and «ثلاثية غرناطة» are in stock: the novels about «الأندلس» (the same-as
        # words of «غرناطة») had the tag «رواية تاريخية» and filled the pool, and the two with the place itself in their title, and no
        # subgenre recorded, were never looked at. «مدينة القديس أوغسطين» (its summary: «أنظمة الذكاء الاصطناعي») had no «خيال علمي»
        # tag. A book whose title carries the subject's own words, or whose summary carries a subject of two words or more as a whole
        # phrase, stays a candidate without the subgenre tag (every other wish and the shelf asked for still hold)
        _in_pool27 = {b["book_id"] for b in pool}
        def _lit27(b):
            _t = " " + " ".join(normalize(re.sub(r"[^\w\s]", " ", b.get("title") or "")).split()) + " "
            _s = " " + " ".join(normalize(re.sub(r"[^\w\s]", " ", b.get("summary") or "")).split()) + " "
            for p in topic_phrases:
                _pw = [w for w in normalize(re.sub(r"[^\w\s]", " ", p)).split() if w not in _NO_CUT_SMALL]
                if not _pw or sum(len(w) for w in _pw) < 4:
                    continue
                if all(re.search(r" [وبلفك]?(?:ال)?" + re.escape(strip_al(w)) + " ", _t) for w in _pw):
                    return True
                if len(_pw) >= 2 and re.search(r" [وبلفك]?" + r" ".join(re.escape(w) for w in _pw) + " ", _s):
                    return True
            return False
        _ex27 = [b for b in pool_before if b["book_id"] not in _in_pool27 and is_available(b) and all(h(b) for h in hard) and _lit27(b)]
        if _ex27:
            _p27 = dict(prefs, subgenres=[], soft_subgenres=[])
            _ex27 = constrain_relaxing(_ex27, _p27, pprefs, exclusions, cards)[0][:4]
            if _ex27:
                it["literal_topic27"] = [b["book_id"] for b in _ex27]
                pool = pool + _ex27
                scored = scored + [(s + 4, r, b) for s, r, b in score_books(_ex27, _p27, pprefs, cards)]
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
    if sem and all(topics.split_region(topic_phrases)):
        # CHATFIX30c (3): «الإسلام في أوروبا» — a book near in meaning to «أوروبا» alone («مسيرة الفيل») or to «الإسلام» alone is
        # not on the subject; only the books the region-aware match accepts stay
        sem = {p: [(i, x) for i, x in hits if topics.match(topic_phrases, i, topic_index)[0]] for p, hits in sem.items()}
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
    # QADFIX28 #2 (QAD 2026-11-05 case 5.1, left by QADFIX27; 11-04 11.1 class): «رواية عن عائلة تنزح من بيتها أيام القصف في بيروت
    # الثمانينات» → «العائلة التي ابتلعت رجالها» (a Syrian family) and «يحدث في البيوت», while «شريد المنازل» and «حجر الضحك» (the
    # Lebanese war itself) were missed: the place and the time matched the summaries word for word and the event did not. When the
    # visitor names a war event (القصف، الاقتتال، النزوح، الحرب …) and a place, a book about that war there (its text names war, and
    # the place or its country — in the text or as the author's country) comes before one that only shares the place or the time
    _wp28 = war_place28(user_messages[-1], topic_phrases + list(unsupported)) if not (author_only or similar_author or publisher_name
                                                                                    or it["mentioned_titles"]) else None
    if _wp28:
        _wp28["decade"] = (it.get("decade_setting27") or [None])[0]
        _orig28 = book_origins()
        _have28 = {b["book_id"] for _, _, b in scored}
        _gen28 = set(prefs.get("genres") or ())
        _add28 = [b for b in pool_before if b["book_id"] not in _have28 and is_available(b) and all(h(b) for h in hard)
                  and (not _gen28 or (b.get("genre") or "").strip() in _gen28) and war_place_fit28(b, _wp28, _orig28) == 2]
        if _add28:
            _add28.sort(key=lambda b: (-war_place_close28(b, _wp28, user_messages[-1]), b["book_id"] in shown, b["book_id"]))
            _add28 = constrain_relaxing(_add28, prefs, pprefs, exclusions, cards)[0][:6]
            scored = scored + score_books(_add28, prefs, pprefs, cards)
        _lab28 = (f"about the war in {_wp28['place']}" if language == "en" else f"يتناول الحرب في {_wp28['place']}")
        _sc28, _fit28 = [], {}
        _top28 = max([max(s, 0) for s, _, _ in scored] or [0])
        for s, r, b in scored:
            _f = _fit28[b["book_id"]] = war_place_fit28(b, _wp28, _orig28)
            _sc28.append((max(s, 1) + _top28 + 12, [_lab28] + r, b) if _f == 2 else (s + 4, r, b) if _f == 1 else (s, r, b))
        # the war there first, whatever the place / time / family words the others share with the request
        _cl28 = {b["book_id"]: war_place_close28(b, _wp28, user_messages[-1]) for _, _, b in scored if _fit28.get(b["book_id"]) == 2}
        scored = sorted(_sc28, key=lambda x: (_fit28.get(x[2]["book_id"]) == 2, _cl28.get(x[2]["book_id"], 0), x[0]), reverse=True)
        if any(v == 2 for v in _fit28.values()):
            it["war_place28"] = _wp28["place"]
            topic_ids |= {i for i, v in _fit28.items() if v == 2}
    # QADFIX29 #4 (QAD 2026-11-06 cases 10.1–10.2, grave): «رواية تدور احداثها في القاهرة ايام الستينات» → «ليالي القاهرة» (set in 2011),
    # then «ما لقيت هنا رواية واضحة إنها تدور في القاهرة خلال الستينات» while «قالت ضحى» (summary: «في القاهرة الستينيات») is in stock:
    # «القاهرة» in a title outscored both words in a summary, and «ستيني» (a man of sixty) counted as the decade. A book whose own text
    # names the decade (and the other subjects, e.g. the place) comes first
    _dec29 = None
    if not (author_only or similar_author or publisher_name or it["mentioned_titles"]):
        _dec29 = (it.get("decade_setting27") or [None])[0]
        if not _dec29:
            for _u29 in list(unsupported) + list(topic_phrases):
                _d29 = decade_said(_u29)
                if _d29:
                    _dec29 = _d29[0]
                    break
    if _dec29:
        _oth29 = [p for p in topic_phrases if not decade_said(p)]
        _gen29 = set(prefs.get("genres") or ())
        def _fit29(b):
            return decade_fit29(b, _dec29) and (not _oth29 or bool(topics.match(_oth29, b["book_id"], topic_index)[0]))
        _have29 = {b["book_id"] for _, _, b in scored}
        _add29 = [b for b in pool_before if b["book_id"] not in _have29 and is_available(b) and all(h(b) for h in hard)
                  and (not _gen29 or (b.get("genre") or "").strip() in _gen29) and _fit29(b)]
        if _add29:
            _add29 = constrain_relaxing(_add29, prefs, pprefs, exclusions, cards)[0][:6]
            scored = scored + score_books(_add29, prefs, pprefs, cards)
        _f29 = {b["book_id"]: _fit29(b) for _, _, b in scored}
        if any(_f29.values()):
            _lab29 = (f"its own text sets it in {decade_name29(_dec29, 'en')}" if language == "en"
                      else f"نبذته تذكر أن أحداثه في {decade_name29(_dec29)}")
            _top29 = max([max(s, 0) for s, _, _ in scored] or [0])
            scored = sorted([((max(s, 1) + _top29 + 12, [_lab29] + r, b) if _f29[b["book_id"]] else (s, r, b)) for s, r, b in scored],
                            key=lambda x: (_f29[x[2]["book_id"]], x[0]), reverse=True)
            it["decade29"] = _dec29
            topic_ids |= {i for i, v in _f29.items() if v}
    _common25 = [u for u in unsupported if u not in topic_phrases and len(normalize(u).split()) == 1
                 and len(strip_al(normalize(u))) >= 3 and not author_only and not similar_author]
    if _common25 and not it["mentioned_titles"]:
        # QADFIX25 (QAD 2026-10-31, cases 45.1, 30.2): «فكر وفلسفة خفيفة»، «رواية عن أي حرب» — a word too common for the subject
        # search («فلسفة»، «الحرب») was dropped, and «حياة الكتب السرية» / «ما لقيت رواية عن الحرب» came while «مدخل إلى الفلسفة»،
        # «الدفتر الكبير»، «حرب نهاية العالم» were in stock: a book that carries the word in its title or recorded subjects comes first
        _cw25 = {strip_al(normalize(u)).removesuffix("ه") for u in _common25}
        def _carries25(b):
            _t = " ".join(strip_al(w) for w in normalize((b.get("title") or "") + " " + " ".join(_json_list(b.get("subjects")))).split())
            return [w for w in _cw25 if re.search(r"(?:^| )[وبلفك]?" + re.escape(w) + r"(?:ه|ي|يه|ات)?(?: |$)", _t)]
        _sc25 = []
        for s, r, b in scored:
            _c = _carries25(b)
            _sc25.append((s + 3, [("its title or subjects name «" if language == "en" else "عنوانه أو موضوعاته تذكر «") + "»، «".join(_c) + "»"] + r, b)
                         if _c else (s, r, b))
        scored = sorted(_sc25, key=lambda x: x[0], reverse=True)
    _parkeep35 = set()   # QADFIX35 (case 7.1): the parenting books of a parent reader, kept up front by the language order below
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
                    - (5 if _teen and _small else 0) - (3 if b["book_id"] in shown else 0) - (6 if _screen35(b) else 0))
        # QADFIX35 (QAD 2026-11-12 case 7.1, medium): «ولدنا اللي بالمتوسط صاير يرد علينا بقلة أدب» — a parents' guide to one medium («دليل
        # الآباء لوسائل التواصل»، «… للثقافة الرقمية») is not the general parenting book for a behaviour the visitor tells of, unless he speaks
        # of screens himself: it comes after them, and does not count as one of the two parenting books
        _scr_wish35 = bool(_SCREEN35.search(normalize(" ".join(user_messages[-3:]))))
        def _screen35(b):
            return not _scr_wish35 and bool(_SCREEN35.search(normalize(b.get("title") or "")))
        # QADFIX34 #7 (QAD 2026-11-11 case 44, medium): «أنا وزوجي تعبنا مع بنتنا ١٥ سنة، فيه كتاب للأهل عن المراهقة؟» → Sontag's own diaries
        # («مراهقتها» in its keywords) first, then «ما لقيت كتابًا واضحًا» with «إميل» and «لماذا نعيش؟», while «200 خطوة نحو التربية
        # برفق» is in stock: for a parent asking for himself, a memoir, a novel or stories about the subject word come after the parenting
        # books, and the parenting books in stock are brought in until there are two
        scored = sorted(((s + 8 + _pbonus(b), ["كتاب للوالدين عن التربية"] + r, b)
                         if _parenting(b) and (b.get("genre") or "").strip() not in _nonpar
                         else (s - 6, r, b) if (b.get("genre") or "").strip() in _nonpar else (s, r, b) for s, r, b in scored),
                        key=lambda x: x[0], reverse=True)
        def _parenting_named(b):   # the title or the library's keywords say it, not one word of a summary
            return (bool(_PARENTING_NAMED.search(normalize(" ".join(str(b.get(k) or "") for k in ("title", "keywords", "subjects")))))
                    and not _harm(b) and (b.get("genre") or "").strip() not in _nonpar)
        # QADFIX35b (test_qadfix35 flaky 2 of 7): a parenting book with no score at all stood in the first six only by the random order of
        # equal scores (score_books shuffles ties) and was counted as one of the two, so the others were not brought in and it stayed at 0;
        # only books that carry a score (the ones the candidates take) count
        if sum(1 for _s35, _, b in scored[:6] if _s35 > 0 and _parenting_named(b) and b["book_id"] not in shown and not _screen35(b)) < 2:   # QADFIX34 #7: two, not one
            # the subject words («عصبي»، «المراهقة») narrowed the books to ones without a parenting book: the
            # parenting books in stock are brought in, about teenagers first when the visitor speaks of one
            _par = [b for b in pool_before if is_available(b) and (_parenting_named(b) or (_parenting(b) and (b.get("genre") or "").strip() not in _nonpar))]
            if _par:
                _ids = {b["book_id"] for b in _par}
                _top = max([x[0] for x in scored] or [0])
                _add = [(_top + 2 + _pbonus(b), ["كتاب للوالدين عن التربية"], b) for b in _par]
                scored = sorted(_add + [x for x in scored if x[2]["book_id"] not in _ids], key=lambda x: x[0], reverse=True)
        _parkeep35 = {x[2]["book_id"] for x in scored if "كتاب للوالدين عن التربية" in x[1] and not _screen35(x[2])}
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
    # (QADFIX35, QAD 2026-11-12 case 8.2, minor; returned from 11-11 1.2: «عندكم شي ثاني لها؟» for a niece of 12 read as «children» → 3221
    # «6+» again: the reader of 12–17 read on the children's path too)
    if (reader_age is not None and 12 <= reader_age <= 17 and pprefs.get("audience") in ("young_adult", "children") and not author_only
            and not it["mentioned_titles"] and not re.search(r"(?:^| )(?:رف )?(?:ال|لل)?اطفال(?: |$)|kids|children", normalize(user_messages[-1]))):
        # QADFIX34 #8 (QAD 2026-11-11 case 1.2, minor): «عندكم شي ثاني غير كيكي؟» for a girl of 13 → «جيم الزر وسائق القطار» (children's
        # shelf, «6+»): QADFIX32b's rule (a children's-shelf book only when its recorded age covers the reader) held on the young path
        # but not for a book that came by its subgenre; a children's-shelf book whose recorded age does not cover the reader goes,
        # when two other books are left
        _out34 = [x for x in scored if kid_shelf32(x[2], cards.get(x[2]["book_id"])) and x[2].get("age_min") is not None
                  and not age_covers32(x[2], reader_age)]
        if _out34 and len(scored) - len(_out34) >= 2:
            _ids34 = {x[2]["book_id"] for x in _out34}
            scored = [x for x in scored if x[2]["book_id"] not in _ids34]
            it["kid_out34"] = sorted(_ids34)
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
    # QADFIX30 #1 (QAD 2026-11-07 cases 46.1–46.2, grave): «أغلى كتاب فنون عندكم كم سعره؟» → «المنمنمات الإسلامية ٥٢٫٣٠» from the few
    # candidates on «الفنون», then «… على الإطلاق، مو من المعروض» → «ما أقدر أحدد أغلى كتاب فن»; «The Cosmic Script» (700) is in stock.
    # The most expensive is ordered from books.price over everything the request allows, like «الأرخص», highest first
    _dear30 = chat_rules.dearest_asked(_last_msg) and not cheapest and not _short
    if _dear30:
        cheapest = True   # the price order below, turned round by _dear30
    # QADFIX31 (QAD 2026-11-08 case 35.1, minor): «أيهم أقصر وأسهل: الغريب لكامو ولا المسخ لكافكا؟» — a comparison of two books named is
    # answered from those two (the compare path), never by ordering the whole stock («أنت والكون» 32 pages, a puberty guide…)
    if (it.get("intent") == "compare" and len(it.get("mentioned_titles") or []) >= 2
            and match_titles(list(it["mentioned_titles"]) + list(it.get("title_variants") or []), all_books, max_rank=1)):
        _short, cheapest, _dear30 = None, False, False
        # QADFIX33 (QAD 2026-11-10 case 11.1, medium): «أيهم أطول: الأم لغوركي ولا الجريمة والعقاب؟» got «الزائر سأل عن الأطول … الدون
        # الهادئ ٢١٤١ صفحة» in its guidance (the whole stock ordered): «أطول» of two books named is the comparison too
        _longest27 = None
        it["compare_named31"] = True
    # QADFIX13 #1 (case 6.1): «شي يشبه 1984 بس أحدث» is not «the newest books in the shop»: the books like it that came out
    # after it are found from its links below (_newer_than), so the whole stock is not sorted by year here
    _like_pre = any(isinstance(r, dict) and r.get("relation") == "similar" and match_titles([r.get("title") or ""], all_books)
                    for r in it.get("references") or [])
    if ((it.get("newest_award33") or it.get("oldest_award35") or it.get("list_all33") or it.get("list_all_prev35")) and prefs.get("awards")
            and not (cheapest or _short) and not author_only):
        # QADFIX35 #1 (QAD 2026-11-12 case 28.2: «بؤساء بلفيل» (1975) was not among the candidates; case 3.1–3.2, medium: «الفن الهندي» (2007)
        # was not in facts, so «ما عندي تفاصيل السابع» and «ذكري له كان خطأ»): for the newest, the oldest or the whole list, every book in stock
        # that won the prize itself within the request comes into the candidates, as the whole list in the guidance counts them
        _have35 = {x[2]["book_id"] for x in scored}
        _gw35 = wanted_genres(prefs, pprefs)
        _add35 = [b for b in sorted(all_books.values(), key=lambda b: b["book_id"])
                  if b["book_id"] not in _have35 and is_available(b) and b.get("active", 1) and all(h(b) for h in hard)
                  and (not _gw35 or (b.get("genre") or "").strip() in _gw35 or _novel_prize_ok(b, prefs, _gw35))
                  and any(x.startswith("فاز") for x in honors.matches_award(b["book_id"], prefs["awards"], prefs.get("award_year"),
                                                                             prefs.get("award_min_year"), prefs.get("award_max_year"),
                                                                             prefs.get("award_results")))
                  and own_win_year33(b["book_id"], prefs["awards"])]
        if _add35:
            scored = scored + [(1, ["فاز بالجائزة نفسها"], b) for b in _add35]
            it["award_added35"] = [b["book_id"] for b in _add35]
    if it.get("oldest_award35") and prefs.get("awards") and not (cheapest or _short) and not author_only:
        # QADFIX35 #1: the oldest prize first — the books that won it themselves by the (earliest) year they won (1975, 1984, 1995 …), then
        # the others as they were; never the year of our edition
        _wy35 = {x[2]["book_id"]: own_win_year35(x[2]["book_id"], prefs["awards"]) for x in scored}
        _top35 = max([x[0] for x in scored] + [1])
        scored = sorted(((_top35 + 1 + (2200 - _wy35[b["book_id"]]) / 1000, r, b) if _wy35.get(b["book_id"]) else (s, r, b)
                         for s, r, b in scored), key=lambda x: (-x[0], x[2]["book_id"]))
    elif it.get("list_all33") and not it.get("newest_award33") and prefs.get("awards") and not (cheapest or _short) and not author_only:
        # QADFIX35 (case 3.1): the whole list — the winners themselves first, newest prize first (the order of the guidance's list)
        _wy35 = {x[2]["book_id"]: own_win_year33(x[2]["book_id"], prefs["awards"]) for x in scored}
        _top35 = max([x[0] for x in scored] + [1])
        scored = sorted(((_top35 + 1 + (_wy35[b["book_id"]] - 1800) / 1000, r, b) if _wy35.get(b["book_id"]) else (s, r, b)
                         for s, r, b in scored), key=lambda x: (-x[0], x[2]["book_id"]))
    elif it.get("newest_award33") and prefs.get("awards") and not (cheapest or _short) and not author_only:
        # QADFIX33 #3 (QAD 2026-11-10 case 33.1): «الغونكور، وش أحدث رواية فائزة فيها عندكم؟» — the newest prize first: the books that won
        # it themselves by the year they won (2022, 2021, 2015 …), then the others as they were
        _wy33 = {x[2]["book_id"]: own_win_year33(x[2]["book_id"], prefs["awards"]) for x in scored}
        _top33 = max([x[0] for x in scored] + [1])
        scored = sorted(((_top33 + 1 + (_wy33[b["book_id"]] - 1800) / 1000, r, b) if _wy33.get(b["book_id"]) else (s, r, b)
                         for s, r, b in scored), key=lambda x: (-x[0], x[2]["book_id"]))
    elif (((pub_years and not pub_missing and re.search(r"(?:^| )(?:احدث|الاحدث|اخر كتاب|اخر الكتب|اخر الروايات|اخر ما)|latest|newest|most recent",
                                                         normalize(_last_msg))) or _model_order == "newest")
            and not (cheapest or _short) and not _like_pre):
        # «وش أحدث كتاب وصلكم؟»: the newest first, with its year (QAD 2026-10-13, case 44)
        scored = sorted(((max(s, 1), r + [f"صدر سنة {year_for_new(b)}"] if year_for_new(b) else r, b) for s, r, b in scored),
                        key=lambda x: (-(year_for_new(x[2]) or 0), -x[0], x[2]["book_id"]))
    _oldest = ((bool(re.search(r"(?:^| )(?:اقدم|الاقدم)(?: |$)|oldest|earliest", normalize(_last_msg))) or _model_order == "oldest")
               and not _EDITION_ASK.search(normalize(_last_msg)))
    if it.get("oldest_award35") and prefs.get("awards") and not author_only:
        _oldest = False   # QADFIX35 #1 (case 28): the oldest on a prize is the year of the prize, ordered above — not the whole stock by first publication
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
            # QADFIX16 (QAD 2026-10-24, case 4.2): a shown book with no recorded first year is not left out — our edition's
            # year is the latest it can have come out, and the reply says it is the edition's year
            _shown_b = [all_books[i] for i in last_batch if i in all_books and is_available(all_books[i])
                        and (first_published(all_books[i]) or edition_year(all_books[i]))]
            _ob = (sorted(_shown_b, key=lambda b: (first_published(b) or edition_year(b), b["book_id"])) if _shown_b
                   else ([b for b in _ob if b["book_id"] in set(last_batch)] or _ob))
        # QADFIX28 (QAD 2026-11-05 case 45.2): «رواية عربية … أقدم من كذا، قبل الثلاثينات» — no Arabic novel in stock came out before
        # 1930, so the novel wish was eased and «منطق الطير» (1177) came; the answer is «none before 1930; the oldest is …» within the
        # request as the visitor said it (its kind and its origin)
        _ig28 = set(it["prefs"].get("genres") or ()) - CHILD_GENRES
        if pub_years and pub_years[1] and not pub_years[0] and (_ig28 or it["prefs"].get("original_languages")):
            _ip28 = dict(prefs, original_languages=set(it["prefs"].get("original_languages") or ()) or prefs.get("original_languages"),
                         translated=it["prefs"].get("translated") or prefs.get("translated"))
            _req28 = sorted((b for b in all_books.values() if is_available(b) and first_published(b) and b.get("active", 1)
                             and (not _ig28 or (b.get("genre") or "").strip() in _ig28) and origin_allowed(b, _ip28)),
                            key=lambda b: (first_published(b), b["book_id"]))
            if _req28 and first_published(_req28[0]) > pub_years[1]:
                q7["before_none28"] = (pub_years[1] + 1, _req28[0]["title"], first_published(_req28[0]), [b["book_id"] for b in _req28[:4]])
                _ob = _req28
        if _ob and _mt_shown:
            it["mentioned_titles"] = []
            if it.get("intent") == "about_book":
                it["intent"] = "recommend"
        if _ob:
            order_by, order_first, order_list = "oldest", _ob[0], _ob[:4]
            scored = [(1000 - n, [(f"first published in {first_published(b)}" if language == "en" else f"صدر العمل أول مرة سنة {first_published(b)}")
                                  if first_published(b) else oldest_year_label(b, language == "en")], b)
                      for n, b in enumerate(_ob[:6])]
            if it.get("intent") in ("clarify", "smalltalk"):
                it["intent"] = "recommend"
    # QADFIX14 م4/م5 (QAD 2026-10-23, cases 33.2, 36.2): «مين فيهم أقصر؟»، "Which of those is the shortest?" — the order is
    # taken among the books just shown, in the editions shown (the model reads it: about_shown), not over the whole stock
    # and not in another edition of one of them («Great Expectations» in Arabic, 720 pages)
    _shown_only = bool(it.get("about_shown")) and bool(last_batch) and bool(cheapest or _short or _oldest)
    # QADFIX23 (QAD 2026-10-28, case 23.2): «أبي فائزة ثانية بس أقصر» asks for ANOTHER book, shorter — a new search with the same
    # wishes, not «which of the shown ones is the shortest»
    if _shown_only and re.search(r"(?:^| )(?:ثاني|ثانيه|ثانيه|ثانية|غيرها|غيره|اخري|اخرى|ثانيين|another|other|different|new one)(?: |$)",
                                 " ".join(normalize(user_messages[-1]).split())):
        _shown_only = False
    # QADFIX23 (case 7.2): «أقصر من كذا شوي» after two long novels on migration is a book SHORTER THAN THESE — a new search on
    # the same subject («موسم الهجرة إلى الشمال», 151 pages), not «the shorter of the two shown»
    if (_shown_only and _short == "shorter"
            # QADFIX28 (QAD 2026-11-04 case 11.3): «أقصر شوي» (a bit shorter) is also a new search
            and re.search(r"(?:^| )(?:من كذا|من كذي|منها|منهم|منهن|من هذا|من هذي|من هذول|من هذولا|من ذول|من اللي|شوي|شويه|شوية|شوي بعد"
                          r"|than these|than those|than that|than them|a bit|a little|bit)(?: |$)",
                          " ".join(normalize(user_messages[-1]).split()))):
        _shown_only = False
    # QADFIX24 (QAD 2026-10-30, case 6.2, recurring from QAD 2026-10-29 9.2): «وش أقصر رواية له عندكم؟» after three of Mahfouz's
    # novels asks over ALL his books in stock («الكرنك» 116), not among the three shown («قشتمر» 150)
    if _shown_only and it.get("absolute29"):
        _shown_only = False   # QADFIX29 #2: «… عندكم أصلاً» is over the whole stock
    if _shown_only and _AUTHOR_WIDE.search(" " + " ".join(normalize(re.sub(r"[؟?!.,،]", " ", user_messages[-1])).split()) + " "):
        _shown_only = False
    # QADFIX33 #4 (QAD 2026-11-10 case 49.2, grave; regressed after QADFIX31/32, 11-08 1.3 class): «وش أرخص ديوان شعر عندكم؟» → «وأغلى
    # واحد؟» → «الأغلى ضمن طلبك هو «فن الرباعي» ٦٫٥٧ ريال» (the model's about_shown: the order ran among the cheapest poetry just shown)
    # while «نهر الشعر العربى» (121.76) is in stock. After an order over the stock («أرخص ديوان»), or when the message names the kind
    # («وأغلى ديوان؟»), the cheapest / dearest / shortest / longest is over the whole stock of the kind in force, unless the message
    # itself points at the books shown («أيهم أغلى؟»، «مين فيهم أقصر؟»، «أرخصها»)
    _stock33 = chat_rules.stock_order_asked(user_messages[-1]) if (cheapest or _short or _longest27) else None
    if _stock33 and len(user_messages) >= 2 and not chat_rules.among_shown_said(user_messages[-1]) and (
            chat_rules.stock_order_asked(user_messages[-2])
            or re.search(r"(?:^| )[وفب]?(?:ال)?(?:كتاب|كتب|روايه|روايات|ديوان|دواوين|قصه|قصص|مجموعه|سيره|مسرحيه|book|novel|collection)(?: |$)",
                         " ".join(normalize(re.sub(r"[؟?!.,،]", " ", user_messages[-1])).split()))):
        it["stock_order33"] = _stock33
        _shown_only = False
    it["shown_only33"] = _shown_only
    if _shown_only:
        it["mentioned_titles"] = []
        if _short == "shorter":
            _short = "shortest"
    if (cheapest or _short) and not similar_author:
        _val = (lambda b: b.get("price")) if cheapest else (lambda b: b.get("page_count"))
        _base = [x for x in scored if x[0] > 0]
        if _shown_only:
            _base = [(1, [], all_books[i]) for i in last_batch if i in all_books and is_available(all_books[i])]
        if (author_only or len(_base) < 3) and not _shown_only:
            # QADFIX15 (replay case 103.2 «أيها الأقصر؟» after two books): «among those» stays among those, however few
            _base = list(scored)
        elif (pprefs.get("moods") or pprefs.get("weight") or topic_phrases) and not cheapest:
            # «أقصر» after «شي يونّس، ماله خص بالحزن»: shorter among the books that fit the wish well, not any book
            # with a point («ما أقدر أؤكد إنه مرح» for three short poetry books, QAD 2026-10-14, case 43.2)
            _top = max(x[0] for x in _base)
            _fit = [x for x in _base if x[0] >= 0.6 * _top]
            if len(_fit) >= 2:
                _base = _fit
        if (_dear30 or it.get("stock_order33")) and not _shown_only:
            # QADFIX30 #1: every book the request allows, not only the ones the subject search scored («الفنون» brought 52٫30)
            # (QADFIX33 #4: «وأغلى واحد؟ / وأرخص واحد؟ / وأقصر واحد؟» after an order over the stock, likewise)
            _ids30 = {x[2]["book_id"] for x in _base}
            _base = _base + [(1, [], b) for b in pool if b["book_id"] not in _ids30 and is_available(b) and (_val(b) or 0) > 0]
        if topic_phrases and not cheapest and not _shown_only:
            # QADFIX23 (replay of case 7.2): «أقصر من كذا شوي» after novels on migration — shorter books ON THAT SUBJECT
            # («موسم الهجرة إلى الشمال»), not the shortest books of the shop («الشاب»، 48 pages)
            _tb = [x for x in _base if x[2]["book_id"] in (topic_ids | topic_about_ids)]
            if not _tb:
                _tb = [x for x in scored if x[2]["book_id"] in (topic_ids | topic_about_ids)]
            if _short == "shorter" and shown:
                # QADFIX30 m9 (QAD 2026-11-07 case 39.3, medium): «رواية عن عالم بعد نهاية البشر وكاتبها عربي» → «شي ثاني» → «أقصر» gave
                # «فضاء البنفسج» (248) and «ليالي ألف ليلة» (286) while «الطوباوية» (224), shown two turns before on that very subject, is
                # shorter: the books on the subject shown earlier in the conversation count too (QADFIX28: said to be shown before)
                _tbi30 = {x[2]["book_id"] for x in _tb}
                _tb += [(1, [], all_books[i]) for i in shown
                        if i in all_books and i not in _tbi30 and i not in set(last_batch or ()) and is_available(all_books[i])
                        and i in (topic_ids | topic_about_ids) and all(h(all_books[i]) for h in hard) and origin_allowed(all_books[i], prefs)
                        and (not prefs.get("genres") or all_books[i].get("genre") in prefs["genres"])]
            if _tb:
                _base = _tb
        if (_short == "shorter" and not cheapest and not _shown_only and len(shown) > len(last_batch or ()) and (topic_phrases or unsupported)
                and not any(x[2]["book_id"] in (topic_ids | topic_about_ids) for x in _base)):
            # QADFIX30 m9 (case 39.3): the subject is in force but no book is found on it now («انقراض البشر» in unsupported_requests):
            # «أقصر» is among the books shown for it earlier in this conversation, not the shortest Arabic novels of the shop
            _lb30 = set(last_batch or ())
            _sh30 = [(1, [], all_books[i]) for i in shown
                     if i in all_books and i not in _lb30 and is_available(all_books[i]) and all(h(all_books[i]) for h in hard)
                     and origin_allowed(all_books[i], prefs) and (not prefs.get("genres") or all_books[i].get("genre") in prefs["genres"])]
            if _sh30:
                _base = _sh30
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
        if edition:
            # QADFIX34 #11 (QAD 2026-11-11 case 4.3, minor; replay): «وأرخص واحد منها بالإنجليزي؟» after the cheapest and dearest science
            # fiction → «النمل» (Arabic) with «كتب facts نسخها بهذه اللغة»: the order over the stock keeps the copy language asked
            _ed34 = [x for x in _base if ((x[2].get("language") or "ar").strip() or "ar") == edition]
            if not _ed34:
                _ed34 = [(1, [], b) for b in pool_before if is_available(b) and all(h(b) for h in hard)
                         and ((b.get("language") or "ar").strip() or "ar") == edition
                         and (not prefs.get("genres") or b.get("genre") in prefs["genres"])
                         and (not prefs.get("subgenres") or matching_subgenres(b, prefs, card=cards.get(b["book_id"])))]
            if _ed34:
                _base = _ed34
        if language == "en" and not edition:
            # an English-speaking visitor: the shortest of the copies in English when there are any (QAD 2026-10-15, case 47.2)
            _en = [x for x in _base if (x[2].get("language") or "ar") == "en" and _val(x[2])]
            if _en:
                _base = _en
        elif language != "en" and not edition and not it.get("copy_language") and not _dear30:
            # (QADFIX30 #1: the most expensive book of the shop is a fact over every copy, whatever its language)
            # QADFIX28 (QAD 2026-11-05 case 13.2): «أقصر من كذا، أبي شي أخلصه بجلسة» → «Chronicle of a Death Foretold» in English while
            # Arabic copies fit — a visitor writing in Arabic gets the shorter copies in Arabic when there are some
            _arb28 = [x for x in _base if (x[2].get("language") or "ar") == "ar" and _val(x[2])]
            if len(_arb28) >= 2:
                _base = _arb28
            # QADFIX28 (replay of QAD 2026-11-05 case 40.3): «أقصر» → «Chronicle of a Death Foretold» (English, 120 pages) while its
            # Arabic copy «قصة موت معلن» is in stock — an English copy of a work we have in Arabic is not offered to him
            _bao28 = book_author_map()
            def _has_ar28(b):
                return bool(b.get("original_year")) and any(
                    (o.get("language") or "ar") == "ar" and o.get("original_year") == b["original_year"] and is_available(o)
                    and same_work_copy(b, o, _bao28) for o in work_copies_other_titles(b, all_books))  # AUDFIX 2026-11-05: proven same work
            _base = [x for x in _base if not ((x[2].get("language") or "ar") == "en" and _has_ar28(x[2]))] or _base
        _lg28 = ({all_books[i].get("genre") for i in last_batch if i in all_books} - {None}) if last_batch else set()
        if (_short == "shorter" and not cheapest and not _shown_only and last_batch and pprefs.get("audience") not in ("children", "young_adult")
                and not any((all_books[i].get("genre") or "").strip() in CHILD_GENRES
                            or (cards.get(i) or {}).get("format") in ("children", "young_adult") for i in last_batch if i in all_books)):
            # QADFIX33 (QAD 2026-11-10 re-run of 11-09 case 16.2, medium): «يومي كان سيء … يرفع معنوياتي» → «عينا مونا» and «بلدي المخترع» →
            # «أقصر من كذا» → «فكر بغيرك» (a picture book, 20 pages) and «البيت الهادئ» (a poem for children, 24 pages): a grown-up's
            # «shorter» is not a children's book when no child was spoken of
            _ad33 = [x for x in _base if (x[2].get("genre") or "").strip() not in CHILD_GENRES
                     and (cards.get(x[2]["book_id"]) or {}).get("format") not in ("children", "young_adult")]
            if _ad33:
                _base = _ad33
        # QADFIX33 (11-09 case 16.2): the books shown may be of two kinds (a novel and a memoir); a shorter one is of those kinds too
        if _short == "shorter" and not cheapest and not prefs.get("genres") and not author_only and not _shown_only and 1 <= len(_lg28) <= 2:
            # QADFIX28 (QAD 2026-11-05 case 13.2): two novels shown, then «أقصر من كذا» → «كيف تحمي منزلك من الغرق» (self-help): a
            # shorter one of the same kind as the books shown, when there is one
            _gk28 = [x for x in _base if x[2].get("genre") in _lg28]
            _gw28 = sorted((x for x in scored if x[0] > 0 and x[2].get("genre") in _lg28 and x[2]["book_id"] not in shown
                            and x[2].get("page_count") and (language == "en" or edition or (x[2].get("language") or "ar") == "ar")),
                           key=lambda x: -x[0])
            _lim28 = min([all_books[i].get("page_count") or 10 ** 6 for i in last_batch if i in all_books] or [0])
            if not any(_val(x[2]) and _val(x[2]) < _lim28 for x in _gk28):
                _gk28 = _gk28 + [x for x in _gw28 if x[2]["page_count"] < _lim28][:6]
            if any(_val(x[2]) and _val(x[2]) < _lim28 for x in _gk28):
                _base = _gk28
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
                # QADFIX33 (QAD 2026-11-10 re-run of 11-09 case 16.2, medium): «أقصر من كذا» after a novel and a memoir chosen for the mood
                # → two picture poems for children (20 / 24 pages) from the books that scored nothing: the ones that fit the wish of the
                # kinds shown first, then of the kinds shown, then those that fit the wish; never a children's book for a grown-up who
                # spoke of no child
                _lk33 = {all_books[i].get("genre") for i in last_batch if i in all_books} - {None}
                _grown33 = (pprefs.get("audience") not in ("children", "young_adult") and not _lk33 & CHILD_GENRES)
                _ok33 = (lambda b: not _grown33 or ((b.get("genre") or "").strip() not in CHILD_GENRES
                                                    and (cards.get(b["book_id"]) or {}).get("format") not in ("children", "young_adult")))
                if set(pprefs.get("moods") or ()) & {"uplifting", "funny", "comforting", "warm"}:
                    # a cheerful wish: not a book whose record says it is sad or dark
                    _less = [x for x in _less if not any(_medium_or_better(cards.get(x[2]["book_id"]), "moods", m) for m in ("sad", "dark"))] or _less
                _less = ([x for x in _less if x[0] > 0 and x[2].get("genre") in _lk33 and _ok33(x[2])]
                         or [x for x in _less if x[2].get("genre") in _lk33 and _ok33(x[2])]
                         or [x for x in _less if x[0] > 0 and _ok33(x[2])] or [x for x in _less if _ok33(x[2])] or _less)
                if not _less:
                    _lg = {all_books[i].get("genre") for i in last_batch if i in all_books} - {None}
                    _less = [(1, [], b) for b in pool_before if is_available(b) and b.get("page_count")
                             and b["page_count"] < _lim and b.get("genre") in _lg]
                if not _less:
                    shorter_none = _lim
            _wm34 = [m for m in (pprefs.get("moods") or ()) if m in ("funny", "uplifting", "comforting", "warm")]
            if _wm34 and _lim and not cheapest:
                # QADFIX34 #5 (QAD 2026-11-11 case 12.2, medium; 11-09 16.2): «ابي رواية تضحكني» → «أقصر من كذا» → «الشاب» (48 pages) and
                # «المموه», «ما أقدر أؤكد إن أيًّا منهما مرح», while «تربية النحل حسب صمويل بيكيت» (80 pages, funny) and «يوميات آدم وحواء»
                # are in stock: the cheerful mood asked stays with «shorter» — the shorter books recorded so first, then from the stock of
                # the kinds shown; only when none is shorter, the shorter ones as they are (and the reply says the mood is not confirmed)
                def _mood34(b):
                    return any(_medium_or_better(cards.get(b["book_id"]), "moods", m) for m in _wm34)
                _lm34 = [x for x in _less if _mood34(x[2])]
                if not _lm34:
                    _lk34 = {all_books[i].get("genre") for i in last_batch if i in all_books} - {None}
                    _grown34 = pprefs.get("audience") not in ("children", "young_adult") and not _lk34 & CHILD_GENRES
                    _lm34 = [(1, [], b) for b in pool_before
                             if is_available(b) and b.get("page_count") and b["page_count"] < _lim and b["book_id"] not in shown
                             and (not _lk34 or b.get("genre") in _lk34) and all(h(b) for h in hard) and _mood34(b)
                             and (not _grown34 or ((b.get("genre") or "").strip() not in CHILD_GENRES
                                                   and (cards.get(b["book_id"]) or {}).get("format") not in ("children", "young_adult")))]
                    if language != "en" and not edition:
                        _lm34 = [x for x in _lm34 if (x[2].get("language") or "ar") == "ar"] or _lm34
                if _lm34:
                    _less = _lm34
                    it["shorter_mood34"] = [x[2]["book_id"] for x in _lm34[:6]]
            if _less:
                _have = _less
            elif _lim:
                _have = []
        if (not cheapest and topic_phrases and any(x[2]["book_id"] in topic_about_ids and not _val(x[2]) for x in _base)
                and not any(x[2]["book_id"] in topic_about_ids for x in _have)):
            # QADFIX7 #2 (case 39.2): the books about the subject have no page count: they stay, and the reply says so
            q7["nopages"] = [x[2]["title"] for x in _base if x[2]["book_id"] in topic_about_ids and not _val(x[2])][:2]
            _have = []
        _have.sort(key=lambda x: ((-_val(x[2]) if _dear30 else _val(x[2])), x[2]["book_id"]))   # QADFIX30 #1: highest price first
        if _shown_only and not cheapest:
            # QADFIX24 (QAD 2026-10-30, case 2.2): «وش أقصر واحد فيهم؟» with one of the two shown having no page count — «أنا
            # عبوة ديناميت (510) أقصر واحد» was said; the reply gives the known count and says the other's is not recorded
            q7["short_nopages"] = [x[2]["title"] for x in _base if not _val(x[2])][:3]
        if _have:
            order_by, order_first = ("dearest" if _dear30 else "cheapest" if cheapest else _short), _have[0][2]
            order_list = [x[2] for x in _have[:4]]
            scored = [(1000 - n, [(f"سعره {b['price']:g} ريال" if cheapest else f"عدد صفحاته {fmt_num(b['page_count'])}")] + r, b)
                      for n, (_s, r, b) in enumerate(_have[:6])]
    elif price_max and not price_missing:
        scored = [(s, r + [f"سعره {b['price']:g} ريال"], b) for s, r, b in scored]
    # QADFIX27 #4 (QAD 2026-11-05 case 15.3, grave): «طيب وش أطول رواية عندكم؟» → «الرجل الذي كان يحب الكلاب، ٧٦٢ صفحة» from the few
    # candidates, while «الدون الهادئ» (2,141 pages) is in stock — the longest is ordered from page_count over everything the request
    # allows (the page limit said earlier stays), like «الأقصر»; one copy per work
    if _longest27 == "longest" and not order_by and not (cheapest or _short) and not similar_author:
        _lb27 = [b for b in pool if is_available(b) and (b.get("page_count") or 0) > 0]
        if topic_phrases and (topic_ids | topic_about_ids):
            _lb27 = [b for b in _lb27 if b["book_id"] in (topic_ids | topic_about_ids)] or _lb27
        if it.get("about_shown") and last_batch and not it.get("stock_order33"):   # QADFIX33 #4: «وأطول واحد؟» after «أقصر رواية»
            _lb27 = [all_books[i] for i in last_batch if i in all_books and is_available(all_books[i])
                     and (all_books[i].get("page_count") or 0) > 0] or _lb27
        _lb27.sort(key=lambda b: (-b["page_count"], b["book_id"]))
        _seen27, _ol27 = set(), []
        for b in _lb27:
            _wk27 = (chat_rules.title_key(b.get("title") or ""), author_flow.display(b.get("author")))
            if _wk27 not in _seen27:
                _seen27.add(_wk27)
                _ol27.append(b)
        if _ol27:
            order_by, order_first, order_list = "longest", _ol27[0], _ol27[:4]
            scored = [(1000 - n, [(f"{b['page_count']} pages" if language == "en" else f"عدد صفحاته {fmt_num(b['page_count'])}")], b)
                      for n, b in enumerate(_ol27[:6])]
            if it.get("intent") in ("clarify", "smalltalk", "about_book"):
                it["intent"] = "recommend"
            it["longest27"] = [b["book_id"] for b in _ol27[:4]]
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
        # QADFIX23 (QAD 2026-10-28, case 13.1): «بنت اختي عمرها ٧ ومجنونة ديناصورات» — «Magic Tree House» (its summary takes the
        # children to the age of the dinosaurs) is the only copy on the subject: one whose summary names the subject is not pushed
        # out either, so «ما لقينا كتاب عن الديناصورات» is not said while one is here (the reply says it is in English)
        scored = sorted(((s - (1.5 if (b.get("language") or "ar") == "en" and b["book_id"] not in topic_about_ids
                                     and b["book_id"] not in topic_ids else 0), r, b)
                         for s, r, b in scored),
                        key=lambda x: x[0], reverse=True)
        _topic_en = [b for s, r, b in scored if s > 0 and b["book_id"] in (topic_ids | topic_about_ids) and (b.get("language") or "ar") == "en"]
        if _topic_en and not any(s > 0 and b["book_id"] in (topic_ids | topic_about_ids) and (b.get("language") or "ar") != "en"
                                 for s, r, b in scored):
            guidance_extra_en = True   # QADFIX23 (case 13.1): the reply says the copy on the subject is in English
    # QADFIX23 (QAD 2026-10-28, case 50.2): «مثل لعبة العروش… فانتازيا وممالك وسحر» offered «ملك رماد النار» (part 2) while its
    # part 1 «سارق غبار النجوم» was in stock — a title named in the conversation no longer stops the swap; only a book the
    # visitor named himself keeps its own part
    _named_keys = {chat_rules.title_key(normalize(t)) for t in it["mentioned_titles"] if t}
    if not order_by and not author_only:
        # a later part of a series whose first part is in stock: the first part is offered instead
        # («البلدة الأخيرة» (3) before «غابة الصنوبر» (1), QAD 2026-10-15, case 4.1)
        _ids = {b["book_id"] for _s, _r, b in scored}
        _swapped = []
        _p1_24 = None   # QADFIX24: part-1 titles in stock, built when first needed
        for _s, _r, b in scored:
            _pos, _ser = b.get("series_position"), (b.get("series_name") or "").strip()
            if (_ser and isinstance(_pos, (int, float)) and _pos > 1
                    and not any(k and (k in chat_rules.title_key(normalize(b.get("title") or ""))) for k in _named_keys)):
                _first = next((x for x in all_books.values() if is_available(x) and (x.get("series_name") or "").strip() == _ser
                               and x.get("series_position") == 1 and x["book_id"] not in shown and x["book_id"] not in _ids), None)
                if _first:
                    _ids.add(_first["book_id"])
                    _swapped.append((_s, _r + ([f"the first part of «{_ser}»"] if language == "en" else [f"الجزء الأول من «{_ser}»"]), _first))
                    continue
            # QADFIX24 (QAD 2026-10-30, cases 49.1, 45.1): «روايتي لروايتي (ج 2)»، «الزميل: الانتقام الهادئ ج3» carry their part in
            # the title only (no series_name): part 1 in stock («(ج 1)»، «ج1») is offered instead, as for a recorded series
            _tp = title_part(b.get("title"))
            if (not _ser and _tp and _tp[1] > 1
                    and not any(k and (k in chat_rules.title_key(normalize(b.get("title") or ""))) for k in _named_keys)):
                if _p1_24 is None:
                    _p1_24 = {}
                    for x in all_books.values():
                        _xp = title_part(x.get("title")) if is_available(x) else None
                        if _xp and _xp[1] == 1:
                            _p1_24.setdefault(_xp[0], x)
                _first = _p1_24.get(_tp[0])
                if _first and (_first["book_id"] in shown or _first["book_id"] in _ids):
                    _first = None
                if _first:
                    _ids.add(_first["book_id"])
                    _swapped.append((_s, _r + (["the first part"] if language == "en" else ["الجزء الأول"]), _first))
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
    # QADFIX27 #10 (QAD 2026-11-05 cases 4.1–4.2, 2026-11-04 cases 9.1–9.2, 11-01 44: open since 11-01): «أبي كتاب مبسط يعرّفني على
    # علم الاجتماع، ما عندي أي خلفية» → «أمثال شعبية قالوها في الحارة» first, then a study of Weber: a first book in a field asks for
    # the field's general works (its theories, an encyclopaedia, an introduction — by their titles) before studies of one subject,
    # and when none is a simple introduction the reply says so first
    _f27 = [g for g in _GENRE_TITLE26 if g in (set(prefs.get("genres") or ()) | set((it.get("prefs") or {}).get("genres") or ()))]
    _lm27 = " " + " ".join(normalize(re.sub(r"[؟?!.،,]", " ", user_messages[-1])).split()) + " "
    if (_f27 and not author_only and not similar_author and not it["mentioned_titles"] and not order_by
            and ("beginner" in ((it.get("prefs") or {}).get("reader_level") or [])
                 or re.search(r" (?:مدخل|مبسط|مبسطه|مبسّط|اول مره|ابدا|ابدأ|مبتدي|مبتدئ|خلفيه|يعرفني|تعرفني|basics|introduction|beginner) ", _lm27))):
        _gen27 = re.compile(r"نظري|موسوع|مدخل|مقدم|مباد|اسس|اساسيات|معجم|قاموس|introduc|theor|encyclop|basics")
        _intro27 = re.compile(r"(?:^| )(?:مدخل|مقدمه|مبادي|اساسيات|introduction|intro|basics)(?: |$)")
        _gb27 = [b for b in all_books.values() if is_available(b) and _genre_in_title(b, set(_f27)) and all(h(b) for h in hard)
                 and _gen27.search(normalize(b.get("title") or "").lower())]
        # «أبي شي أبسط، مدخل…» right after them (replay 11-04 9.2): the general works shown come back when fewer than two are left
        _gb27 = [b for b in _gb27 if b["book_id"] not in shown] if sum(b["book_id"] not in shown for b in _gb27) >= 2 else _gb27
        if _gb27:
            _top27 = max([x[0] for x in scored] or [0])
            _ids27 = {b["book_id"] for b in _gb27}
            _why27 = ("a general work on the field itself, by its title" if language == "en" else "كتاب عام عن العلم نفسه، من عنوانه")
            scored = sorted([(_top27 + 3 + (2 if _intro27.search(normalize(b.get("title") or "")) else 0), [_why27], b) for b in _gb27]
                            + [x for x in scored if x[2]["book_id"] not in _ids27], key=lambda x: x[0], reverse=True)
            q7["intro27"] = {"field": _f27[0], "simple": any(_intro27.search(normalize(b.get("title") or "")) for b in _gb27),
                             "titles": [b["title"] for b in _gb27[:3]]}
    if (any(women_wish(t, _ttl35) for t in user_messages[-3:]) and not author_only and not similar_author
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
            return _g.get(b["book_id"]) == "f" or (_g.get(b["book_id"]) != "m" and bool(_TITLE_WOMEN.search(normalize(b.get("title")))))
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
            # QADFIX21 (case 45.2): «سجلات المؤلفين تقول إنها لنساء» only of the books whose author record says so
            # QADFIX29 #5 (QAD 2026-11-06 case 1.3): «باهبل… ما عندي تأكيد أن مؤلفتها امرأة» (رجاء عالم, gender f) — the list held
            # only the first four of the pool, so a woman's book the model chose was «not confirmed»; every one in the pool counts
            q7["women"] = ([x[2]["title"] for x in _wf[:12] if _g.get(x[2]["book_id"]) == "f"]
                           or [x[2]["title"] for x in _wf[:4]])
    _short31 = bool(_SHORTLIST_RX.search(" ".join(normalize(re.sub(r"[؟?!.،,«»]", " ", " ".join(user_messages[-2:]))).split()))
                    or re.search(r"(?:^| )(?:ل|لل|ال|بال)?قصيره(?: |$)", " ".join(normalize(" ".join(user_messages[-2:])).split())))
    if (prefs.get("awards") and not author_only and any(women_wish(t, _ttl35) for t in user_messages[-3:])
            and (longlist_only30(user_messages) or _short31)):
        # QADFIX30 m6 (replay of QAD 2026-11-07 case 5.2): «سمّ لي اللي وصلت الطويلة بس» → cards «بريد الليل» (won) and «حجر الضحك»: the
        # books by women that reached the longlist only come first in facts, so the reply names them and their cards
        # QADFIX31 #2 (QAD 2026-11-08 cases 6.1–6.2, grave): «الأديبات … اللي ترشحن لقصيرة جائزة الرواية العربية» → «بريد الليل» alone and
        # «ما أقدر أحدد بقية الأسماء»: for the shortlist asked, the women's books that reached it (the winners too) come first the same way
        _wac31 = women_award_counts(all_books, prefs, wanted_genres(prefs, pprefs))
        _lo30i = (_wac31.get("long_only_ids") or [] if longlist_only30(user_messages)
                  else list(dict.fromkeys((_wac31.get("won_ids") or []) + (_wac31.get("short_listed_ids") or []))))
        if _lo30i:
            _lo30s = set(_lo30i)
            _lo30r = (("longlisted only (not shortlisted)" if language == "en" else "وصل القائمة الطويلة فقط (ما دخل القصيرة)")
                      if longlist_only30(user_messages) else
                      ("reached the shortlist (or won), by a woman" if language == "en" else "وصل القائمة القصيرة (أو فاز)، ومؤلفته امرأة"))
            _have30 = {x[2]["book_id"] for x in scored}
            scored = ([(max(s, 1) + 12, [_lo30r] + r, b) for s, r, b in scored if b["book_id"] in _lo30s]
                      + [(13, [_lo30r], all_books[i]) for i in _lo30i if i not in _have30 and i in all_books and is_available(all_books[i])]
                      + [x for x in scored if x[2]["book_id"] not in _lo30s])
            # (replay 5.2: «ما عندي تأكيد الآن إن مؤلفتيهما امرأتان» — every one of them is a woman's book by our author records)
            q7["women"] = list(dict.fromkeys(list(q7.get("women") or []) + [all_books[i]["title"] for i in _lo30i if i in all_books]))
    if _ya_pred30b:
        # QADFIX31 #3 (QAD 2026-11-08 case 5.1): after the subject search and the other paths, the QADFIX30b rule again
        _out31 = {x[2]["book_id"] for x in scored if _ya_pred30b(x[2])}
        if _out31:
            scored = [x for x in scored if x[2]["book_id"] not in _out31]
            it["ya_out30b"] = sorted(set(it.get("ya_out30b") or ()) | _out31)
        # the shelf's books that made the rule hold come first again (a later path may have put them below or left them out):
        # «ريمي بلا عائلة» / «الصياد» for a boy of 15 who wants a journey, not an adult novel
        _in31 = {x[2]["book_id"] for x in scored}
        _top31 = max([x[0] for x in scored] + [1])
        _fit31 = [(_top31 + 1 + s, r, b) for s, r, b in _ya_fit31]
        _fid31 = {x[2]["book_id"] for x in _fit31}
        scored = _fit31 + [x for x in scored if x[2]["book_id"] not in _fid31]
        ya_unknown30 = []
    if it.get("refused_ids30") and not prefs.get("genres") and not author_only and not order_by:
        # QADFIX30 #4 (QAD 2026-11-07 case 45.1): «كتاب عن بناء العادات بس مو العادات الذرية لأني قريته» — a book of the same kind as the
        # one he read (تطوير ذاتي) comes before novels whose summary only names «العادات» (customs)
        _rg30 = {(all_books[i].get("genre") or "").strip() for i in it["refused_ids30"] if i in all_books} - {""}
        if _rg30:
            scored = ([x for x in scored if x[0] > 0 and (x[2].get("genre") or "").strip() in _rg30]
                      + [x for x in scored if not (x[0] > 0 and (x[2].get("genre") or "").strip() in _rg30)])
    if (language != "en" and _ed_said != "en" and not re.search(r"انجليز|انقليز|english", normalize(" ".join(user_messages[-2:])))
            and order_by != "dearest"):   # QADFIX30 #1: «The Cosmic Script» (700, English) is the most expensive; it stays first
        # QADFIX18 (inbox 2026-10-24, «ابي رواية تشد…» → «Inferno» first): a visitor writing in Arabic who has not asked for an
        # English copy gets the Arabic copies first; an English copy comes after them (or alone when no Arabic copy fits)
        _is_en = lambda b: ((b.get("language") or "ar").strip() or "ar") == "en"
        if any(s > 0 and not _is_en(b) for s, r, b in scored):
            # QADFIX23 (QAD 2026-10-28, case 13.1): «بنت اختي عمرها ٧ ومجنونة ديناصورات» — the Arabic children's books (on no
            # subject) pushed «Magic Tree House» (the only copy whose summary takes the children to the dinosaurs) out of the six,
            # and «ما لقينا كتاب عن الديناصورات» was said. With a subject asked for, an English copy on it comes before Arabic
            # copies that are not on it (the reply says it is in English: guidance_extra_en above)
            # (QADFIX35, case 7.1: a parent reader's parenting books count as on the subject — «200 خطوة نحو التربية برفق» had gone below
            # the books on the word «التواصل»)
            _on = ((lambda b: b["book_id"] in topic_ids or b["book_id"] in topic_about_ids or b["book_id"] in _parkeep35) if topic_phrases
                   else (lambda b: True))
            scored = ([x for x in scored if not _is_en(x[2]) and _on(x[2])] + [x for x in scored if _is_en(x[2]) and _on(x[2])]
                      + [x for x in scored if not _is_en(x[2]) and not _on(x[2])] + [x for x in scored if _is_en(x[2]) and not _on(x[2])])
    if young_fit33:
        # QADFIX33 #1 / #2 (cases 5.1, 25.1): after the subject search, the other paths and the language order, the young-shelf books
        # of the kind wished come first again — those on the subject asked («تنين» in «التنين الأبيض») before the others — so an adult
        # novel found by «سحر» in its summary («المعلم ومرغريتا») never comes before them
        _sc33 = {x[2]["book_id"]: x for x in scored}
        _yf33 = [all_books[i] for i in it.get("young_fit33") or () if i in all_books and i in _sc33]   # (none a later path left out)
        _on33 = (lambda b: bool(topics.match(topic_phrases, b["book_id"], topic_index)[0])) if topic_phrases else (lambda b: False)
        _yf33 = [b for _n, b in sorted(enumerate(_yf33), key=lambda x: (not _on33(x[1]), x[0]))]
        _top33 = max([x[0] for x in scored] + [1])
        _front33 = [(_top33 + 1 + 0.01 * (len(_yf33) - n), _sc33[b["book_id"]][1], b) for n, b in enumerate(_yf33)]
        scored = _front33 + [x for x in scored if x[2]["book_id"] not in {b["book_id"] for b in _yf33}]
    _k25 = set(prefs.get("genres") or ()) & {"أعمال", "تطوير ذاتي", "علم نفس"}
    if "practical_learning" in (pprefs.get("use_cases") or ()) and _k25 and not author_only:
        # QADFIX25 (QAD 2026-10-31, case 32.1): «صديقي ترقى مدير، أبغى أهديه كتاب يفيده بالإدارة» — two studies of the history of
        # administration («إدارة الدولة في صدر الإسلام») came before «Leaders Eat Last» / «Start with Why»: for a practical wish the
        # kind asked for comes first, a history or a memoir that only has the word after it
        scored = [x for x in scored if (x[2].get("genre") or "").strip() in _k25] + [x for x in scored if (x[2].get("genre") or "").strip() not in _k25]
    if _rating_wish:
        # CHATFIX30c (1): «روايات تاريخية تقييمها عالي» — among the books that fit, those with a Goodreads average (50 ratings or
        # more) come first, the highest first; the rest keep their order after them
        def _gr(b):
            return (float(b.get("goodreads_rating") or 0)
                    if (b.get("goodreads_ratings_count") or 0) >= chat_rules.RATING_MIN_COUNT else None)
        scored = ([x for x in sorted((x for x in scored if _gr(x[2]) is not None), key=lambda x: -_gr(x[2]))]
                  + [x for x in scored if _gr(x[2]) is None])
    candidates = [] if similar_author else [(b, reason_text(r, language), "direct") for s, r, b in scored if s > 0][:6]
    if (candidates and prefs.get("awards") and not author_only
            and (it.get("list_all33") or it.get("newest_award33") or it.get("oldest_award35") or it.get("list_all_prev35"))):
        # QADFIX35 (QAD 2026-11-12 case 3.1–3.2, medium): «ابيها كلها بالاسم» — the seventh winner («الفن الهندي») was not among the six
        # candidates, so the model said «ما عندي تفاصيل السابع» and next turn called it «خطأ»: every winner itself of the list the guidance
        # names is in facts (up to ten)
        _cid35 = {c[0]["book_id"] for c in candidates}
        candidates += [(b, reason_text(r, language), "direct") for s, r, b in scored
                       if s > 0 and b["book_id"] not in _cid35 and own_win_year33(b["book_id"], prefs["awards"])][:max(0, 10 - len(candidates))]
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
    _q24_pre = None
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
        if _ya_pred30b or ya_added30 or kid_added32:
            # QADFIX31 #3 (QAD 2026-11-08 case 5.1; replay fresh «ولدي ١٤ سنة يحب قصص المغامرات والسفر بس بدون عنف»): the young readers'
            # shelf books that fit a teenager (or were offered from that shelf for him) stay beside the subject's books
            _keep_par |= {x[2]["book_id"] for x in _ya_fit31} | {x[2]["book_id"] for x in ya_added30} | {b["book_id"] for _r, b in kid_added32}
        _q24_before_strict = list(candidates)
        _q24_pre = len(_q24_before_strict)
        candidates = [c for c in candidates if c[0]["book_id"] in topic_ids or c[0]["book_id"] in _keep_par]
        # the books shown earlier that fit the subject count too: «رواية تدور أحداثها في أكتوبر» right after
        # «الخريف» and «Drums of Autumn» were shown is not «nothing» (they come back as «shown» below)
        topic_nothing = not candidates and (_more_only or not any(_fits_topic(i) for i in shown[-4:]))
        # QADFIX24 (QAD 2026-10-30, case 46.1, grave): «بنت اختي ١٣ سنة تحب العلوم والتجارب» — «التجارب» is a side of the field
        # the child likes, named in the same message; no book about experiments is no reason to show nothing: the field's
        # books for her age are offered and the reply says none is about experiments in particular (non-fiction fields only:
        # «رواية عن أكتوبر» still gets only books on the subject, Ibrahim 2026-10-13)
        _q24_kind_now = {g for g in set(prefs.get("genres") or ()) | set((it.get("prefs") or {}).get("genres") or ())
                         if g not in _Q7_FICTION and g not in CHILD_GENRES
                         and (len(user_messages) == 1 or g in set(latest.get("genres") or ()))}
        if topic_nothing and _q24_kind_now and not _more_only and pprefs.get("audience") in ("children", "young_adult"):
            _kb24 = [c for c in _q24_before_strict if (c[0].get("genre") or "").strip() in _q24_kind_now]
            if not _kb24:
                # the field itself was relaxed away: its books in stock, those made for the reader's age first
                # (the children's shelves hold the field too: «سلسلة لوسي وستيفن هوكينج» is on «أطفال», its summary on physics)
                _ra = pprefs.get("reader_age")
                _fw = re.compile("|".join({"علوم": r"العلوم|علمي|علميه|فيزياء|الفلك|الفضاء|الكون|كيمياء|اختراع|تجرب"}.get(g, normalize(g))
                                          for g in sorted(_q24_kind_now)))
                def _in_field(b):
                    g = (b.get("genre") or "").strip()
                    return g in _q24_kind_now or (g in CHILD_GENRES and bool(_fw.search(normalize((b.get("title") or "") + " " + (b.get("summary") or "")))))
                _fb = [b for b in all_books.values() if is_available(b) and _in_field(b) and all(h(b) for h in hard)
                       and not (_ra and b.get("age_max") is not None and b["age_max"] < _ra - 2)
                       and not (_ra and b.get("age_max") is None and b.get("age_min") is not None and b["age_min"] <= _ra - 8)]
                _fb.sort(key=lambda b: (-(age_fit(b, _ra) if _ra else 0), (b.get("genre") or "").strip() not in CHILD_GENRES, b["book_id"]))
                _why = ["from the shelf you asked for" if language == "en" else f"من تصنيف «{'، '.join(sorted(_q24_kind_now))}» كما طلبت"]
                _kb24 = [(b, reason_text(_why, language), "direct") for b in _fb[:6]]
            if _kb24:
                candidates = _kb24
                topic_nothing = False
                q7["within_kind"] = (list(topic_phrases), sorted(_q24_kind_now))
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
    # QADFIX27 #8 (QAD 2026-11-04 case 40.1): «فيه نسخة فرنسية من الغريب؟» → the card of «الغريب – دراسة في الجهيمانية…» (a history
    # study) beside Camus's novel: when a title named matches books with exactly that title, a book whose title only starts with it
    # and that stands on another shelf is another work
    for _mt27 in it["mentioned_titles"] or []:
        _mk27 = chat_rules.title_key(_mt27)
        _h27 = [b for b in asked_books if b in match_titles([_mt27], all_books)]
        _ex27t = [b for b in _h27 if chat_rules.title_key(b.get("title") or "") == _mk27]
        if _ex27t and len(_ex27t) < len(_h27):
            _eg27 = {(b.get("genre") or "").strip() for b in _ex27t}
            # (QADFIX35, case 24.1: «اللص والكلاب (رواية مصورة)» by the same author is the same work in another form, not another work)
            _ea35 = {author_flow.display(b.get("author")) for b in _ex27t}
            asked_books = [b for b in asked_books if b not in _h27 or b in _ex27t or (b.get("genre") or "").strip() in _eg27
                           or (chat_rules.title_key(normalize(_head34(b.get("title")))) == chat_rules.title_key(normalize(_mt27))
                               and author_flow.display(b.get("author")) in _ea35)]
    # QADFIX23 (QAD 2026-10-29, case 37, grave): «الغريب لكامو ولا المسخ لكافكا؟» — «المسخ» matched «المسخ الصغير» of another
    # writer, while Kafka's book is here as «التحول». A title said with its author («X لـY»، «X حق Y»): a copy by someone else
    # is not that book; the named author's books in stock are looked at instead (the reply judges by their summaries)
    _title_authors = []
    for _mt in list(it["mentioned_titles"]):
        _said_by = title_author_said(_mt, " ".join(user_messages[-2:]), all_books)
        if not _said_by:
            continue
        _hits = [b for b in match_titles([_mt], all_books) if b in asked_books]
        # QADFIX34 #9: «فوما غوردييف لغوركي» is our «فوما جوردييف — مكسيم جوركي» (out of stock): the author's name in other letters for
        # the same sounds (غ / ج / ق / ك, long vowels) is the same author, not «a book of a similar title by someone else»
        _sk_by34 = chat_rules.title_skeleton(_said_by)
        if _hits and any(author_matches(_said_by, b.get("author"))
                         or (len(_sk_by34) >= 3 and (" " + _sk_by34 + " ") in (" " + chat_rules.title_skeleton(b.get("author") or "") + " "))
                         for b in _hits):
            # QADFIX33 (QAD 2026-11-10 cases 11.1–11.2, medium; open since 11-09 10.1): «أيهم أطول: الأم لغوركي ولا الجريمة والعقاب؟» →
            # «ما عندي عدد صفحات … مع «الأم»» and the card «الأم — غير معروف» (5167: no author, no pages) beside Gorky's 592-page copy: a
            # copy of that title whose author is not the one the visitor named (or is not recorded) is not the book he means
            # (said in the latest message itself: «وفيه رواية ثانية بنفس الاسم لكاتب غيره؟» after «الجزيرة حقت هكسلي» asks for the other)
            _not33 = ([b for b in _hits if not (b.get("author") and (author_matches(_said_by, b.get("author")) or (
                           len(_sk_by34) >= 3 and (" " + _sk_by34 + " ") in (" " + chat_rules.title_skeleton(b.get("author") or "") + " "))))]
                      if title_author_said(_mt, user_messages[-1], all_books) else [])
            if _not33:
                asked_books = [b for b in asked_books if b not in _not33]
                it.setdefault("title_author_not33", []).extend(b["book_id"] for b in _not33)
            continue
        _his = named_author_books([_said_by], all_books, _mt + " " + user_messages[-1], limit=3)
        if _his:
            asked_books = [b for b in asked_books if b not in _hits] + [b for b in _his if b not in asked_books]
            _title_authors.append((_mt, _said_by, [b["title"] for b in _his]))
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
    # QADFIX26 #3 (QAD 2026-11-01 case 23.2 «الحرب والسلام وآنا كارنينا»، 2026-11-02 case 3.1 «موبي ديك ولا العجوز والبحر»): with two
    # titles named, one found in stock no longer hides the other's copy under another title («War and Peace», «Moby-Dick»): the
    # other names are looked up whenever a named title has no copy in stock of its own (one variant per title when the model gave
    # them in the same order; otherwise all of them)
    _variants26 = list(it.get("title_variants") or [])
    _unmatched26 = []
    if len(it["mentioned_titles"]) >= 2 and any(is_available(b) for b in asked_books):
        _unmatched26 = [k for k, m in enumerate(it["mentioned_titles"])
                        if not any(is_available(b) for b in match_titles([m], all_books))]
        if _unmatched26 and len(_variants26) == len(it["mentioned_titles"]):
            _variants26 = [_variants26[k] for k in _unmatched26]
        it["unmatched_titles26"] = [it["mentioned_titles"][k] for k in _unmatched26]
    if (it["mentioned_titles"] or it.get("references")) and (not any(is_available(b) for b in asked_books) or _unmatched26):
        for v in _variants26:
            # the whole title only: «ديون» (Dune) must not bring a book that merely starts with that word
            _vh = match_titles([v], all_books, max_rank=0.5)
            if not _vh and chat_rules.title_key(v):
                # QADFIX26 (case 3.1): … or that title before a subtitle («Moby-Dick, or, The whale» for "Moby-Dick")
                _vh = [b for b in match_titles([v], all_books, max_rank=1)
                       if chat_rules.title_key(re.split(r"\s*[:,(؛–—]\s*", (b.get("title") or "").strip(), maxsplit=1)[0])
                       == chat_rules.title_key(v)]
            for b in _vh + chat_rules.series_books(v, all_books)[:5]:
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
    # QADFIX27 #3 (QAD 2026-11-05 case 3.1, grave; 2026-11-04 cases 50.1–50.2): «قريت «غرناطة» أول جزء من ثلاثية رضوى عاشور، أبي
    # «مريمة» اللي بعدها» → «مريمة مو متوفرة» beside Walid Saif's «غرناطة (آخر الأيام)»; «خلصت الجزء الأول من ثلاثية غرناطة، الثاني
    # عندكم؟» → «ما عندي جزء آخر مسجّل»: «ثلاثية غرناطة» (2581) is one volume that holds غرناطة, مريمة and الرحيل (its summary says
    # so). A part named that is no book of its own here but that an omnibus in stock holds (its title «ثلاثية / رباعية / الأعمال
    # الكاملة …», its title or summary naming the part), or the next part of an omnibus named, is answered with that volume
    omnibus27, omnibus27_parts = None, []
    if not author_only and not it.get("whole_series_found") and (it["mentioned_titles"] or part_wanted):
        omnibus27, omnibus27_parts = omnibus_for(it["mentioned_titles"] or [], user_messages[-1], bool(part_wanted), all_books)
    if not omnibus27 and it["mentioned_titles"] and not it.get("whole_series_found"):
        # QADFIX29 #9 (QAD 2026-11-06 case 3.1): a part listed in backend/omnibus_parts.json, also when the author is named
        _op29 = omnibus_part29(it["mentioned_titles"], user_messages[-1], all_books)
        if _op29:
            omnibus27, omnibus27_parts = _op29
    if omnibus27:
        _oa27 = author_flow.display(omnibus27.get("author"))
        _said27 = " ".join(normalize(" ".join(user_messages[-2:])).split())
        _same27 = bool(_oa27) and any(len(w) >= 4 and w in _said27.split() for w in normalize(_oa27).split())
        asked_books = [omnibus27] + [b for b in asked_books if b["book_id"] != omnibus27["book_id"]
                                     and (not _same27 or author_flow.display(b.get("author")) == _oa27)]
        part_wanted, part_book, series_anchor, series_rest = None, None, None, []
        it["intent"] = "about_book"
        it["omnibus27"] = {"id": omnibus27["book_id"], "parts": omnibus27_parts}
    # «عندكم نسخته الانجليزية؟» / an asked book out of stock: its other editions in stock by the
    # same author record (English editions are linked to the author by name_latin)
    if asked_books and (not any(is_available(b) for b in asked_books)
                        or re.search(r"انجليز|انقليز|english", normalize(user_messages[-1]))):
        for b in list(asked_books):
            rec = authors.lookup(b.get("author") or "")
            for bid in (authors.book_ids(rec["author_id"]) if rec else ()):
                other = all_books.get(bid)
                if (other and is_available(other) and other.get("language") == "en"
                        and other["book_id"] not in [x["book_id"] for x in asked_books]
                        # QADFIX34 #10 (case 33.1): «Chronicle of a Death Foretold» (1981) is not an English copy of «مئة عام من العزلة» (1967)
                        and not (first_published(b) and first_published(other) and first_published(b) != first_published(other))):
                    asked_books.append(other)
    if not asked_books and not it["mentioned_titles"] and not author_only and not similar_author and not _greeting_only:
        # «فن الحرب»، «دار نشر فن الحرب»، «عندكم فن الحرب؟»: the message is a stocked title (after a few asking
        # words), even when one of its words is also a topic («الحرب»): that book, not books about war
        # (inbox 2026-10-11 §3.3; audit 2026-10-12 #8)
        exact = exact_title_books(user_messages[-1], all_books)
        if exact:
            asked_books, it["mentioned_titles"], it["intent"] = exact, [exact[0]["title"]], "about_book"
            candidates, starters = [], False
    _frag = chat_rules.named_title(user_messages[-1]) if not asked_books and not _greeting_only else None
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
            and 2 <= len(normalize(user_messages[-1]).split()) <= 6 and not _greeting_only
            and not keyword_prefs.is_greeting(user_messages[-1])
            and not keyword_prefs.understood_anything(latest)):
        # «خبز على»: the visitor typed the start of a title (library trial, 2026-10-08)
        guess = match_titles([user_messages[-1]], all_books, max_rank=1)
        if guess:
            asked_books, it["mentioned_titles"], it["intent"] = guess, [user_messages[-1]], "about_book"
            candidates, starters = [], False
    # CHATFIX30 (inbox 2026-10-29 §8.1): «ابحث عن كتاب تشريح الفيلم» read as a wish (recommend) whose only subject is the title
    # asked for: it is a question about that book — said to be missing, answered by likeness only, logged as a missing book
    if (it["intent"] in ("recommend", "clarify") and it["mentioned_titles"] and not asked_books and not topic_phrases
            and not similar_author and not author_only and not publisher_name
            and any(isinstance(r, dict) and r.get("relation") in (None, "", "same_book")
                    and chat_rules.phrase_is_title(r.get("title"), it["mentioned_titles"]) for r in it.get("references") or [])
            and not chat_rules.general_words(" ".join(chat_rules.title_key(user_messages[-1]).replace(
                chat_rules.phrase_title_key(it["mentioned_titles"][0]), " ").split()))[0]):
        it["intent"] = "about_book"
    missing_title = (it["intent"] == "about_book" and bool(it["mentioned_titles"]) and not asked_books
                     and not similar_author and not author_only)
    # QADFIX22 (QAD 2026-10-27, case 2.1, grave): «عندكم رواية ديفيد معلوف اللي تحكي عن حياة الشاعر أوفيد؟» — the model gave a
    # title that is not in the message («أنطونيك») and no author request; the author the visitor named is in stock, so his books
    # are looked at before anything is called unavailable, and the reply tells by their subject whether one is the book asked for
    _by_named_author = []
    if _about_books and not asked_books:
        # QADFIX23 (case 30): the books about the writer asked for are shown
        asked_books, missing_title = list(_about_books), False
        candidates = []
    if missing_title:
        _na_names = list(prefs.get("authors") or []) + [author_request.get("full_name"), author_request.get("name_as_written")]
        _by_named_author = named_author_books(_na_names, all_books, " ".join(user_messages[-2:]))
        if _by_named_author:
            asked_books, missing_title = _by_named_author, False
            candidates = []
    # QADFIX22 (QAD 2026-10-27, case 25.1): «دستويفسكي ولا تولستوي، مين أسهل للبداية؟» — a comparison of two or three named
    # authors: each of them brings his own books in stock (any spelling of his name), so the answer can compare both
    _cmp_authors = []
    if it.get("intent") == "compare" and not asked_books and not author_only:
        _cmp_names = [a for a in (prefs.get("authors") or []) if isinstance(a, str) and a.strip()]
        _cmp_names += [p for p in (author_request.get("people") or {}) if p not in _cmp_names]
        if len(_cmp_names) >= 2:
            for _cn in _cmp_names[:3]:
                _cmp_authors.append((_cn, named_author_books([_cn], all_books, user_messages[-1], limit=3)))
            _cmp_books = [b for _n, bs in _cmp_authors for b in bs]
            if sum(1 for _n, bs in _cmp_authors if bs) >= 2:
                asked_books, candidates = _cmp_books, []
            else:
                _cmp_authors = []
    if missing_title:
        candidates = []
    if author_only and language == "en" and not edition and candidates:
        # QADFIX23 (QAD 2026-10-29, case 22.1): "Do you have books by Haruki Murakami?" — an English-speaking visitor gets the
        # author's English copies first (Norwegian Wood, Kafka on the Shore…), then the Arabic ones
        _is_en_c = lambda b: ((b.get("language") or "ar").strip() or "ar") == "en"
        _shown_c = {b["book_id"] for b, _w, _k in candidates}
        _names_c = {author_flow.display(b.get("author")) for b, _w, _k in candidates}
        _more_en = [(b, ("By " + author_flow.display(b.get("author"))), "direct") for b in all_books.values()
                    if is_available(b) and _is_en_c(b) and b["book_id"] not in _shown_c
                    and (b.get("author") in (author_names or ()) or author_flow.display(b.get("author")) in _names_c)]
        candidates = ([c for c in candidates if _is_en_c(c[0])] + _more_en[:4] + [c for c in candidates if not _is_en_c(c[0])])
    # QADFIX28 (QAD 2026-11-05 case 20.2; 2026-11-04 case 3.2): «قصص حيوانات» for a boy of 8 → «من أنا / حيوانات الغابة؟» (a lift-the-flap
    # book from age 1) — with the child's age known, a book whose recorded range starts five years or more below it and does not
    # reach it comes after the books made for his age
    _age28 = pprefs.get("reader_age") or (audience_age(user_messages) if pprefs.get("audience") in ("children", "young_adult") else None)
    if _age28 and _age28 >= 6 and candidates and not asked_books:
        def _young28(b):
            lo, hi = b.get("age_min"), b.get("age_max")
            if (b.get("genre") or "") not in CHILD_GENRES:
                return False
            if lo is None and hi is None:
                # no age recorded: the toddlers' formats by their titles («كلماتي الأولى»، «كتابي الأول»، «ألعاب الدمى»)
                return bool(_TODDLER28.search(" " + " ".join(normalize(b.get("title") or "").split()) + " "))
            return lo is not None and lo <= _age28 - 5 and (hi is None or hi < _age28)
        _ok28 = [c for c in candidates if not _young28(c[0])]
        if len(_ok28) >= 2:
            candidates = _ok28 + [c for c in candidates if _young28(c[0])]
            it["too_young28"] = [c[0]["book_id"] for c in candidates if _young28(c[0])]

    if it.get("title_words32") and not order_by:
        # QADFIX32 #5 (QAD 2026-11-09 case 28.1; replay fresh «فيها كلمة حرير وكلمة غزالة»): the book whose title holds every word the
        # visitor remembers is the book asked for, whatever the subject search found on one of the words («طريق الحرير») or a loose
        # match of a title the model guessed («مختلف»)
        asked_books = [all_books[i] for i in it["title_words32"] if i in all_books]
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
    # QADFIX27 #9 (QAD 2026-11-04 case 24.1): «فيه كتاب اسمه شي زي «لأنك الله»؟» asks for a book by (about) that title — «زي» there is
    # about the name, not a wish for books like it: the title is the book asked for (its cards), not a model kept out of the cards
    _lm27n = " " + " ".join(normalize(re.sub(r"[؟?!.،,«»\"]", " ", user_messages[-1])).split()) + " "
    _by_title27 = re.search(r" (?:اسمه|اسمها|عنوانه|عنوانها|بعنوان|باسم|يسمي|تسمي|called|titled|named) ", _lm27n)
    # QADFIX33 (QAD 2026-11-10 case 21.2, minor; QADFIX10 class): "something shorter than Foundation, a standalone" — the model gave the
    # title as «المؤسسة» (its Arabic name), so «shorter than Foundation» was not seen and the Foundation card stayed: the title's variants
    # the model gave (as the visitor wrote it) are looked for too
    _cmp_names33 = list(it["mentioned_titles"]) + ([v for v in it.get("title_variants") or [] if v not in it["mentioned_titles"]]
                                                  if it["mentioned_titles"] else [])
    for _mt in _cmp_names33:
        if _by_title27 and (" " + " ".join(normalize(_mt).split()) + " ") in _lm27n[_by_title27.start():]:
            continue
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
    # QADFIX15 (Codex/AGY audit 2026-10-23, case 4.2): a book the visitor rules out («دون كيخوته إسبانية مو من أمريكا
    # اللاتينية!»), read by the model in any wording (relation rejected), is never a card nor an example; its facts stay so
    # the reply can say why
    _rejected = {" ".join(normalize(r.get("title") or "").split()) for r in it.get("references") or []
                 if isinstance(r, dict) and r.get("relation") == "rejected" and (r.get("title") or "").strip()}
    _rejected_ids = ({b["book_id"] for _mt in it["mentioned_titles"] if " ".join(normalize(_mt).split()) in _rejected
                      for b in match_titles([_mt], all_books)}
                     | {b["book_id"] for _t in _rejected for b in match_titles([_t], all_books)})
    _example_ids -= _rejected_ids
    _like_ids -= _rejected_ids
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
                # QADFIX20 (QAD 2026-10-25, case 40.1; AGY-20261025-01 b): «أيهم أخف: عالم جديد شجاع ولا ١٩٨٤؟» — the edition kept
                # for the work has no page count while two of its other editions do: the work's length is theirs, said with
                # the edition it comes from, never «I don't have its page count»
                if facts[-1].get("pages_approx") is None:
                    _paged = sorted((e for e in facts[-1]["other_editions"] if e.get("pages_approx")),
                                    key=lambda e: (e.get("copy_language") != facts[-1].get("copy_language"), e["pages_approx"]))
                    if _paged:
                        facts[-1]["pages_approx_other_edition"] = {"pages": _paged[0]["pages_approx"],
                                                                   "publisher": _paged[0].get("publisher")}
        # QADFIX21 (QAD 2026-10-26, case 10.1): «مزرعة الحيوان كم صفحة تقريبًا؟» — no copy under this title has a page count,
        # the English «Animal Farm» (140) does: the same author's copies under another title or language go to the model,
        # which says the count only for the one that IS this work, with its language
        _no_pages = (facts and facts[-1]["id"] == b["book_id"] and facts[-1].get("pages_approx") is None
                     and not facts[-1].get("pages_approx_other_edition"))
        if facts and facts[-1]["id"] == b["book_id"] and (_LANGS_ASKED.search(" ".join(normalize(user_messages[-1]).split())) or _no_pages):
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
    # QADFIX27 #2 (replay of QAD 2026-11-04 case 16.1): «مدينة القديس أوغسطين», brought in by its summary («أنظمة الذكاء الاصطناعي»)
    # without the «خيال علمي» tag, was then dropped as a card and its sentence cut, leaving a question only — it is held to the
    # same relaxed subgenre as a book about the subject
    _lit27ids = set(it.get("literal_topic27") or ())
    suitable = {b["book_id"] for b in all_books.values()
                if age_allowed(cards.get(b["book_id"]), pprefs.get("audience"), pprefs.get("avoid") or (), b, pprefs.get("reader_age"),
                               _cartoon_id(b["book_id"]))
                and (not prefs.get("subgenres") or matching_subgenres(b, prefs, card=cards.get(b["book_id"]))
                     # QADFIX22 (QAD 2026-10-27, case 17.1): «رواية ملحمية عن عيلة عبر أجيال» — a book ABOUT the subject named
                     # («آل بودنبروك») is offered although no card calls it «ملحمية»; all four chosen books were dropped and
                     # their sentence cut, leaving a question only
                     or b["book_id"] in _lit27ids or b["book_id"] in topic_about_ids)
                and origin_allowed(b, prefs)}
    # every stated constraint (length, genre, excluded moods ... as in constrain()): required of
    # each book shown as a card except a title the visitor asked about by name
    _genres = wanted_genres(prefs, pprefs)
    _prefs_about = dict(prefs, subgenres=[]) if prefs.get("subgenres") else prefs   # QADFIX22 (case 17.1), as in suitable
    strict = {b["book_id"] for b in all_books.values()
              if meets_constraints(b, cards.get(b["book_id"]),
                                   (_prefs_about if b["book_id"] in topic_about_ids else prefs) if b["book_id"] not in _lit27ids else _prefs_about,
                                   pprefs, exclusions, _genres)}
    # A reference book the visitor wants something similar to ("تشبه فتاة البرتقال"): only when
    # it is not in stock with a researched card, the model's general description of it is used to
    # find in-stock books by the same author or with shared themes/moods (Ibrahim, 2026-09-28).
    ref_notes = []
    # QADFIX18b (AGY-20261024-02, case 7.1 «من نوع كليلة ودمنة أو ألف ليلة وليلة»): a book of ours whose title carries the
    # name of the work wanted as a kind is that kind whatever subgenre («حكايات») or shelf («قصص قصيرة» / «رواية») the model
    # guessed from the reference — the visitor named no form; it still has to suit the reader (age, content, origin, hard wishes)
    _kind_ids = set()
    # … and a book chosen as close to the named work, when the visitor named no form himself («ابي شي من نوع…»): the shelf the
    # model guessed («رواية» for «كليلة ودمنة») does not take its card away («البنتاميرون: حكاية الحكايات»)
    _form_named = bool(local_understand([user_messages[-1]]).get("genres"))
    def _kind_ok(b):
        # AUDFIX 2026-10-25 (Codex audit P2): only the form guessed from the named work is eased; every wish and refusal the
        # visitor stated («من نوع ألف ليلة وليلة بس بدون واقعية سحرية»: subgenres_exclude, moods, length ...) still holds
        return (meets_constraints(b, cards.get(b["book_id"]), prefs, pprefs, exclusions, _genres, form=False)
                and all(h(b) for h in hard))
    # CHATFIX30 (inbox 2026-10-29 §8.1): a book asked for by its title and missing here is matched by what IT is about; the kind of
    # book wished for earlier in the conversation («رواية تاريخية» four turns before «تشريح الفيلم») does not narrow the likeness
    # search (it found «إنكي» for a book on film language); every wish and refusal held by _kind_ok still applies
    _ref_suitable = suitable
    if missing_title and it.get("references"):
        _ref_suitable = {b["book_id"] for b in all_books.values() if is_available(b) and _kind_ok(b)}
    if it.get("references") and (it["intent"] != "about_book" or missing_title):
        formats = {bid: (p or {}).get("format") for bid, p in _profile_formats().items()}
        for ref in it["references"]:
            if it.get("omnibus27") and chat_rules.title_key(ref.get("title") or "") in {
                    chat_rules.title_key(p) for p in it["omnibus27"]["parts"]}:
                continue   # QADFIX27 #3: «مريمة» is inside «ثلاثية غرناطة» here — no «something like it» search
            inside = [b for b in match_titles([ref["title"]], all_books)
                      if is_available(b) and b["book_id"] in cards and b["book_id"] in formats and formats[b["book_id"]]]
            if inside:
                continue  # researched and in stock: its own card and related books are used instead
            in_stock = (any(is_available(b) for b in match_titles([ref["title"]], all_books))
                        or any(is_available(b) for b in other_form))   # here under another form of its title
            if in_stock and it["intent"] == "compare":
                continue  # QADFIX17 (case 44.1): two books of ours compared — no «something like it» search
            # a book we do not have is looked up outside (Wikidata, Open Library, Wikipedia; Goodreads after
            # the answer) and kept for next time; what is found there replaces the model's memory of it,
            # which once named a wrong author for «اخلع حذائك» (Ibrahim 2026-10-11)
            outside = None if in_stock else outside_books.lookup(ref["title"])
            if outside:
                # the model's themes stay unless it named another author (then it was thinking of another book);
                # «عمرو عبد الحميد» and «عمرو عبدالحميد» are the same man
                said, real = normalize(ref.get("author") or ""), normalize(outside.get("author") or "")
                same = (not said or not real or _same_author(said, real)
                        or said.replace(" ", "") == real.replace(" ", "")
                        # CHATFIX30 (inbox 2026-10-29 §8.1): «برنارد ف. ديك» / "Bernard F. Dick" — names in two scripts cannot
                        # be compared letter by letter; the model's themes of «تشريح الفيلم» («السينما») were dropped and no
                        # film book could be found
                        or bool(re.search(r"[a-z]", said)) != bool(re.search(r"[a-z]", real)))
                ref = dict(ref, author=outside.get("author") or "", known=True,
                           themes=ref.get("themes") if same else [], moods=ref.get("moods") if same else [],
                           format=ref.get("format") if same else None)
            # CHATFIX30 (inbox 2026-10-29 §8.1, «سجون نختار أن نحيا فيها»: two NOVELS offered for a book of essays): the books
            # chosen for their likeness to a book we do not have are of its form — fiction for fiction, non-fiction for non-fiction
            # CHATFIX30b (4): … and a self-help / spiritual book («قوة الآن») gets self-help, psychology, religion or thought
            # books, never a memoir of illness («ظلام مرئي: مذكرات الجنون»)
            _ref_fiction = None if in_stock else chat_rules.reference_form(ref, outside)
            found = reference_matches(ref, all_books, cards, _ref_suitable, formats) if ref["known"] else []
            found = [x for x in found if chat_rules.same_form_as(_ref_fiction, x[0])]
            if outside:
                text = outside_books.text_for_meaning(outside)
                got = {b["book_id"] for b, _, _ in found}
                near = [i for i in _ref_suitable if i not in got and i in all_books and is_available(all_books[i])
                        and normalize(all_books[i]["title"]) != normalize(ref["title"])
                        and chat_rules.same_form_as(_ref_fiction, all_books[i])]
                for bid, _sim in (embeddings.similar_to(text, near, top=4) if text else []):
                    found.append((all_books[bid], (f"Close to «{ref['title']}» in what it is about" if language == "en"
                                                   else f"قريب من «{ref['title']}» في موضوعه"), "reference"))
            if len(found) < 2 and ref.get("known") and not in_stock:
                # a well-known book we do not have: books of the same kind for this reader (QAD 2026-10-14, case 48)
                _kind = reference_kind_matches(ref, all_books, {i for i in _ref_suitable if i in all_books and chat_rules.same_form_as(_ref_fiction, all_books[i])},
                                               audience_age(user_messages) if pprefs.get("audience") in ("children", "young_adult") else None,
                                               exclude={b["book_id"] for b, _, _ in found}, language=language)
                found = found + _kind
            if len(found) < 2 and outside and not in_stock:
                # CHATFIX30 (1): «تشريح الفيلم» unknown to the model, tagged "Film" outside: our books on film (by their own
                # titles, keywords and subgenres), never books with the word «تشريح»
                found = found + outside_subject_matches(
                    chat_rules.outside_subject_words(outside), ref, all_books,
                    [i for i in _ref_suitable if i in all_books and chat_rules.same_form_as(_ref_fiction, all_books[i])],
                    exclude={b["book_id"] for b, _, _ in found}, language=language, limit=4 - len(found))
            if not in_stock and ref.get("relation") in (None, "example", "similar"):
                # QADFIX17 (QAD 2026-10-24, case 7.1): «من نوع كليلة ودمنة أو ألف ليلة وليلة» — a book of ours whose title
                # carries the work's name («ليالي ألف ليلة» by Mahfouz) is of that kind
                _rk = chat_rules.title_key(ref["title"]).split()
                _ph = " ".join(_rk[:2]) if len(_rk) >= 2 else ""
                if len(_ph) >= 7:
                    _got = {x[0]["book_id"] for x in found}
                    _n = 0
                    for _b in all_books.values():
                        if (_n < 3 and _b["book_id"] not in _got and is_available(_b) and _kind_ok(_b)
                                and (" " + _ph + " ") in (" " + chat_rules.title_key(_b.get("title") or "") + " ")):
                            found.append((_b, (f"Its title comes from «{ref['title']}»" if language == "en"
                                               else f"عنوانه مأخوذ من «{ref['title']}»"), "reference"))
                            _kind_ids.add(_b["book_id"])
                            _n += 1
            # QADFIX16 (QAD 2026-10-24, case 8.2): "similar but by an Iranian author" — the books chosen for the likeness keep
            # the visitor's hard wishes too (the authors' country, publisher, years)
            found = [x for x in found if all(h(x[0]) for h in hard) and chat_rules.same_form_as(_ref_fiction, x[0])]
            for b, why, match in found:
                add(b, why, match)
            ref_notes.append((ref, in_stock, bool(found), outside))
    # QADFIX17 (QAD 2026-10-24, case 7.1): «من نوع … ألف ليلة وليلة» — a work named as the kind wanted that we do not stock: a book of
    # ours whose title carries its name («ليالي ألف ليلة» by Mahfouz) is of that kind
    _have_keys = None
    for _mt in list(it.get("mentioned_titles") or [])[:3]:
        _mk = chat_rules.title_key(_mt).split()
        _ph = " ".join(_mk[:2]) if len(_mk) >= 2 else ""
        if len(_ph) < 7 or len(_mk) < 3:
            continue
        if _have_keys is None:
            _have_keys = {chat_rules.title_key(b.get("title") or "") for b in all_books.values() if is_available(b)}
        if chat_rules.title_key(_mt) in _have_keys:
            continue
        _n = 0
        for _b in all_books.values():
            _tk = chat_rules.title_key(_b.get("title") or "")
            if (_n < 2 and is_available(_b) and _kind_ok(_b) and _tk != chat_rules.title_key(_mt)
                    and (" " + _ph + " ") in (" " + _tk + " ")):
                add(_b, (f"Its title comes from «{_mt}»" if language == "en" else f"عنوانه مأخوذ من «{_mt}»"), "reference")
                _kind_ids.add(_b["book_id"])
                _n += 1
    _ref_country_books = []
    if ref_notes and country and not country_missing and not any(n[2] for n in ref_notes):
        # QADFIX16 (QAD 2026-10-24, case 8.2): "anything similar but by an Iranian author?" and nothing like the book is by an
        # author from there: the books of authors from there are offered, said as such (no likeness is claimed)
        _cn = "، ".join(country[1][:3])
        for b in [b for b in pool if is_available(b)][:6]:
            add(b, (f"By an author from {_cn}" if language == "en" else f"لمؤلف من {_cn}"), "direct")
            _ref_country_books.append(b["book_id"])
    # «أبغى كتاب يشبه اخلع حذائك» (Ibrahim 2026-10-11): a likeness request never gets random «for variety»
    # fillers; and when the book could not be described or matched and the visitor said nothing else to
    # search by, no book is shown at all and the visitor is asked what they liked in it.
    if ref_notes:
        candidates = [c for c in candidates if c[2] != "filler"]
    other_wishes = any((prefs or {}).get(k) for k in llm.PREFS_SCHEMA["properties"] if k not in ("unsupported_requests", "title_keywords"))
    ref_unmatched_only = (bool(ref_notes) and not any(n[2] for n in ref_notes) and not missing_title and not _ref_country_books
                          and not similar_author and not author_only and not topic_phrases and not publisher_name
                          and not other_wishes)
    if ref_unmatched_only:
        candidates = []
    if publisher_unknown and not topic_phrases and not other_wishes and not asked_books:
        candidates = []   # only a publisher we do not stock was asked for: no book is passed off as theirs
    _ref_dbg = {"refs": len(ref_notes), "found": [n[2] for n in ref_notes], "outside": [bool(n[3]) for n in ref_notes], "missing_title": missing_title, "similar_author": bool(similar_author),
                "author_only": bool(author_only), "topic_phrases": list(topic_phrases or []), "publisher": publisher_name,
                "other_wishes": [k for k in llm.PREFS_SCHEMA["properties"] if (prefs or {}).get(k) and k not in ("unsupported_requests", "title_keywords")],
                "unmatched_only": ref_unmatched_only, "q24": {"pre_strict": _q24_pre, "within_kind": q7.get("within_kind"), "edition_soft": q7.get("edition_soft")}}
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
        if topic_phrases:
            # QADFIX21 (QAD 2026-10-26, case 11.2): «وش يشبه كتبه عن الأساطير الإغريقية؟» — the subject named beside the likeness
            # is searched too: books about it (not his own) come first, then the ones like his style
            _about = sorted(((topics.match(topic_phrases, b["book_id"], topic_index)[0], b) for b in pool_before
                             if b["book_id"] in suitable and b["book_id"] not in (own_ids if author_record else set())
                             and is_available(b)), key=lambda x: -x[0])
            for _sc, b in [x for x in _about if x[0] > 0][:3]:
                add(b, (f"About «{'», «'.join(topic_phrases)}»" if language == "en" else f"عن «{'»، «'.join(topic_phrases)}»"), "direct")
                similar_found = True
        _like = author_style_matches(style, author_name, all_books, cards, allowed, researched)
        _classic = bool(re.search(r"كلاسيك|كلاسيكي|كلاسيكيه|قديم|قديمه|classic", normalize(user_messages[-1])))
        if _classic:
            # «شي كلاسيكي زي روايات جول فيرن»: works first published before 1950 first, not «Inferno» (QADFIX4, QAD
            # 2026-10-16 case 5.2)
            _like = sorted(_like, key=lambda m: not ((first_published(m[0]) or 9999) < 1950))
        for b, why, match in _like:
            add(b, why, match)
            similar_found = True
        if author_record and not several and author_request.get("mode") == "similar":
            # «شي كلاسيكي زي روايات جول فيرن» asks for classics of that kind, his own among them: they are named too,
            # after the likeness ones (QADFIX4, case 5.2).
            # INBOXFIX 2026-10-29 (library inbox 12:08, conversation 8.27): «رواية درامية نفس كتب دوستوفيسكي» got «ما لقيت في
            # سجلاتنا كتبًا لدوستويفسكي نفسه» with ~20 of his books in stock (his own books were kept out of facts, so the model
            # took them for absent), and «ومن دار التنوير» then got Tolstoy instead of his التنوير editions. His own available
            # books now come too, after the likeness ones, said to be his (two at most; the guidance below says so)
            for b in [x for x in pool if x["book_id"] in own_ids and x["book_id"] in suitable][:2]:
                add(b, (f"By {author_name} himself, in stock too" if language == "en" else f"من كتب {author_name} نفسه، ومتوفر أيضًا"), "related")
                own_extra.append(b["book_id"])
    # QADFIX13 #1: when the visitor named a book as the model of what he wants, the books linked to it as similar come
    # first and the general matches after them
    # QADFIX18 (inbox 2026-10-24): the nearest books by authors from the asked country to a prize wish none of theirs meets
    # (on a list, nominated) — offered as what they are, never as winners
    _near_ids = set()
    for b, _lab in country_near:
        add(b, (f"By an author from {country_place(list(country[1]))}; {honors.to_english(_lab)} (a list or nomination, not a win)"
                if language == "en" else f"لمؤلف من {country_place(list(country[1]))}؛ {_lab} (قائمة أو ترشيح، لا فوز)"), "related")
        _near_ids.add(b["book_id"])
    # QADFIX21 (QAD 2026-10-26, case 22.1): «رواية تونسية فازت بالبوكر العربية، اسمها نازلة شي» — the book asked for is not in
    # stock: the same author's books in stock and the in-stock books of that prize are the alternatives, never «we have no
    # book linked to the prize»
    if asked_books and prefs.get("awards") and not any(is_available(b) for b in asked_books):
        _gw = set(prefs.get("genres") or ())
        _asked_ids = {b["book_id"] for b in asked_books}
        _ba = book_author_map()
        _aids = set().union(*[set(_ba.get(b["book_id"], [])) for b in asked_books])
        _same = [b for b in all_books.values() if b["book_id"] not in _asked_ids and is_available(b)
                 and _aids & set(_ba.get(b["book_id"], []))][:2]
        for b in _same:
            add(b, ("By the same author, in stock" if language == "en" else "للمؤلف نفسه، ومتوفر"), "related")
            _near_ids.add(b["book_id"])
        _pz = []
        for b in all_books.values():
            if b["book_id"] in _asked_ids or not is_available(b) or (_gw and (b.get("genre") or "").strip() not in _gw):
                continue
            _rs = honors.matches_award(b["book_id"], prefs["awards"], prefs.get("award_year"), prefs.get("award_min_year"),
                                       prefs.get("award_max_year"), prefs.get("award_results"))
            if _rs:
                _pz.append((not _rs[0].startswith("فاز"), b, _rs[0]))
        for _lost, b, _r in sorted(_pz, key=lambda x: x[0])[:3]:
            add(b, _r, "related")
            _near_ids.add(b["book_id"])
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
            # INBOXFIX2 (library inbox 2026-10-29, conversation 8.7): «رشّح لي كتابًا يشبه «ظل الريح»» got four parts of one
            # series («تحقيقات نوح الألفي» 1–3, 5) and none of Zafón's own. The like-books are varied: one book per series among
            # the similar ones (its earliest part), then the next part of the book's own series, then two of its author's books
            def _pos(r):
                t = all_books.get(r["target_book_id"]) or {}
                return t.get("series_position") or 99
            _sim, _seen_series, _rest = [], set(), []
            for r in sorted(rel[_fid], key=lambda r: (r["relation_type"] != "similar", _pos(r))):
                t = all_books.get(r["target_book_id"]) or {}
                _sn = normalize(t.get("series_name") or "")
                if r["relation_type"] == "similar":
                    if _sn and _sn in _seen_series:
                        _rest.append(r)
                        continue
                    _seen_series.add(_sn) if _sn else None
                    _sim.append(r)
                else:
                    _rest.append(r)
            _order = {"series_next": 0, "same_author": 1}
            _others = sorted([r for r in _rest if r["relation_type"] in _order], key=lambda r: _order[r["relation_type"]])
            _authors = [r for r in _others if r["relation_type"] == "same_author"]
            _next = [r for r in _others if r["relation_type"] == "series_next"]
            _tail = [r for r in _rest if r["relation_type"] not in _order]
            _mix = _sim[:2] + _next[:1] + _authors[:2] + _sim[2:] + [r for r in _authors[2:]] + _tail
            _dedup, _ids_seen = [], set()
            for r in _mix:
                if r["target_book_id"] not in _ids_seen:
                    _dedup.append(r)
                    _ids_seen.add(r["target_book_id"])
            rel[_fid] = _dedup
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
        "compare": ("Compare the named books and answer the question asked plainly (which is lighter, sadder, shorter …): from their facts"
                    " (moods, weight, pace, pages), and for a well-known work without reader details from what is generally known about it"
                    " (rule 2, «What is generally known about this work…»). Never answer that you cannot say when facts or general knowledge"
                    " give an answer; then help the visitor choose."),
        "smalltalk": "Reply warmly in one sentence, then ask about the mood or type of reading they want.",
        "off_topic": "The request is outside reading: decline briefly and return to books. Do not show books.",
    } if language == "en" else {
        "recommend": "اقترح من 2 إلى 4 كتب متوفرة (available=true) من facts، مع سبب قصير لكل كتاب من صفاته في facts، ثم سؤال واحد يساعد يضيّق الاختيار.",
        "clarify": "الطلب ما زال عامًا: اسأل سؤالًا واحدًا لطيفًا يساعدك تختار (المزاج، الوقت، آخر كتاب عجبه، خفيف ولا عميق، متعة ولا معرفة). لا تقترح أي كتاب الآن (show_book_ids فارغة).",
        "about_book": "أجب عن الكتاب المسؤول عنه من معلوماته في facts فقط. إذا كان غير متوفر قل ذلك واقترح بديلًا متوفرًا. لا تعرض كتبًا جديدة إلا إذا طلب.",
        "compare": ("قارن بين الكتب المذكورة وأجب عن سؤاله بوضوح (أيها أخف، أحزن، أقصر …): من معلوماتها في facts (الأجواء، الثقل،"
                    " الإيقاع، الصفحات)، وللعمل المعروف الذي ما عندنا عنه آراء قرّاء فمن المعروف عنه عمومًا (القاعدة 2: «المعروف عن هذا العمل"
                    " عمومًا أن…»). لا تقل إنك ما تقدر تحدد والمعلومات أو المعرفة العامة تجيب، ثم ساعده يختار."),
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
    if q7.get("within_kind"):
        # QADFIX24 (case 46.1): the field's books for the young reader, the side of it asked for not found
        _wk_t, _wk_g = q7["within_kind"]
        guidance += ((f" None of our books is about «{', '.join(_wk_t)}» in particular: say so in one short sentence, then offer the"
                      f" {', '.join(_wk_g)} books in facts that suit the reader's age (each with its age range when facts give it).")
                     if language == "en" else
                     (f" ما لقينا كتابًا عن «{'، '.join(_wk_t)}» تحديدًا: قل ذلك في جملة قصيرة، ثم اعرض كتب «{'، '.join(_wk_g)}»"
                      " اللي في facts وتناسب عمر القارئ (واذكر العمر المناسب لكل كتاب إذا كان في facts). لا تقل إنه ما عندنا شي قريب."))
    _months_asked = [p for p in topic_phrases if months_in(p)] if topic_phrases and not topic_nothing else []
    if _months_asked and not _EVENT_WORDS.search(normalize(" ".join(user_messages))):
        _season = _SEASONS[MONTH_SEASON[months_in(_months_asked[0])[0]]]
        guidance += ((f" «{_months_asked[0]}» here is the month itself (no war or event was named): the books in facts mention it"
                      f" or are close to its season ({_season[1]}); say which honestly, never claim a book is set in that month unless its facts say so.")
                     if language == "en" else
                     f" «{_months_asked[0]}» هنا الشهر نفسه (ما ذكر حربًا ولا حدثًا): كتب facts إما تذكره أو قريبة من أجواء {_season[0]}؛"
                     " قل ذلك بصراحة، ولا تقل إن كتابًا تدور أحداثه في هذا الشهر إلا إذا قالت معلوماته ذلك.")
    _unrated = [f for f in facts if f["id"] in all_books
                and unrated_comic_ok(cards.get(f["id"]), all_books[f["id"]], pprefs.get("audience"), pprefs.get("reader_age"))]
    # QADFIX23 (case 25.3): a book with no age range recorded offered to a child only for its cartoon or its young edition
    _unaged = [f for f in facts if f["id"] in all_books and pprefs.get("audience") == "children"
               and all_books[f["id"]].get("age_min") is None and all_books[f["id"]].get("age_max") is None
               and (all_books[f["id"]].get("genre") or "").strip() not in ("أطفال",)
               and (not cards.get(f["id"]) or cards[f["id"]].get("format") not in AGE_OK.get("children", ()))
               and f["id"] not in {u["id"] for u in _unrated}]
    if _unaged:
        guidance += ((" Books in facts with no age range recorded: " + ", ".join(f"«{f['title']}»" for f in _unaged[:4])
                      + ". If you show one to this child, say its suitable age is not recorded with us.")
                     if language == "en" else
                     (" كتب في facts عمرها المناسب غير مسجل عندنا: " + "، ".join(f"«{f['title']}»" for f in _unaged[:4])
                      + ". إذا عرضت منها شيئًا لهذا الطفل فقل إن عمرها المناسب غير مسجل عندنا."))
    if _unrated:
        # QADFIX22 (cases 19.1/19.3): comics offered to a child of 10 or more with no age range recorded
        guidance += ((" Comics in facts with no recorded age range: " + ", ".join(f"«{f['title']}»" for f in _unrated[:4])
                      + ". If you show one, say its suitable age is not recorded with us and a staff member can confirm it;"
                      " never say we have no comics.")
                     if language == "en" else
                     (" قصص مصورة في facts عمرها المناسب غير مسجل عندنا: " + "، ".join(f"«{f['title']}»" for f in _unrated[:4])
                      + ". إذا عرضت منها شيئًا فقل إن عمرها المناسب غير مسجل عندنا ويقدر الموظف يتأكد منه؛"
                      " ولا تقل إنه ما عندنا قصص مصورة."))
    if _cmp_authors:
        # QADFIX22 (case 25.1): two authors compared — both have books in facts
        _cn_have = [n for n, bs in _cmp_authors if bs]
        guidance += ((" The visitor compares these authors: " + ", ".join(_cn_have) + ". facts hold books in stock by each of them:"
                      " answer the comparison plainly (what is generally known about each, and their books in facts such as length),"
                      " name a book of each, and never say you lack details about one of them.")
                     if language == "en" else
                     (" الزائر يقارن بين هؤلاء المؤلفين: " + "، ".join(_cn_have) + ". في facts كتب متوفرة لكل واحد منهم:"
                      " أجب عن المقارنة بوضوح (المعروف عامةً عن كل منهما، وكتبه في facts مثل طولها)، واذكر كتابًا لكل واحد،"
                      " ولا تقل إنك ما عندك تفاصيل عن أحدهما."))
    # QADFIX23 (QAD 2026-10-28, case 50.2): «ابي شي مثل لعبة العروش بس أخف… مو بطولها ولا دمويتها» — the work named as the model
    # is not itself an answer, even when we have it
    _um2 = " " + " ".join(normalize(" ".join(user_messages[-2:])).split()) + " "
    _like_named = [t for t in it["mentioned_titles"] if t and re.search(
        r"(?:مثل|زي|يشبه|تشبه|شبه|نفس جو|علي غرار|like|similar to)\s+(?:(?:روايه|رواية|سلسله|سلسلة|كتاب)\s+)?" + re.escape(" ".join(normalize(t).split())), _um2)]
    # only «like X BUT lighter / shorter / without…»: a plain «كلاسيكيات زي دون كيخوته» keeps X as an example (QADFIX15)
    if not re.search(r" (?:بس|لكن|بدون|اخف|اقصر|اقل|ابسط|مو ب\S*|but|without|lighter|shorter|less) ", _um2):
        _like_named = []
    _like_ids = {b["book_id"] for t in _like_named for b in match_titles([t], all_books, max_rank=1)}
    if _like_named:
        _like_ids |= {b["book_id"] for r in (it.get("references") or []) if isinstance(r, dict) and r.get("title")
                      for b in match_titles([r["title"]], all_books, max_rank=1)}
    if _like_named:
        guidance += ((" The visitor wants books LIKE «" + "», «".join(_like_named) + "», not that work itself: never offer it or another part of it as an answer.")
                     if language == "en" else
                     (" الزائر يبي كتبًا **مثل** «" + "»، «".join(_like_named) + "» لا العمل نفسه: لا تعرضه ولا جزءًا آخر منه كإجابة."))
    for _mt, _by, _his_t in _title_authors:
        # QADFIX23 (case 37): «المسخ لكافكا» — the copy under that title is someone else's; his books are in facts
        # QADFIX30 (QAD 2026-11-07 case 22.1, minor): «ابنة الحارس الأخير» لعلوان (a title no one knows) → «نفسها غير متوفرة عندنا الآن», as
        # if it existed: a title the interpreter does not know as a real book is «no book of that title here», not «not in stock now»
        _known30 = any(isinstance(r, dict) and r.get("known") and chat_rules.title_key(r.get("title") or "") == chat_rules.title_key(_mt)
                       for r in it.get("references") or [])
        # QADFIX34 #9 (QAD 2026-11-11 case 23, minor): «ابغى رواية فوما غوردييف لغوركي» → «ما عندنا كتاب بعنوان «فوما غوردييف»» twice, while
        # our record «فوما جوردييف» (مكسيم جوركي) is out of stock: a title in our records under another spelling of its letters is a
        # book we know, only not in stock now
        _sk34 = chat_rules.title_skeleton(_mt)
        if not _known30 and _sk34 and len(_sk34) >= 4:
            _known30 = any(chat_rules.title_skeleton(b.get("title") or "") == _sk34 for b in all_books.values())
        _miss_en30 = (f"«{_mt}» itself is not in stock now" if _known30 else f"we have no book titled «{_mt}» (do not say it is out of stock)")
        _miss_ar30 = (f"«{_mt}» نفسه غير متوفر عندنا الآن" if _known30 else f"ما عندنا كتاب بعنوان «{_mt}» (لا تقل إنه غير متوفر الآن)")
        guidance += ((f" The visitor asked for «{_mt}» by {_by}. A book of ours with a similar title is by someone else and is not it."
                      f" facts hold {_by}'s books in stock ({', '.join('«' + x + '»' for x in _his_t)}): if one of them is this very work"
                      f" under another (translated) title — judge by its title and summary — say plainly that we have it under that title."
                      f" Never call his book unavailable when it is in facts. If none of them is it, say first that {_miss_en30},"
                      f" then offer these books of his as the alternative (QADFIX24, replay of case 25.1).")
                     if language == "en" else
                     (f" الزائر طلب «{_mt}» لـ{_by}. الكتاب اللي عندنا بعنوان قريب منه لمؤلف آخر وليس هو."
                      f" في facts كتب {_by} المتوفرة ({'، '.join('«' + x + '»' for x in _his_t)}): إذا كان أحدها هو العمل نفسه بعنوان"
                      f" آخر (ترجمة أخرى) — احكم من عنوانه ونبذته — فقل بوضوح إنه عندنا بهذا العنوان. لا تقل عن كتاب له في facts إنه غير متوفر."
                      f" وإذا ما كان أحدها هو، فقل أولًا: {_miss_ar30}، ثم اعرض كتب {_by} هذه بديلًا، ولا تحيله للموظف."))
    if _about_books:
        # QADFIX23 (case 30): his own works are not here; books about him are
        guidance += ((f" The visitor asked for {_about_name}; none of his own works (his poetry collection, his books) is in stock."
                      f" The books in facts are books ABOUT him, in stock: say plainly in one short sentence that his own works are not"
                      f" here, then offer these books about him (fitting as a gift if a gift is asked for). Never ask «do you mean"
                      f" {_about_name}?», never call a book in facts unavailable.")
                     if language == "en" else
                     (f" الزائر طلب {_about_name}؛ ما عندنا من أعماله نفسها (ديوانه أو كتبه) شيء متوفر. كتب facts كتب **عنه** ومتوفرة:"
                      f" قل في جملة قصيرة إن أعماله نفسها مو عندنا، ثم اعرض هذه الكتب عنه (وتناسب الهدية إن طلب هدية)."
                      f" لا تسأل «تقصد {_about_name}؟»، ولا تقل عن كتاب في facts إنه غير متوفر."))
    if _by_named_author:
        # QADFIX22 (case 2.1): the book was asked by its author and what it is about; the title the model gave was not found
        guidance += (" The visitor asked for a book by its author and what it is about; we could not find it by the title as read."
                     " The books in facts are this author's books in stock: if one of them is the book described (judge by its"
                     " title and summary), say plainly that we have it and show it. If none is, say that this book is not here"
                     " and offer his books in facts instead. Never call a book unavailable that is in facts."
                     if language == "en" else
                     " الزائر سأل عن كتاب بمؤلفه وموضوعه، وما وجدناه بالعنوان كما فُهم. كتب facts هي كتب هذا المؤلف المتوفرة عندنا:"
                     " إذا كان أحدها هو الكتاب الموصوف (احكم من عنوانه ونبذته) فقل بوضوح إنه متوفر واعرضه. وإذا ما كان أحدها هو،"
                     " فقل إن هذا الكتاب غير موجود عندنا واعرض كتبه الموجودة في facts بدله. لا تقل عن كتاب في facts إنه غير متوفر.")
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
    if it.get("omnibus27") and it["omnibus27"]["id"] in all_books:
        # QADFIX27 #3 (QAD 2026-11-05 case 3.1, 2026-11-04 case 50)
        _ob27 = all_books[it["omnibus27"]["id"]]
        _pt27 = it["omnibus27"]["parts"]
        guidance += ((f" The visitor asks about a part of a series: we have «{_ob27['title']}» by {author_flow.display(_ob27.get('author'))},"
                      " one volume in stock that holds its parts (its summary in facts says which)"
                      + (f", among them {', '.join('«' + p + '»' for p in _pt27)}" if _pt27 else "") + ". Say that first: the part asked"
                      " about is inside this same volume; never say that part is not in stock or that no other part is recorded. A book with"
                      " a similar title by another author is not that part: never present it as such.")
                     if language == "en" else
                     (f" الزائر يسأل عن جزء من سلسلة: عندنا «{_ob27['title']}» لـ{author_flow.display(_ob27.get('author'))}، مجلد واحد متوفر"
                      " يضم أجزاءها (نبذته في facts تذكرها)" + (f"، ومنها {'، '.join('«' + p + '»' for p in _pt27)}" if _pt27 else "")
                      + ". قل ذلك أولًا: الجزء اللي يسأل عنه داخل هذا المجلد نفسه، ولا تقل إن هذا الجزء غير متوفر ولا إن ما عندنا جزء"
                      " مسجّل. وأي كتاب بعنوان قريب لمؤلف آخر ليس هو الجزء المقصود: لا تقدّمه على أنه هو."))
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
    # QADFIX27 #8 (QAD 2026-11-04 case 40.1): «فيه نسخة فرنسية من الغريب؟» → «ما عندي تأكيد عن توفر نسخة فرنسية» — the language of
    # our copies is recorded (books.language): a copy in a language we do not hold is a plain «no»
    _el27 = re.search(r"(?:^| )(?:بال|ال|ب)?(فرنسي|فرنسيه|فرنساوي|الماني|المانيه|اسباني|اسبانيه|تركي|تركيه|روسي|روسيه|ايطالي|ايطاليه"
                      r"|french|german|spanish|turkish|russian|italian)(?: |$)", " ".join(normalize(re.sub(r"[؟?!.،,]", " ", user_messages[-1])).split()))
    if _el27 and not (_el27.group(0).strip().startswith("بال") or re.search(
            r"نسخه|نسخ|طبعه|ترجمه|edition|copy|version|لغه|language", normalize(user_messages[-1]))):
        _el27 = None   # «رواية تركي الحمد» is an author's name, not a language
    if _el27 and asked_books and not author_only:
        _code27 = {"فرن": "fr", "الم": "de", "اسب": "es", "ترك": "tr", "روس": "ru", "ايط": "it", "fre": "fr", "ger": "de", "spa": "es",
                   "tur": "tr", "rus": "ru", "ita": "it"}.get(_el27.group(1)[:3])
        _have27 = [b for b in asked_books + [x for x in other_form if x not in asked_books] if is_available(b)]
        if _code27 and _have27 and not any((b.get("language") or "ar") == _code27 for b in _have27):
            _ln27 = {"ar": ("Arabic", "بالعربية"), "en": ("English", "بالإنجليزية")}
            _langs27 = sorted({(b.get("language") or "ar") for b in _have27})
            guidance += ((f" The visitor asks for a copy in another language: none of our copies of «{_have27[0]['title']}» is in that language"
                          f" (our records give each copy's language). Say plainly no, we have no copy in that language; the copies in facts are"
                          f" {', '.join(_ln27.get(l, (l, l))[0] for l in _langs27)}.")
                         if language == "en" else
                         (f" الزائر يسأل عن نسخة بلغة أخرى: ما عندنا من «{_have27[0]['title']}» نسخة بهذه اللغة (لغة كل نسخة مسجّلة عندنا)."
                          f" قل بوضوح: لا، ما عندنا نسخة بهذه اللغة؛ والنسخ اللي في facts {' و'.join(_ln27.get(l, (l, l))[1] for l in _langs27)}."
                          " ولا تقل إنك ما تقدر تتأكد."))
    if pprefs.get("audience") in AGE_OK and CLEAN_YOUNG in (pprefs.get("avoid") or ()):
        # QADFIX26 #1 (QAD 2026-11-03 case 17): every book in facts passed clean_young_ok — never «I can't confirm it is clean»
        guidance += ((" The reader is young and the visitor wants a book with nothing for adults: every book in facts is from the young"
                      " readers' or children's shelf (or its recorded age range fits the reader) and our records list no sexual content"
                      " for it. Say that simply (\"from our young readers' shelf, nothing for adults in its record\"); never say you"
                      " cannot confirm it is clean, and never offer a book that is not in facts. If facts has no book, say plainly you found"
                      " none that fits both the request and the reader's age, and offer books from the young readers' shelf instead.")
                     if language == "en" else
                     (" القارئ صغير والزائر يبغى كتابًا ما فيه شي للكبار: كل كتب facts من رف اليافعين أو الأطفال (أو عمرها المسجّل يناسبه)"
                      " وما في سجلاتها أي تنبيه لمشاهد جريئة. قل ذلك ببساطة (مثل «من رف اليافعين، وما في سجله تنبيه لمحتوى للكبار»)،"
                      " ولا تقل أبدًا إنك ما تقدر تؤكد خلوّه، ولا تعرض كتابًا ليس في facts. وإن ما كان في facts كتاب فقل بصراحة إنك ما لقيت"
                      " كتابًا يجمع طلبه ويناسب عمره، واعرض عليه كتبًا من رف اليافعين بدلًا منه."))
    elif pprefs.get("audience") in AGE_OK:
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
    for ref, in_stock, found, outside in ([] if _ref_country_books else ref_notes):
        title = ref["title"]
        about = outside_books.brief(outside) if outside else None
        if about:
            # what we found outside about the visitor's book may be said, with its source; never as our stock
            src = " و".join(about.get("sources") or []) or "مصادر عامة"
            guidance += ((f" About «{title}» (not in our stock) we found outside ({', '.join(about.get('sources') or [])}): "
                          + json.dumps(about, ensure_ascii=False) + ". You may say in one short sentence who wrote it and what kind of book it is,"
                          " without naming the source unless the visitor asks where it comes from (QADFIX17); never present it as available here, and never add anything that is not in this record.")
                         if language == "en" else
                         (f" عن «{title}» (غير موجود عندنا) لقينا من {src}: " + json.dumps(about, ensure_ascii=False)
                          + ". تقدر تقول في جملة قصيرة مين كتبه ووش نوعه، ولا تذكر المصدر («حسب Goodreads / ويكيبيديا») إلا إذا سأل الزائر من وين المعلومة (QADFIX17)، ولا تقدّمه كأنه متوفر عندنا،"
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
    if _ref_country_books:
        _rt = "، ".join("«" + n[0]["title"] + "»" for n in ref_notes)
        _cn = "، ".join(country[1][:3])
        guidance += ((f" {_rt}: in our stock (never say otherwise), but none of our books like it is by an author from {_cn}. The books"
                      " in facts are by authors from there: say that in one short sentence and offer them as books by authors from there,"
                      " not as similar ones; ask no question about what the visitor meant.")
                     if language == "en" else
                     (f" {_rt} موجود عندنا (لا تقل غير ذلك)، لكن ما عندنا كتاب يشبهه لمؤلف من {_cn}. كتب facts لمؤلفين من هناك: قل ذلك"
                      " في جملة قصيرة واعرضها على أنها لمؤلفين من هناك، لا على أنها تشبهه، ولا تسأله وش يقصد."))
    if ref_unmatched_only:
        title = ref_notes[0][0]["title"]
        guidance = (f"The visitor wants a book like «{title}», and we do not know enough about that book to find a similar one among ours. "
                    "Say so honestly in one sentence; do not describe or guess anything about that book; show no books (show_book_ids empty). "
                    "Then ask one question: what did they like in it (its topic? its style? its kind of book?). "
                    "In suggestions give 5 short, concrete answers they could tap, each naming a different kind of book or reading (a genre, a mood or a subject), written in the visitor's voice; never just «its topic» or «its style»."
                    if language == "en" else
                    f"الزائر يبغى كتابًا يشبه «{title}»، وما نعرف عن هذا الكتاب ما يكفي عشان نلقى له شبيهًا بين كتبنا: "
                    "قل ذلك بصراحة في جملة واحدة، ولا تصف الكتاب ولا تخمّن عنه شيئًا، ولا تعرض أي كتاب (show_book_ids فارغة). "
                    "ثم اسأل سؤالًا واحدًا: وش اللي عجبه فيه (موضوعه؟ أسلوبه؟ نوعه؟). "
                    "وفي suggestions ضع 5 إجابات قصيرة ومحددة يقدر يضغطها، كل وحدة تسمّي نوع كتب أو قراءة مختلف (تصنيف أو جو أو موضوع) بصوت الزائر، ولا تكتب «موضوعه» أو «أسلوبه» وحدها.")
    if _like_last:
        guidance += (f" The visitor has just seen «{_like_last}» and asks for another book like it: do not show or describe «{_like_last}» itself again; start with the books like it."
                     if language == "en" else
                     f" الزائر شاف «{_like_last}» للتو ويطلب كتابًا ثانيًا يشبهه: لا تعرض «{_like_last}» نفسه ولا تعيد وصفه، وابدأ بالكتب الشبيهة مباشرة.")
    if similar_author and own_extra and not own_shown:
        guidance += (f" After the similar books say in one sentence that we also have {author_name}'s own books in facts (their reason says so); never say his books are not in stock."
                     if language == "en" else f" بعد الكتب المشابهة قل في جملة إن عندنا أيضًا من كتب {author_name} نفسه ما في facts (سببها يقول ذلك)، ولا تقل أبدًا إن كتبه غير متوفرة.")
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
                            else (" None of our researched books is close enough to his style: say so plainly in one sentence, then offer his own books in facts (their reason says they are his)."
                                  if own_extra else
                                  " None of our researched books is close enough: say so plainly, show no books, and ask what they enjoy in that author's books (e.g. horror, mystery, humour, short fast reads).")))
        else:
            guidance += (f" الزائر يبغى كتبًا تشبه كتابات «{author_name}»."
                         + ("؛ الكتب المختارة للشبه موجودة في facts، وسبب كل كتاب يوضح وين الشبه ووين الاختلاف. قل الشبه بصراحة (في الجو أو النوع، مو نفس الأسلوب تمامًا) واذكر الاختلاف إن وُجد" + ("." if own_shown or own_extra else "، ولا تتكلم عن كتب المؤلف نفسه.") if similar_found
                            else ("؛ ما لقينا بين كتبنا المبحوثة شيئًا قريبًا من أسلوبه بما يكفي: قل ذلك بصراحة في جملة، ثم اعرض كتبه نفسه اللي في facts (سببها يقول إنها من كتبه)."
                                  if own_extra else
                                  "؛ ما لقينا بين كتبنا المبحوثة شيئًا قريبًا بما يكفي: قل ذلك بصراحة، لا تعرض كتبًا، واسأله وش يعجبه في كتبه (الرعب؟ الغموض؟ السخرية؟ القِصر والسرعة؟).")))
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
    # QADFIX20 (QAD 2026-10-25, case 21.3; AGY-20261025-01 d): «تحب الألغاز والتحقيق» — «ألغاز» found its books by subject
    # («لغز المدرسة»، «لغز كرة القدم»), so it is not «unconfirmed»; and a book whose summary shows it is of the kind
    # («يكشفه محققان صغيران») is offered as one: the model reads the summary, the shelf label is only what we could not filter on
    _relaxed_said = [x for x in relaxed_subgenres if x not in topics_found]
    if _relaxed_said:
        named = "، ".join(_relaxed_said)
        guidance += ((f" No available book is classified as «{named}» within the rest of the request" + (" (" + ", ".join(localize_reason(x) for x in labels) + ")" if labels else "")
                      + ". The books in facts keep every other wish: offer them as suggestions. A book whose summary or reason shows it is «" + named + "» or something that means it is offered as one without a hedge; say you cannot confirm it only of a book whose summary does not show it. Do not say we have nothing.") if language == "en" else
                     (f" ما لقينا كتابًا متوفرًا مصنّفًا «{named}» ضمن باقي الطلب" + (" (" + "، ".join(labels) + ")" if labels else "")
                      + f". الكتب في facts تلتزم بباقي الطلب: اعرضها كاقتراح. الكتاب اللي توضّح نبذته أو سببه أنه «{named}» أو ما يدل عليه قدّمه على أنه منه بلا تحفظ، وقل «ما أقدر أؤكد» فقط عن كتاب ما توضّح نبذته ذلك. لا تقل إنه ما عندنا شيء."))
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
    # QADFIX20 (case 21.3): a shelf label we could not filter on is already said above (read by the summary), not again as
    # «I found no book about it» beside the books whose summary is about it
    _unsup_said = [u for u in unsupported if u not in (relaxed_subgenres or ())]
    if _unsup_said:
        guidance += ((" The search cannot reliably apply these wishes: " + ", ".join(_unsup_said) + ". Do not claim the catalog lacks this data."
                      " If one of them is a subject (what the book should be about) and no book in facts is clearly about it by its summary or subjects, say plainly first that you found no book about it here, then offer the books in facts as what they are, never as being about it.") if language == "en" else
                     " البحث لا يستطيع تطبيق هذه الرغبات بدقة: " + "، ".join(_unsup_said) + ". لا تقل إن بياناتها غير موجودة في المكتبة."
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
        # QADFIX18 (inbox 2026-10-24): counted among the books that meet every wish in force (a relaxed search widens the pool,
        # not the request); the whole pool only when none does
        _cnt_pool = [b for b in pool if b["book_id"] in strict] or pool
        for b in _cnt_pool:
            labels_ = honors.matches_award(b["book_id"], prefs["awards"], prefs.get("award_year"),
                                           prefs.get("award_min_year"), prefs.get("award_max_year"), prefs.get("award_results"))
            k = (normalize(b["title"]), normalize(b.get("author")))
            _kinds["book" if any(x.startswith("فاز") for x in labels_) else "author" if any(x.startswith("مؤلفه فاز") for x in labels_) else "list"].add(k)
        _kinds["author"] -= _kinds["book"]
        _kinds["list"] -= _kinds["book"] | _kinds["author"]
        _n_people = len({author_flow.display(b.get("author")) for b in _cnt_pool})
        # QADFIX18 (inbox 2026-10-24, «أبي جائزة للرواية» → «كتابان»): the counts are within the visitor's other wishes in force
        # (a mystery, Arabic copies ...); the whole library's own winners of the same shelf are given beside them, so a small
        # number is said as «within what you asked», never as all the prize books we have
        _gw = wanted_genres(prefs, pprefs)
        _lib_won = {(normalize(b["title"]), normalize(b.get("author"))) for b in all_books.values()
                    if is_available(b) and (not _gw or b.get("genre") in _gw)
                    and any(x.startswith("فاز") for x in honors.matches_award(b["book_id"], prefs["awards"], prefs.get("award_year"),
                                                                               prefs.get("award_min_year"), prefs.get("award_max_year"),
                                                                               prefs.get("award_results")))}
        # QADFIX27 #1 (QAD 2026-11-04 cases 8.1–8.3, grave; 2026-11-05 cases 1.1–1.2; open since 11-01): «رواية مؤلفتها نفسها حازت
        # البوكر العربية» → «أبي بس اللي فازت فيها امرأة» → «ما عندكم رواية ثانية فازت فيها كاتبة غير بريد الليل؟» was answered «إيه،
        # عندنا ٢٦ كتابًا فاز مؤلفه» (the counts were of every author), and «جنس المؤلفين مو مسجّل عندنا» was said. With women authors
        # asked for, the counts are of the books our author records give to a woman (book_author_gender), over the whole stock
        _women27 = (any(women_wish(t, _ttl35) for t in user_messages[-3:]) and not author_only) or bool(q7.get("women"))
        if _women27:
            q7["women_award27"] = women_award_counts(all_books, prefs, _gw)
        if _n_people > 1 and not _women27:
            nb, na, nl = len(_kinds["book"]), len(_kinds["author"]), len(_kinds["list"])
            guidance += ((f" In stock now for this prize wish: {nb} books that won it themselves, {na} more whose author won it, {nl} more that were only shortlisted / longlisted / nominated ({_n_people} authors in all). facts hold only some: when asked who / which / how many / what else, start with the number that fits the question (a «who won» / «which winners» question: the books that won it themselves), then name a few as examples and offer more; never call the books in facts all of them or count only those (QADFIX14 م1).")
                         if language == "en" else
                         f" المتوفر عندنا الآن لطلب الجائزة: {fmt_num(nb)} كتابًا فاز هو نفسه، و{fmt_num(na)} كتابًا فاز مؤلفه، و{fmt_num(nl)} كتابًا وصل للقوائم أو رُشّح فقط (لـ{fmt_num(_n_people)} مؤلفًا). facts فيها بعضها فقط: إذا سأل «مين» أو «أي» أو «كم» أو «وش غيره» فابدأ بالعدد المناسب لسؤاله (سؤال «وش فاز» = الفائزة هي نفسها) ثم اذكر بعضها أمثلة واعرض أن تذكر المزيد؛ لا تقل إن كتب facts هي كلها ولا تعدّها وحدها.")
            if nb == 0 and nl:
                # QADFIX25 (QAD 2026-10-31, case 27.1): «الكتب اللي أخذت آرثر سي كلارك؟» — none won it itself; «فرانكشتاين في بغداد»
                # (shortlisted 2019) was left out for books whose authors won, and the reply named the work an author won for from
                # memory («عن سكة حديدية تحت الأرض») while saying no book here won it
                guidance += (" None of our books won this prize itself: first offer the books that were themselves shortlisted / longlisted /"
                             " nominated (as their awards say in facts), then books whose author won it. Never say which work an author"
                             " won it for unless facts write that work." if language == "en" else
                             " ما عندنا كتاب فاز بهذه الجائزة هو نفسه: اعرض أولًا الكتب التي وصلت هي نفسها لقوائمها أو رُشّحت لها (كما في"
                             " awards في facts)، ثم كتب مؤلفين فازوا بها. ولا تقل عن أي عمل إن مؤلفه فاز عنه إلا إذا كان ذلك العمل مكتوبًا في facts.")
        if 1 <= len(_kinds["book"]) <= 6 and _kinds["author"] and not _women27:
            # QADFIX30 m7 (QAD 2026-11-07 case 24.1, medium): «من فايزين البوليتزر، فيه شي من عقد الثمانينات؟» → «عندنا كتابان… منها «اللون
            # الأرجواني»» and «The Bluest Eye» (its author won for «Beloved»), while «Beloved» itself was in facts: the winners themselves
            # are named as the answer, before any book whose author won
            _own30, _seen30 = [], set()
            _en30 = []
            for b in _cnt_pool:
                _k30 = (normalize(b["title"]), normalize(b.get("author")))
                if _k30 in _kinds["book"] and _k30 not in _seen30:
                    _seen30.add(_k30)
                    _own30.append(b["title"])
                    if ((b.get("language") or "ar").strip() or "ar") == "en":
                        _en30.append(b["title"])
            if _own30:
                # QADFIX34 #12 (QAD 2026-11-11 case 25.2, minor; replay): «مزرعة الحيوانات (١٩٤٦، نسختها بالإنجليزية)» — an Arabic copy was
                # called English: «(and the English copy among them too, saying it is in English)» was said with no English copy in the
                # list; it now names the English copies, or says every copy listed is in Arabic
                _ennote = ((" (" + ", ".join(f"«{t}»" for t in _en30) + " is an English copy: say so)") if _en30 and language == "en"
                           else " (every copy listed is in Arabic)" if language == "en"
                           else (" (" + "، ".join(f"«{t}»" for t in _en30) + " نسخة إنجليزية: قل ذلك عنها وحدها)") if _en30
                           else " (كل النسخ المذكورة عربية: لا تقل عن أي منها إنها بالإنجليزية)")
                guidance += ((" The books that won it themselves within the request: " + ", ".join(f"«{t}»" for t in _own30)
                              + ". They are the answer: name each of them first" + _ennote + "; a book whose"
                              " author won comes only after them, said as such.")
                             if language == "en" else
                             (" الكتب اللي فازت هي نفسها ضمن طلبه: " + "، ".join(f"«{t}»" for t in _own30)
                              + ". هي الجواب: سمّها كلها أولًا" + _ennote + "، وأي كتاب فاز مؤلفه يأتي بعدها"
                              " ويُقال عنه ذلك."))
        if ((it.get("newest_award33") or it.get("list_all33") or it.get("oldest_award35") or it.get("list_all_prev35"))
                and _kinds["book"] and not _women27):
            # QADFIX33 #3 (QAD 2026-11-10 cases 33.1–33.2, grave; 32.1–32.2, medium): «وش أحدث رواية فائزة؟» → «عندنا كتابان» (a span
            # of recent years hid five of the seven Goncourt winners); «سمّها كلها» → two named and «أقدر أذكر المزيد». The books that won
            # it themselves within the request, by the year they won, newest first: the newest is the answer; with the whole list asked,
            # every one of them is named
            _own33, _seen33 = [], set()
            for b in _cnt_pool:
                _k33 = (normalize(b["title"]), normalize(b.get("author")))
                if _k33 in _kinds["book"] and _k33 not in _seen33:
                    _seen33.add(_k33)
                    _own33.append((b, own_win_year33(b["book_id"], prefs["awards"])))
            # (replay 33.2: «الفائزة … ٥ كتب» — the two shown the turn before were not in the pool of new options: the whole list counts
            # every winner in stock that meets the hard wishes, shown or not)
            for b in sorted(all_books.values(), key=lambda b: b["book_id"]):
                _k33 = (normalize(b["title"]), normalize(b.get("author")))
                if (_k33 not in _seen33 and is_available(b) and b.get("active", 1) and all(h(b) for h in hard)
                        and (not _gw or (b.get("genre") or "").strip() in _gw or _novel_prize_ok(b, prefs, _gw))
                        and any(x.startswith("فاز") for x in honors.matches_award(b["book_id"], prefs["awards"], prefs.get("award_year"),
                                                                                   prefs.get("award_min_year"), prefs.get("award_max_year"),
                                                                                   prefs.get("award_results")))
                        and own_win_year33(b["book_id"], prefs["awards"])):
                    _seen33.add(_k33)
                    _own33.append((b, own_win_year33(b["book_id"], prefs["awards"])))
            _old35 = bool(it.get("oldest_award35"))
            if _old35:
                # QADFIX35 #1 (case 28): the oldest asked — by the earliest year each one won, oldest first
                _own33 = [(b, own_win_year35(b["book_id"], prefs["awards"]) or y) for b, y in _own33]
                _own33.sort(key=lambda x: (x[1] or 9999, x[0]["book_id"]))
            else:
                _own33.sort(key=lambda x: (-(x[1] or 0), x[0]["book_id"]))
            it["own_winners33"] = [b["book_id"] for b, _y in _own33]
            _ol33 = ([f"«{b['title']}» ({y})" if y else f"«{b['title']}»" for b, y in _own33] if language == "en" else
                     [f"«{b['title']}» ({fmt_num(y)})" if y else f"«{b['title']}»" for b, y in _own33])
            _n33 = len(_own33)
            if it.get("list_all33"):
                # (the general count rule «name a few as examples and offer more» gives way to the whole list)
                guidance = (guidance.replace("then name a few as examples and offer more", "then name them all as below")
                            .replace("ثم اذكر بعضها أمثلة واعرض أن تذكر المزيد", "ثم سمّها كلها كما يلي"))
                # QADFIX34 #3 (QAD 2026-11-11 case 3.2, medium): «سمّهم كلهم» → «عندنا ١٠ روايات… وأقدر أذكر لك منها» and 8 named — the count
                # opening the guidance («٨ كتابًا فاز هو نفسه», the new-options pool) contradicted the whole list (10): one number, the list's
                guidance = re.sub(r"(المتوفر عندنا الآن لطلب الجائزة: )\S+( كتابًا فاز هو نفسه)", lambda m: m.group(1) + fmt_num(_n33) + m.group(2), guidance)
                guidance = re.sub(r"(In stock now for this prize wish: )\d+( books that won it themselves)", lambda m: m.group(1) + str(_n33) + m.group(2), guidance)
                it["list_all34"] = [(b["book_id"], y) for b, y in _own33]
                guidance += ((f" The visitor asks for the whole list: the {_n33} books in stock that won it themselves within the request, "
                              + ("oldest" if _old35 else "newest") + f" prize first: {', '.join(_ol33)}. This is the right count of the whole list ({_n33}, the ones shown before"
                              " included). Name every one of them in the reply (title and the year it won), in this"
                              " order, in a short plain sentence or two; never stop at some of them and never offer to name more. The cards"
                              " under the reply show only some of them; that is fine.")
                             if language == "en" else
                             (f" الزائر طلب القائمة كلها: الكتب المتوفرة اللي فازت هي نفسها ضمن طلبه {fmt_num(_n33)}، من "
                              + ("الأقدم" if _old35 else "الأحدث") + " فوزًا: "
                              + "، ".join(_ol33) + f". هذا هو العدد الصحيح للقائمة كلها ({fmt_num(_n33)}، بما فيها ما عُرض قبل). سمّها كلها في الرد"
                              " بهذا الترتيب (العنوان وسنة الفوز) في جملة أو جملتين، ولا تكتفِ ببعضها ولا تعرض أن تذكر المزيد؛ البطاقات تحت"
                              " الرد تعرض بعضها فقط، وهذا طبيعي."))
            elif _old35:
                # QADFIX35 #1 (QAD 2026-11-12 cases 28.1–28.3, grave): «ابي أقدمها» / «متأكد إنها أقدم وحدة؟» — the oldest by the year the book
                # itself won the prize (book_awards), never by the year of our edition; the whole count, never «none» or «two»
                _b35, _y35 = _own33[0]
                guidance += ((f" The visitor asks for the oldest: the {_n33} books in stock that won it themselves within the request, by the"
                              f" year they won, oldest first: {', '.join(_ol33[:4])}{' …' if _n33 > 4 else ''}. The oldest is «{_b35['title']}»"
                              f" (won in {_y35}): answer with it first and that year, then the next ones with theirs; say we have {_n33} winners"
                              " in stock (never none, never fewer). These are the years of the prize in our records, not of our editions:"
                              " never order them or doubt them by an edition's year, and never call another one the oldest.")
                             if language == "en" else
                             (f" الزائر سأل عن الأقدم: الكتب المتوفرة اللي فازت هي نفسها ضمن طلبه {fmt_num(_n33)}، مرتبة حسب سنة فوزها من"
                              f" الأقدم: " + "، ".join(_ol33[:4]) + ("…" if _n33 > 4 else "") + f". الأقدم فوزًا هو «{_b35['title']}»"
                              f" (فاز سنة {fmt_num(_y35)}): ابدأ به وبسنة فوزه، ثم اللي بعده بسنواتها، وإذا ذكرت العدد فهو {fmt_num(_n33)}"
                              " (لا تقل إنه ما عندنا ولا أقل من ذلك). هذه سنوات الفوز من سجل الجائزة عندنا، لا سنوات طبعاتنا: لا ترتّبها ولا"
                              " تشكك فيها بسنة طبعة، ولا تقل عن غيره إنه الأقدم."))
            elif it.get("list_all_prev35") and not it.get("newest_award33"):
                # QADFIX35 (QAD 2026-11-12 case 3.2, medium): «وش السابع اللي قلت ما عندك تفاصيله؟» → «ذكري لـ«الفن الهندي» كان خطأ» (a true winner,
                # 2007): the whole list given the turn before, with each one's year; none is a mistake
                guidance += ((f" The whole list given the turn before: the {_n33} books in stock that won it themselves within the request, newest"
                              f" prize first: {', '.join(_ol33)}. Each of them really won it (our records; each is in facts with its prize): when he"
                              " asks about one of them (\"the seventh\", \"the last one\"), answer about it from facts; never call any of them a"
                              " mistake and never say you have no details of it.")
                             if language == "en" else
                             (f" القائمة كلها اللي ذُكرت له في الدور السابق: الكتب المتوفرة اللي فازت هي نفسها ضمن طلبه {fmt_num(_n33)}، من الأحدث"
                              " فوزًا: " + "، ".join(_ol33) + ". كلها فازت فعلًا حسب سجلاتنا، وكل واحد منها في facts بجائزته: إذا سأل عن واحد"
                              " منها («السابع» أو «الأخير» مثلًا) فأجب عنه من facts، ولا تقل عن أي منها إنه خطأ ولا إن ما عندك تفاصيله."))
            else:
                guidance += ((f" The visitor asks for the newest: the {_n33} books in stock that won it themselves within the request, by the year"
                              f" they won, newest first: {', '.join(_ol33[:4])}{' …' if _n33 > 4 else ''}. Answer with the newest one and its"
                              f" year first; say we have {_n33} winners in stock (never fewer).")
                             if language == "en" else
                             (f" الزائر سأل عن الأحدث: الكتب المتوفرة اللي فازت هي نفسها ضمن طلبه {fmt_num(_n33)}، مرتبة حسب سنة فوزها من"
                              f" الأحدث: " + "، ".join(_ol33[:4]) + ("…" if _n33 > 4 else "") + ". ابدأ بالأحدث وسنة فوزه، وإذا ذكرت العدد"
                              f" فهو {fmt_num(_n33)} (لا أقل)."))
                # (replay 33.1: «أحدث رواية فائزة عندنا هي «ذاكرة البشر…» ٢٠٢١» — the model passed over «حياة متعجلة» (2022), filed on the
                # autobiography shelf: the newest is named, and a prize given to novels makes it an answer whatever its shelf)
                _b33, _y33 = _own33[0]
                _g33 = (_b33.get("genre") or "").strip()
                _off33 = bool(prefs.get("genres")) and _g33 and _g33 not in set(prefs.get("genres") or ())
                guidance += ((f" The newest is «{_b33['title']}» ({_y33}): answer with it first"
                              + (f" (it is on our «{_g33}» shelf, but it won this prize itself, so it answers his question; you may say its shelf)" if _off33 else "")
                              + "; never call another one the newest.")
                             if language == "en" else
                             (f" الأحدث فوزًا هو «{_b33['title']}» ({fmt_num(_y33)}): ابدأ به"
                              + (f" (رفّه عندنا «{_g33}»، لكنه فاز بالجائزة نفسها فهو جواب سؤاله؛ لك أن تذكر رفّه)" if _off33 else "")
                              + "، ولا تقل عن غيره إنه الأحدث."))
        # (QADFIX33, replay 3 of fresh 333.2 «سمّهم كلهم»: «في المكتبة ١٠ … و٨ منها ضمن شروط بحثك» — the two missing were only the ones shown
        # the turn before; with the whole list asked and counted above, this second count is not given)
        _skip33 = bool(it.get("list_all33") and len(it.get("own_winners33") or ()) >= len(_lib_won))
        if len(_lib_won) > len(_kinds["book"]) and not _skip33 and _n_people > 1 and not _women27:
            guidance += ((f" Those counts are within his other wishes now in force (subgenre, mood, copy language, country ...): {len(_kinds['book'])}"
                          f" won it themselves within them. Without those wishes, the whole library has {len(_lib_won)} available books of this kind"
                          f" that won it themselves. Keep the two apart: never say {len(_lib_won)} fit his request; when the number within his request"
                          " is small, say it is within what he asked and offer to widen the search.")
                         if language == "en" else
                         f" هذه الأعداد ضمن شروطه الأخرى الحالية (النوع الفرعي، المزاج، لغة النسخة، البلد...): {fmt_num(len(_kinds['book']))} فاز هو نفسه ضمنها."
                         f" وبدون هذه الشروط، في المكتبة كلها {fmt_num(len(_lib_won))} كتابًا متوفرًا من هذا النوع فاز هو نفسه. لا تخلط بينهما: لا تقل إن"
                         f" {fmt_num(len(_lib_won))} تناسب طلبه؛ وإذا كان عدد ما يناسب طلبه قليلًا فقل إنه ضمن طلبه، واعرض عليه توسيع البحث.")
    if prefs.get("award_max_year") or prefs.get("award_years_said"):
        _span = ((f"from {prefs['award_min_year']} " if prefs.get("award_min_year") else "") + (f"until {prefs['award_max_year']}" if prefs.get("award_max_year") else "")).strip()
        _span_ar = ((f"من {prefs['award_min_year']} " if prefs.get("award_min_year") else "") + (f"حتى {prefs['award_max_year']}" if prefs.get("award_max_year") else "")).strip()
        guidance += ((f" The visitor asked for prizes {_span}: the books in facts are in that span; give each prize's year.")
                     if language == "en" else f" الزائر طلب جوائز {_span_ar}: كتب facts جوائزها ضمن هذه السنوات؛ اذكر سنة كل جائزة.")
    if it.get("award_branch27") or it.get("award_branch27_none"):
        # QADFIX27 #5 (QAD 2026-11-05 cases 29.1–29.2)
        _br27 = it.get("award_branch27") or it.get("award_branch27_none")
        guidance += (((f" The visitor asked for the prize's branch «{_br27}»: every book in facts won it in that branch (its awards say the"
                       " branch); name the branch and the year. Never say you have no details of the branch.") if it.get("award_branch27") else
                      (f" No book in stock won this prize in the branch «{_br27}» by our records: say so plainly, then offer the winners of its"
                       " other branches in facts with their branch (as their awards say)."))
                     if language == "en" else
                     ((f" الزائر طلب فرع «{_br27}» من الجائزة: كل كتب facts فازت في هذا الفرع (awards فيها تذكر الفرع)؛ اذكر الفرع"
                       " والسنة، ولا تقل إنه ما عندك تفاصيل عن فرع الجائزة.") if it.get("award_branch27") else
                      (f" ما عندنا كتاب متوفر فاز بهذه الجائزة في فرع «{_br27}» حسب سجلاتنا: قل ذلك بصراحة، ثم اعرض الفائزين في فروعها"
                       " الأخرى من facts مع فرع كل واحد (كما في awards).")))
    if honor_wish:
        named = "، ".join(honor_wish)
        # QADFIX32 (QAD 2026-11-09 case 6.1, minor): «هل إبراهيم الكوني أخذ جائزة نوبل؟» → «لا تتوفر لدينا معلومات … ولم نجد كتابًا
        # متوفرًا له صلة بها» with no book, while his books are in stock: for an author asked about with a linked record, the Nobel rows
        # (the official nobelprize.org list matched to our authors) are a sourced «not in our list» (AUDFIX 2026-11-08), and his books
        # are named — not the Nobel books of other writers
        _nob32, _arec32 = [], _arec
        _nobq32 = ("nobel" in (prefs.get("awards") or ()) or any("نوبل" in str(h) for h in honor_wish)) and re.search(
            r"نوبل|nobel", normalize(user_messages[-1]).lower())
        if _nobq32 and not _arec32:
            # the author the visitor names in the message itself, when the interpreter did not mark an author question (replay: deepseek)
            _w32 = re.sub(r"[؟?!.،,«»\"]", " ", user_messages[-1]).split()
            for _n32 in (3, 2):
                _arec32 = next((r for r in (authors.lookup(" ".join(_w32[k:k + _n32]), spelling=False)
                                            for k in range(len(_w32) - _n32 + 1)) if r), None)
                if _arec32:
                    break
        _nostock33 = False
        if _nobq32 and _arec32 and _arec32.get("author_id"):
            _arec = _arec32
            _aids32 = authors.book_ids(_arec["author_id"])
            _nob32 = sorted((b for b in all_books.values() if b["book_id"] in _aids32 and is_available(b)),
                            key=lambda b: -(b.get("quantity") or 0))
            if (any(honors.matches_award(b["book_id"], ["nobel"]) for b in _nob32)
                    or any("نوبل" in str(r.get("award_name") or "") or r.get("award_key") == "nobel"
                           for r in honors.load()["author"].get(_arec["author_id"], []))):
                _nob32 = []   # a recorded Nobel (literature, or another branch: «نوبل للسلام»): the facts path below says it
            elif not _nob32:
                # QADFIX33 (QAD 2026-11-10 case 8.1, minor; back from 11-09 6.1): «عبدالرحمن منيف أخذ جائزة نوبل؟» (a linked author, none of
                # his books in stock) → «ما عندي تفاصيل مؤكدة»: the sourced «not in our list» holds with no book of his to offer too
                _nostock33 = True
        if _nostock33:
            guidance += ((f" By our records {_arec.get('name_latin') or _arec['name_ar']} is not among the Nobel laureates in our list (the official"
                          " nobelprize.org list matched to our authors): say that plainly, never «I have no details»; none of their books is in"
                          " stock now: say so too, and do not offer other writers' prize books as if they were the answer.")
                         if language == "en" else
                         (f" حسب سجلاتنا {_arec['name_ar']} ليس ضمن الفائزين بجائزة نوبل للآداب في قائمتنا (قائمة nobelprize.org الرسمية مطابقة"
                          " لمؤلفينا): قل ذلك بوضوح، ولا تقل «ما عندي تفاصيل»؛ وما عندنا من كتبه شيء متوفر الآن: قل ذلك أيضًا، ولا تعرض كتب كتّاب"
                          " آخرين على أنها الجواب."))
            it["nobel_not33"] = _arec.get("author_id")
        elif _nob32:
            _nt32 = [b["title"] for b in _nob32[:3]]
            guidance += ((f" By our records {_arec.get('name_latin') or _arec['name_ar']} is not among the Nobel laureates in our list (the official"
                          " nobelprize.org list matched to our authors): say that plainly (the prize goes to a writer for the whole of their"
                          " work), never «I have no details», and do not offer other writers' prize books instead. We have books of theirs in"
                          " stock: " + ", ".join(f"«{t}»" for t in _nt32) + " — name them by these exact titles and offer them.")
                         if language == "en" else
                         (f" حسب سجلاتنا {_arec['name_ar']} ليس ضمن الفائزين بجائزة نوبل للآداب في قائمتنا (قائمة nobelprize.org الرسمية مطابقة"
                          " لمؤلفينا): قل ذلك بوضوح (الجائزة تُمنح للكاتب عن مجمل أعماله)، ولا تقل «ما عندي تفاصيل»، ولا تعرض بدلها كتب كتّاب"
                          " آخرين فازوا بجوائز. وعندنا من كتبه متوفرًا: " + "، ".join(f"«{t}»" for t in _nt32) + " — سمّها بعناوينها هذه واعرضها عليه."))
            it["nobel_not32"] = [b["book_id"] for b in _nob32[:3]]
        elif any(f["available"] and (f.get("awards") or f.get("author_awards") or f.get("adaptations")) for f in facts):
            guidance += ((f" The visitor asked for: {honors.to_english(named)}. The matching books in facts carry it under awards / author_awards / adaptations: name the prize or the film exactly as written there, with its year.")
                         if language == "en" else
                         f" الزائر طلب: {named}. الكتب المطابقة في facts مذكور فيها ذلك (awards أو author_awards أو adaptations): اذكر الجائزة أو العمل المرئي كما هو مكتوب هناك وبسنته.")
        else:
            guidance += ((f" We found no available book recorded as: {honors.to_english(named)}. Say so plainly, add that our records of prizes and adaptations are still being completed, and ask what else they would like. Never name a prize or a film from your own knowledge.")
                         if language == "en" else
                         f" ما لقينا في سجلاتنا كتابًا متوفرًا {named}. قل ذلك بصراحة، وأضف أن سجلات الجوائز والأفلام عندنا ما زالت تُستكمل، واسأله وش يبغى بداله. لا تذكر جائزة أو فيلمًا من معرفتك.")
            open_questions.record("adaptation" if prefs.get("adaptation") and not prefs.get("awards") else "award", named)
    guidance += " Facts are a selected candidate list, NOT the entire inventory."
    if not absent_author:
        # QADFIX24 (QAD 2026-10-30, cases 10.2, 33.2): when the records already say none of the author's books is in stock, this
        # line made the model answer «ما أقدر أجزم… اسأل الموظف» instead of «ما عندنا من كتبه شيء الآن»
        guidance += " Never infer that an author's works are absent from the library from their absence here. Author metadata may be incomplete."
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
        # QADFIX28 (QAD 2026-11-05 case 22.1–22.2): «غازي القصيبي عندكم له شعر؟» → «يعني ما عندكم ولا ديوان له؟» was answered «ما أقدر
        # أؤكد لك إذا عندنا ديوان … موظف المكتبة يقدر يفيدك» while our records hold his eight books in stock, none of them poetry: a
        # kind of book asked of one author is answered from all his books in stock, not from facts
        _lm28 = " " + " ".join(normalize(re.sub(r"[؟?!.،,«»]", " ", user_messages[-1])).split()) + " "
        _kind28 = ("شعر" if re.search(r" [وب]?(?:ال)?(?:شعر|شعره|ديوان|ديوانه|دواوين|دواوينه|قصائد|قصايد|قصائده|poetry|poems?) ", _lm28)
                   else "مسرحية" if re.search(r" [وب]?(?:ال)?(?:مسرحيه|مسرحيات|مسرحياته|plays?) ", _lm28) else None)
        if _kind28 and len(prefs["authors"]) == 1 and not missing_authors and len(people) < 2:
            _rec28 = authors.lookup(prefs["authors"][0]) or authors.lookup(author_flow.display(prefs["authors"][0]))
            _his28 = ([all_books[i] for i in authors.book_ids(_rec28["author_id"]) if i in all_books and is_available(all_books[i])]
                      if _rec28 else [])
            if _his28 and not any((b.get("genre") or "").strip() == _kind28 for b in _his28):
                _kn28 = {"شعر": ("poetry collection", "ديوان شعر"), "مسرحية": ("play", "مسرحية")}[_kind28]
                _nm28 = author_flow.display(_rec28.get("name") or _rec28.get("name_ar") or prefs["authors"][0])
                guidance += ((f" By our records, all {len(_his28)} books of his in stock now are other kinds; none is a {_kn28[0]} of his."
                              f" Say plainly that we have no {_kn28[0]} by him in stock now (not «I can't confirm», not «ask a member of"
                              " staff»), then offer his books we do have as what they are.")
                             if language == "en" else
                             (f" حسب سجلاتنا، كتب {_nm28} المتوفرة عندنا الآن {fmt_num(len(_his28))} وكلها من أنواع أخرى، وما فيها {_kn28[1]} له."
                              f" قل بوضوح إنه ما عندنا {_kn28[1]} له متوفر الآن (لا «ما أقدر أؤكد» ولا «اسأل الموظف»)، ثم اعرض كتبه"
                              " المتوفرة على حقيقتها."))
                it["kind_none28"] = _kind28
    _nw_dbg = None
    if pub_years:
        lo, hi = pub_years
        _span = (f"{lo}–{hi}" if lo and hi else f"from {lo} on" if lo else f"up to {hi}")
        _span_ar = (f"من {lo} إلى {hi}" if lo and hi else f"من {lo} وبعدها" if lo else f"حتى {hi}")
        if it.get("first_publication") and not q7.get("before_none28"):
            # QADFIX12 #11: the year the work FIRST came out, never our edition's year
            guidance += ((f" The visitor wants works first published {_span} (not new editions): " + (
                            "none available is recorded as first published then; say so plainly, and never present an edition year as the year a work first came out."
                            if pub_missing else "the books in facts first came out in that span (first_published); give that year, never edition_year as the first one."))
                         if language == "en" else
                         (f" الزائر يبغى أعمالًا صدرت أول مرة {_span_ar} (مو طبعات جديدة): " + (
                            "ما عندنا كتاب متوفر مسجّل أنه صدر أول مرة ضمن ذلك؛ قل ذلك بصراحة، ولا تقدّم سنة طبعة على أنها سنة صدور العمل أول مرة."
                            if pub_missing else "كتب facts صدرت أول مرة ضمن ذلك (first_published)؛ اذكر تلك السنة، ولا تقدّم edition_year على أنها سنة الصدور الأول.")))
        elif q7.get("before_none28"):
            # QADFIX28 (QAD 2026-11-05 case 45.2): nothing of this kind before that year — said so, then the oldest we have
            _bn28 = q7["before_none28"]
            _bt28 = [all_books[i]["title"] for i in _bn28[3] if i in all_books]
            guidance += ((f" The visitor wants books of this kind published before {_bn28[0]}: none in stock is (by our records). Say that plainly"
                          f" first, then that the oldest we have is «{_bn28[1]}» ({_bn28[2]}), and offer only these, the oldest of his kind, with"
                          f" their years: «{'», «'.join(_bt28)}» — never a book of another kind or origin, and never say they came out before {_bn28[0]}.")
                         if language == "en" else
                         (f" الزائر يبغى كتبًا من هذا النوع صدرت قبل {_bn28[0]}: ما عندنا منها شيء متوفر حسب سجلاتنا. قل ذلك بوضوح أولًا،"
                          f" ثم إن الأقدم عندنا «{_bn28[1]}» ({_bn28[2]})، واعرض فقط هذه، وهي الأقدم من نوع طلبه، بسنواتها: «{'»، «'.join(_bt28)}»"
                          f" — لا كتابًا من نوع آخر ولا مترجمًا، ولا تقل إنها صدرت قبل {_bn28[0]}."))
        else:
            guidance += ((f" The visitor wants books published {_span}: " + ("none available is recorded with such a year; say so and ask what else they would like." if pub_missing
                           else "the books in facts came out in that span (first_published, or edition_year when first_published is missing); mention the year."))
                         if language == "en" else
                         (f" الزائر يبغى كتبًا منشورة {_span_ar}: " + ("ما لقينا كتابًا متوفرًا مسجلة سنة نشره ضمن ذلك؛ قل ذلك واسأله وش يبغى بداله." if pub_missing
                           else "كتب facts صدرت ضمن ذلك (first_published، أو edition_year إذا ما فيه first_published)؛ اذكر السنة.")))
        if _new_wish == "carried" and lo and not hi:
            # CHATFIX31 (3), Ibrahim 2026-11-01: the «جديدة» asked earlier still holds through this new request; the reply says
            # so in a few words («من الكتب الصادرة من ٢٠٢٣»), or, when none of these is that new, says that plainly
            guidance += ((f" The wish for new books said earlier still holds (the badge «published from {lo}» is on): "
                          + (f"none of our books for this request came out from {lo}; say so in a few words, show the cards as the"
                             " nearest ones, and say the visitor can remove «published from " + str(lo) + "» above to see all years."
                             if pub_missing else f"say in a few words that these are from the books published from {lo}."))
                         if language == "en" else
                         (f" طلب الكتب الجديدة اللي قاله الزائر قبل ما زال قائمًا (شارة «صادرة من {fmt_num(lo)}» ظاهرة): "
                          + (f"ما عندنا لهذا الطلب كتاب صادر من {fmt_num(lo)}؛ قل ذلك باختصار، واعرض البطاقات على أنها الأقرب،"
                             f" وقل إنه يقدر يشيل «صادرة من {fmt_num(lo)}» من فوق ليشوف كل السنوات." if pub_missing
                             else f"قل باختصار إنها «من الكتب الصادرة من {fmt_num(lo)}».")))
            _nw_dbg = {"from": lo, "met": not pub_missing}
    if q7.get("edition_soft"):
        # QADFIX24 (replay of case 7.1)
        _es_l, _es_t = q7["edition_soft"]
        _es = {"en": ("English", "الإنجليزية"), "ar": ("Arabic", "العربية")}.get(_es_l, (_es_l, _es_l))
        guidance += ((f" The visitor prefers copies in {_es[0]}, and none of ours in that language is about «{', '.join(_es_t)}»: say that in"
                      " a few words, then offer the books in facts on that subject, saying the language of each copy.")
                     if language == "en" else
                     (f" الزائر يفضّل نسخًا باللغة {_es[1]}، وما عندنا نسخة بهذه اللغة عن «{'، '.join(_es_t)}»: قل ذلك في كلمات قليلة، ثم اعرض"
                      " كتب facts عن الموضوع واذكر لغة نسخة كل كتاب."))
    if edition:
        _ed = {"en": ("English", "الإنجليزية"), "ar": ("Arabic", "العربية")}[edition]
        guidance += ((f" The visitor wants copies in {_ed[0]}: " + ("none is available; say so and ask what else they would like." if edition_missing
                       else "the books in facts are copies in that language."))
                     if language == "en" else
                     (f" الزائر يبغى نسخًا باللغة {_ed[1]}: " + ("ما لقينا نسخة متوفرة بهذه اللغة؛ قل ذلك واسأله وش يبغى بداله." if edition_missing
                       else "كتب facts نسخها بهذه اللغة.")))
        _asked_in = [b for b in asked_books if is_available(b)]
        # QADFIX19 (QAD 2026-10-25, case 43.1): «ترجمة عربية لكبرياء وتحامل؟» — the work may be here in that language under its
        # translated title («كبرياء وهوى», same author): those copies go to the model, which knows whether one is the same work
        _alt_ed = []
        if _asked_in and not any(((b.get("language") or "ar").strip() or "ar") == edition for b in _asked_in):
            _a0 = _asked_in[0]
            _alt_ed = [o for o in work_copies_other_titles(_a0, all_books, exclude={b["book_id"] for b in _asked_in})
                       if ((o.get("language") or "ar").strip() or "ar") == edition]
            _same_year = [o for o in _alt_ed if first_published(o) and first_published(o) == first_published(_a0)]
            # QADFIX34 #10 (QAD 2026-11-11 case 33.1, minor): «عندكم مئة عام من العزلة بالانجليزي؟» → «ما عندي تفاصيل عن توفرها بالإنجليزية»:
            # his English copies («Love in the Time of Cholera» 1985, «Chronicle of a Death Foretold» 1981) were offered as maybe the same work,
            # though their first year differs from the asked one's (1967): a copy whose recorded first year differs is not that work
            _alt_ed = (_same_year or [o for o in _alt_ed if not (first_published(o) and first_published(_a0))])[:4]
            for o in _alt_ed:
                if o["book_id"] not in {f["id"] for f in facts}:
                    _w = (f"By the same author, a copy in {_ed[0]}: it may be «{_a0['title']}» under its translated title" if language == "en"
                          else f"للمؤلف نفسه بنسخة {_ed[1]}: قد يكون «{_a0['title']}» نفسه بعنوانه المترجم")
                    facts.insert(1, book_fact(o, cards.get(o["book_id"]), _w, language))
                    reason_by_id[o["book_id"]] = (_w, "asked")
        if _alt_ed:
            _at = "، ".join(f"«{o['title']}»" for o in _alt_ed)
            guidance += ((f" Before saying we have no copy of «{_asked_in[0]['title']}» in {_ed[0]}: facts hold copies in {_ed[0]} by the same author ({_at})."
                          " If one is that same work under its translated title (you can tell from the title, the summary and first_published), say we have it in"
                          f" {_ed[0]} under that title and show it first; only if none is, say we have no copy in {_ed[0]}.")
                         if language == "en" else
                         (f" قبل أن تقول إنه ما عندنا من «{_asked_in[0]['title']}» نسخة باللغة {_ed[1]}: في facts نسخ باللغة {_ed[1]} للمؤلف نفسه ({_at})."
                          f" إذا كان أحدها هو العمل نفسه بعنوانه المترجم (تعرف ذلك من العنوان والنبذة وfirst_published)، فقل إنه عندنا باللغة {_ed[1]} بهذا العنوان"
                          f" واعرضه أولًا؛ وإذا ما كان أي منها هو، فقل إنه ما عندنا منه نسخة باللغة {_ed[1]}."))
        elif _asked_in and not any(((b.get("language") or "ar").strip() or "ar") == edition for b in _asked_in):
            # QADFIX16 (QAD 2026-10-24, case 47.1): «الأمير الصغير عندكم بالانجليزي؟» — the book asked about has no copy in that
            # language: the reply starts with that, then says which copy we have
            _t = _asked_in[0]["title"]
            guidance += ((f" We have no copy of «{_t}» in {_ed[0]}: say that plainly in the first sentence, then say the copy we have is in "
                          + ("Arabic" if edition == "en" else "English") + ".")
                         if language == "en" else
                         (f" ما عندنا من «{_t}» نسخة باللغة {_ed[1]}: قل هذا بوضوح في أول جملة، ثم قل إن نسختنا المتوفرة "
                          + ("بالعربية" if edition == "en" else "بالإنجليزية") + "."))
    if (author_only and language != "en" and not edition and candidates
            # QADFIX25 (QAD 2026-10-31, case 7.2): said once in a conversation, not again in the next turn
            and not any("نسخ إنجليزية من كتبه" in (c.get("content") or "") for c in conv if c.get("role") == "assistant")):
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
    # QADFIX19 (QAD 2026-10-25, case 11.1): «على منوال حي بن يقظان أو رسالة الغفران» — a work named as the kind wanted that is
    # itself in stock is said to be here and shown first (the reply had named only the one we lack)
    _ref_titles = {chat_rules.title_key(r.get("title") or "") for r in it.get("references") or []
                   if isinstance(r, dict) and r.get("relation") != "rejected" and (r.get("title") or "").strip()}
    if _ref_titles:
        # the other works named beside it in the same wish («حي بن يقظان أو رسالة الغفران»: only the one we lack is a reference)
        _ref_titles |= {chat_rules.title_key(t) for t in it.get("mentioned_titles") or [] if (t or "").strip()}
    _named_here = [b for b in asked_books if is_available(b) and b["book_id"] not in _cmp_ids and b["book_id"] not in _rejected_ids
                   and any(k and (chat_rules.title_key(b.get("title") or "") == k or chat_rules.title_key(b.get("title") or "").startswith(k + " "))
                           for k in _ref_titles)]
    # … one kept out as a card because the visitor wants books like it (QADFIX12 #8, «زي ظل الريح») is still said to be here
    _named_like = [b for b in asked_books if is_available(b) and b["book_id"] in _cmp_ids and b["book_id"] not in _rejected_ids
                   and any(k and (chat_rules.title_key(b.get("title") or "") == k or chat_rules.title_key(b.get("title") or "").startswith(k + " "))
                           for k in _ref_titles)]
    _named_kind_dbg = {"refs": sorted(_ref_titles), "here": [b["book_id"] for b in _named_here],
                       "like": [b["book_id"] for b in _named_like], "asked": [b["book_id"] for b in asked_books]}
    if _named_like and not _named_here and it.get("intent") != "about_book":
        _nl = "، ".join(dict.fromkeys(f"«{b['title']}»" for b in _named_like[:2]))
        guidance += ((f" {_nl}, named as the kind he wants, is itself in stock: say so in one short clause (in case he has not read it), then"
                      " offer the books like it.")
                     if language == "en" else
                     f" {_nl} اللي ذكره الزائر نموذجًا متوفر عندنا نفسه: قل ذلك في عبارة قصيرة (لو ما قرأه)، ثم اعرض الكتب اللي تشبهه.")
    if _named_here and len(_ref_titles) >= 1 and it.get("intent") != "about_book":
        _nh = "، ".join(dict.fromkeys(f"«{b['title']}»" for b in _named_here[:2]))
        guidance += ((f" The visitor named {_nh} as the kind he wants, and we have it in stock: say so in the first sentence and show it first,"
                      " then the books like it.")
                     if language == "en" else
                     f" الزائر ذكر {_nh} نموذجًا لما يبغاه، وهو متوفر عندنا نفسه: قل ذلك في أول جملة واعرضه أولًا، ثم الكتب اللي تشبهه.")
    # QADFIX19 (QAD 2026-10-25, case 35.1): «فيلم كرتون ديزني» — which studio or company made an adaptation is not recorded
    _company = (it.get("adaptation_company") or "").strip()
    if _company and prefs.get("adaptation") and not _PLATFORM.search(normalize(_company)):
        guidance += ((f" The visitor names «{_company}» as the maker of the adaptation: we do not record which studio or company made a film or series."
                      " Say that plainly in one short sentence, offer the adaptations in facts with their kind and year, and never call one of them a"
                      f" «{_company}» production.")
                     if language == "en" else
                     f" الزائر ذكر «{_company}» صانعًا للعمل المقتبس: ما نسجّل الشركة أو الاستوديو اللي أنتج الفيلم أو المسلسل. قل ذلك بوضوح في جملة"
                     f" قصيرة، واعرض الأعمال المقتبسة في facts بنوعها وسنتها، ولا تقل عن أي منها إنه من إنتاج «{_company}».")
    if _PLATFORM.search(normalize(" ".join(user_messages[-2:]))):
        # «رواية تحولت لمسلسل على نتفلكس» (QAD 2026-10-15, case 33): which platform showed a film is not stored
        guidance += (" The visitor names a streaming platform: we do not record which platform showed an adaptation."
                     " Offer the adaptations in facts with their year, say plainly that the platform is not recorded, and never claim one was on it."
                     if language == "en" else
                     " الزائر ذكر منصة عرض (مثل نتفلكس): ما نسجّل المنصة التي عُرض عليها العمل. اعرض الأعمال المقتبسة في facts بسنتها،"
                     " وقل بوضوح إن المنصة غير مسجلة عندنا، ولا تدّعِ أن عملًا عُرض عليها.")
    if country:
        _place = country_place(list(country[1]))
        if country_missing and country_has_books:
            # QADFIX18 (inbox 2026-10-24): authors from there are in stock; only the rest of the wish (a prize ...) fits none
            _nt = "، ".join(f"«{b['title']}»" for b, _l in country_near)
            guidance += ((f" The visitor asked for authors from {_place}: we do have available books by authors from there, but none of them fits the rest of the request."
                          " Say exactly that (never that no author from there is recorded)"
                          + (f"; the closest are {', '.join(chr(171) + b['title'] + chr(187) for b, _l in country_near)}, on a prize list or nominated (see why_it_matches): say what they reached, never that they won" if country_near else "")
                          + ". The other books in facts are not by authors from there: never call them that, and ask what he would like.")
                         if language == "en" else
                         (f" طلب الزائر كتّابًا من {_place}: عندنا كتب متوفرة لمؤلفين من هناك، لكن ما فيها كتاب يناسب بقية طلبه. قل ذلك بالضبط (ولا تقل إنه ما فيه مؤلف مسجّل من هناك)"
                          + (f"؛ وأقربها {_nt}، وصلت قائمة جائزة أو رُشّحت (انظر why_it_matches): قل ما وصلت إليه، ولا تقل إنها فازت" if country_near else "")
                          + ". وبقية كتب facts ليست لمؤلفين من هناك: لا تنسبها لهم، واسأله وش يناسبه."))
        elif country_missing:
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
            # QADFIX15 (case 4.2): those books are explained, not offered — no card, not «an example of what he wants»
            _not7_ids = {f["id"] for f in facts if f["title"] in _not7}
            _rejected_ids |= _not7_ids
            _example_ids -= _not7_ids
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
    if child_theme and not any(f["available"] and f["id"] not in theme_score for f in facts):
        # QADFIX25 (replay of case 4.1): said only when every book offered is from the young readers' shelves — the general science
        # books a teenager now gets are not «كتب أطفال تتناول الموضوع»
        # QADFIX28 (QAD 2026-11-04 case 3.2, 2026-11-05 cases 18.2–18.3): «ما عندنا تصنيف باسم العلوم للصغار» read oddly to a parent —
        # the books are presented as children's books on the subject, with no word about our categories
        guidance += (f" The visitor asked for {child_theme} books for a child: these are children's books that explain it; present them as such,"
                     " and say nothing about our categories or shelf names (never «we have no category called …»)."
                     if language == "en" else
                     f" طلب الزائر كتب {child_theme} لطفل: هذه كتب أطفال تشرح الموضوع؛ قدّمها كذلك، ولا تتكلم عن تصنيفاتنا أو أسماء الرفوف"
                     " (لا تقل «ما عندنا تصنيف باسم …»).")
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
    if not edition and not it.get("copy_language") and not order_by:
        # QADFIX28 (QAD 2026-11-04 case 30.1; 2026-11-05 cases 13.2, 40.1): «جنتلمان في موسكو by أمور تاولز … our copy is in Arabic» for
        # an English visitor while «A Gentleman in Moscow» is in stock in English; «Chronicle of a Death Foretold» in English for an
        # Arabic visitor while «قصة موت معلن» is in stock in Arabic — the copy in the visitor's language of the same work (same author
        # record, same first year) takes the other's place, or the other goes when it is in facts already
        _want28 = "en" if language == "en" else "ar"
        _ba28 = book_author_map()
        _fids28 = {f["id"] for f in facts}
        for f in list(facts):
            _b28 = all_books.get(f["id"]) or {}
            if (((_b28.get("language") or "ar") == _want28) or not _b28.get("original_year")
                    or reason_by_id.get(f["id"], ("", ""))[1] == "asked" or f not in facts):
                continue
            # AUDFIX 2026-11-05 (Codex audit P2): only a copy proven to be the same work (same_work_copy: the same title, work
            # record or Goodreads book), never another book of the author from the same year; unproven, the book stays (with
            # its copy language said)
            _alt28 = [o for o in work_copies_other_titles(_b28, all_books)
                      if (o.get("language") or "ar") == _want28 and o.get("original_year") == _b28["original_year"] and is_available(o)
                      and same_work_copy(_b28, o, _ba28)
                      # AUDFIX 2026-11-06 (Codex audit P2): the other copy must meet every limit the visitor set (price, pages,
                      # publisher …: the hard filters the pool passed), or the book stays as it is
                      and all(h(o) for h in hard)
                      and age_allowed(cards.get(o["book_id"]), pprefs.get("audience"), pprefs.get("avoid") or (), o,
                                      pprefs.get("reader_age"), _cartoon_id(o["book_id"]))]
            if not _alt28:
                continue
            _o28 = next((o for o in _alt28 if o["book_id"] in _fids28), _alt28[0])
            if _o28["book_id"] in _fids28:
                facts = [x for x in facts if x["id"] != f["id"]]
            else:
                _w28 = reason_by_id.get(f["id"], ("", "direct"))
                _w28 = (_copy_reason1106(_w28[0], _b28, _o28), _w28[1])   # AUDFIX 2026-11-06: the reason quotes this copy's price / pages
                facts[facts.index(f)] = book_fact(_o28, cards.get(_o28["book_id"]), _w28[0], language)
                reason_by_id[_o28["book_id"]] = _w28
                _fids28.add(_o28["book_id"])
            it.setdefault("own_language_copy28", []).append([f["id"], _o28["book_id"]])
    if language == "en" and not edition and any((all_books.get(f["id"]) or {}).get("language", "ar") != "en" for f in facts if f["id"] in all_books):
        guidance += (" The visitor writes in English: for each book you present whose copy_language is Arabic, say plainly that our copy is in Arabic."
                     " Prefer the books whose copy is in English.")
    if language == "en" and any(re.search(r"[؀-ۿ]", (all_books.get(f["id"]) or {}).get("title") or "") for f in facts):
        # QADFIX28 (QAD 2026-11-04 cases 14.2, 30.1; 2026-11-05 cases 14.2, 23, 50.1): «أصدقائي by إيمانويل بوف», «We have novels by
        # إليف شافاق» — an English reply in Arabic script (also when he asked for English copies and we have only Arabic ones)
        guidance += (" Write the whole reply in Latin letters, with no Arabic script: name each author by author_latin when facts give it"
                     " (otherwise write the name in Latin letters as it is usually spelled in English); for a book whose copy is in Arabic, give its"
                     " title in English — its published English title when you are sure of it, else a short English rendering of its meaning in"
                     " quotes — and say our copy is in Arabic (its card under the reply shows the Arabic title).")
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
        if _pub_key27:
            # QADFIX27 #6 (QAD 2026-11-05 cases 7.1–7.2: «كم عددها بالضبط؟» → «ما أقدر أحدد العدد»; 2026-11-04 case 27.1: one book named
            # of 23): the count from the catalog, one copy per work, like the prize counts
            _pw27 = {(chat_rules.title_key(b.get("title") or ""), author_flow.display(b.get("author"))) for b in all_books.values()
                     if is_available(b) and publisher_key(b.get("publisher")) == _pub_key27}
            _pr27 = {(chat_rules.title_key(b.get("title") or ""), author_flow.display(b.get("author"))) for b in pool
                     if is_available(b) and publisher_key(b.get("publisher")) == _pub_key27}
            it["publisher_count27"] = len(_pw27)
            guidance += ((f" In stock now from this publisher: {len(_pw27)} titles in all" + (f" ({len(_pr27)} of them within his other wishes)" if 0 < len(_pr27) < len(_pw27) else "")
                          + ". When asked how many, answer with this number; facts hold only some of them as examples: never say you cannot tell the number, and offer more.")
                         if language == "en" else
                         (f" المتوفر عندنا الآن من إصدارات هذا الناشر: {fmt_num(len(_pw27))} عنوانًا" + (f" (منها {fmt_num(len(_pr27))} ضمن باقي طلبه)" if 0 < len(_pr27) < len(_pw27) else "")
                          + ". إذا سأل كم، فأجب بهذا العدد؛ وكتب facts بعضها فقط أمثلة: لا تقل إنك ما تقدر تحدد العدد، واعرض أن تذكر المزيد."))
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
    _qual = pprefs.get("qualities") or set()
    if "simple_style" in _qual:
        # QADFIX18 (inbox 2026-10-24, «سردها بسيط»): how a book is written, from readers' signs we hold for some books only
        guidance += (" The visitor wants simple, easy narration: that is how the book is written, not a subject. Say a book's language is easy"
                     " only when its facts say so (easy language, direct style); for the others do not claim it, and never search or describe it as a topic."
                     if language == "en" else
                     " الزائر يبغى سردًا بسيطًا ولغة سهلة: هذه صفة في طريقة الكتابة لا موضوع. قل إن لغة كتاب سهلة فقط إذا قالتها معلوماته في facts"
                     " (لغة سهلة، أسلوب مباشر)؛ وغيرها لا تجزم بذلك، ولا تتكلم عنها كموضوع.")
    if "original" in _qual:
        # QADFIX18 (inbox 2026-10-24, «رواية غير مبتذلة»): no measure of it in our records — said honestly
        guidance += (" The visitor wants something not clichéd or formulaic: we hold no measure of that. Say so in one short phrase, offer the books"
                     " with the sign we do have (a prize or prize list of the book itself in facts) and say that reason; never call a book «not clichéd»"
                     " on your own, and never treat «cliché» as what a book is about."
                     if language == "en" else
                     " الزائر يبغى كتابًا غير مبتذل وغير تقليدي: ما عندنا مقياس لهذا. قل ذلك في عبارة قصيرة، واعرض الكتب بالعلامة اللي عندنا"
                     " (جائزة الكتاب نفسه أو وصوله لقائمة جائزة في facts) واذكر هذا السبب؛ لا تصف كتابًا من عندك بأنه غير مبتذل، ولا تتكلم عن الابتذال كموضوع للكتاب.")
    if pages_max or pages_min:
        _lim = (f"{pages_max} pages or fewer" if pages_max else f"at least {pages_min} pages") if en else (
            f"لا يتجاوز {fmt_num(pages_max)} صفحة" if pages_max else f"لا يقل عن {fmt_num(pages_min)} صفحة")
        guidance += ((f" The visitor set the length as a number: {_lim}. " + (f"Every book in facts is within it; say each page count (pages_approx). {pages_n} available books fit." if not pages_missing
                       else "Nothing in the request has that many pages recorded; say so."))
                     if en else
                     (f" الزائر حدّد الطول بعدد الصفحات: {_lim}. " + (f"كل كتب facts ضمن هذا الحد؛ اذكر عدد صفحات كل كتاب (pages_approx). المتوفر ضمن طلبه {fmt_num(pages_n)} كتابًا، فلا تقل إنه ما عندنا إلا كتاب أو كتابين." if not pages_missing
                       else "ما لقينا ضمن طلبه كتابًا بهذا العدد من الصفحات المسجلة؛ قل ذلك.")))
    # QADFIX33 (QAD 2026-11-10 case 31.1, minor): «ناشر الكرمة، وش أحدث ثلاث كتب منه عندكم؟» → two books: the number of books the visitor
    # asks for is the number named (when facts hold that many in stock)
    _cnt33 = chat_rules.books_count_asked33(user_messages[-1]) if it.get("intent") == "recommend" else None
    if _cnt33 and sum(1 for f in facts if f.get("available")) >= _cnt33:
        it["count_asked33"] = _cnt33
        guidance += ((f" The visitor asks for {_cnt33} books: name exactly {_cnt33} available books from facts (in facts' order when he asked for"
                      " an order), each in a few words.")
                     if language == "en" else
                     f" الزائر طلب {fmt_num(_cnt33)} كتب: سمّ {fmt_num(_cnt33)} كتب متوفرة من facts بالضبط (بترتيب facts إذا طلب ترتيبًا)، كل واحد بكلمات قليلة.")
    if order_by == "oldest":
        _ol = "، ".join(f"«{b['title']}» ({oldest_year_label(b)})" for b in order_list)
        guidance += ((" The visitor asks for the oldest: by the year each work was first published in our records (when it is not recorded, our edition's year, the latest it can have come out), from the oldest: "
                      + ", ".join(f"«{b['title']}» ({oldest_year_label(b, True)})" for b in order_list) + ". Answer with these titles and years only, saying which year is only our edition's; name no other year.")
                     if en else f" الزائر يسأل عن الأقدم: حسب سنة صدور العمل أول مرة في سجلاتنا (وإذا ما سُجلت فسنة طبعتنا، وهي آخر موعد ممكن لصدوره)، من الأقدم: {_ol}. أجب بهذه العناوين وسنواتها فقط، وقل أي سنة هي سنة طبعتنا فقط، ولا تذكر سنة غيرها.")
    if order_by == "cheapest":
        guidance += (f" The visitor asked for the cheapest: facts are sorted from the cheapest within the request, and the cheapest is «{order_first['title']}» at {order_first['price']:g} SAR. Say each price; never call another book the cheapest."
                     if en else
                     f" الزائر سأل عن الأرخص: كتب facts مرتبة من الأرخص ضمن طلبه، وأرخصها «{order_first['title']}» بسعر {order_first['price']:g} ريال. اذكر سعر كل كتاب، ولا تقل عن كتاب آخر إنه الأرخص.")
    elif order_by == "dearest":
        # QADFIX30 #1 (QAD 2026-11-07 cases 46.1–46.2): «ما أقدر أحدد أغلى كتاب فن على الإطلاق» while the prices are in our records
        _dl30 = (("; ".join(f"«{b['title']}» {b['price']:g} SAR" for b in order_list)) if en else
                 "، ".join(f"«{b['title']}» {fmt_num(format(b['price'], 'g'))} ريال" for b in order_list))
        # QADFIX33 #4 (case 49.2): «over all the books in stock» is said only when the order really ran over them, not among the shown
        _all33 = not it.get("shown_only33")
        guidance += ((f" The visitor asked for the most expensive: facts are sorted from the highest price within the request"
                      + (" (our prices over all the books in stock that fit it)" if _all33 else " (among the books just shown, as he asked)")
                      + f", and the most expensive is «{order_first['title']}» at {order_first['price']:g} SAR."
                      f" Exact order: {_dl30}. Answer with it first and its price, even if it was shown before (show it again); never say you"
                      " cannot tell which is the most expensive, and never call another book the most expensive.")
                     if en else
                     (f" الزائر سأل عن الأغلى: كتب facts مرتبة من الأعلى سعرًا ضمن طلبه "
                      + ("(حسب أسعارنا لكل الكتب المتوفرة اللي تناسبه)" if _all33 else "(بين الكتب اللي عُرضت له قبل، كما سأل)") + "، وأغلاها"
                      f" «{order_first['title']}» بسعر {fmt_num(format(order_first['price'], 'g'))} ريال. الترتيب الصحيح: {_dl30}. ابدأ به واذكر سعره"
                      " حتى لو سبق عرضه (اعرضه مرة ثانية)، ولا تقل إنك ما تقدر تحدد الأغلى، ولا تقل عن كتاب آخر إنه الأغلى."))
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
        # QADFIX28 (QAD 2026-11-04 case 11.3): the shorter book on the subject was shown earlier in the conversation — offered again
        # as such, not swapped for the shortest books of the shop
        _again28 = [b["title"] for b in order_list[:2] if b["book_id"] in set(shown) and b["book_id"] not in set(last_batch or ())]
        if _again28:
            guidance += ((" Of these, «" + "», «".join(_again28) + "» was shown to the visitor earlier in this conversation: offer it again"
                          " and say in a few words that it was shown before (it is the shorter one on what he asked for).")
                         if en else
                         ((" ومن هذه «" + _again28[0] + "» سبق عرضه للزائر في هذه المحادثة: اعرضه مرة ثانية وقل بكلمات قليلة إنه"
                           " سبق عرضه (هو الأقصر في الموضوع اللي طلبه).") if len(_again28) == 1 else
                          (" ومن هذه «" + "»، «".join(_again28) + "» سبق عرضهما للزائر في هذه المحادثة: اعرضهما مرة ثانية وقل بكلمات"
                           " قليلة إنهما سبق عرضهما (هما الأقصر في الموضوع اللي طلبه).")))
    elif order_by == "longest":
        # QADFIX27 #4 (QAD 2026-11-05 case 15.3)
        _ll27 = (("; ".join(f"«{b['title']}» {b['page_count']} pages" for b in order_list)) if en else
                 "، ".join(f"«{b['title']}» {fmt_num(b['page_count'])} صفحة" for b in order_list))
        guidance += ((f" The visitor asked for the longest: facts are sorted from the longest within the request (our page counts over all"
                      f" the books in stock), and the longest is «{order_first['title']}» ({order_first['page_count']} pages). Exact order: {_ll27}."
                      " Present them in that order with their page counts, naming the longest first even if it was shown before; never call"
                      " another book the longest.")
                     if en else
                     (f" الزائر سأل عن الأطول: كتب facts مرتبة من الأطول ضمن طلبه (حسب عدد الصفحات المسجّل لكل الكتب المتوفرة)، وأطولها"
                      f" «{order_first['title']}» ({fmt_num(order_first['page_count'])} صفحة). الترتيب الصحيح: {_ll27}. قدّمها بهذا الترتيب"
                      " واذكر عدد صفحات كل كتاب، وابدأ بالأطول حتى لو سبق عرضه، ولا تقل عن كتاب آخر إنه الأطول."))
    _tw32g = [all_books[i]["title"] for i in it.get("title_words32") or [] if i in all_books and i in {f["id"] for f in facts}]
    if _tw32g and not order_by:
        # QADFIX32 #5 (case 28.1): the title the visitor half remembers is in stock — offered, not asked about
        guidance += ((" The visitor remembers words of a title: every one of them is in the title of " + ", ".join(f"«{t}»" for t in _tw32g)
                      + " (in stock). Offer it first as the book they may mean and ask if it is the one; do not ask a general question instead.")
                     if en else
                     (" الزائر يتذكر كلمات من عنوان: كلها في عنوان " + "، ".join(f"«{t}»" for t in _tw32g)
                      + " (متوفر عندنا). اعرضه أولًا على أنه قد يكون الكتاب اللي يقصده واسأله إن كان هو، ولا تسأل سؤالًا عامًا بدله."))
    if order_by in ("shortest", "shorter") and q7.get("short_nopages"):
        # QADFIX24 (case 2.2)
        _np24 = q7["short_nopages"]
        guidance += ((" These shown books have no page count recorded: " + ", ".join(f"«{t}»" for t in _np24)
                      + ". So never call another book the shortest of them or shorter than these: give the page count you have and say theirs is not recorded.")
                     if en else
                     (" هذه الكتب المعروضة ما عندنا عدد صفحاتها: " + "، ".join(f"«{t}»" for t in _np24)
                      + ". فلا تقل عن كتاب آخر إنه الأقصر بينها أو أقصر منها: اذكر عدد الصفحات المعروف، وقل إن عدد صفحاتها غير مسجّل."))
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
    if avoid_authors30 and not author_only:
        # QADFIX30 #3 (QAD 2026-11-07 case 25.2): «الروايات اللي تحولت لمسلسل كلها لنجيب محفوظ… ويوجد بعد نسخ إنجليزية من كتبه»
        guidance += (f" The visitor does not want books by {avoid_authors30[0]}: every book in facts is by someone else, so offer them"
                     " normally; never say that what fits is all his, and never offer his books or their copies in another language."
                     if en else f" الزائر ما يبغى كتب {avoid_authors30[0]}: كل كتب facts لغيره، فاعرضها عادي؛ لا تقل إن المناسب كله له، ولا تعرض"
                     " كتبه ولا نسخها بلغة أخرى.")
    if kid_added32 and any(f["id"] in {b["book_id"] for _r, b in kid_added32} for f in facts):
        # QADFIX32b (Ibrahim «أ», case 3.1)
        guidance += ((" The books whose reason says «from the children's shelf» stand on our children's shelf, and the age recorded for each"
                      " covers this reader: offer them after the young readers' shelf books, saying so in a few words (its age from facts).")
                     if en else
                     (" الكتب اللي سببها «من رف الأطفال» موجودة على رف الأطفال عندنا، والعمر المسجّل لكل واحد منها يشمل عمر القارئ: اعرضها"
                      " بعد كتب رف اليافعين، وقل ذلك بكلمات قليلة (مع عمره من facts)."))
    if _ya30 and (_yas30 or ya_added30 or ya_unknown30 or _ya_fit31):   # QADFIX31 #3: also when the rule kept the shelf books
        # QADFIX30 #5 (QAD 2026-11-07 cases 17.2–17.3): «ما عندي تفاصيل… ولا عن كتب رف اليافعين»; «كافكا على الشاطئ» offered to a girl of 13
        # who does not like violence, with no content record
        _yr30 = ("from the young readers' shelf" if en else "من رف اليافعين")
        guidance += ((f" The books whose reason says «{_yr30}» stand on our young readers' shelf: offer them first and say so; never say you"
                      " have no details about that shelf. Say honestly what each one is from its facts, and do not call a book realistic, or"
                      " anything else asked for, unless its facts say it.")
                     if en else
                     (f" الكتب اللي سببها «{_yr30}» موجودة على رف اليافعين عندنا: قدّمها أولًا وقل إنها من رف اليافعين، ولا تقل إنه ما عندك"
                      " تفاصيل عن هذا الرف. قل بصدق وش هو كل كتاب من معلوماته في facts، ولا تصف كتابًا بأنه واقعي أو بأي صفة طلبها إلا إذا"
                      " قالتها معلوماته."))
        if ya_unknown30:
            guidance += ((" We hold no content record for these books (they are not on the young readers' shelf): "
                          + ", ".join(f"«{t}»" for t in ya_unknown30[:3]) + ". Never say they are free of what the visitor wants to avoid or that they"
                          " suit the reader's age; offer them only after the shelf's books, saying they are for adults and their content is not known to us.")
                         if en else
                         (" ما عندنا سجل محتوى لهذه الكتب (ليست من رف اليافعين): " + "، ".join(f"«{t}»" for t in ya_unknown30[:3])
                          + ". لا تقل إنها خالية مما يتجنبه الزائر ولا إنها تناسب عمر القارئ؛ إن عرضتها فبعد كتب الرف، وقل إنها من رف الكبار"
                          " وما عندنا تفاصيل عن محتواها."))
    if re.search(r"نوبل|nobel", normalize(user_messages[-1]).lower()) and asked_books:
        # QADFIX31 (QAD 2026-11-08 case 49.1, minor): «صحيح إن موسم الهجرة إلى الشمال فازت بجائزة نوبل؟» → «ما عندي تفاصيل مؤكدة عن فوزها
        # بنوبل»: the Nobel Prize in Literature goes to a writer, never to one novel — the reply corrects it kindly from our records
        _nb31 = [b for b in asked_books[:2] if not any("نوبل" in x for x in honors.matches_award(b["book_id"], ["nobel"]))]
        _nw31 = [b for b in asked_books[:2] if b not in _nb31]
        # AUDFIX 2026-11-08 (Codex audit P2): an empty lookup is a sourced «no» only when the book is linked to a known author
        # record (the Nobel rows are the official nobelprize.org list matched to our author records); with no linked or a
        # missing author it is unknown — never stated as a non-win
        _bam08 = book_author_map()
        _nu08 = [b for b in _nb31 if not _bam08.get(b["book_id"])
                 or (b.get("author") or "").strip() in ("", "غير معروف", "مجهول", "Unknown")]
        _nb31 = [b for b in _nb31 if b not in _nu08]
        if _nu08:
            guidance += ((" For " + ", ".join(f"«{b['title']}»" for b in _nu08) + " we hold no linked author record, so we cannot tell whether its"
                          " author won the Nobel Prize: say so plainly (the prize goes to a writer, not a book), and never say he did or did not win it.")
                         if en else
                         (" ما عندنا سجل مؤلف مرتبط بـ" + "، ".join(f"«{b['title']}»" for b in _nu08) + "، فما نقدر نحدد إن كان مؤلفه فاز بنوبل:"
                          " قل ذلك بوضوح (الجائزة تُمنح للكاتب لا للكتاب)، ولا تقل إنه فاز ولا إنه ما فاز."))
        if _nb31 or _nw31:
            guidance += ((" The Nobel Prize in Literature is given to a writer for the whole of their work, never to one book: say so kindly."
                          + (" By our records the author of " + ", ".join(f"«{b['title']}» ({author_flow.display(b.get('author'))})" for b in _nb31)
                             + " is not among the Nobel laureates in our list (the official nobelprize.org list matched to our authors); say that plainly." if _nb31 else "")
                          + (" By our records the author of " + ", ".join(f"«{b['title']}»" for b in _nw31) + " did win it (author_awards)." if _nw31 else ""))
                         if en else
                         (" جائزة نوبل للآداب تُمنح للكاتب عن مجمل أعماله، ولا تُمنح لرواية بعينها: صحّح ذلك بلطف."
                          + (" وحسب سجلاتنا مؤلف " + "، ".join(f"«{b['title']}» ({author_flow.display(b.get('author'))})" for b in _nb31)
                             + " ليس ضمن الفائزين بها في قائمتنا (قائمة nobelprize.org الرسمية مطابقة لمؤلفينا)؛ قل ذلك بوضوح." if _nb31 else "")
                          + (" وحسب سجلاتنا مؤلف " + "، ".join(f"«{b['title']}»" for b in _nw31) + " فاز بها (author_awards)." if _nw31 else "")))
    if it.get("refused_ids30"):
        # QADFIX30 #4 (QAD 2026-11-07 case 45.1)
        _rt30 = "، ".join(f"«{all_books[i]['title']}»" for i in it["refused_ids30"][:2] if i in all_books)
        if _rt30:
            guidance += (f" The visitor has already read {_rt30} and does not want it: never offer it; the books in facts are others."
                         if en else f" الزائر قرأ {_rt30} وما يبغاه: لا تعرضه، وكتب facts غيره.")
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
                     " translation of it), give each one's language and number of copies, and leave the others out. When the page count is"
                     " asked and the book itself has none, give the page count of the copy that IS the same work, with its language and publisher."
                     if en else
                     " في الكتاب المسؤول عنه قائمة same_author_maybe_same_work: نسخ للمؤلف نفسه بعنوان أو لغة أخرى. احسب منها فقط ما هو"
                     " العمل نفسه (Animal Farm الإنجليزية هي «مزرعة الحيوان»، و«مزرعة الحيوانات» ترجمة أخرى له)، واذكر لغة كل نسخة وعدد نسخها،"
                     " واترك الباقي. وإذا سُئل عن عدد الصفحات وما للكتاب نفسه عدد صفحات، فاذكر عدد صفحات النسخة التي هي العمل نفسه مع لغتها وناشرها.")
    if it.get("no_age_fit") is not None:
        _a = it["no_age_fit"]
        guidance += ((f" No book in facts is made for a {_a}-year-old in what was asked (language or kind): say that plainly first, then"
                      " give the closest ones with the ages they are for, or offer the same kind in the other language.")
                     if en else
                     (f" ما في facts كتاب مناسب لعمر {fmt_num(_a)} سنوات بالطلب نفسه (اللغة أو النوع): قل ذلك بوضوح أولًا، ثم اعرض الأقرب"
                      " واذكر العمر الذي يناسبه، أو اعرض النوع نفسه باللغة الثانية."))
    _ra24 = pprefs.get("reader_age") if pprefs.get("audience") in ("children", "young_adult") else None
    _young24 = [all_books[f["id"]]["title"] for f in facts if _ra24 and f.get("id") in all_books
                and all_books[f["id"]].get("age_max") is not None and all_books[f["id"]]["age_max"] < _ra24 - 1]
    if _young24:
        # QADFIX24 (QAD 2026-10-30, case 20.1): «Magic Tree House» (6–9) offered for a boy in the sixth grade (11) — the reply says
        # plainly that it is written for younger children, so the parent can judge
        guidance += ((" Written for younger children than this reader (" + str(_ra24) + "): " + ", ".join(f"«{t}»" for t in _young24[:3])
                      + ". Say so plainly in a few words when you show it (it may be easy or young for him).")
                     if en else
                     (f" هذه مكتوبة لأطفال أصغر من عمر القارئ ({fmt_num(_ra24)}): " + "، ".join(f"«{t}»" for t in _young24[:3])
                      + ". قل ذلك بوضوح في كلمات قليلة إذا عرضتها (قد تكون سهلة أو صغيرة عليه)."))
    if second_look_note:
        guidance += second_look_note
    _ser25 = {}
    for f in facts:
        _b = all_books.get(f["id"]) or {}
        if f["available"] and (_b.get("series_name") or "").strip() and isinstance(_b.get("series_position"), (int, float)):
            _ser25.setdefault(_b["series_name"].strip(), []).append(_b)
    for _sn, _bs in _ser25.items():
        if len(_bs) >= 2:
            # QADFIX25 (QAD 2026-10-31, case 11.2): «النار والعنقاء: صقر قريش (٢)» recommended in the words, its part 1 only a card
            _bs = sorted(_bs, key=lambda b: b["series_position"])
            guidance += ((f" «{_bs[0]['title']}» is an earlier part of the same series as «{_bs[-1]['title']}»: if you recommend the series, "
                          "start with the earlier part and say the later one comes after it; never offer a later part as the start.")
                         if language == "en" else
                         f" «{_bs[0]['title']}» جزء سابق من نفس سلسلة «{_bs[-1]['title']}»: إن رشّحت السلسلة فابدأ بالجزء السابق وقل إن اللاحق"
                         " يأتي بعده، ولا تقدّم جزءًا لاحقًا كبداية.")
    if q7.get("nopages"):
        guidance += ((f" The book(s) about the subject asked for have no page count recorded: {', '.join(q7['nopages'])}. Say it is the one we have on that subject and that its page count is not recorded; do not say you found no book on the subject."
                      if en else f" الكتاب الموجود عندنا عن الموضوع ما عندي عدد صفحاته: «{'»، «'.join(q7['nopages'])}». قل إنه الموجود عندنا عن الموضوع وإن عدد صفحاته غير مسجل، ولا تقل إنك ما لقيت كتابًا عن الموضوع."))
    if q7.get("series"):
        _sl = "، ".join(f"«{n}» ({k})" for n, k in q7["series"])
        guidance += ((" The visitor wants a series: the facts are series recorded in our catalog with the number of parts in stock (" + _sl + "). Name the series and how many parts we have; do not say you cannot confirm it is a series."
                      if en else f" الزائر يبغى سلسلة: كتب facts من سلاسل مسجلة عندنا، مع عدد أجزائها المتوفرة ({_sl}). اذكر اسم السلسلة وكم جزء عندنا منها، ولا تقل إنك ما تقدر تؤكد أنها سلسلة."))
    if q7.get("intro27"):
        # QADFIX27 #10
        _i27 = q7["intro27"]
        _it27 = "، ".join(f"«{t}»" for t in _i27["titles"])
        if _i27["simple"]:
            guidance += ((f" The visitor wants a first book in {_i27['field']}: start with the introduction among these general works: {', '.join(_i27['titles'])}."
                          " Books on one narrow subject come after them, said as such.")
                         if en else
                         f" الزائر يبغى أول كتاب في {_i27['field']}: ابدأ بالمدخل من بين هذه الكتب العامة عن العلم: {_it27}، والكتب عن موضوع خاص واحد بعدها وقل إنها كذلك.")
        else:
            guidance += ((f" The visitor wants a simple first book in {_i27['field']}: we have no simplified introduction to the field. Say that plainly"
                          f" in the first sentence, then offer the closest general works on the field ({', '.join(_i27['titles'])}) as what they are"
                          " (academic or reference books, not simplified); never offer a book on one narrow subject (proverbs, one thinker) as an introduction.")
                         if en else
                         (f" الزائر يبغى كتابًا مبسّطًا أول مرة في {_i27['field']}: ما عندنا مدخل مبسّط للعلم نفسه. قل ذلك بصراحة في أول جملة، ثم"
                          f" اعرض الأقرب: الكتب العامة عن العلم ({_it27}) على حقيقتها (أكاديمية أو مرجعية، مو مبسّطة)؛ ولا تقدّم كتابًا عن موضوع"
                          " خاص واحد (أمثال شعبية، مفكر واحد) على أنه مدخل."))
    # QADFIX28 (QAD 2026-11-05 case 34.1): «تاريخ الخط العربي وتطوّر أنواعه» → «أحجار شاهدية… بمكة» (its summary mentions the
    # script) first and «رؤية فنية معاصرة للخط العربي» (the one book about it) second: the books about the subject are named first
    def _why28(f):
        return str(reason_by_id.get(f["id"], ("", ""))[0])
    _about28 = [f["title"] for f in facts if f.get("available") and ("يتناول «" in _why28(f) or "عنوانه عن «" in _why28(f))]
    _ment28 = [f["title"] for f in facts if f.get("available") and "نبذته تذكر «" in _why28(f) and f["title"] not in _about28]
    if _about28 and _ment28 and language != "en" and len(_about28) <= 3 and not order_by and not it.get("war_place28"):
        guidance += (" الكتب اللي سببها أنها تتناول الموضوع نفسه: «" + "»، «".join(_about28) + "» — ابدأ بها. والكتب اللي نبذتها تذكره فقط («"
                     + "»، «".join(_ment28[:3]) + "») تأتي بعدها، وقل إن نبذتها تذكره فقط.")
        it["about_first28"] = [f["id"] for f in facts if f["title"] in _about28]
    if it.get("decade29") and not it.get("war_place28"):
        # QADFIX29 #4 (QAD 2026-11-06 case 10)
        _dn29 = decade_name29(it["decade29"], "en" if en else "ar")
        guidance += ((f" The books whose reason says their own text sets them in {_dn29} are the ones that take place then: present them"
                      " first, as such. A book that only shares the place, or is set in another time, comes after them, said as such; never"
                      f" say we have no book set in {_dn29} when one of them is in facts; if they were shown already, say they are the ones we"
                      " have set then and show them again before any other book.")
                     if en else
                     (f" الكتب اللي سببها «نبذته تذكر أن أحداثه في {_dn29}» هي اللي تدور أحداثها في ذلك العقد: قدّمها أولًا على هذا الأساس."
                      " والكتاب اللي يشارك المكان فقط أو تدور أحداثه في زمن آخر يأتي بعدها، على حقيقته؛ ولا تقل إنك ما لقيت رواية تدور في"
                      f" {_dn29} وفي facts كتاب منها. وإن كانت هذه الكتب سبق عرضها، فقل إنها هي اللي عندنا تدور في {_dn29} واعرضها مرة ثانية"
                      " قبل أي كتاب آخر."))
    if it.get("war_place28"):
        # QADFIX28 #2 (QAD 2026-11-05 case 5.1): the war event first, the place / time / family after
        _pl28 = it["war_place28"]
        guidance += ((f" The visitor names a war event in {_pl28} (shelling, fighting, displacement): the books whose reason says «about the war"
                      f" in {_pl28}» are the closest — present them first, as novels about that war; if none is clearly about every detail he"
                      " named (a family leaving its home, the decade), say so in a few words. A book that only shares the place, the time or"
                      " the family comes after them, said as such, never as being about the war.")
                     if en else
                     (f" الزائر يذكر حدث حرب في {_pl28} (قصف، اقتتال، نزوح): الكتب اللي سببها «يتناول الحرب في {_pl28}» هي الأقرب — قدّمها"
                      " أولًا على أنها روايات عن تلك الحرب، وإذا ما فيها كتاب واضح أنه عن كل تفاصيل طلبه (عائلة تترك بيتها، العقد) فقل ذلك"
                      " بكلمات قليلة. والكتاب اللي يشارك المكان أو الزمن أو العائلة فقط يأتي بعدها، على حقيقته، لا على أنه عن الحرب."))
    _wa27 = q7.get("women_award27")
    if _wa27:
        # QADFIX27 #1: the prize wish among the books by women in our author records — a firm answer, not «جنس المؤلف غير مسجل»
        _won27, _aw27, _ls27, _oth27 = _wa27["won"], _wa27["author_won"], _wa27["listed"], _wa27["others_won"]
        _unk27 = _wa27.get("unknown_won") or []
        # QADFIX30 m6 (QAD 2026-11-07 cases 5.2–5.3, medium): «سمّ لي اللي وصلت الطويلة بس وما دخلت القصيرة» → «ما عندي تفاصيل عن استبعاد
        # الروايات من القائمة القصيرة», then «كم وحدة بالضبط؟» → «ما عندي العدد الدقيق… ١٥» (17 the turn before): the list each book
        # reached is in book_awards.result — the longlist only and the shortlist are counted apart, the same numbers every turn
        _lo30, _sh30 = _wa27.get("long_only") or [], _wa27.get("short_listed") or []
        if _lo30 or _sh30:
            _only30 = longlist_only30(user_messages)
            guidance += ((f" Of the books by women that did not win it themselves, by the list the book itself reached: longlisted only (never reached the shortlist): {len(_lo30)}"
                          + (f" ({', '.join(_lo30[:12])})" if _lo30 else "") + f"; reached the shortlist: {len(_sh30)}"
                          + (f" ({', '.join(_sh30[:12])})" if _sh30 else "") + ". These numbers are from our records of each book's result:"
                          " give them exactly and keep them the same in every reply; never say you have no details about which list a book reached."
                          + (" The visitor asks for the longlist only: answer with the first number and those titles." if _only30 else "")
                          + ((f" The visitor asks for the shortlist: the women's books that reached it number {len(_sh30) + len(_won27)} (of them"
                              f" {len(_won27)} won): " + ", ".join((_won27 + _sh30)[:12]) + ". Answer with that number and name the writers and"
                              " their books; never say you cannot count or name them.") if (_short31 and not _only30) else ""))
                         if en else
                         (f" ومن كتب الكاتبات اللي ما فازت هي نفسها، حسب القائمة اللي وصلها الكتاب نفسه: وصلت القائمة الطويلة فقط (ما دخلت القصيرة): {fmt_num(len(_lo30))}"
                          + (f" ({'، '.join(_lo30[:12])})" if _lo30 else "") + f"؛ ووصلت القائمة القصيرة: {fmt_num(len(_sh30))}"
                          + (f" ({'، '.join(_sh30[:12])})" if _sh30 else "") + ". هذه الأعداد من سجلاتنا لنتيجة كل كتاب: اذكرها كما هي وثابتة في كل رد،"
                          " ولا تقل إنه ما عندك تفاصيل عن القائمة اللي وصلها الكتاب."
                          + (" الزائر يسأل عن الطويلة فقط: أجب بالعدد الأول وبهذه العناوين." if _only30 else "")
                          # QADFIX31 #2 (QAD 2026-11-08 cases 6.1–6.2): the shortlist asked — its number is the shortlisted plus the winners
                          + ((f" الزائر يسأل عن القائمة القصيرة: عدد كتب الكاتبات اللي وصلتها {fmt_num(len(_sh30) + len(_won27))} (منها"
                              f" {fmt_num(len(_won27))} فازت): " + "، ".join((_won27 + _sh30)[:12]) + ". أجب بهذا العدد وسمّ الكاتبات وكتبهن؛"
                              " لا تقل إنك ما تقدر تعدّهن أو تسمّيهن.") if (_short31 and not _only30) else "")))
        # «رواية ثانية فازت فيها كاتبة» (QAD 2026-11-04 case 8.3): «فازت فيها / بها» is the book itself winning — the model read it as
        # «حتى لو الرواية نفسها ما فازت؟ نعم…» with two other novels of the same author
        _lmw27 = " " + " ".join(normalize(re.sub(r"[؟?!.،,«»]", " ", user_messages[-1])).split()) + " "
        if re.search(r" (?:و)?فاز(?:ت)? (?:فيها|بها|فيه|به) | (?:ال)?(?:روايه|كتاب) (?:نفسها|نفسه) | (?:فازت|فاز) (?:هي|هو) نفس| (?:نفسها|نفسه) (?:فازت|فاز|حازت|حاز) "
                     r"| won it (?:herself|itself)| itself won", _lmw27):
            guidance += ((f" His question is about a book that won the prize itself («won with it»): answer from the first number only"
                          f" ({len(_won27)}); if that number is 1 and he asks for another one, the answer is plainly «no, it is the only one we"
                          " have», then you may add that her other books here are by a winning author (they did not win themselves). If that"
                          " winner was shown already, start by saying it is the only one (shown above) before any other book; never offer a"
                          " book only listed, or whose author won for another book, as the answer.")
                         if en else
                         (f" سؤاله عن رواية فازت هي نفسها («فازت فيها/بها»): أجب من العدد الأول فقط ({fmt_num(len(_won27))})؛ فإن كان واحدًا"
                          " وسأل عن غيرها فالجواب بوضوح: «لا، هي الوحيدة عندنا»، ثم لك أن تضيف أن رواياتها الأخرى هنا لمؤلفة فائزة لكنها ما فازت"
                          " هي نفسها. وإن كانت هذه الفائزة سبق عرضها، فابدأ بأنها هي الوحيدة (سبق عرضها) قبل أي كتاب آخر، ولا تقدّم كتابًا"
                          " وصل للقائمة فقط أو فازت مؤلفته عن غيره على أنه جواب طلبه. ولا تفسّر سؤاله بغير ذلك."))
            # QADFIX28 #1 (QAD 2026-11-04 case 8.2, left by QADFIX27): «أبي بس اللي فازت فيها امرأة» after «بريد الليل» was shown — the
            # reply offered «أهل الهوى» of the same author beside it, as if it had won: when the one winner by a woman is the whole
            # answer and he asks for no other book, the reply says it is the only one in stock and shows that card alone
            if (len(_won27) == 1 and (_wa27.get("won_ids") or [])
                    and not re.search(r" (?:ثاني|ثانيه|غير|غيرها|غيره|اخري|اخرى|كمان|بعد|another|other|else|more) ", _lmw27)):
                q7["only_won28"] = _wa27["won_ids"][0]
                _ow28 = all_books.get(q7["only_won28"], {}).get("title", "")
                guidance += ((f" «{_ow28}» is the only book in stock by a woman that won this prize itself: say so plainly in the first"
                              " sentence (if it was shown already, say it is that one); offer no other book now — not her other novels"
                              " (they did not win) nor any book by a man. show_book_ids: that book only.")
                             if en else
                             (f" «{_ow28}» هي الوحيدة المتوفرة عندنا لكاتبة وفازت بالجائزة هي نفسها: قل ذلك بوضوح في أول جملة (وإن سبق"
                              " عرضها فقل إنها هي نفسها اللي عرضتها)، ولا تعرض الآن أي كتاب آخر — لا رواياتها الأخرى (ما فازت هي) ولا كتابًا"
                              " لرجل. show_book_ids: هذا الكتاب وحده."))
        guidance += ((f" The visitor asks about women writers and this prize. By our author records, the books in stock by women that won it"
                      f" themselves: {len(_won27)}" + (f" ({', '.join(_won27[:6])})" if _won27 else "") + "."
                      + (f" Books whose woman author won it herself (for another book, or for her work as a whole) but not the book: {', '.join(_aw27[:4])}." if _aw27 else "")
                      + (f" Only shortlisted / longlisted, by women: {len(_ls27)}" + (f" ({', '.join(_ls27[:10])})" if _ls27 else "") + "." if _ls27 else "")
                      + (f" The other winners in stock are by men, by our records: {', '.join(_oth27[:10])}." if _oth27 else "")
                      + (f" Winners whose author's gender our records do not give: {', '.join(_unk27[:10])} — if you name one, say our records do"
                         " not say; do not count them as women's books." if _unk27 else "")
                      + " Answer from these numbers only: when asked whether there is another one, or whether it is the only one, answer"
                      " plainly yes or no from them; when the question is about a book that won it itself, a book whose author won it but"
                      " not the book does not count (say it as it is)" + ("; say «by our records», since some winners' gender is not"
                      " recorded" if _unk27 else "") + ". Never say authors' gender is not recorded at all (the women above are), and never"
                      " use the general prize counts for this.")
                     if en else
                     (f" الزائر يسأل عن كاتبات وهذه الجائزة. حسب سجلات المؤلفين عندنا، الكتب المتوفرة لكاتبات اللي فازت بالجائزة هي نفسها:"
                      f" {fmt_num(len(_won27))}" + (f" ({'، '.join(_won27[:6])})" if _won27 else "") + "."
                      + (f" وكتب فازت مؤلفتها نفسها بالجائزة (عن كتاب آخر أو عن مجمل أعمالها) لكنها ما فازت هي: {'، '.join(_aw27[:4])}." if _aw27 else "")
                      + (f" ووصل للقوائم فقط لكاتبات: {fmt_num(len(_ls27))}" + (f" ({'، '.join(_ls27[:10])})" if _ls27 else "") + "." if _ls27 else "")
                      + (f" وبقية الكتب الفائزة المتوفرة لرجال حسب سجلاتنا: {'، '.join(_oth27[:10])}." if _oth27 else "")
                      + (f" وفائزون ما في سجلاتنا جنسهم: {'، '.join(_unk27[:10])} — إن ذكرت أحدًا منهم فقل إن سجلاتنا ما تحدد ذلك، ولا تعدّ"
                         " كتبهم لكاتبات." if _unk27 else "")
                      + " أجب من هذه الأعداد فقط: إذا سأل هل في غيرها أو هل هي الوحيدة، فأجب بصراحة بنعم أو لا منها؛ وإذا كان سؤاله عن"
                      " رواية فازت هي نفسها، فالكتاب اللي فازت مؤلفته فقط ما يُعدّ (قله على حقيقته)" + ("، وقل «حسب سجلاتنا» لأن بعض الفائزين"
                      " ما يحدد سجلنا جنسهم" if _unk27 else "") + ". ولا تقل إن جنس المؤلفين غير مسجّل عندنا عمومًا (سجلات الكاتبات أعلاه"
                      " موجودة)، ولا تستعمل أعداد الجائزة العامة لهذا."))
    if q7.get("women"):
        guidance += ((f" The visitor asks for women authors: our author records say these are by women: {', '.join(q7['women'])}. Offer them as such; any other book in facts is not confirmed as a woman's — say so if you name it."
                      if en else f" الزائر يبغى كتبًا لمؤلفات: سجلات المؤلفين عندنا تقول إن هذه الكتب لنساء: «{'»، «'.join(q7['women'])}». قدّمها على هذا الأساس؛ وأي كتاب آخر في facts غير مؤكد أنه لامرأة، فقل ذلك إن ذكرته."))
    elif women_wish(user_messages[-1], _ttl35) and not author_only and not _wa27:
        # QADFIX7 #12 (case 49.1): the author's gender is not recorded in our data
        guidance += (" The author's gender is not recorded in our catalog: say so plainly, and do not call an author a woman or a man unless the facts say it."
                     if en else " جنس المؤلف غير مسجل عندنا: قل ذلك بصراحة، ولا تقل عن كاتب إنه امرأة أو رجل إلا إذا كان ذلك في facts.")
    # QADFIX35 (QAD 2026-11-12 case 23.1, minor): «ودي أعرف إذا «شرق المتوسط» متوفرة» → «إيه، «شرق المتوسط وتكوين التيار الراديكالي…»
    # متوفر» (a study whose longer title only begins with those words; Munif's novel is not in our list): a title asked that is only the
    # beginning of a longer title of ours, running on in the same sentence (not a subtitle after «:»), is said as such
    for _m35 in (it.get("mentioned_titles") or [])[:2]:
        _mk35 = chat_rules.title_key(_m35 or "")
        if len(_mk35.split()) < 2 or any(chat_rules.title_key(_head34(b.get("title"))) == _mk35 or chat_rules.title_key(b.get("title") or "") == _mk35
                                         for b in all_books.values()):
            continue
        _lg35 = [b for b in all_books.values() if is_available(b) and re.match(r"\s*[^:：(\[–—\-]+$", b.get("title") or "")
                 and chat_rules.title_key(b.get("title") or "").startswith(_mk35 + " ")
                 and len(chat_rules.title_key(b.get("title") or "").split()) >= len(_mk35.split()) + 2]
        if _lg35:
            it["title_head_only35"] = [b["book_id"] for b in _lg35[:2]]
            guidance += ((f" The visitor named «{_m35}»: no work of ours has that title alone; «{_lg35[0]['title']}» only begins with those words."
                          f" If «{_m35}» is the title of another known work (a novel, say), first say that work is not in our list, then offer"
                          " ours for what it is; never say it is the one he asked for.")
                         if en else
                         (f" الزائر ذكر «{_m35}»: ما عندنا عمل بهذا العنوان وحده، و«{_lg35[0]['title']}» عنوانه يبدأ بهذه الكلمات فقط. إذا كان"
                          f" «{_m35}» عنوان عمل معروف آخر (رواية مثلًا) فقل أولًا إن ذلك العمل ليس في قائمتنا، ثم اعرض كتابنا على حقيقته؛"
                          " ولا تقل إنه هو المطلوب."))
            break
    if language != "en":
        # QADFIX29 (QAD 2026-11-06 case 3.2): «City of Glass، ثم Ghosts، ثم The Locked Room» in an Arabic reply
        guidance += (" في الرد العربي اكتب عناوين الأعمال بالعربية (مثل أجزاء مجلد أو أعمال أخرى للمؤلف)، ولا تكتب عنوانًا بالحروف"
                     " اللاتينية إلا إذا كان هو عنوان النسخة المتوفرة نفسها.")
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
    _rej_f = [all_books[i]["title"] for i in sorted(_rejected_ids & {f["id"] for f in facts}) if i in all_books]
    if _rej_f:
        guidance += ((" The visitor rules out «" + "», «".join(_rej_f) + "»: if needed say briefly why it does not fit, and never put it in show_book_ids.")
                     if language == "en" else
                     (" الزائر استبعد «" + "»، «".join(_rej_f) + "»: إن احتجت فاذكر باختصار ليش ما يناسب طلبه، ولا تضعه في show_book_ids أبدًا."))
    if author_only and author_names:
        # QADFIX17 (QAD 2026-10-24, case 12.2): «فيه شي ثاني لنفس الكاتب؟» when every book of his in stock was shown already:
        # said plainly, not «I cannot tell from here»
        _ab = [b for b in all_books.values() if is_available(b) and b.get("author") in author_names]
        if _ab and all(b["book_id"] in shown for b in _ab):
            _tl = "، ".join("«" + b["title"] + "»" for b in _ab[:4])
            guidance += ((f" Every book of this author in stock was shown already: {_tl}. Say plainly that we have nothing else by him now.")
                         if language == "en" else
                         f" كل كتب هذا المؤلف المتوفرة عندنا سبق عرضها: {_tl}. قل بوضوح إن ما عندنا له غيرها حاليًا.")
    if (CLEAN_YOUNG in (pprefs.get("avoid") or ()) and pprefs.get("audience") in AGE_OK and not author_only
            and not any(f["available"] for f in facts)):
        # QADFIX26 #1: nothing fits both the request and a clean book for this young reader — said, and the young readers'
        # (children's) shelf offered instead: its books in stock that pass the same strict check, Arabic copies first
        _alt26 = clean_young_shelf(all_books, shown, cards, pprefs["audience"], pprefs.get("reader_age"), language)
        for b in _alt26[:4]:
            add(b, CLEAN_YOUNG_SHELF_REASON["en" if language == "en" else "ar"], "related")
        if any(f["available"] for f in facts):
            it["clean_young_shelf26"] = [b["book_id"] for b in _alt26[:4]]
            guidance += ((" Nothing in stock fits both the request and a clean book for this reader's age: say that first, plainly,"
                          " then offer the books in facts as books from our young readers' shelf (they do not match the rest of the request).")
                         if language == "en" else
                         (" ما عندنا كتاب متوفر يجمع طلبه ويناسب عمره بلا محتوى للكبار: قل هذا أولًا بوضوح، ثم اعرض كتب facts على أنها"
                          " من رف اليافعين بديلًا (لا تطابق باقي طلبه)."))
    if not any(f["available"] for f in facts) and not it.get("nobel_not32"):   # QADFIX32 (6.1): his books are named above
        guidance += (" No matching books are available; say so plainly and ask about an alternative."
                     if language == "en" else " لا توجد كتب متوفرة مطابقة؛ قل ذلك بصراحة واسأله عن بديل.")

    debug = {"mode": "ai", "intent": it["intent"], "prefs": it["prefs"], "new_wish_carried": _nw_dbg,
             # QADFIX28 (replays of QAD 2026-11-05 case 45.2, 2026-11-04 case 11.3): an answer our records settle («none before 1930;
             # the oldest is …», «the only one», «no poetry of his», «the shortest on that war») gets no second reading
             "firm28": bool(q7.get("before_none28") or q7.get("only_won28") or it.get("kind_none28")
                            or (order_by in ("shorter", "shortest") and topic_phrases) or shorter_none),
             "author_request": author_request, "author_style": it.get("author_style"), "style_source": style_source,
             "author_record": author_record and author_record["author_id"],
             "mentioned_titles": it["mentioned_titles"], "wants_new_options": it["wants_new_options"],
             "relaxed_subgenres": relaxed_subgenres,
             "candidates": [{"id": f["id"], "title": f["title"], "available": f["available"],
                             "why": f["why_it_matches"]} for f in facts],
             "guidance": guidance}
    # QADFIX20 (QAD 2026-10-25, case 12.1): «أبدأ برجال في الشمس ولا عائد إلى حيفا؟» answered «عائد إلى حيفا أقصر بوضوح» while
    # «رجال في الشمس» has no page count in any edition we hold: a length compared with a missing count is not said
    if it.get("intent") == "compare":
        _no_len = [f.get("title") for f in facts if f.get("title") and f.get("pages_approx") is None
                   and not f.get("pages_approx_other_edition")]
        if _no_len and len(facts) >= 2:
            guidance += ((" We hold no page count for «" + "», «".join(_no_len) + "»: never compare its length with another book's,"
                          " never call another book shorter or longer than it.") if language == "en" else
                         (" ما عندنا عدد صفحات «" + "»، «".join(_no_len) + "»: لا تقارن طوله بكتاب آخر، ولا تقل إن غيره أقصر أو أطول منه."))
    if language != "en":
        # English guidance lines say "a bookseller"; the model translated it as «البوّاق» / «الكتبّاس»
        # (QA 2026-10-08): the Arabic words are given
        guidance = guidance.replace("a bookseller", "«موظف المكتبة»").replace("bookseller", "«موظف المكتبة»")
    if _q30_general:
        # CHATFIX30 (5): a general first message — no book on this turn: the model gets none to name, and asks one question
        it["intent"] = "clarify"
        facts = []
        _subj = " ".join(chat_rules.general_words(user_messages[-1])[0])
        guidance = ((f"This is the visitor's first message and it is still general{(' (only «' + _subj + '»)') if _subj else ''}. Do not"
                     " suggest or name any book yet (show_book_ids empty), and never say books are unavailable or that you cannot see"
                     " titles (we have many; the question is to pick the best for him). If he asked a question, answer it first in one"
                     " short sentence."
                     " Then ask ONE short, friendly question that narrows what he wants"
                     + (f" from «{_subj}» (which side of it, light or deep, a beginner or not)" if _subj else
                        " (mood, a recent favourite, light or deep, story or knowledge)") + ".")
                    if language == "en" else
                    (f"هذي أول رسالة من الزائر وطلبه ما زال عامًا{(' (قال «' + _subj + '» فقط)') if _subj else ''}. لا تقترح ولا تذكر أي"
                     " كتاب الآن (show_book_ids فارغة)، ولا تقل إن الكتب غير متوفرة أو إنك ما تشوف عناوين (عندنا كتب كثيرة، والسؤال"
                     " عشان نختار له الأنسب). إذا سأل سؤالًا فأجب عنه أولًا في جملة قصيرة. ثم اسأله سؤالًا واحدًا قصيرًا ولطيفًا"
                     " يوضّح طلبه" + (f" في «{_subj}» (أي جانب منه، خفيف ولا عميق، مبتدئ ولا متعمق)" if _subj else
                                       " (المزاج، آخر كتاب عجبه، خفيف ولا عميق، قصة ولا معرفة)") + "."))
        guidance += f" Today's date is {time.strftime('%Y-%m-%d')}."
    elif (it.get("intent") in ("recommend", "clarify") and not asked_books and not author_only and not similar_author
          and chat_rules.subject_question(user_messages[-1])):
        # CHATFIX30 (5): a visitor's own question about a subject («وش أهم شي في الفلسفة الرواقية؟») is answered first; books
        # follow only when they fit (inbox item 10: «أقترح أن تجيبني على سؤالي أولاً بمعزل عن الكتب التي تود اقتراحها»)
        guidance += (" The visitor asked a question: answer that question itself first, briefly (one or two sentences), before any"
                     " book; then offer books from facts only if they fit what he asked." if language == "en" else
                     " الزائر سأل سؤالًا: أجب عن سؤاله نفسه أولًا باختصار (جملة أو جملتين) قبل أي كتاب، ثم اعرض من facts الكتب"
                     " اللي تناسب سؤاله فقط إن وجدت.")
    # CHATFIX30b (2), Ibrahim 2026-10-30: «ممتاز خلها تساير السائل باسلوبه» — a visitor who writes فصحى is answered in فصحى
    # (the reply is also rewritten after the model in run_chat: chat_rules.fusha_rewrite)
    # QADFIX25 (replay of case 2.1): a second look's rewrite («هل لديكم رواية لرجاء عالم؟») is not the visitor's own register
    if language != "en" and chat_rules.visitor_register(
            register_msgs or user_messages, titles=list(it.get("mentioned_titles") or []) + [f.get("title") for f in facts]) == "fusha":
        guidance += chat_rules.FUSHA_GUIDANCE
        debug["register"] = "fusha"
    # CHATFIX30c (1): ratings only from data — goodreads_rating in facts, never a prize or an impression called a rating
    if _rating_wish and facts:
        if any(f.get("goodreads_rating") for f in facts):
            guidance += (" The visitor wants highly rated books: the books in facts that carry goodreads_rating come first, the"
                         " highest first. For each book you offer, give its rating as it is in facts, e.g. «its average on Goodreads"
                         " is 4.2 out of 5 (1,530 ratings)»; for a book with no goodreads_rating, say we have no rating for it."
                         " A prize is a prize, never a rating." if language == "en" else
                         " الزائر يبغى كتبًا تقييمها عالٍ: كتب facts اللي فيها goodreads_rating مرتّبة من الأعلى تقييمًا. لكل كتاب"
                         " تعرضه اذكر تقييمه كما هو في facts بالأرقام العربية، مثل «متوسط تقييمه على Goodreads ٤٫٢ من ٥ (١٬٥٣٠ تقييمًا)»؛"
                         " والكتاب اللي ما فيه goodreads_rating قل إن ما عندنا تقييم له. الجائزة جائزة وليست تقييمًا.")
        else:
            guidance += (" The visitor wants highly rated books, and none of the books in facts has a rating in our records: say"
                         " plainly that we have no rating for these books (call none of them highly rated), then offer them for"
                         " the other wishes." if language == "en" else
                         " الزائر يبغى كتبًا تقييمها عالٍ، وما في كتب facts أي كتاب له تقييم في سجلاتنا: قل بوضوح إن ما عندنا"
                         " تقييمات لهذه الكتب (ولا تصف أيًا منها بأن تقييمه عالٍ)، ثم اعرضها على باقي طلبه.")
        debug["rating_wish"] = [f["id"] for f in facts if f.get("goodreads_rating")]
    resp = (chat.respond(conv, json.dumps(facts, ensure_ascii=False), guidance, active, language)
            if language == "en" else chat.respond(conv, json.dumps(facts, ensure_ascii=False), guidance, active))
    if resp is None and missing_title:
        # QADFIX22 (QAD 2026-10-27, case 29.1): the model timed out on «"ساق البامبو" متوفرة؟» and the keyword fallback offered
        # «الشدياق والساق على الساق» for the word «ساق»: a title asked for and not found is said as such, with no stand-in
        _t = it["mentioned_titles"][0]
        return {"reply": (f"«{_t}» is not available here at the moment. Would you like something close to it?" if language == "en"
                          else f"«{_t}» غير متوفرة عندنا حاليًا. تبغى أقترح لك شيئًا قريبًا منها؟"),
                "books": [], "suggestions": [], "understanding": "ai", "provider": active[0], "model": active[1],
                "_debug": {"mode": "ai", "interpreted": debug, "failed_stage": "respond", "missing_title_answer": True}}
    if resp is None:
        out = chat_keyword_fallback(user_messages, all_books, set(shown), cards, language, last_ids=last_reply_ids)
        out["understanding"] = "keywords"
        out["_debug"].update(interpreted=debug, failed_stage="respond",
                             call=dict(chat.last_failure.get("call") or {}))
        return out
    if language != "en" and not it.get("visitor_female"):
        resp["reply"] = chat_rules.masculine_address(resp["reply"])   # QADFIX17 (case 45.2)
    if not any(f.get("thalath_readers") for f in facts):
        # QADFIX23 (QAD 2026-10-29, cases 9.1, 23.1, 38.1): «قرّاء مكتبة ثلاث وصفوا أجواءها…» with no approved review of the library's
        # readers in facts — the general readers' impressions are «بعض القرّاء», never the library's own readers
        resp["reply"] = thalath_readers_unclaimed(resp["reply"])
    # CHATFIX30c (1): «بعض القرّاء أعطوه تقييمًا عاليًا: فاز بالبوكر العربية 2026»، «وتقييم بعض القرّاء له مرتفع» — a rating claimed
    # for a book whose facts carry none is taken out (the prize stays, said as a prize)
    _rep0 = resp["reply"]
    resp["reply"] = chat_rules.strip_unsourced_rating(resp["reply"], [f.get("title") for f in facts
                                                                       if f.get("goodreads_rating") or f.get("thalath_readers")])
    if resp["reply"] != _rep0:
        debug["unsourced_rating_removed"] = True
    if it.get("list_all34"):
        # QADFIX34 #3: the whole list asked and the reply left some winners out: they are added in one sentence, before the closing question
        _miss34 = list_all_missing34(resp["reply"], it["list_all34"], all_books, language)
        if _miss34:
            resp["reply"] = list_all_completed34(resp["reply"], _miss34, all_books, language)
            debug["list_all_added34"] = [i for i, _y in _miss34]
    if _like_last:
        # INBOXFIX2: «رشّحي لي كتابًا» right after «ظل الريح» — the book just seen is not offered again
        # (««ظل الريح» نفسه متوفر عندنا إذا ما قرأته.» opened every replay answer)
        _lk = chat_rules.title_key(_like_last)
        # «"X" متوفر عندنا إذا ما قرأته، وأقرب الخيارات له …»: the opening clause about X goes, the rest stays
        resp["reply"] = re.sub(r"^\s*«" + re.escape(_like_last) + r"»\s*(?:نفسه\s*)?(?:متوفر|موجود)[^،.؟!]*[،]\s*و?", "",
                               resp["reply"]).strip()
        _parts = re.split(r"(?<=[.!؟?])\s+", resp["reply"])
        _kept = [x for x in _parts if not (_lk and _lk in chat_rules.title_key(x) and len(re.findall(r"«", x)) <= 1)]
        if _kept and len(_kept) < len(_parts):
            resp["reply"] = " ".join(_kept)
        resp["show_book_ids"] = [i for i in resp["show_book_ids"]
                                 if not (i in all_books and chat_rules.title_key(all_books[i]["title"]) == _lk)]
    # what the visitor asked and our data could not answer: short phrases only (layer 4, 2026-10-08)
    # CHATFIX30 (inbox 2026-10-29 §3 rows 4 and 6): «كتاب زمان الملائكه» was logged as «موضوع ما لقينا عنه كتابًا» while book 1412
    # «زمان الملائكة» was the answer, «كتاب تشريح الفيلم» as «رغبة لا نقدر نبحث بها» beside the missing book: a phrase that is the
    # title of a book named, asked about, offered or shown is never a subject or a wish (the missing one is logged below as such);
    # and a general first message answered with a question (CHATFIX30 (5)) searched for nothing, so nothing is logged for it
    _logged_titles = (list(it.get("mentioned_titles") or []) + [b.get("title") for b in asked_books]
                      + [f.get("title") for f in facts] + [all_books[i]["title"] for i in shown[-8:] if i in all_books]
                      + [r.get("title") for r in it.get("references") or [] if isinstance(r, dict)])
    for p in ([] if _q30_general else topic_phrases):
        if (p not in topics_found and p not in topics_mentioned and p not in topics_semantic
                and not chat_rules.phrase_is_title(p, _logged_titles)):
            open_questions.record("topic", p)
    for p in ([] if _q30_general else unsupported):
        if p not in topic_phrases and not chat_rules.phrase_is_title(p, _logged_titles):
            open_questions.record("wish", p)
    if missing_title:
        open_questions.record("missing_book", it["mentioned_titles"][0])
    if resp.get("unanswered"):
        open_questions.record("question", resp["unanswered"], asked_books[0]["title"] if asked_books else None)
    # a book the visitor named is shown whatever the earlier wishes (language, length ...), but
    # never past the age check (library trial, 2026-10-08)
    if it.get("ya_out30b"):
        debug["ya_unknown_out30b"] = it["ya_out30b"][:20]   # QADFIX30b
    valid = {f["id"] for f in facts if f["available"] and f["id"] not in _cmp_ids and f["id"] not in _rejected_ids
             and f["id"] not in set(it.get("ya_out30b") or ())   # QADFIX30b: never a card, whatever path brought it
             and not (_ya_pred30b and f["id"] in all_books and _ya_pred30b(all_books[f["id"]])
                      and reason_by_id.get(f["id"], ("", ""))[1] != "asked")   # QADFIX31 #3 (case 5.1)
             and ((f["id"] in suitable and f["id"] in strict)
                  # QADFIX15 (case 36.2): «أي منها الأقصر؟» — the books just shown are the ones asked about, whatever the
                  # filters carried from the wording; they suited this reader when they were shown
                  or (_shown_only and f["id"] in last_batch)
                  # the book the visitor names now is his own latest wish; a book whose title is the subject asked for is not,
                  # so a kind he refused in words keeps it out (AUDFIX, Codex audit 2026-10-25)
                  or ((reason_by_id[f["id"]][1] == "asked"
                       or (f["id"] in topic_title_ids and f["id"] in all_books
                           and refusals_ok(all_books[f["id"]], cards.get(f["id"]), prefs, exclusions)))
                      and age_allowed(cards.get(f["id"]), pprefs.get("audience"), pprefs.get("avoid") or (),
                                      all_books.get(f["id"]), pprefs.get("reader_age"), _cartoon_id(f["id"])))
                  or f["id"] in _named_card_ok
                  # QADFIX30 #5 (replay of QAD 2026-11-07 case 17.2): a book of the young readers' shelf offered for a teenager
                  # («يد ملأى بالنجوم») is not dropped for a subgenre its record does not carry; its refusals and the age check hold
                  or (f["id"] in {x[2]["book_id"] for x in ya_added30} and f["id"] in all_books
                      and refusals_ok(all_books[f["id"]], cards.get(f["id"]), prefs, exclusions)
                      and age_allowed(cards.get(f["id"]), pprefs.get("audience"), pprefs.get("avoid") or (),
                                      all_books.get(f["id"]), pprefs.get("reader_age"), _cartoon_id(f["id"])))
                  # QADFIX32b (Ibrahim «أ»): a children's-shelf book offered for a reader of 12–15 whose recorded age covers him, likewise
                  or (f["id"] in {b["book_id"] for _r, b in kid_added32} and f["id"] in all_books
                      and refusals_ok(all_books[f["id"]], cards.get(f["id"]), prefs, exclusions)
                      and age_allowed(cards.get(f["id"]), pprefs.get("audience"), pprefs.get("avoid") or (),
                                      all_books.get(f["id"]), pprefs.get("reader_age"), _cartoon_id(f["id"])))
                  # QADFIX18: the nearest book by an author from the asked country is offered as «on a list», whatever the
                  # other wishes it does not meet (the reply says so); never past the age check nor a refused kind
                  # QADFIX18b (AGY-20261024-02): a book of the kind of the work named (its title carries that work's name);
                  # only the guessed form is eased, every stated wish and refusal still holds (_kind_ok, AUDFIX 2026-10-25)
                  or ((f["id"] in _kind_ids or (reason_by_id[f["id"]][1] == "reference" and not _form_named))
                      and f["id"] in all_books and _kind_ok(all_books[f["id"]]))
                  or (f["id"] in _near_ids and f["id"] in all_books
                      and refusals_ok(all_books[f["id"]], cards.get(f["id"]), prefs, exclusions)
                      and age_allowed(cards.get(f["id"]), pprefs.get("audience"), pprefs.get("avoid") or (),
                                      all_books.get(f["id"]), pprefs.get("reader_age"), _cartoon_id(f["id"]))))}
    ids = []
    for i in resp["show_book_ids"]:
        if i in valid and i not in ids:
            ids.append(i)
    # QADFIX22 (QAD 2026-10-27, case 17.1): «ابي رواية ملحمية عن عيلة عبر أجيال» answered with a question only while «آل بودنبروك»
    # was in facts: when books matching the request are in facts and the guidance does not hold them back, a reply with no book
    # is asked again once, to offer them
    _direct_ok = [f["id"] for f in facts if f["id"] in valid and reason_by_id.get(f["id"], ("", ""))[1] == "direct"]
    if (not ids and it.get("intent") == "recommend" and len(_direct_ok) >= 2 and not missing_title
            and not any(w in guidance for w in ("Show no books", "لا تعرض أي كتاب", "show_book_ids فارغة", "show_book_ids empty"))):
        _again = (chat.respond(conv, json.dumps(facts, ensure_ascii=False), guidance + (
                      " Your previous draft only asked a question: offer 2–4 of the matching books in facts now, each with why it fits;"
                      " a short question may follow." if language == "en" else
                      " مسودتك السابقة كانت سؤالًا فقط: اعرض الآن 2–4 من الكتب المطابقة في facts مع سبب مناسبة كل واحد، ويجوز سؤال قصير بعدها."),
                                active, language) if language == "en" else
                  chat.respond(conv, json.dumps(facts, ensure_ascii=False), guidance +
                               " مسودتك السابقة كانت سؤالًا فقط: اعرض الآن 2–4 من الكتب المطابقة في facts مع سبب مناسبة كل واحد، ويجوز سؤال قصير بعدها.",
                               active))
        if _again and [i for i in _again["show_book_ids"] if i in valid]:
            resp = _again
            if language != "en" and not it.get("visitor_female"):
                resp["reply"] = chat_rules.masculine_address(resp["reply"])
            ids = []
            for i in resp["show_book_ids"]:
                if i in valid and i not in ids:
                    ids.append(i)
            debug["asked_again_for_books"] = True
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
    # QADFIX15 (case 36.2): an English reply quotes titles with "…" or “…”, not «…»
    _quoted = [chat_rules.title_key(q) for g in re.findall(r'«([^»]{2,120})»|"([^"]{2,120})"|“([^”]{2,120})”', resp["reply"])
               for q in g if q]
    _reply_key = " " + chat_rules.title_key(re.sub(r'«[^»]{2,120}»|"[^"]{2,120}"|“[^”]{2,120}”', " | ", resp["reply"])) + " "
    _in_play = set(resp.get("show_book_ids") or []) | set(last_batch or [])

    def _same_title_other32(i, chosen):
        """QADFIX32 (case 45.1): a chosen card has this book's title by another author, and the reply does not name this author."""
        tk = chat_rules.title_key(all_books[i].get("title") or "")
        au = author_flow.display(all_books[i].get("author"))
        if not any(j != i and j in all_books and chat_rules.title_key(all_books[j].get("title") or "") == tk
                   and author_flow.display(all_books[j].get("author")) != au for j in chosen):
            return False
        words = [w for w in chat_rules.title_key(au).split() if len(w) >= 3]
        return not any(re.search(r"(?:^| )[لوب]?(?:ال)?" + re.escape(strip_al(w)) + r"(?: |$)", _reply_key) for w in words)

    # QADFIX32b (Ibrahim 2026-11-09 14:0x on QADFIX32's question 2: «ب»; QAD 2026-11-09 case 31.1): «عندنا خمسة كتب فازت بالميدالية …»
    # named all five and showed four cards (the card limit) — a reply that names a complete list of exactly five works in stock from
    # facts, and says «five», shows all five; otherwise four, as before
    _five32 = five_list32(facts, all_books, _quoted, resp.get("reply") or "", last_batch or ())
    _cap32 = 5 if _five32 else 4
    if _five32:
        debug["five_cards32"] = _five32

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
        # QADFIX15 (case 36.2): a one-word title («Atonement») counts for a book shown or chosen in this turn; a one-word
        # title of any other book stays a word of the reply («الجليد» inside «أغنية الجليد والنار»)
        return any((len(f.split()) >= 2 or (i in _in_play and len(f) >= 5)) and (" " + f + " ") in _reply_key for f in forms)
    # QADFIX16 (QAD 2026-10-24, cases 24.2, 25.2): a book this turn's checks dropped from the model's choice, or one the reply
    # names only to rule it out, never gets its card back from being named
    # QADFIX25 (QAD 2026-10-31, case 20.2): «ولو تبي شي بالإنجليزي؟» — «A Game of Thrones» (part 1) was dropped by the checks while
    # «A Feast for Crows» (part 4) of the same series stayed: the reply lost its first sentence and began «و«A Feast for Crows»…»
    # with «الكتابان». An earlier part of a series whose later part is valid is valid too (same reader checks)
    _vser25 = {}
    for i in valid:
        _b = all_books.get(i) or {}
        if (_b.get("series_name") or "").strip() and isinstance(_b.get("series_position"), (int, float)):
            _k = (_b["series_name"].strip(), (_b.get("language") or "ar").strip() or "ar")
            _vser25[_k] = max(_vser25.get(_k, 0), _b["series_position"])
    for f in facts:
        _b = all_books.get(f["id"]) or {}
        _k = ((_b.get("series_name") or "").strip(), (_b.get("language") or "ar").strip() or "ar")
        if (f["id"] not in valid and f["available"] and _k in _vser25 and isinstance(_b.get("series_position"), (int, float))
                and _b["series_position"] < _vser25[_k] and f["id"] not in _cmp_ids and f["id"] not in _rejected_ids
                and age_allowed(cards.get(f["id"]), pprefs.get("audience"), pprefs.get("avoid") or (), _b,
                                pprefs.get("reader_age"), _cartoon_id(f["id"]))):
            valid.add(f["id"])
    _dropped_now = {i for i in resp["show_book_ids"] if i not in valid}
    _excluded_reply = set(resp.get("excluded_book_ids") or [])
    # QADFIX23 (QAD 2026-10-28, case 16): «عندكم شي لماركيز غير مية عام من العزله؟» — a title the visitor ruled out in his own
    # words («غير / عدا / ما عدا / سوى / بدون X») is never brought back as a card by being named in the reply
    _ruled_out = visitor_ruled_out([f["id"] for f in facts] + list(ids), all_books, user_messages[-3:]) | _like_ids
    _excluded_reply |= _ruled_out
    ids = [i for i in ids if i not in _ruled_out]
    for f in facts:
        i = f["id"]
        _k = (" ".join(normalize(all_books[i]["title"]).split()), " ".join(normalize(all_books[i].get("author")).split())) if i in all_books else None
        if i in _dropped_now or i in _excluded_reply:
            continue
        _tp24 = title_part(all_books[i].get("title")) if i in all_books else None
        if _tp24 and any((title_part(all_books[j].get("title")) or (None,))[0] == _tp24[0] for j in ids if j in all_books):
            # QADFIX24 (replay of QAD 2026-10-30 cases 45.1, 49.1): the reply named part 1 («الزميل: الانتقام الهادئ ج1»); parts 2
            # and 3 share its head title and were taken as named too — one card per series named by title
            continue
        if i in all_books and _same_title_other32(i, ids):
            # QADFIX32 (QAD 2026-11-09 case 45.1, minor): «الأخلاق» لأرسطو in the reply brought the card of «الأخلاق» by G. E. Moore too —
            # another work of the same title gets its card only when the reply names its author as well
            continue
        if (len(ids) < _cap32 and f["available"] and i not in ids and i in all_books and _k not in _seen_books and _named_in_reply(i)
                and age_allowed(cards.get(i), pprefs.get("audience"), pprefs.get("avoid") or (), all_books.get(i),
                                pprefs.get("reader_age"), _cartoon_id(i))
                # AUDFIX 2026-10-25: named in the reply is no way back for a kind the visitor refused in words (a book he
                # asked about by name stays his own wish)
                and (reason_by_id.get(i, ("", ""))[1] == "asked" or refusals_ok(all_books[i], cards.get(i), prefs, exclusions))):
            ids.append(i)
            _seen_books.add(_k)
    # QADFIX33 (QAD 2026-11-10 cases 1.1–1.2, medium): «ثلاثية جهينة … الجزء الأول، والجزء الثاني، والجزء الثالث» / «ابدأ بجهينة ج1» — the
    # parts of the series asked about that the reply names by their number get their cards (first, in their order)
    _sp33 = [i for i in series_parts_named33(resp.get("reply") or "", [b for b in asked_books if is_available(b)])
             if i in all_books and i not in _excluded_reply and i not in _cmp_ids and i not in _rejected_ids
             and age_allowed(cards.get(i), pprefs.get("audience"), pprefs.get("avoid") or (), all_books.get(i),
                             pprefs.get("reader_age"), _cartoon_id(i))]
    if _sp33:
        ids = _sp33 + [i for i in ids if i not in _sp33]
        for i in _sp33:
            if reason_by_id.get(i, ("", ""))[1] != "asked":
                reason_by_id[i] = (("Asked for by its series, and it is in stock." if language == "en" else "سألت عن سلسلته، وهو متوفر عندنا."), "asked")
        debug["series_parts33"] = _sp33
    # QADFIX27 #9 (QAD 2026-11-04 case 24.1): «فيه كتاب اسمه شي زي «لأنك الله»؟» → «إيه، «لأنك الله» بجزأيه متوفر» with the cards of two
    # other books only: the title asked for, quoted in the reply as in stock, gets its card(s) first even when the full title is
    # longer («لأنك الله – الجزء الأول: …», «لأنك الله: معراج النفوس …»)
    _mk27c = {chat_rules.title_key(m) for m in it["mentioned_titles"] or [] if len(chat_rules.title_key(m)) >= 4}
    _qr27 = {chat_rules.title_key(q) for q in re.findall(r"«([^»]{2,80})»", resp.get("reply") or "")}
    if _mk27c & _qr27 and not chat_rules.says_not_found(resp["reply"]) and not author_only:
        def _head27(b):
            tk = chat_rules.title_key(b.get("title") or "")
            return any(tk == k or (tk.startswith(k) and not tk[len(k):len(k) + 1].isalnum()) for k in _mk27c & _qr27)
        _pin27 = [b["book_id"] for b in asked_books if is_available(b) and b["book_id"] not in ids and _head27(b)
                  and b["book_id"] not in _excluded_reply and b["book_id"] not in _cmp_ids and b["book_id"] not in _rejected_ids
                  and age_allowed(cards.get(b["book_id"]), pprefs.get("audience"), pprefs.get("avoid") or (), b,
                                  pprefs.get("reader_age"), _cartoon_id(b["book_id"]))][:2]
        if _pin27:
            for i in _pin27:
                reason_by_id[i] = (("Asked for by its title, and it is in stock." if language == "en" else "سألت عنه بعنوانه، وهو متوفر عندنا."), "asked")
            ids = (_pin27 + ids)[:4] if _cap32 == 4 else (_pin27 + ids)[:_cap32]   # QADFIX32b: five for a complete list of five
            debug["pinned_asked27"] = _pin27
    # QADFIX5 #6 (QAD 2026-10-17, case 7.1): a stocked book the reply names in «…» that is not in facts (the model
    # knew «ياسمين والمولود الجديد» but the search had not passed it) gets its card too, when its whole title
    # matches one book in stock and it suits the reader
    if len(ids) < _cap32 and _quoted:
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
            # AUDFIX 2026-10-25 (Codex audit P2): a book of a kind the visitor refused in words («بدون واقعية سحرية») does not
            # get its card by being quoted in the reply
            if not refusals_ok(b, cards.get(b["book_id"]), prefs, exclusions):
                continue
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
        ids = [i for i in ids if not (reason_by_id[i][1] == "asked" and not _named_in_reply(i) and i not in _sp33   # QADFIX33 (1.2)
                                      and chat_rules.title_key(all_books[i].get("title") or "") not in
                                      {chat_rules.title_key(t) for t in it["mentioned_titles"]})]
    # still asking (fewer than two questions on a vague request): no book yet, whatever the model chose
    if it["intent"] == "clarify" and streak < 2 and not skip_questions and not asked_books:
        # … except a book the question itself names («عندنا «ذواتا أفنان» من منشورات حياة، تبغى شي مثله؟»): the text and
        # the cards agree (QADFIX6, QAD 2026-10-18 case 22.1; open since 10-15 m3)
        ids = [i for i in ids if _named_in_reply(i)]
    if ref_unmatched_only:  # nothing chosen for likeness: no book, whatever the model chose
        ids = []
    if _q30_general:        # CHATFIX30 (5): a general first message gets a question, no book
        ids = []
    if missing_title:
        # CHATFIX30 (1): «"تشريح الفيلم" غير متوفر عندنا، وما لقيت … شيئًا قريبًا منه كفاية» and still two anatomy books: a book
        # asked for and missing gets only books chosen for their likeness to it, and none when nothing close was found
        ids = ([i for i in ids if reason_by_id.get(i, ("", ""))[1] in ("reference", "asked")]
               if any(n[2] for n in ref_notes) else [])
    if topic_nothing:       # nothing about the subject asked for: no book; the visitor is asked instead
        # … except a book shown earlier that the reply names again as the closest (QADFIX5 #9, case 36.3: the parent
        # guide named without its card)
        ids = [i for i in ids if i in shown and _named_in_reply(i)]
    if _rejected_ids:
        ids = [i for i in ids if i not in _rejected_ids]   # QADFIX15 (case 4.2): ruled out, never a card
    if _excluded_reply:
        ids = [i for i in ids if i not in _excluded_reply]   # QADFIX16 (case 25.2): the reply ruled it out
    if q7.get("only_won28") in all_books and is_available(all_books[q7["only_won28"]]):
        ids = [q7["only_won28"]]   # QADFIX28 #1 (QAD 2026-11-04 case 8.2): the one winner by a woman, alone
    if q7.get("before_none28"):
        # QADFIX28 (replay of QAD 2026-11-05 case 45.2): «قادة الفكر» (فكر) and «مرتفعات ويذرنج» (translated) were offered for «رواية
        # عربية قبل الثلاثينات» — the cards are the oldest of his kind only
        _bi28 = [i for i in q7["before_none28"][3] if i in all_books and is_available(all_books[i])]
        ids = [i for i in ids if i in _bi28] or _bi28[:2]
    if it.get("about_first28"):
        # QADFIX28 (QAD 2026-11-05 case 34.1): the card of the book about the subject before one whose summary only mentions it
        ids = [i for i in ids if i in it["about_first28"]] + [i for i in ids if i not in it["about_first28"]]
    if _shown_only and order_first and order_first["book_id"] in ids:
        # QADFIX15 (case 36.2): «أيها الأقصر/الأرخص/الأقدم؟» — the card of the one that answers comes first
        ids = [order_first["book_id"]] + [i for i in ids if i != order_first["book_id"]]
    if order_by == "dearest" and order_first and ids and ids[0] != order_first["book_id"]:
        # QADFIX30 #1 (replay of QAD 2026-11-07 case 46.2): «… على الإطلاق» after «The Cosmic Script» was shown → «أغلى كتاب فنون عندنا
        # هو «شروق وغروب - غلاف لين» ٣٦٠» (the model skipped the books shown): when the reply names it, its card comes first
        if order_first["book_id"] in ids or chat_rules.title_key(order_first["title"]) in chat_rules.title_key(resp.get("reply") or ""):
            ids = [order_first["book_id"]] + [i for i in ids if i != order_first["book_id"]]
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
    # QADFIX17 (QAD 2026-10-24, case 19.1): two parts of one series show the earlier part first («A Game of Thrones» (1) before
    # «A Feast for Crows» (4)); the other cards keep their places
    _ser = {}
    for n, i in enumerate(ids):
        _sn = (all_books[i].get("series_name") or "").strip() if i in all_books else ""
        if _sn and isinstance(all_books[i].get("series_position"), (int, float)):
            _ser.setdefault(_sn, []).append(n)
    for _slots in _ser.values():
        if len(_slots) >= 2:
            _parts = sorted((ids[n] for n in _slots), key=lambda i: all_books[i]["series_position"])
            for n, i in zip(_slots, _parts):
                ids[n] = i
    # QADFIX29 #7 (QAD 2026-11-06 cases 37.1, 42.1): a third card the reply never names («الذكاء الإصطناعي» with no author and no
    # summary, «مختارات من ديوان شمس تبريزي ج1») — when the reply names two others, an unnamed third or fourth card goes, and an
    # unnamed card with no summary; the first two cards stay (an omnibus the reply forgot to name is still the answer, case 3.1)
    if len(ids) >= 3 and resp.get("reply"):
        # QADFIX33 (QAD 2026-11-10 case 24.2, minor): «سِلْسِلَة القِيَم الإنْسَانِيَّة: كَيْفَ أكُون مُحْتَرَمًا» named in the reply as «كيف
        # أكون محترمًا», «سماح و خطة النّجاح» as «سماح وخطة النجاح»: titles and reply are compared without diacritics, with «و» joined to
        # its word, and a card that shares a series head with another is named by the rest of its title (after «:»)
        def _k33(s):
            _s = re.sub(r"[«»\"“”()\[\]،,.:：؛;!?؟]", " ", chat_rules.title_key(s or ""))
            return " ".join(re.sub(r"(?:^| )و (?=\S)", " و", " " + " ".join(normalize(_s).split())).split())
        _rk29 = " " + _k33(resp["reply"]) + " "
        def _head29(i):
            return _k33(re.split(r"\s*[:：(\[–—-]\s*", all_books[i].get("title") or "")[0]) if i in all_books else ""
        # QADFIX33 (case 50.1, minor): «الثقوب السوداء» (5370, no author) kept as named because the reply speaks of «الثقوب السوداء»
        # (the subject asked): a card whose whole title is the subject is named only when the reply quotes it
        _subj33 = {_k33(p) for p in list(topic_phrases or []) + list(unsupported or [])}
        _qk33 = {_k33(x) for g in re.findall(r'«([^»]{2,120})»|"([^"]{2,120})"|“([^”]{2,120})”', resp["reply"]) for x in g if x}
        def _named29(i):
            _k = _head29(i)
            if _k and sum(1 for j in ids[:_cap32] if _head29(j) == _k) >= 2:
                # QADFIX30 (QAD 2026-11-07 case 3.1, minor): «سلسلة الكَوْن: القَمر» and «… الشَّمْس» were kept unnamed because the reply
                # names «سلسلة الكَوْن: الفَضاء» — cards that share a series head are named by the rest of their title
                _k = _k33(all_books[i].get("title") or "")
                _tail33 = _k33(re.split(r"\s*[:：]\s*", all_books[i].get("title") or "", maxsplit=1)[-1])
                if _tail33 and _tail33 != _k and len(_tail33) >= 6 and re.search(r"(?:^| )[وبلف]?" + re.escape(_tail33) + r"(?: |$)", _rk29):
                    return True
            if _k in _subj33 and _k not in _qk33 and _k33(all_books[i].get("title") or "") == _k:
                return False
            # QADFIX31 (QAD 2026-11-08 case 4.1, minor): «رأس المال» (1182) counted as named because the reply names «رأس المال في القرن
            # الحادي والعشرون» (5269) — a title found only inside another card's longer title is not named
            _rk31 = _rk29
            for j in ids[:_cap32]:
                _kj = _k33(all_books[j].get("title") or "") if j != i and j in all_books else ""
                if _k and _kj and _kj != _k and (" " + _k + " ") in (" " + _kj + " "):
                    _rk31 = _rk31.replace(" " + _kj + " ", " ")
            # QADFIX33 (case 50.1): «… وتاريخ موجز للزمان …» — a title may carry «و / ب / ل» in front of it
            return bool(_k) and bool(re.search(r"(?:^| )[وبلف]?" + re.escape(_k) + r"(?: |$)", _rk31))
        _nm29 = [i for i in ids[:_cap32] if _named29(i)]
        if len(_nm29) >= 2:
            _dropn29 = [i for n, i in enumerate(ids[:_cap32]) if i not in _nm29 and reason_by_id.get(i, ("", ""))[1] not in ("asked", "reference")
                        and (n >= 2 or not (all_books.get(i, {}).get("summary") or "").strip())]
            if _dropn29:
                ids = [i for i in ids if i not in _dropn29]
                debug["unnamed_cards29"] = _dropn29
    # QADFIX33 (QAD 2026-11-10 case 13.2, minor): «وفيه رواية ثانية بنفس الاسم لكاتب غيره؟» → «الجزيرة» لراغنار يوناسون with the card of
    # Huxley's «الجزيرة» (shown the turn before) still beside it: of two cards of one title by different authors, the one whose author the
    # reply does not name goes when the reply names the other's author (QADFIX32's rule, the other way round)
    if len(ids) >= 2 and resp.get("reply"):
        _st33 = [i for i in ids[:_cap32] if i in all_books and _same_title_other32(i, ids[:_cap32])]
        _keep33 = [i for i in ids[:_cap32] if i in all_books and i not in _st33 and any(
            j in _st33 and chat_rules.title_key(all_books[j].get("title") or "") == chat_rules.title_key(all_books[i].get("title") or "")
            for j in ids[:_cap32])]
        _drop33 = [j for j in _st33 if any(chat_rules.title_key(all_books[j].get("title") or "") ==
                                           chat_rules.title_key(all_books[i].get("title") or "") for i in _keep33)]
        if _drop33:
            ids = [i for i in ids if i not in _drop33]
            debug["same_title_other33"] = _drop33
    # QADFIX30 (QAD 2026-11-07 case 29.1, minor): «لاعب الشطرنج» of زفايغ in two editions (4206, 4351) as two cards, the reply naming it
    # once: a second edition of a card's work is a card only when the reply speaks of editions
    if len(ids) >= 2 and resp.get("reply") and not re.search(r"طبع|نسخت|نسختين|نسخ |edition|copies|two copies", normalize(resp["reply"])):
        _seen30, _dup30 = [], []
        for i in ids[:_cap32]:
            b = all_books.get(i) or {}
            _tk30, _aw30 = chat_rules.title_key(b.get("title") or ""), set(normalize(b.get("author") or "").split())
            if any(_tk30 and _tk30 == t and (_aw30 & a) for t, a in _seen30) and reason_by_id.get(i, ("", ""))[1] != "asked":
                _dup30.append(i)
            else:
                _seen30.append((_tk30, _aw30))
        if _dup30:
            ids = [i for i in ids if i not in _dup30]
            debug["edition_cards30"] = _dup30
    # QADFIX17 (QAD 2026-10-24, case 24.3): a book this turn's checks dropped is not named in the reply either — the sentence
    # that offers it goes (a book the visitor asked about by name may still be talked about)
    _gone = [i for i in _dropped_now if i in all_books and i not in ids[:_cap32] and reason_by_id.get(i, ("", ""))[1] != "asked"
             and i not in {b["book_id"] for b in asked_books}]
    if _gone:
        _gk = [k for k in (chat_rules.title_key(re.split(r"\s*[:：(\[–—-]\s*", all_books[i].get("title") or "")[0]) for i in _gone)
               if k and (len(k.split()) >= 2 or len(k) >= 5)]
        _sents = re.split(r"(?<=[.!؟?؛])\s+", resp["reply"])
        _keep, _cut = [], False
        for _s in _sents:
            if any((" " + k + " ") in (" " + chat_rules.title_key(_s) + " ") for k in _gk):
                _cut = True
                continue
            # QADFIX23 (QAD 2026-10-28, case 15.2): the sentence right after the dropped book's that names no title and asks
            # nothing goes on about that book («يتناول الفضاء وبأسلوب قصص مصورة…») — it goes too
            if (_cut and "«" not in _s and not re.search(r"[؟?]\s*$", _s)
                    and (not _keep or re.match(r"\s*(?:و?(?:ي|ت)(?:تناول|دور|حكي|روي|قدم|ناسب)|و?(?:هو|هي|فيه|فيها|بأسلوب|باسلوب|قصص|احداثه|أحداثه|احداثها|أحداثها))", _s))):
                continue
            _cut = False
            _keep.append(_s)
        if _keep and len(_keep) < len(_sents) and all(re.search(r"[؟?]\s*$", x) for x in _keep):
            # QADFIX29 #6 (QAD 2026-11-06 case 31.2): «والإلياذة نفسها مو المصورة؟» → the whole reply «تحب تعرف أكثر عن النسخة المصورة؟»
            # once the graphic novel he ruled out was dropped: a reply left with a question only says what the answer is
            _miss29 = [b for b in match_titles(it.get("mentioned_titles") or [], all_books) if not is_available(b)] if it.get("mentioned_titles") else []
            _lead29 = ((f"«{_miss29[0]['title']}» itself is not in stock now." if language == "en"
                        else f"«{_miss29[0]['title']}» نفسها غير متوفرة عندنا الآن.") if _miss29
                       else ("I found nothing else that fits your request now." if language == "en" and shown
                             else "I found nothing that fits your request now." if language == "en"
                             # QADFIX35 #1 (case 28.1): «كتابًا ثانيًا» was said in the first turn, with no book shown before
                             else "ما لقيت الآن كتابًا ثانيًا يناسب طلبك." if shown else "ما لقيت الآن كتابًا يناسب طلبك."))
            _keep = [_lead29, ("Would you like something close to it?" if language == "en" else "تحب أقترح لك شيئًا قريبًا منه؟")]
        if _keep and len(_keep) < len(_sents):
            # QADFIX25 (case 20.2): the sentence left first no longer follows another — «و«A Feast for Crows»…» loses its «و»
            _keep[0] = re.sub(r"^\s*و(?=«|[A-Za-z])", "", _keep[0])
            resp["reply"] = " ".join(_keep)
            debug["reply_trimmed"] = _gone
        # QADFIX23 (cases 15.2, 50.2): no helper names a book the reply no longer offers («قولي لي عن ألترامان»)
        _gw = {w for i in _gone for w in chat_rules.title_key(normalize(re.split(r"\s*[:：(\[–—-]\s*", all_books[i].get("title") or "")[0])).split()
               if len(w) >= 5}
        if _gw:
            resp["suggestions"] = [c for c in (resp.get("suggestions") or [])
                                   if not ({strip_al(w) for w in chat_rules.title_key(normalize(c)).split()} & {strip_al(w) for w in _gw})]
    books = []
    for i in ids[:_cap32]:
        why, match = reason_by_id[i]
        # a book shown earlier that still fits is not «a book you asked about» (2026-10-06)
        reason = (why if match in ("direct", "filler", "related", "reference")
                  else ("Shown to you earlier, and it still fits." if language == "en" else "عرضته لك قبل، وما زال يناسب طلبك.") if match == "shown"
                  else "سألت عنه، وهو متوفر الآن.")
        if match in ("direct", "filler", "related"):
            reason = card_line(reason, all_books[i], language)   # CHATFIX31 (1a): never the bare shelf when the book says more
        books.append(dict(all_books[i], reason=reason, match=match))
    if not books and chat_rules.says_not_found(resp["reply"]):
        # QADFIX5 #15 (QAD 2026-10-17, case 16.1): «الحزام غير موجود» with chips about a book («كم سعره؟ وين موقعه؟») —
        # no card means nothing to ask about it; the chips offer the way on instead
        _about_it = re.compile(r"(?:عنه|عنها|سعره|سعرها|موقعه|موقعها|صفحاته|صفحاتها|مؤلفه|مؤلفها|its |it\?|about it|price|where is it)")
        _kept = [c for c in (resp.get("suggestions") or []) if not _about_it.search(normalize(c))]
        resp["suggestions"] = _kept or (["Something similar", "Suggest something to my taste"] if language == "en"
                                         else ["كتب قريبة منه", "رشّحي لي حسب ذوقي"])
    debug["named_kind"] = _named_kind_dbg
    debug["model_book_ids"] = resp["show_book_ids"]
    debug["reference"] = _ref_dbg
    debug["calls"] = {"interpret": interpret_call, "respond": dict(llm.last_call)}
    debug["dropped_ids"] = [i for i in resp["show_book_ids"] if i not in valid]
    debug["excluded_ids"] = sorted(_excluded_reply)
    # CHATFIX31 (1b): a reply that still runs far over the 45–60 words asked of the model is cut at sentence boundaries; the
    # first sentence, every sentence naming a book and the final question stay
    resp["reply"], _trimmed = chat_rules.trim_reply(resp["reply"], [b.get("title") for b in books])
    if _trimmed:
        debug["trimmed"] = _trimmed
    # CHATFIX30c (4): «… وبعض القرّاء يحسّون «التميمة» قراءة خفيفة…» — a card book named without its author gets him at its
    # first mention («التميمة» لنبيل فاروق)
    resp["reply"] = chat_rules.introduce_authors(resp["reply"], [(b.get("title"), author_flow.display(b.get("author") or ""))
                                                                 for b in books], "en" if language == "en" else "ar")
    return {"reply": resp["reply"], "books": [with_origin(b, language) for b in books], "suggestions": resp["suggestions"],
            "understanding": "ai", "provider": active[0], "model": active[1],
            "audience": pprefs.get("audience"),
            "audience_age": audience_age(user_messages) if pprefs.get("audience") else None,
            "audience_gender": audience_gender(user_messages) if pprefs.get("audience") else None,
            "context": chat_context(prefs, pprefs, user_messages, publisher_name, country if country and not country_missing else None,
                                    topics_found + [x for x in topics_mentioned if x not in topics_found], language,
                                    edition if edition and not edition_missing else None,
                                    # CHATFIX31 (3): a carried «جديدة» keeps its badge even when this request has none that new,
                                    # so the visitor sees it is still on and can remove it
                                    pub_years if pub_years and (not pub_missing or _new_wish == "carried") else None,
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


def _favorites_answer(all_books, shown, language, active):
    """«تفضيلات قرّاء مكتبة ثلاث» (Ibrahim 2026-10-27): the books in stock this library's own readers rated highest in the
    reviews staff approved (visitor_reviews.favorites); offered only once enough books have reviews (READY_BOOKS)."""
    en = language == "en"
    meta = {"provider": active[0] if active else None, "model": active[1] if active else None}
    works = visitor_reviews.by_work()
    if not visitor_reviews.ready(works):
        reply = ("Not enough Thalath readers have shared their reviews yet. You can share yours from «What are you reading now?» "
                 "on the start page. Meanwhile, tell me what you are in the mood for." if en else
                 "لسّا ما تجمّعت عندنا آراء كافية من قرّاء مكتبة ثلاث. تقدر تشاركنا رأيك في كتاب قرأته من «ماذا تقرأ الآن؟» "
                 "في الصفحة الأولى، وإلى ذاك قل لي وش مزاجك اليوم؟")
        chips = helper_chips.pad([], False, language)
        return dict({"reply": reply, "books": [], "suggestions": chips, "understanding": "ai",
                     "_debug": {"mode": "thalath_favorites", "ready": False}}, **meta)
    stock = [b for b in all_books.values() if is_available(b)]
    favs = visitor_reviews.favorites(stock, exclude=set(shown), limit=4)
    def line(s):
        tags = "، ".join(t["label"] for t in s["tags"][:2]) if not en else ", ".join(t["label"] for t in s["tags"][:2])
        avg = f"{s['avg']:g}"
        if en:
            return f"Thalath readers: {avg} out of 5 ({s['count']} {'reader' if s['count'] == 1 else 'readers'})" + (f" · {tags}" if tags else "")
        return f"قرّاء مكتبة ثلاث: {avg} من ٥ ({s['count']} {'قارئ' if s['count'] == 1 else 'قرّاء'})" + (f" · {tags}" if tags else "")
    books = [with_origin(dict(b, reason=line(s)), language) for b, s in favs]
    if books:
        reply = ("These are the books in stock that Thalath readers rated highest. Tap one to see what they said."
                 if en else "هذي من الكتب المتوفرة اللي أعطاها قرّاء مكتبة ثلاث أعلى تقييم. اضغط على أي واحد وتشوف وش قالوا عنه.")
        chips = [helper_chips.FAVORITES_MORE[language if en else "ar"]]
    else:
        reply = ("You have seen all of our readers' favourites in stock for now. Tell me what you are in the mood for and I will look further."
                 if en else "شفت كل تفضيلات قرّائنا المتوفرة الحين. قل لي وش مزاجك وأدوّر لك أكثر.")
        chips = []
    chips = helper_chips.pad(chips, bool(books), language)
    return dict({"reply": reply, "books": books, "suggestions": chips, "understanding": "ai",
                 "_debug": {"mode": "thalath_favorites", "ready": True, "books": [b["book_id"] for b in books]}}, **meta)


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
    try:
        # at least five tap-ready helpers (Ibrahim 2026-10-27: «اقل شيء ٥ اختيارات مساعدة»)
        if isinstance(res, dict) and isinstance(res.get("suggestions"), list):
            _lang = "en" if (language == "en" or _english_reply(res)) else "ar"
            _dbg = res.get("_debug") if isinstance(res.get("_debug"), dict) else {}
            if _dbg.get("mode") != "start_path" and res["suggestions"]:
                # CHATFIX31 (1c), Ibrahim 2026-11-01 «ابدا»: the model's quick answers are cleaned at the server before the
                # padding — never the visitor's own words again, never longer than five words, never a price or a page count
                # no shown book has, and one tied to the cards first («كم سعره؟»، «أي واحد أقصر؟»)
                _last_user = next((str(m.get("content") or "") for m in reversed(messages if isinstance(messages, list) else [])
                                   if isinstance(m, dict) and m.get("role") == "user"), "")
                _before = list(res["suggestions"])
                res["suggestions"] = chat_rules.clean_chips(_before, _last_user, res.get("books") or [], _lang)
                if res["suggestions"] != _before:
                    res.setdefault("_debug", {})["chips_cleaned"] = _before
            res["suggestions"] = helper_chips.pad(res["suggestions"], bool(res.get("books")), _lang, visitor_reviews.ready(),
                                                  none_on_purpose=_dbg.get("mode") == "start_path")
    except Exception as exc:  # noqa: BLE001 - helpers never break a turn
        app.logger.warning("helper chips failed: %s", exc)
    try:
        # CHATFIX30b (2), Ibrahim 2026-10-30 («خلها تساير السائل باسلوبه»): a visitor who writes فصحى gets every reply in فصحى —
        # the model's (steered by FUSHA_GUIDANCE) and the fixed ones («ما عندنا الآن كتب لـX. تبغى أقترح لك…» -> «ليس لدينا الآن كتب
        # لـX. هل تريد أن أقترح عليك…»): the commonest dialect words are swapped, whole words only. The chips are not touched
        # (the server knows several of them by their exact words)
        if isinstance(res, dict) and res.get("reply") and language != "en" and not _english_reply(res):
            _users = [str(m.get("content") or "") for m in (messages if isinstance(messages, list) else [])
                      if isinstance(m, dict) and m.get("role") == "user"]
            if chat_rules.visitor_register(_users, titles=[b.get("title") for b in (res.get("books") or [])]) == "fusha":
                # CHATFIX30c (6), AUDIT 2026-10-30 P2: titles and author names are never rewritten («خيرا تعمل خيرا تلقى»)
                _prot = [x for b in (res.get("books") or []) for x in (b.get("title"), author_flow.display(b.get("author") or ""))]
                _prot += [a for a in _catalog_author_names() if a in res["reply"]]
                res["reply"] = chat_rules.fusha_rewrite(res["reply"], protect=_prot)
                if isinstance(res.get("_debug"), dict):
                    res["_debug"]["register"] = "fusha"
    except Exception as exc:  # noqa: BLE001 - a wording pass never breaks a turn
        app.logger.warning("fusha rewrite failed: %s", exc)
    return res


_author_names_cache = {"n": None, "names": ()}


def _catalog_author_names():
    """CHATFIX30c (6): every author name of the catalog as displayed (each person of a co-authored book), cached while the
    catalog size stays the same — kept verbatim by fusha_rewrite."""
    books = list_books(only_active=False)
    if _author_names_cache["n"] != len(books):
        names = set()
        for b in books:
            for p in author_flow._people(author_flow.display(b["author"] or "")):
                if p and len(p.split()) >= 2 and normalize(p).strip() not in _NO_AUTHOR:   # one-word names may be plain words
                    names.add(p)
        _author_names_cache.update(n=len(books), names=tuple(sorted(names, key=len, reverse=True)))
    return _author_names_cache["names"]


def _english_reply(res):
    """The answer is in English although the page is Arabic (QADFIX12 #6: the visitor writes in English)."""
    text = str(res.get("reply") or "")
    return bool(text) and not re.search(r"[\u0600-\u06FF]", text)


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
                                register_msgs=[str(m.get("content") or "") for m in messages if isinstance(m, dict) and m.get("role") == "user"],
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
    reviewed_map, requeue = _reviewed_map(), research_queue.open_ids()   # BKREV 2026-10-29
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
            "reviewed_at": (reviewed_map.get(b["book_id"]) or {}).get("at"),
            "reviewed_by": (reviewed_map.get(b["book_id"]) or {}).get("by"),
            "requeue": b["book_id"] in requeue,
            "goodreads_url": b.get("goodreads_url"),   # GRURL (MGR 2026-10-30): the developer's «بلا رابط Goodreads» chip
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
        "reviewed": _reviewed_map().get(book_id),          # BKREV 2026-10-29
        "research_queue": research_queue.for_book(book_id),
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


# «الكتب المطلوبة» (Ibrahim 2026-10-24): books (and authors) visitors asked for that were not in stock, with how many times;
# a title that is in stock now is shown as such, so staff see what arrived since. Marked done through the route below.
@app.route("/api/admin/inbox/requested")
def admin_requested_books():
    rows, n = open_questions.requested(request.args.get("status", "open"))
    have = {}
    for b in list_books(only_active=False):
        if is_available(dict(b)):
            have.setdefault(chat_rules.title_key(b["title"] or ""), []).append({"book_id": b["book_id"], "title": b["title"]})
    for r in rows:
        k = chat_rules.title_key(r.get("phrase") or "")
        r["in_stock_now"] = (have.get(k) or [])[:3] if r["kind"] == "missing_book" and len(k) >= 3 else []
    return jsonify({"items": rows, "counts": n})


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
            "location", "location_kind", "page_count", "line", "translator", "original_year", "publication_year",
            "goodreads_url",   # CHATFIX30b (5): the Goodreads readers' link of the book page (GRURL)
            "goodreads_rating", "goodreads_ratings_count")   # CHATFIX31 (2): the card's «★ ٤٫٢ · Goodreads»
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
    _queue_research(book_id, ["cover"])
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
    _queue_research(book_id, ["cover"])
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
    _queue_research(book_id, ["cover"])
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
    return jsonify({"users": auth.list_users(), "roles": [{"value": r, "label": auth.ROLE_LABELS[r]} for r in auth.ASSIGNABLE_ROLES]})


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
    target = auth.get_user(user_id)
    if target and auth.is_developer(target["role"]) and not auth.is_developer((acting_user() or {}).get("role")):
        return jsonify({"error": "حساب المطوّر ما يتعدّل من هنا"}), 403
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


def _safe(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001 - a warm-up only
        pass


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
    # «المؤلفون» (Ibrahim 2026-10-29 «ليه المؤلفون فاضية في الصفحة»): the spelling-pairs check is worked out once in the
    # background at start, so the first opening of the list does not wait for it
    threading.Thread(target=lambda: _safe(authors.probable_duplicates), daemon=True).start()
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
