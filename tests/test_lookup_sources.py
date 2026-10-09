"""scripts/lookup_sources.py: title cleaning and both Goodreads search layouts (no network)."""
import os, sys, importlib.util
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
spec = importlib.util.spec_from_file_location("lookup_sources", os.path.join(ROOT, "scripts", "lookup_sources.py"))
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

ok(L.clean_title("لورنس في شبه الجزيرة العربية DC") == "لورنس في شبه الجزيرة العربية", "DC suffix removed")
ok(L.clean_title("فن الشعر ط 2") == "فن الشعر", "edition marker removed")
ok(L.clean_title("بحثاً عن الزمن المفقود 1/7") == "بحثاً عن الزمن المفقود", "volume fraction removed")

NEW = ('<li><div class="Book" data-testid="book-item-kca://book/x"><img src="https://m.media-amazon.com/images/S/compressed.photo.goodreads.com/books/1i/5900786.jpg" alt="">'
       '<span class="Text Text__title3" data-testid="book-item-title"><a href="/book/show/5900786">حفلة التيس</a></span>'
       '<a class="ContributorLink" href="#"><span class="ContributorLink__name" data-testid="name">Mario Vargas Llosa</span></a>, '
       '<a class="ContributorLink" href="#"><span class="ContributorLink__name" data-testid="name">صالح علماني</span><span data-testid="role" class="ContributorLink__role"> (<!-- -->Translator<!-- -->)</span></a>'
       '<span class="u-sr-only">Average rating of 4.36 stars, 45.6 thousand ratings, 4,488 reviews</span></div></li>')
r = L._goodreads_new(NEW, 5)
ok(len(r) == 1 and r[0]["id"] == "5900786" and r[0]["title"] == "حفلة التيس", "new layout: id and title")
ok([a["role"] for a in r[0]["authors"]] == ["Author", "Translator"] and r[0]["authors"][0]["name"] == "Mario Vargas Llosa", "new layout: authors with roles")
ok(r[0]["ratings"] == 45600 and r[0]["avg"] == 4.36 and r[0]["cover"].endswith("5900786.jpg"), "new layout: rating count in thousands, cover")

OLD = ('<tr itemscope itemtype="http://schema.org/Book"><td><img class="bookCover" itemprop="image" src="https://i.gr-assets.com/x/238749411._SX50_.jpg" /></td>'
       '<td><a class="bookTitle" href="/book/show/238749411?from_search=true"> <span itemprop=\'name\'>الحروب السيبرانية</span> </a>'
       '<a class="authorName" href="#"><span itemprop="name">Charles Arthur</span></a>, '
       '<a class="authorName" href="#"><span itemprop="name">عادل شلبي</span></a> <span class="authorName greyText smallText role">(Translator)</span>'
       '<span class="minirating"> 3.97 avg rating &mdash; 154 ratings</span> <a href="/work/editions/62002972-cyber-wars-hacks">10 editions</a></td></tr>')
L.get = lambda url, timeout=25: OLD
r = L.goodreads("x")
ok(len(r) == 1 and r[0]["id"] == "238749411" and r[0]["ratings"] == 154 and r[0]["work_slug"] == "cyber-wars-hacks", "old layout: id, ratings, English work slug")
ok(r[0]["authors"][1] == {"name": "عادل شلبي", "role": "Translator"} and r[0]["cover"].endswith("238749411.jpg"), "old layout: translator role, full-size cover")
L.get = lambda url, timeout=25: "<html><title>Book search results</title><body>page without results</body></html>"
try:
    L.goodreads("x"); ok(False, "a stripped search page raises")
except RuntimeError:
    ok(True, "a stripped search page raises (not read as «no result»)")
L.get = lambda url, timeout=25: "<html><body>No results.</body></html>"
ok(L.goodreads("x") == [], "a real «No results» page gives an empty list")
# بيت الكتب: fuzzy search results are filtered to the query title; product <title> gives publisher and author
L.time.sleep = lambda s: None
SEARCH = ('<a href="https://www.thebookhome.com/product/index/6418/x">واحة الغروب</a>'
          '<a href="https://www.thebookhome.com/product/index/99/y">واحة الغروب الجديدة جدا</a>'
          '<a href="https://www.thebookhome.com/product/index/7/z">قمر على سمرقند</a>')
