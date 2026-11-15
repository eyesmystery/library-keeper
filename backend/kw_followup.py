# -*- coding: utf-8 -*-
"""[QADKWFIX] Follow-ups about the books just shown, answered without the model (Ibrahim 2026-11-14 on Q-QADKW-1, item 1).

With the model down every follow-up of the QAD round of 2026-11-13 got new, unrelated books («أقصر؟»، «كم صفحة؟»، «كم سعر كل
وحدة؟» — research/fallback_study_2026-11-13/report.md §4). A question about the books of the assistant's last turn is answered
from their own records, in one plain sentence:
  facts   «كم صفحة؟»، «كم سعرها؟»، «متى صدر؟»، «مين المؤلف؟»، «متوفر؟»، «عن إيش؟»، "how many pages", "who wrote it" …
          of the book named in the message (a shown title), «الأول / الثاني», every one («كل وحدة»), or the only one shown;
  order   «أيهم أقصر؟»، «وش أقصر واحد فيهم؟»، «أرخصها»، "which one is the shortest" — the shown books ranked by pages / price /
          first publication;
  more    «أقصر من كذا؟»، «فيه شي أرخص؟»، "something shorter" — new books shorter / cheaper than all the shown ones (the
          search itself is the keyword answer's, app.chat_keyword_fallback; this module only says so and gives the limit).
A bare «أقصر؟» gets the order line and the shorter books.
SETS (book_sets.chat_view): a set's page count is not one book's length — never given as «N صفحة», never ranked; a collection
has no single first-publication year. Nothing is guessed: a value missing from the record is said to be missing.
"""
import re

import book_sets
import chat_rules
from keyword_prefs import normalize

_AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def _clean(text):
    return " ".join(normalize(re.sub(r"[؟?!.،,«»\"']", " ", text or "")).split()).lower()


_PAGES = re.compile(r"(?:^| )(?:و|ف)?(?:كم|بكم|عدد) (?:عدد )?(?:ال)?(?:صفحه|صفحات|صفحاته|صفحاتها|صفحتها|صفحته|صفحاتهم|ورقه)(?: |$)"
                    r"|(?:^| )(?:و|ف)?(?:كم )?(?:صفحاته|صفحاتها|صفحتها|صفحته|صفحاتهم)(?: |$)|(?:^| )(?:و|ف)?عدد (?:ال)?صفحات"
                    r"|how many pages|page count|number of pages|is it (?:long|short)|how long is (?:it|this|that)")
_PRICE = re.compile(r"(?:^| )(?:و|ف)?(?:كم|بكم) (?:ال)?(?:سعر|سعره|سعرها|سعرهم|اسعارها|اسعارهم|ثمن|ثمنه|ثمنها|حقه|حقها)(?: |$)"
                    r"|(?:^| )(?:و|ف)?بكم(?: |$)|(?:^| )(?:و|ف)?(?:سعره|سعرها|سعرهم|اسعارها|اسعارهم)(?: |$)"
                    r"|how much|what s the price|what is the price|the price|price of|cost")
_YEAR = re.compile(r"(?:^| )(?:و|ف)?متي (?:ال)?(?:صدر|صدرت|نزل|نزلت|انكتب|انكتبت|طلع|طلعت|نشر|نشرت|انشر|انشرت|كتب|كتبت|انطبع|انطبعت)"
                   r"|سنه (?:ال)?(?:صدور|اصدار|نشر|النشر|الصدور|الاصدار)|(?:اي|كم) سنه (?:صدر|صدرت|نزل|نزلت|طلع|طلعت|انكتب|انكتبت)"
                   r"|when (?:was|were) (?:it|they|this|that) (?:published|written|released|first published)|publication year|what year"
                   r"|when did (?:it|they) come out")
