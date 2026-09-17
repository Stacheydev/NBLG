"""Discovery: pagination, page-level retry, and domain deduplication.

These are the three reasons a run stopped finding anything.  28 queries
read one page deep is a CLOSED pool of ~195 domains, and once the
database knew ~90% of it no amount of re-running could produce a lead.

Nothing here touches the network: DDGS is replaced by FakeDDGS, which
records every (query, page) it is asked for.  That recording is the
assertion - the point is not that results come back, it is that the
right pages are requested and the wrong ones are not.
"""

import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

import scraper


# ============================================================
# HARNESS
# ============================================================

def result(url, title="Store"):
    """One ddgs text result, shaped as the real library returns it."""
    return {"href": url, "title": title}


class FakeDDGS:
    """Stands in for DDGS.  `pages` maps (query, page) -> outcome.

    An outcome is either a list of results, or an exception INSTANCE to
    raise.  A (query, page) that is absent raises DDGSException, which is
    how the real library reports a query that has run out of results -
    it never returns an empty list.

    An exception outcome is raised on EVERY attempt unless it is wrapped
    in `once()`, which raises the first time and succeeds after.
    """

    def __init__(self, pages):
        self.pages = dict(pages)
        self.calls = []          # every (query, page) asked for, in order
        self.max_results_seen = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, query, max_results=None, page=1):
        self.calls.append((query, page))
        self.max_results_seen.append(max_results)
        outcome = self.pages.get((query, page))

        if outcome is None:
            raise DDGSException("No results found.")
        if callable(outcome):
            return outcome()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def pages_for(self, query):
        return [p for q, p in self.calls if q == query]


def once(exc, then):
    """Raise `exc` on the first attempt, return `then` on every one after."""
    state = {"raised": False}

    def outcome():
        if not state["raised"]:
            state["raised"] = True
            raise exc
        return then

    return outcome


@pytest.fixture
def fake(monkeypatch):
    """Install a FakeDDGS and silence the politeness sleeps."""
    monkeypatch.setattr(scraper.time, "sleep", lambda *a, **k: None)

    installed = {}

    def install(pages):
        ddgs = FakeDDGS(pages)
        installed["ddgs"] = ddgs
        monkeypatch.setattr(scraper, "DDGS", lambda *a, **k: ddgs)
        return ddgs

    return install


# ============================================================
# PAGINATION
# ============================================================

def test_page_one_is_still_searched(fake):
    """The change must not trade page 1 away for the deeper pages."""
    ddgs = fake({("q", 1): [result("https://one.com")]})

    candidates = list(scraper.discover(queries=["q"]))

    assert ("q", 1) == ddgs.calls[0]
    assert [c["website"] for c in candidates] == ["https://one.com"]


def test_pages_two_to_four_are_searched(fake):
    """The whole point of the fix: four pages deep, in order."""
    ddgs = fake({
        ("q", page): [result(f"https://p{page}.com")]
        for page in (1, 2, 3, 4)
    })

    candidates = list(scraper.discover(queries=["q"]))

    assert ddgs.pages_for("q") == [1, 2, 3, 4]
    assert [c["website"] for c in candidates] == [
        "https://p1.com", "https://p2.com", "https://p3.com", "https://p4.com",
    ]


def test_pages_per_query_is_four():
    """The measured default.  Deeper trades precision and run time."""
    assert scraper.PAGES_PER_QUERY == 4


def test_max_results_per_query_is_unchanged():
    """Pagination was the fix; widening each page was not."""
    assert scraper.MAX_RESULTS_PER_QUERY == 10


def test_a_page_never_goes_past_pages_per_query(fake):
    """Every page has results, so only the cap can stop the walk."""
    ddgs = fake({
        ("q", page): [result(f"https://p{page}.com")]
        for page in range(1, 10)
    })

    list(scraper.discover(queries=["q"]))

    assert ddgs.pages_for("q") == [1, 2, 3, 4]


def test_max_results_is_passed_through_to_every_page(fake):
    ddgs = fake({("q", p): [result(f"https://p{p}.com")] for p in (1, 2)})

    list(scraper.discover(queries=["q"], max_results=7, pages=2))

    assert ddgs.max_results_seen == [7, 7]


def test_the_default_max_results_reaches_the_provider(fake):
    ddgs = fake({("q", 1): [result("https://one.com")]})

    list(scraper.discover(queries=["q"], pages=1))

    assert ddgs.max_results_seen == [scraper.MAX_RESULTS_PER_QUERY]


# ============================================================
# EXHAUSTED QUERIES STOP PAGINATING
# ============================================================

