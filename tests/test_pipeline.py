"""The pipeline end to end, and the database it writes to.

main.main() is driven offline: discovery, page fetch and the two
external lookups are stubbed, so nothing here touches the network.
"""

import ast
import sqlite3
from pathlib import Path

import pytest

import database
import main as main_module
import qualification
import scraper
from conftest import make_analysis

ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# HARNESS
# ============================================================

@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway database, wired in wherever the path is read."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DATABASE_NAME", str(path))
    return str(path)


def run_pipeline(monkeypatch, results, analyses, followers=None, products=None):
    """Drive main.main() offline.

    `results` is what search_websites() returns; `analyses` maps a URL to
    the analyze_site() dict (or an Exception instance to raise).
    """
    monkeypatch.setattr(scraper, "discover", lambda **kw: iter(results))

    def fake_analyze(url):
        outcome = analyses[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(scraper, "analyze_site", fake_analyze)
    monkeypatch.setattr(
        scraper, "fetch_products_json", lambda url, **kw: (products or {}).get(url)
    )
    monkeypatch.setattr(
        scraper, "fetch_instagram_followers",
        lambda handle, **kw: (followers or {}).get(handle)
    )
    monkeypatch.setattr(main_module.time, "sleep", lambda *a, **k: None)

    return main_module.main()


def site(url, title=None):
    return {"business": title or url, "website": url,
            "sources": [{"query": "q", "position": 1, "title": title}]}


def sme(name, **overrides):
    """A analyze_site() result for a plain Lebanese Shopify SME."""
    return make_analysis(business_name=name, **overrides)


# ============================================================
# THE DATABASE IS CREATED ONCE AND REUSED
# ============================================================

def test_first_run_creates_the_database_and_saves_leads(db, monkeypatch):
    assert not Path(db).exists()

    url = "https://zeinahandmade.com"
    run_pipeline(monkeypatch, [site(url)], {url: sme("Zeina Handmade")},
                 followers={"zeinahandmade": 900})

    assert Path(db).exists()
    leads = database.all_leads()
    assert len(leads) == 1
    assert leads[0]["business_name"] == "Zeina Handmade"


def test_second_run_skips_the_same_business(db, monkeypatch):
    """Rediscovering the same store must not create a second lead."""
    url = "https://zeinahandmade.com"
    analyses = {url: sme("Zeina Handmade")}

    run_pipeline(monkeypatch, [site(url)], analyses,
                 followers={"zeinahandmade": 900})
    first = database.all_leads()

    run_pipeline(monkeypatch, [site(url)], analyses,
                 followers={"zeinahandmade": 900})
    second = database.all_leads()

    assert len(first) == 1
    assert len(second) == 1
    assert second[0]["id"] == first[0]["id"]


def test_third_run_appends_new_leads_to_the_same_database(db, monkeypatch):
    old = "https://zeinahandmade.com"
    new = "https://byblosceramics.com"

    run_pipeline(monkeypatch, [site(old)], {old: sme("Zeina Handmade")},
                 followers={"zeinahandmade": 900})
    run_pipeline(
        monkeypatch, [site(old), site(new)],
        {old: sme("Zeina Handmade"), new: sme("Byblos Ceramics")},
        followers={"zeinahandmade": 900, "byblosceramics": 400},
    )

    leads = database.all_leads()
    assert len(leads) == 2
    assert {row["business_name"] for row in leads} == {
        "Zeina Handmade", "Byblos Ceramics"
    }
    # One file, not one per run.
    assert len(list(Path(db).parent.glob("*.db"))) == 1


def test_the_same_business_on_a_second_domain_is_not_duplicated(db, monkeypatch):
    """A custom domain and its .myshopify.com twin are one business."""
    first = "https://zeinahandmade.com"
    second = "https://zeina-handmade.myshopify.com"

    run_pipeline(monkeypatch, [site(first)],
                 {first: sme("Zeina Handmade", instagram="zeinahandmade")},
                 followers={"zeinahandmade": 900})
    run_pipeline(monkeypatch, [site(second)],
                 {second: sme("Zeina Handmade", instagram="zeinahandmade")},
                 followers={"zeinahandmade": 900})

    leads = database.all_leads()
    assert len(leads) == 1
    assert "zeina-handmade.myshopify.com" in (leads[0]["aka_domains"] or "")


# ============================================================
# ONLY QUALIFIED BUSINESSES ARE STORED
# ============================================================

def test_a_mixed_run_stores_only_the_qualified_business(db, monkeypatch):
    good = "https://zeinahandmade.com"
    woo = "https://notshopify.com"
    foreign = "https://lebanonpa.com"
    brand = "https://swarovski.com.lb"

    analyses = {
        good: sme("Zeina Handmade"),
        woo: make_analysis(business_name="Not Shopify", is_shopify=False),
        foreign: make_analysis(
            business_name="Lebanon PA Books",
            lebanon_signals={"strong": [], "claim": [], "medium": [],
                             "us_conflict": True}),
        brand: make_analysis(business_name="Swarovski Lebanon"),
    }

    run_pipeline(monkeypatch, [site(u) for u in (good, woo, foreign, brand)],
                 analyses, followers={"zeinahandmade": 900})

    leads = database.all_leads()
    assert [row["business_name"] for row in leads] == ["Zeina Handmade"]


def test_rejected_sites_are_not_refetched_on_the_next_run(db, monkeypatch):
    """The rejection cache: a site already judged is not fetched again."""
    woo = "https://notshopify.com"
    analyses = {woo: make_analysis(business_name="Not Shopify",
                                   is_shopify=False)}

    run_pipeline(monkeypatch, [site(woo)], analyses)

    fetched = []

    def counting_analyze(url):
        fetched.append(url)
        return analyses[url]

    monkeypatch.setattr(scraper, "discover", lambda **kw: iter([site(woo)]))
    monkeypatch.setattr(scraper, "analyze_site", counting_analyze)
    monkeypatch.setattr(scraper, "fetch_products_json", lambda u, **k: None)
    monkeypatch.setattr(scraper, "fetch_instagram_followers",
                        lambda h, **k: None)
    monkeypatch.setattr(main_module.time, "sleep", lambda *a, **k: None)
    main_module.main()

    assert fetched == [], "an already-rejected site was fetched again"
    assert database.all_leads() == []


def test_an_unreachable_site_does_not_crash_the_run(db, monkeypatch):
    broken = "https://broken.com"
    good = "https://zeinahandmade.com"

    run_pipeline(
        monkeypatch, [site(broken), site(good)],
        {broken: ConnectionError("dns failure"), good: sme("Zeina Handmade")},
        followers={"zeinahandmade": 900},
    )

    assert [row["business_name"] for row in database.all_leads()] == [
        "Zeina Handmade"
    ]


# ============================================================
# WHAT GETS STORED
# ============================================================

def test_a_lead_stores_name_instagram_and_root_website(db, monkeypatch):
    url = "https://zeinahandmade.com/collections/all/products/vase"

    run_pipeline(monkeypatch, [site(url)],
                 {url: sme("Zeina Handmade", instagram="zeinahandmade")},
                 followers={"zeinahandmade": 900})

    lead = database.all_leads()[0]
    assert lead["business_name"] == "Zeina Handmade"
    assert lead["instagram_url"] == "https://www.instagram.com/zeinahandmade/"
    assert lead["website"] == "https://zeinahandmade.com"


def test_a_lead_without_instagram_stores_null(db, monkeypatch):
    url = "https://byblosceramics.com"

    run_pipeline(monkeypatch, [site(url)],
                 {url: sme("Byblos Ceramics", instagram=None)})

    assert database.all_leads()[0]["instagram_url"] is None


def test_the_schema_stores_only_the_three_lead_fields(db):
    database.create_database()

    connection = sqlite3.connect(db)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(leads)")}
    connection.close()

    assert {"business_name", "instagram_url", "website"} <= columns
    # No scoring or calibration columns may reappear.
    for gone in ("decision", "score", "reasons", "signals_json",
                 "instagram_followers", "dominant_vendor", "status"):
        assert gone not in columns, f"{gone} is back in the schema"


# ============================================================
# STRUCTURAL GUARDS
# ============================================================

def test_main_qualifies_before_it_saves():
    """No path may store a lead without going through qualification."""
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    names = [
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    ]

    assert "qualify" in names, "main.py never calls qualify()"
    assert names.index("qualify") < names.index("add_lead"), (
        "add_lead() is reached before qualify()"
    )


def test_the_calibration_workflow_is_gone():
    """The labeling/calibration layer must not be reachable: it is not
    part of the product and required manual work after every run."""
    for name in ("calibration.py", "label.py", "ledger.py", "requalify.py",
                 "views.sql"):
        assert not (ROOT / name).exists(), f"{name} is back"

    # Check what main.py actually IMPORTS, not what its prose mentions.
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    for gone in ("ledger", "calibration", "label", "csv"):
        assert gone not in imported, f"main.py still imports {gone}"


def test_main_takes_no_arguments():
    """`python main.py` must be the whole interface."""
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "argparse" not in source
    assert "sys.argv" not in source


def test_no_delete_statement_exists_in_the_codebase():
    """Leads are never destroyed by a normal run."""
    for name in ("main.py", "database.py", "qualification.py", "scraper.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert "DELETE FROM" not in source.upper(), f"{name} deletes rows"
        assert "DROP TABLE" not in source.upper(), f"{name} drops tables"


# ============================================================
# EXTRACTION BUG FIXES (kept from the previous suite)
# ============================================================

def test_sentry_dsn_is_not_treated_as_a_contact_email():
    html = ('<script>dsn:"https://aa4cacd@o4506196830715904.ingest.us.'
            'sentry.io/123"</script><p>hello@realstore.com</p>')
    assert scraper.extract_email(html) == "hello@realstore.com"


def test_shopify_app_vendor_email_is_ignored():
    html = '<p>support@gist-apps.com</p><p>info@realstore.com</p>'
    assert scraper.extract_email(html) == "info@realstore.com"


def test_invalid_instagram_handles_return_none():
    for handle in ["", None, "p", "reel", "a" * 40, "bad handle", "-nope"]:
        assert scraper.fetch_instagram_followers(handle) is None


def test_follower_text_parsing():
    assert scraper._parse_follower_text("117", "K") == 117_000
    assert scraper._parse_follower_text("9", "M") == 9_000_000
    assert scraper._parse_follower_text("8,839", "") == 8839


def test_database_path_is_independent_of_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert Path(database.DATABASE_NAME).is_absolute()


# ============================================================
# CLOSED STOREFRONTS ARE NOT LEADS
# ============================================================

def test_a_closed_store_is_not_saved(db, monkeypatch):
    """A suspended Shopify store carries every other qualifying signal,
    so without this check it sails through and lands in the lead list.
    Two such stores were in the live database."""
    url = "https://moromart.com"
    run_pipeline(monkeypatch, [site(url)],
                 {url: sme("Moromart", is_available=False)})

    assert database.all_leads() == []
    assert database.was_checked("moromart.com")


def test_a_closed_store_is_reported_as_closed_not_as_something_else(exclusions):
    analysis = make_analysis(business_name="Moromart", is_available=False)
    decision = qualification.qualify(
        "https://moromart.com", analysis, exclusions,
        fetch_external=False, products=None, followers=500,
    )
    assert not decision.qualified
    assert decision.reason == qualification.STORE_CLOSED


def test_an_open_store_is_unaffected(db, monkeypatch):
    url = "https://zeinahandmade.com"
    run_pipeline(monkeypatch, [site(url)],
                 {url: sme("Zeina Handmade", is_available=True)},
                 followers={"zeinahandmade": 900})
    assert len(database.all_leads()) == 1


# ============================================================
# A RUN STOPS WHEN IT HAS ENOUGH
# ============================================================

def test_a_run_stops_once_it_reaches_the_target(db, monkeypatch):
    """Discovery is lazy, so hitting the target must stop it consuming
    any further candidates - that is the whole point of the generator."""
    monkeypatch.setattr(main_module, "TARGET_NEW_LEADS", 2)

    urls = [f"https://store{i}.com" for i in range(6)]
    analyses = {u: sme(f"Store Number {i}") for i, u in enumerate(urls)}

    consumed = []

    def lazy(**kwargs):
        for u in urls:
            consumed.append(u)
            yield site(u)

    monkeypatch.setattr(scraper, "discover", lazy)
    monkeypatch.setattr(scraper, "analyze_site", lambda u: analyses[u])
    monkeypatch.setattr(scraper, "fetch_products_json", lambda u, **k: None)
    monkeypatch.setattr(scraper, "fetch_instagram_followers",
                        lambda h, **k: 500)
    monkeypatch.setattr(main_module.time, "sleep", lambda *a, **k: None)
    main_module.main()

    assert len(database.all_leads()) == 2
    assert len(consumed) == 2, (
        f"kept searching after the target: consumed {len(consumed)}"
    )


def test_a_run_ends_cleanly_when_queries_run_out_before_the_target(
        db, monkeypatch):
    monkeypatch.setattr(main_module, "TARGET_NEW_LEADS", 10)

    url = "https://zeinahandmade.com"
    assert run_pipeline(monkeypatch, [site(url)],
                        {url: sme("Zeina Handmade")},
                        followers={"zeinahandmade": 900}) == 0
    assert len(database.all_leads()) == 1


# ============================================================
# THE LEAD LIST CONTAINS ONLY CONTACTABLE BUSINESSES
# ============================================================
#
# `rejected_sites` sits next to `leads` in the same file, so in a database
# browser its rows look like data. They are not: it is an internal cache
# of domains already judged and rejected, kept only so repeat runs do not
# re-fetch the same sites. This pins the separation for every rejection
# reason at once - a rejected business must never reach `leads`.

def test_every_rejection_reason_lands_in_rejected_sites_and_never_in_leads(
        db, monkeypatch):
    good = "https://zeinahandmade.com"
    rejects = {
        "https://notshopify.com": (
            make_analysis(business_name="Woo Store", is_shopify=False),
            qualification.NOT_SHOPIFY),
        "https://closedstore.com": (
            make_analysis(business_name="Closed Store", is_available=False),
            qualification.STORE_CLOSED),
        "https://lebanonpa.com": (
            make_analysis(business_name="Lebanon PA Books",
                          lebanon_signals={"strong": [], "claim": [],
                                           "medium": [], "us_conflict": True}),
            qualification.OUTSIDE_LEBANON),
        "https://swarovski.com.lb": (
            make_analysis(business_name="Swarovski Lebanon"),
            qualification.LARGE_BRAND),
        "https://placeholder.myshopify.com": (
            make_analysis(business_name="My Store"),
            qualification.PLACEHOLDER),
    }

    analyses = {url: a for url, (a, _) in rejects.items()}
    analyses[good] = sme("Zeina Handmade")

    run_pipeline(monkeypatch, [site(u) for u in list(rejects) + [good]],
                 analyses, followers={"zeinahandmade": 900})

    # Only the one qualified business is a lead.
    assert [r["business_name"] for r in database.all_leads()] == [
        "Zeina Handmade"
    ]

    connection = sqlite3.connect(db)
    lead_domains = {r[0] for r in connection.execute(
        "SELECT domain FROM leads")}
    cached = dict(connection.execute(
        "SELECT domain, result FROM rejected_sites"))
    connection.close()

    for url, (_analysis, expected_reason) in rejects.items():
        domain = scraper.clean_url(url)
        assert domain not in lead_domains, f"{domain} reached the lead list"
        assert cached.get(domain) == expected_reason, (
            f"{domain} cached as {cached.get(domain)!r}, "
            f"expected {expected_reason!r}"
        )

    # The two tables never describe the same domain.
    assert not (lead_domains & set(cached))


def test_lead_count_reports_the_lead_table_only(db, monkeypatch):
    """The run summary must never count cached rejects as leads."""
    good = "https://zeinahandmade.com"
    woo = "https://notshopify.com"

    run_pipeline(
        monkeypatch, [site(woo), site(good)],
        {woo: make_analysis(business_name="Woo", is_shopify=False),
         good: sme("Zeina Handmade")},
        followers={"zeinahandmade": 900},
    )

    connection = sqlite3.connect(db)
    cached = connection.execute(
        "SELECT COUNT(*) FROM rejected_sites").fetchone()[0]
    connection.close()

    assert cached == 1
    assert database.lead_count() == 1, "cached rejects leaked into the count"
