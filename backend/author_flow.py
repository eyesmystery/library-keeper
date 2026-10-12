"""Author clarification and catalog-only selection, independent of response generation."""
import re
import unicodedata


def key(text):
    text = unicodedata.normalize('NFKD', str(text or '')).casefold()
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', text.translate(str.maketrans('أإآى', 'اااي'))))


def list_name_from_message(message):
    """Return an explicitly requested author-name listing, if this is one.

    This is deliberately deterministic: a request such as «مين مؤلفين عندكم
    اسمهم أسامة؟» should not depend on an LLM deciding whether it is still a
    book recommendation request.
    """
    text = str(message or '').strip()
    patterns = (
        r'(?:مين|من|وش|ايش)\s+(?:هم\s+)?(?:المؤلفين|المؤلفون|مؤلفين|الكتّاب|الكتاب).*?(?:اسمهم|اسمه|باسم)\s+(.+)',
        r'(?:اعرض|ورني|ورّني|أبغى|ابغى)\s+(?:لي\s+)?(?:المؤلفين|المؤلفون|مؤلفين|الكتّاب|الكتاب).*?(?:اسمهم|اسمه|باسم)\s+(.+)',
        r'(?:which|who)\s+(?:authors?|writers?).*?(?:named|called)\s+(.+)',
        r'(?:show|list)\s+(?:me\s+)?(?:authors?|writers?).*?(?:named|called)\s+(.+)',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            name = match.group(1).strip(" ؟?!.،,. ")
            if name:
                return name[:100]
    return ''


def list_reply(name, catalog, english=False):
    requested = set(key(name).split())
    if not requested:
        return ('What name should I search for?' if english else 'وش الاسم اللي تبغاني أبحث عنه؟')
    matches = sorted({author for author in catalog if requested <= set(key(author).split())}, key=key)
    if not matches:
        return (f'I couldn’t find an author named “{name}” in the current catalog. Try the full name?'
                if english else f'ما لقيت مؤلف باسم «{name}» في الكتالوج الحالي. جرّب الاسم الكامل؟')
    joined = ', '.join(matches[:6]) if english else '، '.join(matches[:6])
    if english:
        label = 'author' if len(matches) == 1 else 'authors'
        return f'I found {label} named “{name}”: {joined}. Which one do you mean?'
    label = 'مؤلف واحد' if len(matches) == 1 else 'مؤلفين'
    return f'عندنا {label} باسم «{name}»: {joined}. أي واحد تقصد؟'


NO = {'لا', 'مو هو', 'no', 'لا ولا واحد', 'ولا واحد', 'مو منهم', 'none', 'neither'}
YES = {'نعم', 'ايه', 'اي', 'ايوه', 'صحيح', 'هو', 'yes', 'correct', 'نعم اقصده'}
_YES_FIRST = {'نعم', 'ايه', 'ايوه', 'ايوا', 'اكيد', 'صح', 'صحيح', 'بالضبط', 'هو', 'yes', 'yeah', 'yep', 'correct', 'exactly'}


def is_yes(reply):
    """«ايه، وش أسهل كتاب له؟» confirms too: a short message that begins with a yes word (QA 2026-10-08)."""
    words = key(reply).split()
    return key(reply) in YES or (bool(words) and words[0] in _YES_FIRST and len(words) <= 12)
ORDINALS = {'الاول': 0, 'اول': 0, 'الاولى': 0, 'first': 0, '1': 0, 'الثاني': 1, 'ثاني': 1, 'الثانيه': 1, 'second': 1, '2': 1,
            'الثالث': 2, 'ثالث': 2, 'third': 2, '3': 2, 'الرابع': 3, 'fourth': 3, '4': 3, 'الخامس': 4, 'fifth': 4, '5': 4}


def display(author):
    """Catalog author string without role notes: «آلان مور (رسوم …)» -> «آلان مور»."""
    text = re.sub(r'\s*\([^)]*\)', '', str(author or '')).strip()
    # an academic title is not part of the name: «د. عمرو عبد العزيز منير» = «عمرو عبد العزيز منير»
    text = re.sub(r'^(?:أ\.\s*د\.|د\.|الدكتور|الدكتورة|دكتور|Dr\.?|Prof\.?)\s*', '', text).strip()
    return text or str(author or '')


def choice_question(options, english=False):
    q = ['«' + o + '»' for o in options]
    if english:
        return f'Do you mean {q[0]} or {q[1]}?' if len(q) == 2 else f'Which one do you mean: {", ".join(q[:-1])} or {q[-1]}?'
    return f'تقصد {q[0]} ولا {q[1]}؟' if len(q) == 2 else f'تقصد مين: {"، ".join(q[:-1])} ولا {q[-1]}؟'


def _picked(reply, options):
    """Which offered author the visitor chose: by ordinal, full name, or a word only that name has."""
    r = key(reply)
    words = set(r.split())
    for w, idx in ORDINALS.items():
        if (r == w or w in words and len(words) <= 3) and idx < len(options):
            return options[idx]
    for o in options:
        if key(o) == r or key(o) in r:
            return o
    common = set.intersection(*(set(key(o).split()) for o in options)) if options else set()
    hits = [o for o in options if words & (set(key(o).split()) - common)]
    return hits[0] if len(hits) == 1 else None


def _choice_made(conversation):
    """The latest author choice the visitor answered in this conversation -> name or None."""
    chosen = None
    for i, c in enumerate(conversation[:-1]):
        if c['role'] != 'assistant' or not re.match(r'^(تقصد|Do you mean|Which one do you mean)', c['content']):
            continue
        options = re.findall(r'«([^»]+)»', c['content'])
        if len(options) < 2 or conversation[i + 1]['role'] != 'user':
            continue
        chosen = _picked(conversation[i + 1]['content'], options) or chosen
    return chosen


def _variant(a, b):
    """Same name, spelling differs only (بارغاس/فارغاس); an extra or different name is another person."""
    import authors  # spelling rules shared with the admin duplicate suggestions
    wa, wb = authors.key(a).split(), authors.key(b).split()
    return len(wa) == len(wb) >= 2 and wa != wb and all(authors._same_word(x, y) for x, y in zip(wa, wb))


# ---------------------------------------------------------------------------
# «تقصد …؟» for a name we do not have (Ibrahim, 2026-10-02). Rules he approved:
# 1. only when the written name is found nowhere (no catalog author, no stored author record);
# 2. the shared part must be the distinctive one: the family name (last word) of the written
#    name, equal (never merely similar) to a non-first word or a stored alias of a catalog author;
#    common names and particles never count;
# 3. the visitor is asked and nothing is shown until they confirm; the name is never replaced silently;
# 4. one to five suggestions, authors with available books first; more than five people with that
#    family name -> the visitor is asked for the full name once instead of a long list;
# 5. no close candidate -> the usual «we don't have this author» answer.
# ---------------------------------------------------------------------------
_COMMON_NAMES = {key(w) for w in (
    "محمد أحمد محمود مصطفى علي عمر عثمان حسن حسين إبراهيم يوسف خالد سعد سعيد صالح ناصر فهد سلمان سليمان "
    "عبد الله عبدالله عبدالرحمن عبدالعزيز الرحمن العزيز الدين بن ابن بنت أبو ابو أم ام آل ال ولد "
    "الأول الثاني الثالث الابن الأب الصغير الكبير "
    "de la le van von der dos da di del al el bin ibn the jr sr ii iii").split()}
NEAR_MAX_AUTHORS = 5   # more catalog people than this with the same family name: ask for the full name instead of listing
NEAR_RE = re.compile(r'^(?:ما عندنا مؤلف باسم (?P<ar>[^«»]+?)، |We don’t have an author named (?P<en>[^«»]+?)\. )'
                     r'(?:تقصد|Do you mean|Which one do you mean)')


TOO_MANY = ['*']  # marker: the family name is carried by too many catalog people to list


def _bare(word):
    """«العقاد» and «عقاد» are the same family name."""
    return word[2:] if word.startswith('ال') and len(word) > 4 else word


def near_authors(written, by_display, books):
    """Catalog authors (display names) the visitor may mean by a name we do not have -> up to 5,
    or TOO_MANY when more people than that carry the family name."""
    words = key(written).split()
    if len(words) < 2:
        return []
    family = words[-1]
    if len(family) < 3 or family in _COMMON_NAMES or family.isdigit():
        return []
    # the candidate must carry it as its own family name (last word), not as a middle name
    hits = {d for d in by_display if len(key(d).split()) >= 2 and _bare(key(d).split()[-1]) == _bare(family)}
    # a misspelt family name («أسامة الشازلي» for «أسامة عبد الرءوف الشاذلي»: ز/ذ, a long or short vowel)
    # counts only with the same first name too (Ibrahim, 2026-10-09: «يتعرف عالاخطاء الاملائية ويسأله»)
    try:
        import authors as _ar
        fam, first = _ar.key(words[-1]), _ar.key(words[0])
        for d in by_display:
            dw = _ar.key(d).split()
            if (len(dw) >= 2 and d not in hits and _ar._same_word(_bare(fam), _bare(dw[-1]))
                    and _ar._same_word(first, dw[0])):
                hits.add(d)
    except Exception:
        pass
    try:  # a stored alias that is exactly this family name («العقاد» -> عباس محمود العقاد)
        import authors as author_records
        rec = author_records.lookup(words[-1]) or author_records.lookup(written.split()[-1])
        if rec:
            stored = {key(n) for n in author_records.names_of(rec)}
            hits |= {d for d in by_display if key(d) in stored}
    except Exception:
        author_records = None
    if not hits:
        return []
    available = {}
    for b in books:
        if b.get('active', 1) and (b.get('quantity') or 0) > 0:
            d = display(b.get('author', ''))
            available[d] = available.get(d, 0) + 1
    # one person catalogued under two spellings (فرانز/فرانتس كافكا, Arabic/Latin) is offered once:
    # the stored canonical name when the catalog shows it, else the spelling with more available books
    people = {}
    for d in sorted(hits, key=lambda d: (-available.get(d, 0), key(d))):
        try:
            rec = author_records.lookup(d) if author_records else None
        except Exception:
            rec = None
        pid = ('rec', rec['author_id']) if rec else ('name', key(d))
        if pid not in people or (rec and key(d) == key(rec['name_ar'])):
            people[pid] = d
    if len(people) > NEAR_MAX_AUTHORS:
        return TOO_MANY
    return sorted(people.values(), key=lambda d: (-available.get(d, 0), key(d)))[:NEAR_MAX_AUTHORS]


def near_question(written, options, english=False):
    lead = f'We don’t have an author named {written}. ' if english else f'ما عندنا مؤلف باسم {written}، '
    if len(options) == 1:
        return lead + (f'Do you mean «{options[0]}»?' if english else f'تقصد «{options[0]}»؟')
    return lead + choice_question(options, english)


def _near_answer(conversation):
    """The visitor's answer to the latest «ما عندنا مؤلف باسم …، تقصد …؟» -> (asked name, chosen or None, declined)."""
    asked, chosen, declined = None, None, False
    for i, c in enumerate(conversation[:-1]):
        m = NEAR_RE.match(c['content']) if c['role'] == 'assistant' else None
        if not m or conversation[i + 1]['role'] != 'user':
            continue
        options = re.findall(r'«([^»]+)»', c['content'])
        reply = key(conversation[i + 1]['content'])
        asked, chosen, declined = (m.group('ar') or m.group('en')), None, False
        if reply in NO:
            declined = True
        elif len(options) == 1 and (is_yes(reply) or key(options[0]) in reply or
                                    (set(reply.split()) - _COMMON_NAMES) & (set(key(options[0]).split()) - _COMMON_NAMES)):
            chosen = options[0]
        elif len(options) > 1:
            chosen = _picked(conversation[i + 1]['content'], options)
    return asked, chosen, declined



def _people(author_text):
    """«أ، ب و ج» -> the single names; one name stays as it is."""
    try:
        import authors as author_records
        names = [display(n) for n in author_records._split_names(author_text)]
    except Exception:
        names = []
    return [n for n in names if n] or [author_text]


def _canonical_names():
    """-> function: a catalog spelling -> the stored canonical name of that person (else unchanged)."""
    try:
        import authors as author_records
        conn = author_records.get_connection()
        try:
            index = author_records._index(conn)
            name = {r[0]: r[1] for r in conn.execute("SELECT author_id, name_ar FROM authors")}
        finally:
            conn.close()
        return lambda d: name.get(index.get(author_records.key(d))) or d
    except Exception:  # no authors table (old database)
        return lambda d: d


_JOINERS = re.compile(r'\s*(?:،|,|&|;)\s*|\s+(?:او|أو|ولا|or|and)\s+', re.IGNORECASE)


def _catalog_for(name, by_display, author_records):
    """Catalog author strings of one person named by the visitor («لغازي القصيبي» too), or empty."""
    for n in (name, name[1:] if name.startswith('ل') and len(name.split()[0]) > 3 else None):
        if not n:
            continue
        rec = author_records.lookup(n)
        if rec:
            stored = {key(x) for x in author_records.names_of(rec)}
            return {a for d, strings in by_display.items() if key(d) in stored for a in strings}
        words = set(key(n).split())
        if len(words) >= 2:
            hits = [d for d in by_display if words <= set(key(d).split())]
            if len(hits) == 1:
                return set(by_display[hits[0]])
    return set()


def _people_said(written, by_display, author_records):
    """«غازي القصيبي أو تركي الحمد» -> two names; «نجيب محفوظ وماركيز» -> two names when each half
    is a known person and the whole is not. One name -> [that name]."""
    def known(n):
        return bool(_catalog_for(n, by_display, author_records)) or bool(author_records.lookup(n))
    out = []
    for part in _JOINERS.split(str(written or '')):
        part = part.strip(' ؟?!.')
        if not part:
            continue
        words = part.split()
        split = None
        if not known(part):
            for i in range(1, len(words)):
                if words[i].startswith('و') and len(words[i]) > 3:
                    left, right = ' '.join(words[:i]), ' '.join([words[i][1:]] + words[i + 1:])
                    if known(left) and known(right):
                        split = [left, right]
                        break
        out += split or [part]
    return out


def _named_before(name, earlier, author_records):
    """Did the visitor himself write this (multi-word) name, allowing for spelling, in an earlier message?"""
    want = author_records.key(name).split()
    if len(want) < 2:
        return False
    for c in earlier:
        if c['role'] != 'user':
            continue
        said = author_records.key(c['content']).split()
        if all(any(author_records._same_word(w, x) for x in said) for w in want):
            return True
    return False


def _spoken_of(proposal, written, earlier, question):
    """The assistant itself named this author in full earlier in the conversation (not inside a
    «تقصد …؟» question), and the visitor now writes his family name."""
    want, said = key(proposal), key(written).split()
    if len(want.split()) < 2 or not said:
        return False
    last = said[-1][1:] if said[-1].startswith('ل') and said[-1][1:] == want.split()[-1] else said[-1]
    if last != want.split()[-1]:
        return False
    for c in earlier:
        text = c['content']
        if c['role'] != 'assistant' or text == question or NEAR_RE.match(text) or re.match(r'^(تقصد|Do you mean|Which one do you mean)', text):
            continue
        if ' ' + want + ' ' in ' ' + key(text) + ' ' or ' ل' + want + ' ' in ' ' + key(text) + ' ':
            return True
    return False


def resolve(request, conversation, books, english=False):
    """Return (reply or None, allowed author names or None, direct-works mode)."""
    mode = request.get('mode')
    last_message = conversation[-1]['content'] if conversation else ''
    explicit_list_name = list_name_from_message(last_message)
    if mode not in ('works', 'similar', 'both', 'list') and not explicit_list_name:
        return None, None, False
    written = str(request.get('name_as_written') or '').strip()[:100]
    full = str(request.get('full_name') or '').strip()[:100]
    aliases = request.get('aliases') or []
    raw_names = [v for v in [written, full] + (aliases[:8] if isinstance(aliases, list) else []) if isinstance(v, str) and v.strip()]
    # Stored spellings of the same person (authors table, 2026-09-30): «زافون» or «فارغاس يوسا»
    # reach the canonical record, whose name is what the catalog shows.
    import authors as author_records
    known_record = False
    record_keys = set()
    for n in list(raw_names):
        rec = author_records.lookup(n)
        if rec:
            known_record = True
            record_keys |= {key(x) for x in author_records.names_of(rec)}
            raw_names += [x for x in author_records.names_of(rec) if x not in raw_names]
            full = rec['name_ar']  # the stored canonical name, not the model's spelling
    names = {key(v) for v in raw_names}
    catalog = {b.get('author', '') for b in books}
    # An explicit listing request in the visitor's last message always wins over
    # an older, cumulative "works" request.
    if explicit_list_name or mode == 'list':
        return list_reply(explicit_list_name or written, catalog, english), None, False
    # Options are people, not catalog strings (Ibrahim, 2026-10-02): a co-authored book
    # («طلال أسد، جوديث بتلر، ويندي براون، صبا محمود») counts for each of its authors and is never
    # offered as a name; one person catalogued under two spellings (Dan Brown / دان براون) is one
    # option under the stored canonical name.
    canon = _canonical_names()
    by_display = {}
    for a in catalog:
        if a and key(a) not in {'غير معروف', 'unknown'}:
            for person in _people(display(a)):
                by_display.setdefault(canon(person), set()).add(a)
    exact = {d for d in by_display if key(d) in names or key(by_display[d] and next(iter(by_display[d]))) in names}
    # Similar names the visitor may mean (Ibrahim, 2026-09-30): the written name inside a longer
    # catalog name (إبراهيم عيسى / إبراهيم أحمد عيسى) or the same name spelled differently.
    partial = {d for d in by_display if written and set(key(written).split()) <= set(key(d).split())}
    variant = {d for d in by_display if any(_variant(n, d) for n in raw_names)}
    options = sorted(exact | partial | variant, key=lambda d: (d not in exact, key(d)))
    last = key(last_message)
    previous = conversation[-2]['content'] if len(conversation) > 1 and conversation[-2]['role'] == 'assistant' else ''

    def allowed(d):
        return set(by_display.get(d, {d}))

    # the interpreter gave no name at all («عندكم لرجاء عالم او ليلى الجهني؟» -> name_as_written ""): the
    # catalog authors written out in full in the visitor's message are the request (QAFIX re-run, case 21)
    if mode == 'works' and not written:
        msg = ' ' + key(last_message) + ' '
        said = [d for d in by_display if len(key(d).split()) >= 2
                and any(' ' + p + key(d) + ' ' in msg for p in ('', 'ل', 'و', 'ب'))]
        said = [d for d in said if not any(d != e and key(d) in key(e) for e in said)]
        if said:
            written = ' أو '.join(said)
            request['name_as_written'] = written
            if len(said) == 1:
                return None, allowed(said[0]), True

    # «لغازي القصيبي أو تركي الحمد»، «نجيب محفوظ وماركيز»: several authors asked for in one message
    # are looked up one by one, never as one name (QA2 2026-10-09, cases 21 and 33). The caller
    # reads request['people'] ({name as said: catalog author strings}) to say which have no books.
    if mode == 'works':
        people = _people_said(written, by_display, author_records)
        if len(people) == 1:
            # the interpreter kept only one of them («ونجيب محفوظ وماركيز؟» -> «ماركيز»): another
            # catalog author written out in full in the visitor's message joins him (QA2 re-run, case 33)
            mine = _catalog_for(people[0], by_display, author_records)
            msg = ' ' + key(last_message) + ' '
            for d, strings in by_display.items():
                kd = key(d)
                if (len(kd.split()) >= 2 and (' ' + kd + ' ' in msg or ' و' + kd + ' ' in msg or ' ل' + kd + ' ' in msg)
                        and not (set(strings) & mine) and kd not in [key(x) for x in people]
                        and kd not in ' | '.join(key(x) for x in [written, full] + sorted(mine))):
                    people.append(d)
            # a name found inside a longer one found too («خالد توفيق» in «أحمد خالد توفيق») is not a person
            people = [p for p in people if not any(p != q and key(p) in key(q) for q in people)]
        if len(people) >= 2:
            per = {p: _catalog_for(p, by_display, author_records) for p in people}
            if any(per.values()):
                request['people'] = {p: sorted(v) for p, v in per.items()}
                return None, set().union(*per.values()), True

    # A choice the visitor already made between offered authors stays valid for this author.
    chosen = _choice_made(conversation)
    if chosen and (chosen in options or key(chosen) in names):
        if mode in ('similar', 'both'):
            return None, None, False
        return None, allowed(chosen), True
    # «أحب دوستويفسكي وتولستوي، وش يشبههم؟»: several authors named together for likeness are not a
    # which-one question; the similarity search takes them all (2026-10-06)
    if (mode in ('similar', 'both') and len(exact) >= 2
            and re.search(r'\sو\S|،|,|&|\sand\s|\sاو\s|\sأو\s', written)):
        return None, None, False
    if len(options) >= 2:
        question = choice_question(options[:5], english)
        if re.findall(r'«([^»]+)»', previous) and previous.startswith(question.split('«')[0]) and last in NO:
            return ('What is the author’s full name?' if english else 'وش اسم المؤلف كامل؟'), None, mode == 'works'
        return question, None, mode == 'works'

    # A name we do not have at all, whose family name a catalog author carries: ask, never replace.
    if not options and not known_record and written:
        asked, near_chosen, near_declined = _near_answer(conversation)
        same = asked is not None and key(asked) == key(written)
        if same and near_chosen in by_display:
            # the confirmed author replaces the unknown name for the rest of the turn (the caller
            # reads the same request dict for «كتب تشبه فلان»)
            request.update(name_as_written=near_chosen, full_name=near_chosen, aliases=[])
            if mode in ('similar', 'both'):
                return None, None, False
            return None, allowed(near_chosen), True
        if not (same and near_declined):
            near = near_authors(written, by_display, books)
            if near is TOO_MANY:
                many = (f'We don’t have an author named {written}, and several of our authors share that family name. What is the full name of the author you mean?'
                        if english else f'ما عندنا مؤلف باسم {written}، وعندنا أكثر من مؤلف بنفس اللقب. وش اسم المؤلف اللي تقصده كامل؟')
                if not any(c['role'] == 'assistant' and c['content'] == many for c in conversation):
                    return many, None, mode == 'works'
            elif near:
                return near_question(written, near, english), None, mode == 'works'

    matches = set(options)
    # One candidate: ask with the model's full name only when the catalog holds that very name
    # (or its alias); a longer or differently written catalog name is proposed as it is written.
    proposal = (full if (full and exact) else '') or (options[0] if options else full)
    question = (f'Do you mean «{proposal}»?' if english else f'تقصد «{proposal}»؟') if proposal else (
        'What is the author’s full name?' if english else 'وش اسم المؤلف كامل؟')
    # Confirmation remains valid across later turns, but only for this proposal.
    confirmed = any(c['role'] == 'assistant' and c['content'] == question and
                    i + 1 < len(conversation) and is_yes(conversation[i+1]['content'])
                    for i, c in enumerate(conversation))
    # «ايه اقصد موراكامي» after the visitor wrote «هاروكي موركامي» himself earlier: the full name is
    # already his, no «تقصد …؟» again (QA2 2026-10-09, case 18)
    confirmed = confirmed or bool(proposal and _named_before(proposal, conversation[:-1], author_records))
    # «وش عندكم لمحفوظ غيرها؟» right after Manar herself spoke of «نجيب محفوظ»: the conversation already
    # says who is meant, no «تقصد نجيب محفوظ؟» (QAFIX 2026-10-11, case 26)
    confirmed = confirmed or bool(proposal and _spoken_of(proposal, written, conversation[:-1], question))
    if previous == question and last in NO:
        return ('What is the author’s full name?' if english else 'وش اسم المؤلف كامل؟'), None, mode == 'works'
    ambiguous = len(key(written).split()) < 2 or (proposal and key(written) != key(proposal) and not exact
                                                   # "Nizar Qabbani" is a stored spelling of «نزار قباني»: no «Do you mean …?» (2026-10-12)
                                                   and key(written) not in record_keys)
    # «أحب ماركيز، عطني شي بنفس الجو»: for likeness, a name our author records know needs no
    # «تقصد فلان؟» first; a request for his own books still confirms a one-word name (2026-10-07)
    if written and mode in ('similar', 'both') and known_record and len(options) <= 1:
        return None, None, False
    if not written or (ambiguous and not confirmed):
        return question, None, mode == 'works'
    if mode in ('similar', 'both'):
        return None, None, False
    return None, {a for d in matches for a in allowed(d)}, True