PROD = ('<title>واحة الغروب-دار الشروق-بهاء طاهر|بيت الكتب</title>'
        '<meta property="og:image" content="https://bookhomemaster.com/ControlPanel/Content/BookImgs/w.jpg">')
L.get = lambda url, timeout=25: SEARCH if "SearchResult" in url else PROD
r = L.bookhome("واحة الغروب")
ok(len(r) == 2 and r[0]["publisher"] == "دار الشروق" and r[0]["author"] == "بهاء طاهر" and r[0]["cover"].endswith("w.jpg"),
   "bookhome: publisher and author from the product page title")
ok(all("7/z" not in c["url"] for c in r), "bookhome: other titles from the fuzzy search are dropped")
L.get = lambda url, timeout=25: SEARCH if "SearchResult" in url else PROD.replace("w.jpg", "siteDefult.jpg")
ok(L.bookhome("واحة الغروب")[0]["cover"] is None, "bookhome: the shop placeholder image is not a cover")
EL_S = '<a href="https://elainpublishinghouse.com/books/abc/">x</a><a href="https://elainpublishinghouse.com/books/abc/#respond">x</a>'
EL_P = ('<title>فوق رأسي سحابة &#8211; دار العين للنشر</title><div class="wd-single-brands"><a href="/b/">دعاء إبراهيم\t\t</a></div>'
        '<img width="1" class="attachment wp-post-image" src="https://elainpublishinghouse.com/c.jpg">')
L.get = lambda url, timeout=25: EL_S if "?s=" in url else EL_P
r = L.elain("فوق رأسي سحابة")
ok(len(r) == 1 and r[0]["author"] == "دعاء إبراهيم" and r[0]["cover"].endswith("c.jpg"), "elain: author (product brand) and cover")
ok(L.norm_ar("أحلام الإسكندرية") == L.norm_ar("احلام الاسكندريه"), "norm_ar unifies alef and ta marbuta")

