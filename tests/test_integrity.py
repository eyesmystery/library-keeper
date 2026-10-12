"""«عن التطبيق» tamper check (Ibrahim 2026-10-12, option 3): reported in the inbox file, never blocking."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import integrity
import inbox_export
from auth_helper import login
import app as A
import models
models.init_db()

def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)

ok(integrity.check() == [], "this version's about page, its CSS and the developer logo match EXPECTED (refresh EXPECTED after an approved change)")
md = inbox_export.build("all", "اختبار")[0]
ok("صفحة «عن التطبيق»: سليمة" in md and "تنبيه لإبراهيم" not in md, "an intact page is reported as intact in the inbox file")

html_path = integrity.ADMIN
orig = open(html_path, encoding="utf-8").read()
try:
    open(html_path, "w", encoding="utf-8").write(orig.replace('<span>تطوير</span>', '<span>تطوير المكتبة</span>'))
    ok(any("محتوى صفحة «عن التطبيق»" in x for x in integrity.check()), "a changed word in the about page is detected")
    md = inbox_export.build("all", "اختبار")[0]
    ok("تنبيه لإبراهيم: صفحة «عن التطبيق» تغيّرت" in md and "صفحة «عن التطبيق»: تغيّرت" in md, "the inbox file warns Ibrahim at the top")
    open(html_path, "w", encoding="utf-8").write(orig.replace('.credit span{font-size:14px}', '.credit span{font-size:14px;display:none}'))
    ok(any("تنسيق صفحة «عن التطبيق»" in x for x in integrity.check()), "hiding it through its CSS is detected")
    a = orig.index("<!-- about:start -->"); b = orig.index("<!-- about:end -->") + len("<!-- about:end -->")
    open(html_path, "w", encoding="utf-8").write(orig[:a] + orig[b:])
    ok(any("محذوف" in x for x in integrity.check()), "removing the page is detected as deleted")
    open(html_path, "w", encoding="utf-8").write(orig.replace("\n      <section class=\"panel about\"", "\n\n        <section class=\"panel about\""))
    ok(not any("محتوى" in x for x in integrity.check()), "whitespace-only changes are not a tamper")
finally:
    open(html_path, "w", encoding="utf-8").write(orig)
logo = integrity.LOGO
data = open(logo, "rb").read()
try:
    open(logo, "wb").write(data + b"x")
    ok(any("شعار المطوّر" in x for x in integrity.check()), "a replaced developer logo is detected")
    os.remove(logo)
    ok(any("شعار المطوّر" in x and "محذوف" in x for x in integrity.check()), "a deleted developer logo is detected")
finally:
    open(logo, "wb").write(data)
ok(integrity.check() == [], "intact again after restoring")
# the admin catalog carries the location used by the new «الموقع» column and filter
c = login(A.app.test_client())
cat = c.get("/api/admin/catalog").get_json()
ok(cat and all("location" in b and "location_kind" in b for b in cat), "admin catalog carries location / location_kind")
ok(all((b["location_kind"] == "shelf") == bool((b["shelf_location"] or "").strip()) for b in cat if b["location"]), "a typed place wins over the genre's section")
