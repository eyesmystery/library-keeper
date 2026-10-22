"""[MGR] Ibrahim 2026-10-16 23:3x: «اذا الرواية لها اكثر من فلم؟ اعتقد بلد الرواية اهم من بلد الفلم».
«رواية تحولت لفيلم عربي / أجنبي / هوليوودي»: the novel's own origin (its authors' countries, else its original language)
comes first; otherwise a film whose recorded country fits; a novel with several films matches when any one does.
Pure check on in-memory rows (no database)."""
import os, sys, time
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import honors
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

def film(country=None, title="Film", year=2000, kind="film"):
    return {"country": country, "title": title, "year": year, "kind": kind, "source": "test", "book_id": 0}

DATA = {"book": {}, "author": {}, "book_authors": {},
        "adapt": {1: [film()],                                   # Arabic novel, film country unknown
                  2: [film()],                                   # American novel, film country unknown
                  3: [film()],                                   # British novel, film country unknown
                  4: [film("مصر"), film("الولايات المتحدة")],    # foreign novel, an Egyptian and a US film
                  5: [film("فرنسا"), film("مصر")],              # Arabic novel, French + Egyptian films
                  6: [film()],                                   # nothing recorded about the novel
                  7: [film(kind="series")]},                     # Arabic novel, a series only
        "novel": {1: {"lang": "ar", "countries": []},
                  2: {"lang": "en", "countries": ["الولايات المتحدة"]},
                  3: {"lang": "en", "countries": ["المملكة المتحدة"]},
                  4: {"lang": "fr", "countries": ["فرنسا"]},
                  5: {"lang": "ar", "countries": ["مصر"]},
                  6: {"lang": "", "countries": []},
                  7: {"lang": "ar", "countries": []}}}
honors._cache.update(at=time.time() + 10**6, data=DATA)
m = lambda b, o, k="film": bool(honors.matches_adaptation(b, k, origin=o))

ok(honors.novel_origins(1) == {"arab"}, "Arabic original language, no author country -> arab")
ok(honors.novel_origins(2) == {"american", "foreign"}, "American author -> american + foreign")
ok(honors.novel_origins(3) == {"foreign"}, "British author -> foreign only")
ok(honors.novel_origins(6) == set(), "nothing recorded -> unknown")
ok(m(1, "arab") and not m(1, "foreign"), "Arabic novel whose film country is unknown answers «فيلم عربي»")
ok(m(2, "american") and m(2, "foreign") and not m(2, "arab"), "American novel answers «هوليوودي» and «أجنبي»")
ok(m(3, "foreign") and not m(3, "american"), "British novel with an unknown film: foreign, not Hollywood")
ok(m(4, "arab") and m(4, "american") and m(4, "foreign"), "several films: the Egyptian one answers «عربي», the US one «هوليوودي»")
labels = honors.matches_adaptation(4, "film", origin="arab")
ok(len(labels) == 1, "foreign novel: only its Egyptian film is named for «فيلم عربي»")
ok(len(honors.matches_adaptation(5, "film", origin="arab")) == 2, "Arabic novel: all its films count for «فيلم عربي»")
ok(not m(6, "arab") and not m(6, "foreign") and m(6, None), "nothing known: no origin wish met, but any-film still matches")
ok(m(7, "arab", "series") and not m(7, "arab", "film"), "the kind still decides (series vs film)")
honors.clear_cache()
print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