_AUTHOR = re.compile(r"(?:^| )(?:و|ف)?(?:مين|منو|من) (?:هو |هي )?(?:ال)?(?:مؤلف|مولف|كاتب|كاتبه|مؤلفه|مولفه|كتبه|كتبها|الفه|الفها|مؤلفها|مؤلفه)(?: |$)"
                     r"|(?:^| )اسم (?:ال)?(?:مؤلف|مولف|كاتب)|who wrote|who is the author|who s the author|whose is it")
_AVAIL = re.compile(r"(?:^| )(?:و|ف)?(?:متوفر|متوفره|موجود|موجوده|متاح|متاحه)(?: |$)|(?:^| )عندكم (?:منه|منها|نسخه)(?: |$)"
                    r"|in stock|available")
_VOLUMES = re.compile(r"(?:^| )كم (?:عدد )?(?:ال)?(?:كتاب|كتب|جزء|اجزاء|مجلد|مجلدات|روايه|قصه) (?:فيها|فيه|منها|منه|بالمجموعه|في المجموعه|بالسلسله|في السلسله)"
                      r"|(?:^| )كم (?:عدد )?(?:ال)?(?:اجزاء|مجلدات)(?:ها|ه)?(?: |$)|how many (?:books|volumes|parts)")
_ABOUT = re.compile(r"(?:^| )عن (?:ايش|وش|ماذا|شو|ايه)(?: |$)|(?:^| )(?:وش|ايش|شو|ما) (?:قصتها|قصته|فكرتها|فكرته|موضوعها|موضوعه)(?: |$)"
                    r"|what (?:is|s) (?:it|this|that) about|what are they about")
# «… من كذا / منهم / منها»، «شي أقصر»: books beyond the shown ones; «أيهم / فيهم / أقصرها»: an order among them (chat_rules)
_MORE = re.compile(r"(?:اقصر|ارخص|اطول|اغلي|اغلا) (?:\S+ ){0,2}?من (?:كذا|كذي|هذول|هذولا|هذولي|هذا|هذي|هذه|اللي|الي)"
                   r"|(?:اقصر|ارخص|اطول|اغلي|اغلا) (?:\S+ ){0,3}?(?:منهم|منها|منه|منهن|منهما)(?: |$)"
                   r"|(?:^| )(?:شي|شيء|كتاب|كتب|روايه|روايات|واحد|وحده|خيار|خيارات) (?:\S+ )?(?:اقصر|ارخص|اطول|اغلي|اغلا)(?: |$)"
                   r"|(?:something|anything|books?|novels?|ones?|options?) (?:a bit |even |much )?(?:shorter|cheaper|longer)"
                   r"|(?:shorter|cheaper|longer) (?:ones|books|options|than (?:these|those|them|that|this))")