def test_no_results_on_a_later_page_stops_that_query(fake):
    """`site:` queries end after page 1.  Asking for page 3 and 4 of a
    query that ended at page 2 is a wasted request, not an error."""
    ddgs = fake({
        ("q", 1): [result("https://one.com")],
        ("q", 2): [result("https://two.com")],
        # page 3 absent -> DDGSException("No results found.")
    })

    candidates = list(scraper.discover(queries=["q"]))

    assert ddgs.pages_for("q") == [1, 2, 3]
    assert 4 not in ddgs.pages_for("q")
    assert len(candidates) == 2


def test_an_exhausted_page_is_not_retried(fake):
    """Retrying "no results" cannot change the answer."""
    ddgs = fake({("q", 1): [result("https://one.com")]})

    list(scraper.discover(queries=["q"]))

    assert ddgs.pages_for("q") == [1, 2]  # page 2 asked ONCE, then stop


def test_an_exhausted_query_does_not_end_the_run(fake):
    """The next query must still be searched."""
    ddgs = fake({("b", 1): [result("https://b.com")]})

    candidates = list(scraper.discover(queries=["a", "b"]))

    assert ddgs.pages_for("a") == [1]
    assert [c["website"] for c in candidates] == ["https://b.com"]


def test_a_query_with_nothing_at_all_yields_nothing(fake):
    fake({})
    assert list(scraper.discover(queries=["q"])) == []


# ============================================================
# RETRY
# ============================================================

@pytest.mark.parametrize("transient", [
    TimeoutException("operation timed out"),
    RatelimitException("429"),
])
def test_one_failed_page_is_retried_once_and_recovers(fake, transient):
    """The measured failure: a page-1 timeout used to discard the whole
    query silently, and the run still reported a clean summary."""
    ddgs = fake({
        ("q", 1): once(transient, [result("https://recovered.com")]),
    })

    candidates = list(scraper.discover(queries=["q"], pages=1))

    assert ddgs.pages_for("q") == [1, 1]  # attempt, then retry
    assert [c["website"] for c in candidates] == ["https://recovered.com"]


def test_a_page_failing_twice_is_skipped_without_crashing(fake):
    """Two attempts, then move on - a timeout says nothing about whether
    the NEXT page has results, so pagination must continue."""
    ddgs = fake({
        ("q", 1): [result("https://one.com")],
        ("q", 2): TimeoutException("operation timed out"),
        ("q", 3): [result("https://three.com")],
        ("q", 4): [result("https://four.com")],
    })

    candidates = list(scraper.discover(queries=["q"]))

    assert ddgs.pages_for("q") == [1, 2, 2, 3, 4]
    assert [c["website"] for c in candidates] == [
        "https://one.com", "https://three.com", "https://four.com",
    ]


def test_retry_is_capped_at_one_attempt(fake):
    """No infinite loop: exactly two attempts per failing page, ever."""
    ddgs = fake({
        ("q", page): TimeoutException("operation timed out")
        for page in (1, 2, 3, 4)
    })

    assert list(scraper.discover(queries=["q"])) == []
    assert ddgs.pages_for("q") == [1, 1, 2, 2, 3, 3, 4, 4]


def test_an_unknown_exception_is_also_retried_then_skipped(fake):
    """An error the library did not wrap must not end the run either."""
    ddgs = fake({
        ("q", 1): ValueError("something unexpected"),
        ("q", 2): [result("https://two.com")],
    })

    candidates = list(scraper.discover(queries=["q"], pages=2))

    assert ddgs.pages_for("q") == [1, 1, 2]
    assert [c["website"] for c in candidates] == ["https://two.com"]


def test_a_failing_query_does_not_stop_the_next_query(fake):
    ddgs = fake({
        ("a", 1): TimeoutException("operation timed out"),
        ("b", 1): [result("https://b.com")],
    })

    candidates = list(scraper.discover(queries=["a", "b"], pages=1))

    assert [c["website"] for c in candidates] == ["https://b.com"]
    assert ddgs.pages_for("a") == [1, 1]


def test_the_retry_backs_off_before_trying_again(fake, monkeypatch):
    slept = []
    monkeypatch.setattr(scraper.time, "sleep", lambda s: slept.append(s))
    fake({("q", 1): once(TimeoutException("t"), [result("https://x.com")])})

    list(scraper.discover(queries=["q"], pages=1))

    assert scraper.SEARCH_RETRY_BACKOFF in slept


# ============================================================
# DOMAIN-LEVEL DEDUPLICATION
# ============================================================

