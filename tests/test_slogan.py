"""The app's slogan on the start page (Ibrahim 2026-10-23: «منار - ذكاء يرشدك إلى ما يناسبك» /
«Manar - Intelligence that guides you to what fits you»; Arabic for the Arabic page, English for the English page)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)
AR, EN = "منار - ذكاء يرشدك إلى ما يناسبك", "Manar - Intelligence that guides you to what fits you"
c = A.app.test_client()
page = c.get("/").get_data(as_text=True)
js = c.get("/static/app.js").get_data(as_text=True)
css = c.get("/static/styles.css").get_data(as_text=True)
ok('id="brandSlogan"' in page and AR in page, "the start page shows the Arabic slogan under the logo")
ok(f"slogan: '{AR}'" in js and f"slogan: '{EN}'" in js, "the slogan has an Arabic and an English text")
ok("$('brandSlogan').textContent = t('slogan')" in js, "the slogan follows the page language")
ok(".brand-slogan{" in css, "the slogan is styled")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