_BARE = re.compile(r"^(?:(?:طيب|زين|اوكي|طب|ok|okay|and|و|ف) )?[وف]?(?:ال)?(?:اقصر|ارخص|اطول|اغلي|اغلا|shorter|cheaper|longer)(?: (?:شوي|شويه|بعد|please))?$")
_FIRST = re.compile(r"(?:^| )(?:ال)?(?:اول|اولي|الاول|الاولي)(?: |$)|(?:^| )first(?: one)?(?: |$)")
_SECOND = re.compile(r"(?:^| )(?:ال)?(?:ثاني|ثانيه)(?: |$)|(?:^| )second(?: one)?(?: |$)")
_EACH = re.compile(r"(?:^| )(?:كل (?:وحده|واحد|واحده|جزء|كتاب|روايه|نسخه|طبعه)|كلها|كلهم|كلهن|الاثنين|الثنتين|الكتابين|الروايتين|each|both|all of them)(?: |$)")
_ORDER_WORDS = {
    "shortest": re.compile(r"(?:^| )[وفب]?(?:ال)?(?:اقصر|انحف)(?:ها|هم|هما|هن)?(?: |$)|shortest|shorter|thinnest"),
    "longest": re.compile(r"(?:^| )[وفب]?(?:ال)?(?:اطول|اضخم|اسمك)(?:ها|هم|هما|هن)?(?: |$)|longest|longer|thickest"),
    "cheapest": re.compile(r"(?:^| )[وفب]?(?:ال)?ارخص(?:ها|هم|هما|هن)?(?: |$)|اقل سعر|cheapest|cheaper|lowest price"),
    "dearest": re.compile(r"(?:^| )[وفب]?(?:ال)?(?:اغلي|اغلا)(?:ها|هم|هما|هن)?(?: |$)|اعلي سعر|most expensive|dearest|priciest"),
    "oldest": re.compile(r"(?:^| )[وفب]?(?:ال)?اقدم(?:ها|هم|هما|هن)?(?: |$)|oldest|older"),
    "newest": re.compile(r"(?:^| )[وفب]?(?:ال)?احدث(?:ها|هم|هما|هن)?(?: |$)|newest|newer|latest"),
}
# words that may stand around such a question without making it a new wish
_FILLER = {normalize(w) for w in (
    "طيب زين اوكي طب يعني بس و ف كم بكم عدد سعر سعره سعرها سعرهم اسعارها ثمن ثمنه ثمنها حقه حقها صفحه صفحات صفحاته صفحاتها صفحتها "
    "صفحته ورقه متي صدر صدرت نزل نزلت انكتب انكتبت طلع طلعت نشر نشرت كتب كتبت سنه الصدور صدور الاصدار مين منو من هو هي المؤلف "
    "مؤلف مولف المولف الكاتب كاتب كاتبه مؤلفه كتبه كتبها الفه الفها اسم متوفر متوفره موجود موجوده متاح متاحه عندكم عندك منه منها "
    "نسخه النسخه الطبعه طبعه اللي الي عن ايش وش شو ماذا ايه قصتها قصته فكرتها فكرته موضوعها موضوعه تقريبا تقريبًا كل وحده واحد واحده "
    "جزء الجزء كتاب الكتاب روايه الروايه كلها كلهم الاثنين الثنتين الكتابين الروايتين اول اولي الاول الاولي ثاني ثانيه الثاني الثانيه "
    "ايهم ايها ايهما ايهن فيهم فيها بينهم بينها منهم هذول هذولي هذا هذي ذا ذي هو هي فيه شي شيء ممكن لو سمحت تكفين تكفى يا منار "
    "اقصر ارخص اطول اغلي اغلا اقدم احدث الاقصر الارخص الاطول الاغلي الاقدم الاحدث انحف اضخم ريال ريالات بالضبط والله "
    "اجزاء اجزاءها مجلد مجلدات المجموعه مجموعه السلسله سلسله كامله كاملها كتب "
    "how many much pages page count price cost is it the a an of this that these those them which one ones who wrote author "
    "when was were published written released year what about available in stock each both all shorter shortest cheaper cheapest "
    "longer longest oldest newest older newer and please do you have long short").split()}


def order_asked(text):
    """-> "shortest" | "longest" | "cheapest" | "dearest" | "oldest" | "newest" or None (the first one said)."""
    t = _clean(text)
    found = [(m.start(), k) for k, rx in _ORDER_WORDS.items() for m in [rx.search(t)] if m]
    return min(found)[1] if found else None


def facts_asked(text):
    """-> the facts asked about: a list of "pages" / "price" / "year" / "author" / "available" / "about" (in that order)."""
    t = _clean(text)
    out = [k for k, rx in (("about", _ABOUT), ("volumes", _VOLUMES), ("pages", _PAGES), ("price", _PRICE), ("year", _YEAR),
                           ("author", _AUTHOR), ("available", _AVAIL)) if rx.search(t)]
    return out


