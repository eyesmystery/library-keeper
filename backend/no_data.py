"""Questions we hold no data for («وش أكثر كتاب مبيعًا؟»): say so in one line and ask before suggesting,
offering what we can answer — recent prize winners first (Ibrahim 2026-10-09: «بعد ما يقول مافي بيانات
للاكثر مبيعا عندنا: ودك تشوف كتب فائزة بجوائز مؤخرا؟»). No books are shown with this answer.
A «yes» to that offer is read as the offer itself."""
import re
import unicodedata


def _n(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub(r"[ً-ْٰـ]", "", text)
    text = text.translate(str.maketrans("أإآٱىة", "اااايه"))
    return re.sub(r"\s+", " ", text).strip().lower()


# sales / popularity: we keep no sales figures
_SALES = re.compile(
    r"(?:اكثر|الاكثر|اعلى|الاعلى)\s*(?:\S+\s+){0,3}?(?:مبيع|مبيعا|مبيعه|مبيعات|طلبا|رواجا|انتشارا|شعبيه|قراءه|بيعا)"
    r"|الاكثر مبيع|الاكثر طلب|(?:ارقام|بيانات|احصائيات) (?:ال)?مبيعات"
    r"|(?:وش|ايش|ايه|شو|ماهو|ما هو|ما هي)\s+(?:\S+\s+){0,3}?(?:ينباع|يبيع|ينطلب|يشترونه|يشتريه الناس|الناس تشتري|الناس يشترون)"
    r"|\bالترند\b|\bترند\b|\bتريند\b"
    r"|best[\s-]?sell|most\s+(?:sold|popular|bought|requested|read)\b|top[\s-]?sell|trending"
)
# a prize, a film or a series in the same message is a question we can answer
_ANSWERABLE = re.compile(r"جائز|جايز|فاز|فائز|فيلم|افلام|مسلسل|prize|award|film|movie|series")

OFFER_AR = "ودّك تشوف كتبًا فازت بجوائز مؤخرًا؟"
OFFER_EN = "Would you like to see books that won prizes recently?"
CHIP_AR = "كتب فازت بجوائز مؤخرًا"
CHIP_EN = "Books that won prizes recently"
_YES = {"نعم", "ايه", "اي", "ايوه", "ايوا", "اكيد", "يب", "تمام", "ودي", "ابي", "ابغى", "طيب", "اوكي", "ok", "okay",
        "yes", "yeah", "yep", "sure", "please"}
_NO = {"لا", "لاء", "no", "nope", "مو"}


def asked(text):
    """Does this message ask for sales / popularity, which we hold no data for?"""
    t = _n(text)
    return bool(_SALES.search(t)) and not _ANSWERABLE.search(t)


def _kind(text):
    t = _n(text)
    if re.search(r"روايه|روايات|novel", t):
        return "روايات", "novels"
    if re.search(r"اطفال|طفل|بنتي|ابني|children|kids", t):
        return "كتب أطفال", "children's books"
    return None, None


def answer(text, language="ar"):
    """-> (reply, suggestions)"""
    ar_kind, en_kind = _kind(text)
    if language == "en":
        chip = f"{en_kind[0].upper() + en_kind[1:]} that won prizes recently" if en_kind else CHIP_EN
        reply = ("We don't keep sales figures, so I can't tell which books sell most here. "
                 + (OFFER_EN.replace("books", en_kind) if en_kind else OFFER_EN))
        return reply, [chip, "Books that became films or series", "Suggest something to my taste"]
    chip = f"{ar_kind} فازت بجوائز مؤخرًا" if ar_kind else CHIP_AR
    offer = OFFER_AR.replace("كتبًا", ar_kind) if ar_kind else OFFER_AR
    reply = "ما عندنا بيانات عن الأكثر مبيعًا عندنا، فما أقدر أحدد وش أكثر كتاب يُباع. " + offer
    return reply, [chip, "كتب تحولت لأفلام أو مسلسلات", "رشّحي لي حسب ذوقي"]


def followup(conv):
    """The visitor says «ايه» to our offer -> the offer as his wish (e.g. «روايات فازت بجوائز مؤخرًا»),
    else None. conv: [{"role", "content"}, ...] ending with the visitor's message."""
    if len(conv) < 2 or conv[-1].get("role") != "user" or conv[-2].get("role") != "assistant":
        return None
    prev = str(conv[-2].get("content") or "")
    en = OFFER_EN[:24] in prev or "won prizes recently?" in prev
    if not en and "فازت بجوائز مؤخرًا؟" not in prev:
        return None
    said = _n(conv[-1].get("content"))
    words = said.replace("،", " ").replace(",", " ").split()
    if not words or len(words) > 6 or words[0] in _NO:
        return None
    if words[0] in _YES:
        if en:
            m = re.search(r"see (.+?) that won prizes", prev)
            kind = m.group(1) if m else "books"
            return f"{kind[0].upper() + kind[1:]} that won prizes recently"
        m = re.search(r"تشوف (.+?) فازت بجوائز", prev)
        kind = m.group(1) if m and m.group(1) != "كتبًا" else "كتب"
        return f"{kind} فازت بجوائز مؤخرًا"
    return None


# ------------------------------------------------------------------ «اختياراتنا» instead of sales figures
# Ibrahim 2026-10-17 (open questions 9, 14): «ما نبغى نعتمد على الاكثر مبيعا لمكتبات اخرى. ممكن تقترح الكتب اللي من
# اختياراتنا» — a sales question is answered with this month's picks of the library when there are any.
def picks_answer(titles, language="ar"):
    """-> (reply, suggestions) for a sales question answered with the library's picks of the month."""
    if language == "en":
        return ("We don't keep sales figures, but here are our picks of the month, chosen by the library: "
                + ", ".join(f"«{t}»" for t in titles) + ". Shall I tell you more about one of them?",
                ["Suggest something to my taste", "Books that won prizes recently"])
    return ("ما عندنا أرقام مبيعات نعتمد عليها، لكن هذي «اختياراتنا هذا الشهر» من المكتبة: "
            + "، ".join(f"«{t}»" for t in titles) + ". تبغى أعرّفك على واحد منها؟",
            ["رشّحي لي حسب ذوقي", "كتب فازت بجوائز مؤخرًا"])


# ------------------------------------------------------------------ book design: outside the library's field
# Ibrahim 2026-10-17 (open question 4 «مصمّم غلاف «الطلياني»»): «الجواب خارج مجالنا. تصميم الكتاب يجاوب انه ما يعرف ومو
# من اختصاص المكتبة». Not recorded as an unanswered question.
_DESIGN = re.compile(
    r"(?:مصمم|مصممه|المصمم|صمم|صممت|تصميم|رسام|راسم|رسم)\S*\s+(?:ال)?(?:غلاف|غلافه|غلافها|الغلاف)"
    r"|(?:غلاف|غلافه|غلافها|الغلاف)\s+(?:\S+\s+){0,2}?(?:من|مين)\s+(?:صممه|صممها|رسمه|رسمها|مصممه)"
    r"|(?:الاخراج|اخراج) الفني|تصميم (?:ال)?كتاب\b(?! عن)"
    r"|who designed|cover design|cover art|cover illustrat|book design")
_ABOUT = re.compile(r"(?:كتاب|كتب|روايه|روايات) (?:عن|في)|books? (?:about|on)")


def design_asked(text):
    """Does this message ask who designed a book or its cover (outside the library's field)?"""
    t = _n(text)
    return bool(_DESIGN.search(t)) and not _ABOUT.search(t)


def design_answer(language="ar"):
    if language == "en":
        return ("I don't know who designed it — book and cover design are outside what the library covers. "
                "I can help with the book itself: its subject, price, or books like it.",
                ["Its subject", "Its price", "Books like it"])
    return ("ما أعرف مين صمّمه، وتصميم الكتب وأغلفتها مو من اختصاص المكتبة. أقدر أساعدك في الكتاب نفسه: موضوعه أو سعره أو كتب تشبهه.",
            ["موضوعه", "سعره", "كتب تشبهه"])

