"""[NOCONFIRM 2026-11-11] Ibrahim: «الكتب اللي تحتاج تاكيد هوية رح نقلل اهميتها.. ما رح نعتبرها مطلوبة خلاص... يكفي الكتب اللي
تحتاج مراجعة». An identity still to confirm no longer puts a book in «تحتاج مراجعة» (the staff's work list and its count);
the «يحتاج تأكيد» chip still lists those books, and the alerts' attention count already left them out."""
import os, re, sys
sys.path.insert(0, os.getcwd())
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
html = open(os.path.join(root, "admin", "index.html"), encoding="utf-8").read()
m = re.search(r"const needsReview = b => ([^;]+);", html)
ok(bool(m) and "needs_confirmation" not in m.group(1), "«تحتاج مراجعة» no longer counts books whose identity is still to confirm")
ok(bool(m) and m.group(1).strip() == "b.status === 'needs_review'", "only cards to review count (NOCONFIRM b, Ibrahim «حتى اختلف القراء على صفاتها ما نحتاجها خلاص»)")
ok("['conflict', 'صفات اختلف فيها القرّاء', b => b.counts.conflict > 0]" in html, "the reader-conflict chip still lists them")
ok("['needs_confirmation', 'يحتاج تأكيد', b => b.research_status === 'needs_confirmation']" in html, "the «يحتاج تأكيد» chip still lists them")
ok("هوية تحتاج تأكيدًا" not in html and "صفات متعارضة)" not in html, "the count's tooltip names only cards to review")
app_src = open(os.path.join(root, "backend", "app.py"), encoding="utf-8").read()
ok('if i["key"] not in ("confirm",)' in app_src, "the alerts' attention count leaves confirmations out (unchanged)")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