def classify(text):
    """-> ("facts", [facts]) | ("order", kind) | ("more", kind) | ("both", kind) | None for a message that is not such a question.
    «أقصر من كذا» is "more"; «أيهم أقصر» "order"; a bare «أقصر؟» "both"."""
    t = _clean(text)
    if not t or chat_rules.absolute_asked(text):
        return None   # «وش أقصر رواية عندكم أصلاً؟»: over the whole stock, not the shown books
    kind = order_asked(text)
    if kind:
        if _BARE.search(t):
            return ("both", kind) if kind in ("shortest", "cheapest", "longest") else ("order", kind)
        if _MORE.search(t) and kind in ("shortest", "cheapest", "longest", "dearest"):
            return ("more", kind)
        t_am = re.sub(r"(?:^| )[وف](?=(?:اي|ايه|ايهم|ايها|ايهما|ايهن|مين|منهم|منها|فيهم|بينهم)(?: |$))", " ", t)   # «وأيهم أقصر؟»
        if chat_rules.among_shown_said(t_am) or re.search(r"(?:اقصر|ارخص|اطول|اغلي|اغلا|اقدم|احدث) (?:واحد|وحده|كتاب|روايه|شي)"
                                                         r"(?: (?:فيهم|فيها|منهم|منها|بينهم|له|لها))?(?: |$)", t):
            return ("order", kind)
        return None
    facts = facts_asked(text)
    return ("facts", facts) if facts else None


def _title_key(title):
    head = re.split(r"[(:：]", str(title or ""))[0]
    return chat_rules.title_key(normalize(head))


def named_books(text, books):
    """The shown books whose title (before «:» / «(») is in the message, longest title first."""
    mk = " " + chat_rules.title_key(_clean(text)) + " "
    out = []
    for b in sorted(books, key=lambda b: -len(_title_key(b.get("title")))):
        k = _title_key(b.get("title"))
        if k and len(k) >= 3 and (" " + k + " ") in mk and b not in out:
            out.append(b)
    return out


def leftover_words(text, books):
    """The message's words that are neither question words nor words of a shown title."""
    title_words = set()
    for b in books:
        title_words |= set(_title_key(b.get("title")).split())
    out = []
    for w in _clean(text).split():
        ws = {w, w[1:] if len(w) > 3 and w[0] in "وفبل" else w, w[2:] if w.startswith("ال") and len(w) > 4 else w}
        if ws & _FILLER or ws & title_words or w.isdigit():
            continue
        out.append(w)
    return out


def referents(text, books):
    """Which of the shown books a facts question is about."""
    named = named_books(text, books)
    if named:
        return named
    t = _clean(text)
    if _EACH.search(t):
        return list(books)
    order = list(books)
    if re.search(r"(?:^| )(?:ال)?(?:جزء|مجلد)(?: |$)|part|volume", t) and any(b.get("series_position") for b in books):
        # «كم سعر الجزء الأول؟» after a series' books: the part by its number in the series, not the first card
        order = sorted(books, key=lambda b: (b.get("series_position") or 99))
    if _SECOND.search(t) and len(order) >= 2:
        return [order[1]]
    if _FIRST.search(t):
        return order[:1]
    return list(books)


# --- values from the record ------------------------------------------------

def fmt_num(n):
    return str(n).translate(_AR)


def fmt_price(p, en=False):
    p = float(p)
    s = f"{int(round(p))}" if abs(p - round(p)) < 0.005 else f"{p:.2f}"
    if en:
        return s + " SAR"
    return s.replace(".", "٫").translate(_AR) + " ريال"


def pages_of(b):
    return book_sets.single_pages(b)


def _set_note(b, en=False):
    lab = book_sets.label(b, "en" if en else "ar") or ""
    return (f"«{b.get('title')}» is {lab}, sold together; we hold no page count of one book of it" if en else
            f"«{b.get('title')}» {lab} تُباع معًا، وما عندي عدد صفحات كتاب واحد منها")


