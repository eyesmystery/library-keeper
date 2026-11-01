"""[GRLINK] Ibrahim 2026-10-30 «ممكن يعطيه رابط يفتح على جواله اراء القراء عن الكتاب في goodreads»: the book page has a link
to the book's readers' reviews on Goodreads (its own page when our records hold it, else a Goodreads search by title and
author). Static checks of the page and the script; the behaviour was checked in Chrome on 2026-10-30 (log [MGR])."""
import os, re, sys, shutil, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

html = open(os.path.join(ROOT, "frontend", "index.html"), encoding="utf-8").read()
js = open(os.path.join(ROOT, "frontend", "app.js"), encoding="utf-8").read()
a = re.search(r'<a id="dGoodreads"[^>]*>', html)
ok(bool(a) and 'target="_blank"' in a.group(0) and "noopener" in a.group(0), "the book page has the Goodreads link, opened apart, noopener")
ok("goodreadsLink: 'آراء القرّاء في Goodreads ↗'" in js and "goodreadsLink: 'Readers’ reviews on Goodreads ↗'" in js, "its words in Arabic and English")
ok("function goodreadsUrl(book)" in js and "$('dGoodreads').href = gr" in js, "showBook sets the link of the book shown")
node = shutil.which("node")
if node:
    fn = js[js.index("function goodreadsUrl(book)"):]
    fn = fn[:fn.index("\n}\n") + 3]
    code = fn + """
const out = [goodreadsUrl({title:'زمان الملائكة', author:'آيريس مردوخ', summary_source:'http://www.goodreads.com/book/show/123.The_Time?x=1'}),
             goodreadsUrl({title:'حيونة الإنسان', author:'ممدوح عدوان', summary_source:'https://www.abjjad.com/x'}),
             goodreadsUrl({title:'كتاب', author:'مجهول'}), goodreadsUrl({})];
console.log(JSON.stringify(out));"""
    import json
    r = json.loads(subprocess.run([node, "-e", code], capture_output=True, text=True, timeout=30).stdout)
    ok(r[0] == "https://www.goodreads.com/book/show/123.The_Time", "the book's own Goodreads page when its summary came from there (https, no query)")
    ok(r[1].startswith("https://www.goodreads.com/search?q=") and "%D8%AD%D9%8A%D9%88%D9%86%D8%A9" in r[1] and "%D8%B9%D8%AF%D9%88%D8%A7%D9%86" in r[1],
       "otherwise a Goodreads search by title and author")
    ok(r[2].endswith("q=%D9%83%D8%AA%D8%A7%D8%A8") and r[3] == "", "an unknown author is left out; no title, no link")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
