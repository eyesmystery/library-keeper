"""Library events: admin «الفعاليات» and «فعالياتنا» on the visitor start page (2026-10-08)."""
import os, sys, sqlite3, struct, zlib
from datetime import date, timedelta
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import io
import models, events, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

def png(w=200, h=120):
    raw = b"".join(b"\x00" + bytes([(x * 7 + y) % 256 for x in range(w * 3)]) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

models.init_db()
con = sqlite3.connect(models.DB_PATH)
ok("events" in {r[0] for r in con.execute("select name from sqlite_master where type='table'")}, "migration creates the table")
bid = con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 1").fetchone()[0]
today = date.fromisoformat(events.today())
visitor = A.app.test_client()
s = login(A.app.test_client(), "staff")

# ---- access
ok(visitor.get("/api/admin/events").status_code == 401 and visitor.post("/api/admin/events", json={}).status_code == 401, "admin routes need sign-in")
ok(s.get("/api/admin/events").status_code == 200, "staff (not only the manager) can manage events")
ok(s._c.post("/api/admin/events", json={"title": "x", "event_date": today.isoformat()}).status_code == 403, "writing needs the CSRF token")
ok(visitor.get("/api/events").get_json()["events"] == [], "no events: the start page list is empty")

# ---- validation
bad = [{}, {"title": "ا", "event_date": today.isoformat()}, {"title": "أمسية", "event_date": "غدا"},
       {"title": "أمسية", "event_date": "2026-02-30"}, {"title": "أمسية", "event_date": today.isoformat(), "event_time": "25:00"},
       {"title": "أمسية", "event_date": today.isoformat(), "end_time": "20:00"},
       {"title": "أمسية", "event_date": today.isoformat(), "book_id": 99999999}]
ok(all(s.post("/api/admin/events", json=b).status_code == 400 for b in bad), "missing name / bad date / bad time / unknown book refused")

# ---- add
r = s.post("/api/admin/events", json={"title": "  أمسية قراءة\x00 ", "description": "نقرأ معًا\r\nالفصل الأول",
                                     "event_date": (today + timedelta(days=3)).isoformat(), "event_time": "19:30", "end_time": "21:00", "book_id": bid})
ok(r.status_code == 200 and r.get_json()["event_id"], "staff add an event about a book")
e1 = r.get_json()["event_id"]
e2 = s.post("/api/admin/events", json={"title": "توقيع كتاب", "event_date": today.isoformat()}).get_json()["event_id"]
e3 = s.post("/api/admin/events", json={"title": "فعالية فاتت", "event_date": (today - timedelta(days=1)).isoformat()}).get_json()["event_id"]
pub = visitor.get("/api/events").get_json()["events"]
ok([e["event_id"] for e in pub] == [e2, e1], "visitors see today's and upcoming events, soonest first; past ones hidden")
p1 = next(e for e in pub if e["event_id"] == e1)
ok(p1["title"] == "أمسية قراءة" and p1["description"] == "نقرأ معًا\nالفصل الأول" and p1["event_time"] == "19:30" and p1["end_time"] == "21:00", "text cleaned, times kept")
ok(p1["book"]["book_id"] == bid and "location" in p1["book"] and "created_by" not in p1, "the event carries its book for the book page; no staff fields")
adm = s.get("/api/admin/events").get_json()["events"]
ok([e["event_id"] for e in adm] == [e2, e1, e3] and next(e for e in adm if e["event_id"] == e3)["past"], "admin lists upcoming first, then past")
ok(next(e for e in adm if e["event_id"] == e1)["created_by"] == "اختبار staff", "the writer is kept")

# ---- picture
ok(s.post(f"/api/admin/events/{e2}/image", data={"file": (io.BytesIO(b"not an image" * 200), "a.png")},
          content_type="multipart/form-data").status_code == 400, "a file that is not an image is refused")
r = s.post(f"/api/admin/events/{e2}/image", data={"file": (io.BytesIO(png(600, 300)), "poster.png")}, content_type="multipart/form-data")
ok(r.status_code == 200 and r.get_json()["image_url"].startswith(f"/event-images/{e2}.png"), "a wide poster is accepted")
url = r.get_json()["image_url"].split("?")[0]
ok(visitor.get(url).status_code == 200 and visitor.get("/event-images/../library.db").status_code == 404, "the picture is served; nothing else is")
pub = visitor.get("/api/events").get_json()["events"]
ok(next(e for e in pub if e["event_id"] == e2)["image_url"].startswith("/event-images/"), "visitors get the event's own picture")
ok(s.delete(f"/api/admin/events/{e2}/image").status_code == 200
   and next(e for e in visitor.get("/api/events").get_json()["events"] if e["event_id"] == e2)["image_url"] is None, "the picture can be removed")

# ---- edit, hide, delete
ok(s.put(f"/api/admin/events/{e1}", json={"title": "أمسية قراءة جديدة", "event_date": (today + timedelta(days=3)).isoformat(),
                                          "book_id": None, "active": False}).status_code == 200, "edit and hide")
ok(e1 not in [e["event_id"] for e in visitor.get("/api/events").get_json()["events"]], "a hidden event is not shown to visitors")
e = next(x for x in s.get("/api/admin/events").get_json()["events"] if x["event_id"] == e1)
ok(e["title"] == "أمسية قراءة جديدة" and e["book_id"] is None and e["active"] == 0 and e["updated_by"] == "اختبار staff", "edit saved")
ok(s.put("/api/admin/events/99999", json={"title": "س س", "event_date": today.isoformat()}).status_code == 404, "unknown event")
ok(s.delete(f"/api/admin/events/{e3}").status_code == 200 and s.delete(f"/api/admin/events/{e3}").status_code == 404, "delete")
acts = [r[0] for r in con.execute("select action from audit_log where action like 'event.%'")]
ok(acts.count("event.add") == 3 and "event.update" in acts and "event.delete" in acts and "event.image" in acts, "changes are in the audit log")

# ---- pages
page = visitor.get("/").get_data(as_text=True)
ok('id="eventsBox"' in page and 'id="eventDetail"' in page, "start page has «فعالياتنا» and the event page")
admin = visitor.get("/admin").get_data(as_text=True)
ok('id="tabEvents"' in admin and "/api/admin/events" in admin, "admin page has the «الفعاليات» tab")

# ---- «أضف إلى التقويم»: an .ics file for an upcoming active event (Ibrahim, 2026-10-09)
import events as EV
cal_ev = {"event_id": 7, "title": "أمسية، قراءة; جديدة", "description": "سطر أول\nسطر ثاني " + "طويل " * 30,
          "event_date": "2026-10-15", "event_time": "19:30", "end_time": "21:00"}
body = EV.ics(cal_ev, "كتاب ما")
ok("DTSTART:20261015T163000Z" in body and "DTEND:20261015T180000Z" in body, "Riyadh 19:30–21:00 written as UTC 16:30–18:00")
ok("SUMMARY:أمسية، قراءة\\; جديدة" in body, "text escaped")
ok(all(len(l.encode()) <= 75 for l in body.split("\r\n")), "lines folded to 75 octets")
ok("عن كتاب «كتاب ما»" in body.replace("\r\n ", ""), "the book is named in the description")
allday = EV.ics(dict(cal_ev, event_time=None, end_time=None))
ok("DTSTART;VALUE=DATE:20261015" in allday and "DTEND;VALUE=DATE:20261016" in allday, "no time -> all-day")
late = EV.ics(dict(cal_ev, event_time="23:00", end_time=None))
ok("DTEND:20261015T210000Z" in late, "no end time -> one hour")
up = [x for x in visitor.get("/api/events").get_json()["events"]]
if up:
    r = visitor.get(f"/api/events/{up[0]['event_id']}/calendar.ics")
    ok(r.status_code == 200 and r.mimetype == "text/calendar" and "BEGIN:VEVENT" in r.get_data(as_text=True)
       and "attachment" in r.headers.get("Content-Disposition", ""), "visitor downloads the .ics of an upcoming event")
ok(visitor.get("/api/events/99999/calendar.ics").status_code == 404, "unknown event: 404")
ok('id="eCalendarIcs"' in page and 'id="eCalendarGoogle"' in page, "event page has the calendar buttons")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