def fact_line(b, fact, en=False, first_year=None, edition_year=None, available=None):
    t = b.get("title") or ""
    if fact == "volumes":
        if book_sets.is_set(b):
            return f"«{t}» is {book_sets.label(b, 'en')}, sold together" if en else f"«{t}» {book_sets.label(b, 'ar')} تُباع معًا"
        return f"«{t}» is one book in our record" if en else f"«{t}» كتاب واحد في سجلنا"
    if fact == "pages":
        if book_sets.is_set(b):
            return _set_note(b, en)
        p = pages_of(b)
        if p:
            return f"«{t}»: about {p} pages" if en else f"«{t}»: حوالي {fmt_num(p)} صفحة"
        return f"«{t}»: its page count is not in our record" if en else f"«{t}»: عدد صفحاته مو مسجل عندي"
    if fact == "price":
        if b.get("price"):
            extra = (f" ({book_sets.label(b, 'en')}, sold together)" if en else f" ({book_sets.label(b, 'ar')} تُباع معًا)") if book_sets.is_set(b) else ""
            return f"«{t}»: {fmt_price(b['price'], en)}{extra}"
        return f"«{t}»: its price is not in our record" if en else f"«{t}»: سعره مو مسجل عندي"
    if fact == "year":
        y = first_year(b) if first_year else None
        ey = edition_year(b) if edition_year else None
        if book_sets.kind(b) == "collection":
            return (f"«{t}» is a set of different books, with no single year of first publication" if en else
                    f"«{t}» مجموعة كتب مختلفة، ما لها سنة صدور أولى واحدة") + ((f"; our edition is from {ey}" if en else f"، وطبعتنا سنة {fmt_num(ey)}") if ey else "")
        if y:
            return (f"«{t}» was first published in {y}" if en else f"«{t}» صدر أول مرة سنة {fmt_num(y)}") + (
                (f" (our edition {ey})" if en else f" (طبعتنا سنة {fmt_num(ey)})") if ey and ey != y else "")
        if ey:
            return (f"«{t}»: its first publication year is not recorded; our edition is from {ey}" if en else
                    f"«{t}»: سنة صدوره الأول مو مسجلة عندي، وطبعتنا سنة {fmt_num(ey)}")
        return f"«{t}»: its year is not in our record" if en else f"«{t}»: سنة صدوره مو مسجلة عندي"
    if fact == "author":
        a = (b.get("author") or "").strip()
        if a and normalize(a) not in ("غير معروف", "unknown"):
            return f"«{t}» is by {a}" if en else f"«{t}» لـ{a}"
        return f"«{t}»: its author is not in our record" if en else f"«{t}»: اسم مؤلفه مو مسجل عندي"
    if fact == "available":
        ok = available(b) if available else bool((b.get("quantity") or 0) > 0)
        return (f"«{t}» is available now" if ok else f"«{t}» is not available right now") if en else (
            f"«{t}» متوفر عندنا الآن" if ok else f"«{t}» غير متوفر الآن")
    if fact == "about":
        s = " ".join(str(b.get("hook") or "").split()) or " ".join(str(b.get("summary") or "").split())
        if s:
            if len(s) > 220:
                cut = s[:220].rsplit(" ", 1)[0]
                s = cut + "…"
            return f"«{t}»: {s}"
        return f"«{t}»: we have no summary of it yet" if en else f"«{t}»: ما عندي نبذة عنه"
    return ""


def _value(b, kind, first_year=None, edition_year=None):
    if kind in ("shortest", "longest"):
        return pages_of(b)
    if kind in ("cheapest", "dearest"):
        return b.get("price") or None
    if kind in ("oldest", "newest"):
        if book_sets.kind(b) == "collection":
            return None
        return (first_year(b) if first_year else None) or None
    return None


def _value_text(v, kind, en=False):
    if kind in ("shortest", "longest"):
        return f"{v} pages" if en else f"{fmt_num(v)} صفحة"
    if kind in ("cheapest", "dearest"):
        return fmt_price(v, en)
    return str(v) if en else f"سنة {fmt_num(v)}"