# title-search phase: same book and same author only (scripts/goodreads_title_search.py, no network)
spec = importlib.util.spec_from_file_location("goodreads_title_search", os.path.join(ROOT, "scripts", "goodreads_title_search.py"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
T = importlib.util.module_from_spec(spec); spec.loader.exec_module(T)
sin = {"ar": ["جيلبير سينويه", "جيلبرت سينويه"], "latin": "Gilbert Sinoué"}
C = [{"id": "1", "title": "أنا و القدس", "authors": [{"name": "سليمان العيسى", "role": "Author"}], "ratings": 0},
     {"id": "2", "title": "أنا القدس", "authors": [{"name": "Gilbert Sinoué", "role": "Author"}, {"name": "صالح الأشمر", "role": "ترجمة"}], "ratings": 94}]
ok(T.pick("أنا القدس", [sin], C)["id"] == "2", "search: same title and the author's stored Latin name is accepted")
ok(T.pick("أنا القدس", [{"ar": ["صالح الأشمر"], "latin": None}], C) is None, "search: a translator is not the author")
ok(T.pick("أنا القدس", [{"ar": ["جيلبير سينويه"], "latin": None}], C) is None, "search: Arabic vs Latin name without a stored Latin name is refused")
ok(not T.same_person({"ar": ["إبراهيم عيسى"], "latin": None}, "إبراهيم أحمد عيسى"), "search: an extra name word is another person")
ok(not T.same_person({"ar": ["فهد العتيق"], "latin": None}, "سعد العتيق"), "search: the family name alone is not enough")
ok(T.same_person({"ar": ["عبد الله القصيمي"], "latin": None}, "عبدالله القصيمي"), "search: «عبد الله» / «عبدالله» are one spelling")
ok(T.same_person({"ar": ["أحمد خالد توفيق"], "latin": None}, "د. أحمد خالد توفيق"), "search: a title before the name is ignored")
ok(T.same_latin("J.K. Rowling", "J. K. Rowling") and T.same_latin("Gabriel García Márquez", "Gabriel Garcia Marquez"), "search: Latin initials and accents")
ok(not T.same_latin("Dan Brown", "Brené Brown"), "search: same family name, another first name")
ok(T.same_title("الطريق", "الطريق (رواية)"), "search: bracket text in the Goodreads title is dropped")
ok(T.same_title("بابر: ما روي عن ابن أيوب", "بابر") is False, "search: a one-word Goodreads title does not match a longer title of ours")
ok(T.same_title("الأم", "الأم الشجاعة"), "search: a subtitle after our title is allowed (the author rule decides)")
ok(not T.same_title("الأم", "الأمير"), "search: a longer word is not the same title")
ok(T.queries("أنا القدس", [sin], True) == ["أنا القدس Gilbert Sinoué", "أنا القدس"], "search: translated book is searched with the Latin name, then the title alone")
ok(T.queries("الغريب - دراسة في الجهيمانية المهدوية المنطلقات الوجودية والسياقات التاريخية ( 1979 )", [{"ar": ["لطيفة الشعلان"], "latin": None}], False)
   == ["الغريب لطيفة الشعلان", "الغريب"], "search: a long title with a subtitle is searched by its main part")
ok(T.same_title("باتمان حلوان", "باتمان_حلوان#"), "search: a hashtag-style Goodreads title is the same title")
ok(not T.same_title("الزميل - الانتقام الهادئ ج4", "الزميل: الانتقام الهادئ 2"), "search: another volume of the same series is refused")
ok(T.same_title("إن شاء الله 2: صرخة الحجارة", "إن شاء الله: 2- صرخة الحجارة"), "search: the same volume number on both sides is accepted")
ok(not T.same_title("الأيام", "الأيام 2") and not T.same_title("الأيام ج2", "الأيام"), "search: a part number on one side only is refused")
ok(T.same_title("الأخوة كارامازوف 4/1", "الأخوة كارامازوف 1") and not T.same_title("الأخوة كارامازوف 4/1", "الأخوة كارامازوف 2"), "search: «4/1» matches part 1 only")
ok(not T.same_title("مدن الملح", "مدن الملح الجزء الثاني"), "search: «الجزء الثاني» on one side only is refused")
ok(T.same_title("فن الشعر ط 2", "فن الشعر") and T.same_title("اقتحام الحرم ( 1979 )", "اقتحام الحرم"), "search: edition markers and years are not volumes")

# goodreads_reviews.find_gid: a page resolved by the title search is used only when Goodreads shows enough ratings
spec = importlib.util.spec_from_file_location("goodreads_reviews", os.path.join(ROOT, "scripts", "goodreads_reviews.py"))
G = importlib.util.module_from_spec(spec); spec.loader.exec_module(G)
class _Con:
    def execute(self, *a):
        return self
    def fetchone(self):
        return ("أمل", "جيلبير سينويه", None)
G.gr_book_id = lambda bid: None
G._search_found = lambda: {"551": {"gid": "77", "ratings": 9}, "553": {"gid": "88", "ratings": 94}}
ok(G.find_gid(_Con(), 553, True, False) == ("show/88", "search"), "fetch: a search-resolved page with enough ratings is used, also with --no-search")
ok(G.find_gid(_Con(), 551, True, False) is None, "fetch: a search-resolved page with fewer than 10 ratings is not fetched")
# Ibrahim 2026-10-04: our title and author come before the ISBN
class _Con2(_Con):
    def fetchone(self):
        return ("أمل", "جيلبير سينويه", "9789953680000")
G._isbn_failed = lambda: {"560": {"reason": "404"}}
ok(G.find_gid(_Con2(), 553, True, False) == ("show/88", "search"), "fetch: the page found by our title and author is used before the ISBN")
ok(G.find_gid(_Con2(), 552, True, False) == ("isbn/9789953680000", "isbn"), "fetch: with no searched page the ISBN is still tried")
ok(G.find_gid(_Con2(), 560, True, False) is None, "fetch: an ISBN that failed before is not asked again")
ok(G.find_gid(_Con2(), 551, True, False) == ("isbn/9789953680000", "isbn"), "fetch: a searched page with too few ratings falls back to the ISBN")
_dar = [{"ar": ["محمود درويش"], "latin": "Mahmoud Darwish"}]
_nol = lambda n: [{"ar": [n], "latin": None}]
ok(G.same_book("اللغة والمجاز: بين التوحيد ووحدة الوجود", _nol("عبد الوهاب المسيري"), "أيام الإنسان السبعة", "عبد الحكيم قاسم") is False, "isbn page: another Arabic title is another book")
ok(G.same_book("آخر الليل", _dar, "Utmost of the Night (اخر الليل) - Akher Allalyl", "Mahmoud Darwish") is True, "isbn page: our title inside a Latin Goodreads title is the same book")
ok(G.same_book("مفارقات الحياة الصغيرة", _nol("توماس هاردي"), "مفارقات الحياة", "Thomas Hardy") is True, "isbn page: a shorter title is the same book")
ok(G.same_book("دلمون: مملكة ببحرين", _nol("جيلبير سينويه"), "دلمون ممكلة ببحرين", "جيلبرت سينويه") is True, "isbn page: spelling variants of title and author pass")
ok(G.same_book("الدون الهادئ", _nol("ميخائيل شولوخوف"), "الدون الهادئ - ج1-4", "Mikhail Sholokhov") is True, "isbn page: a part label does not refuse the page")
ok(G.same_book("النار والعنقاء: صقر قريش (2)", _nol("وليد سيف"), "صقر قريش", "وليد سيف") is True, "isbn page: the volume's own title is the same book")
ok(G.same_book("أنا منهن", _nol("رزان العيسى"), "Memoirs of the Court of Elizabeth, Queen of England", "Lucy Aikin") is None, "isbn page: foreign title and no comparable author is left to the reader")
ok(G.same_book("سد على الباسيفيك", [{"ar": ["مارغريت دوراس"], "latin": "Marguerite Duras"}], "Masajid Misr", "Suad Mahir Muhammad") is None, "isbn page: foreign title by an unrecognised name is left to the reader")
ok(G.same_book("نقد العقل الكلبي ج 1", _nol("بيتر سلوتردايك"), "نقد العقل الكلبي، المجلد الأول", "Peter Sloterdijk") is True, "isbn page: part labels written differently pass")
ok(G.same_book("أنفاس امرأة مخذولة", [{"ar": ["باسم خندقجي"], "latin": "Basem Khandaqji"}], "Anfas Imrah Makhzulah", "Basem Khandaqji") is True, "isbn page: transliterated title by the same person passes")
# the fetched page is kept whole for the other tracks (description, original title, edition language)
import json as _json
_state = {"Book:kca://book/1": {"legacyId": 77, "title": "أمل", "description": "<b>وصف</b> الكتاب", 'description({"stripped":true})': "وصف الكتاب",
                               "primaryContributorEdge": {"node": {"__ref": "Contributor:1"}, "role": "Author"},
                               "secondaryContributorEdges": [{"node": {"__ref": "Contributor:2"}, "role": "Translator"}],
                               "bookGenres": [{"genre": {"name": "Fiction"}}],
                               "details": {"numPages": 210, "publisher": "دار", "language": {"name": "Arabic"}, "isbn13": "9789953680000"}},
          "Contributor:1": {"name": "Gilbert Sinoué"}, "Contributor:2": {"name": "مترجم"},
          "Work:1": {"details": {"originalTitle": "Inch'Allah"}, "stats": {"averageRating": 4.1, "ratingsCount": 12}}}
_pg = '<script id="__NEXT_DATA__" type="application/json">' + _json.dumps({"props": {"pageProps": {"apolloState": _state}}}) + "</script>"
_d = G.parse_page(_pg, 77)
ok(_d["page"]["description"] == "وصف الكتاب" and _d["page"]["original_title"] == "Inch'Allah" and _d["page"]["edition_language"] == "Arabic",
   "fetch: description, original title and edition language are saved with the page")
ok(_d["page"]["contributors"] == [{"name": "Gilbert Sinoué", "role": "Author"}, {"name": "مترجم", "role": "Translator"}] and _d["page"]["pages"] == 210,
   "fetch: contributors with roles and page count are saved with the page")
ok(G.parse_page(_pg.replace('"details": {"numPages"', '"x": {"numPages"'), 77)["page"]["description"] == "وصف الكتاب", "fetch: a page without edition details still parses")
print(f"TOTAL PASS={P} FAIL={F}")