def test_two_urls_from_the_same_domain_yield_one_candidate(fake):
    """Search results are overwhelmingly deep links, so one shop arrives
    as several URLs.  main.py reduces every one to the same domain."""
    fake({("q", 1): [
        result("https://shop.com/products/vase"),
        result("https://shop.com/collections/all"),
        result("https://shop.com/"),
    ]})

    candidates = list(scraper.discover(queries=["q"], pages=1))

    assert [c["website"] for c in candidates] == [
        "https://shop.com/products/vase"
    ]


def test_deduplication_uses_the_projects_own_clean_url(fake):
    """www. and a port must not create a twin - which is exactly what
    clean_url() already decides, so it is what discovery must use."""
    fake({("q", 1): [
        result("https://www.shop.com/a"),
        result("https://shop.com/b"),
        result("https://shop.com:443/c"),
    ]})

    candidates = list(scraper.discover(queries=["q"], pages=1))

    assert len(candidates) == 1


def test_a_subdomain_is_a_different_candidate(fake):
    """clean_url() keeps subdomains: a myshopify host is its own store."""
    fake({("q", 1): [
        result("https://shop.com/a"),
        result("https://store.shop.com/b"),
    ]})

    candidates = list(scraper.discover(queries=["q"], pages=1))

    assert len(candidates) == 2


def test_the_same_domain_is_not_yielded_twice_across_pages(fake):
    fake({
        ("q", 1): [result("https://shop.com/products/one")],
        ("q", 2): [result("https://shop.com/products/two")],
    })

    candidates = list(scraper.discover(queries=["q"], pages=2))

    assert len(candidates) == 1


def test_the_same_domain_is_not_yielded_twice_across_queries(fake):
    fake({
        ("a", 1): [result("https://shop.com/one")],
        ("b", 1): [result("https://shop.com/two")],
    })

    candidates = list(scraper.discover(queries=["a", "b"], pages=1))

    assert len(candidates) == 1


def test_the_first_url_seen_for_a_domain_is_the_one_yielded(fake):
    """analyze_site() reduces it to the root anyway, but the yielded
    value must be predictable."""
    fake({("q", 1): [
        result("https://shop.com/first"),
        result("https://shop.com/second"),
    ]})

    candidates = list(scraper.discover(queries=["q"], pages=1))

    assert candidates[0]["website"] == "https://shop.com/first"


# ============================================================
# EVERYTHING ELSE ABOUT DISCOVERY IS UNCHANGED
# ============================================================

def test_ignored_hosts_are_still_dropped(fake):
    fake({("q", 1): [
        result("https://facebook.com/somepage"),
        result("https://instagram.com/handle"),
        result("https://realshop.com/x"),
    ]})

    candidates = list(scraper.discover(queries=["q"], pages=1))

    assert [c["website"] for c in candidates] == ["https://realshop.com/x"]


def test_a_result_with_no_url_is_skipped(fake):
    fake({("q", 1): [{"title": "no href"}, result("https://real.com")]})

    candidates = list(scraper.discover(queries=["q"], pages=1))

    assert [c["website"] for c in candidates] == ["https://real.com"]


def test_query_order_is_preserved(fake):
    """No randomisation and no rotation were introduced."""
    ddgs = fake({(q, 1): [result(f"https://{q}.com")] for q in "abcde"})

    list(scraper.discover(queries=list("abcde"), pages=1))

    assert [q for q, _ in ddgs.calls] == list("abcde")


def test_on_query_fires_once_per_query_not_once_per_page(fake):
    """It reports progress through the query list, which is still 28
    entries long however many pages each one costs."""
    fake({("q", p): [result(f"https://p{p}.com")] for p in (1, 2, 3, 4)})
    announced = []

    list(scraper.discover(queries=["q"], on_query=announced.append))

    assert announced == ["q"]


def test_the_candidate_shape_is_unchanged(fake):
    """main.py reads ["website"]; nothing else may have moved."""
    fake({("q", 1): [result("https://shop.com/x", title="Shop Name")]})

    candidate = list(scraper.discover(queries=["q"], pages=1))[0]

    assert candidate["business"] == "Shop Name"
    assert candidate["website"] == "https://shop.com/x"
    assert candidate["sources"][0]["query"] == "q"
    assert candidate["sources"][0]["position"] == 1


def test_discovery_stays_lazy(fake):
    """A run that hits its target must not have paid for the rest of the
    sweep - the generator contract main.py depends on."""
    ddgs = fake({(q, p): [result(f"https://{q}{p}.com")]
                 for q in ("a", "b", "c") for p in (1, 2, 3, 4)})

    stream = scraper.discover(queries=["a", "b", "c"])
    next(stream)

    assert {q for q, _ in ddgs.calls} == {"a"}


def test_the_default_query_list_is_still_twenty_eight():
    """No queries were added or removed as part of this change."""
    assert len(scraper.KEYWORDS) == 28