_ORDER_AR = {"shortest": "الأقصر", "longest": "الأطول", "cheapest": "الأرخص", "dearest": "الأغلى", "oldest": "الأقدم صدورًا",
             "newest": "الأحدث صدورًا"}
_ORDER_EN = {"shortest": "The shortest", "longest": "The longest", "cheapest": "The cheapest", "dearest": "The most expensive",
             "oldest": "The oldest", "newest": "The newest"}
_MISSING_AR = {"shortest": "عدد صفحاته", "longest": "عدد صفحاته", "cheapest": "سعره", "dearest": "سعره", "oldest": "سنة صدوره الأول",
               "newest": "سنة صدوره الأول"}


def order_line(books, kind, en=False, first_year=None, edition_year=None):
    """«الأقصر بين الكتب اللي عرضتها «أ» (١٢٠ صفحة)، ثم «ب» (٢٤٠ صفحة).» -> (line, the books in that order)."""
    have = [(b, _value(b, kind, first_year, edition_year)) for b in books]
    ranked = sorted([x for x in have if x[1]], key=lambda x: x[1], reverse=kind in ("longest", "dearest", "newest"))
    missing = [b for b, v in have if not v]
    if not ranked:
        line = ("None of the books I showed has that in its record" if en else
                "ما عندي هذي المعلومة في سجل الكتب اللي عرضتها")
        return line + ".", list(books)
    if len(books) == 1:
        b, v = ranked[0]
        return ((f"I showed you only «{b.get('title')}» ({_value_text(v, kind, True)})." if en else
                 f"عرضت لك «{b.get('title')}» وحده ({_value_text(v, kind)})."), [b])
    if en:
        parts = [f"«{b.get('title')}» ({_value_text(v, kind, True)})" for b, v in ranked]
        line = f"{_ORDER_EN[kind]} of the books I showed is " + parts[0] + ("; then " + ", ".join(parts[1:]) if parts[1:] else "")
    else:
        parts = [f"«{b.get('title')}» ({_value_text(v, kind)})" for b, v in ranked]
        line = f"{_ORDER_AR[kind]} بين الكتب اللي عرضتها " + parts[0] + ("، ثم " + "، ثم ".join(parts[1:]) if parts[1:] else "")
    for b in missing:
        if kind in ("shortest", "longest") and book_sets.is_set(b):
            line += (f"; «{b.get('title')}» is {book_sets.label(b, 'en')}, not compared" if en else
                     f"، و«{b.get('title')}» {book_sets.label(b, 'ar')} فما أقارن صفحاتها")
        else:
            line += (f"; «{b.get('title')}» has no such value in our record" if en else
                     f"، و«{b.get('title')}» {_MISSING_AR[kind]} مو مسجل عندي")
    return line + ".", [b for b, _ in ranked] + missing


def facts_answer(text, books, facts, en=False, first_year=None, edition_year=None, available=None):
    """-> (reply, the books it is about)."""
    refs = referents(text, books)[:4]
    lines = []
    for b in refs:
        bits = [fact_line(b, f, en, first_year, edition_year, available) for f in facts]
        if len(bits) > 1:
            # «حصار الغزالة واو: …؛ حوالي ٢٠٢ صفحة»: one book, several facts in one line
            head = f"«{b.get('title')}»"
            bits = [bits[0]] + [x.replace(head + ": ", "").replace(head + " ", "") for x in bits[1:]]
        lines.append(("; " if en else "؛ ").join(bits))
    return ("\n".join(x + "." for x in lines), refs)


def more_limit(books, kind):
    """The limit «shorter / cheaper than all the shown ones» -> (value, the book at it) or (None, None)."""
    vals = [(b, _value(b, kind)) for b in books]
    vals = [x for x in vals if x[1]]
    if not vals:
        return None, None
    pick = min(vals, key=lambda x: x[1]) if kind in ("shortest", "cheapest") else max(vals, key=lambda x: x[1])
    return pick[1], pick[0]
