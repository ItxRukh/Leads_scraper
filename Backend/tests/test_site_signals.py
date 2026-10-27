import pytest

from utils import site_signals as ss
from utils.site_signals import Page

GOOD_HOME = """
<html><head><meta name="viewport" content="width=device-width"><title>Acme Heating</title>
<script src="https://code.tidio.co/abc.js"></script></head>
<body>
<a href="tel:+15125550142">Call (512) 555-0142</a>
<a href="/contact">Contact us</a>
<p>24/7 emergency service. See what our customers say: 5-star reviews.</p>
<iframe src="https://calendly.com/acme/service"></iframe>
<form action="/send"><input name="email"><textarea name="message"></textarea></form>
<footer>© 2026 Acme Heating</footer>
</body></html>
"""

BAD_HOME = """
<html><head><title>Old Co</title></head>
<body>
<p>Call us at 512-555-0142 for a free estimate.</p>
<a href="/about">About</a> <a href="/contact-us">Contact</a>
<form action="/search" role="search"><input type="search" name="q"></form>
<footer>Copyright 2016 Old Co</footer>
</body></html>
"""

CONTACT_PAGE = '<html><body><form action="/send"><input type="email" name="e"><textarea></textarea></form></body></html>'


def fake_fetch(pages):
    return lambda url: Page(url, pages[url]) if url in pages else None


# ─── analysing pages ─────────────────────────────────────────────────────────

def test_a_well_equipped_site_is_recognised():
    out = ss.analyze([Page("https://acme.example/", GOOD_HOME)])
    facts = out["facts"]
    assert facts["site_has_click_to_call"] and facts["site_has_contact_form"]
    assert facts["site_has_online_booking"] and facts["site_booking_tool"] == "Calendly"
    assert facts["site_has_chat"] and facts["site_chat_tool"] == "Tidio"
    assert facts["site_mentions_after_hours"] and facts["site_has_reviews"]
    assert facts["site_mobile_friendly"] and facts["site_https"] and not facts["site_looks_outdated"]
    assert facts["site_copyright_year"] == 2026


def test_a_bare_site_shows_every_gap():
    facts = ss.analyze([Page("http://old.example/", BAD_HOME)])["facts"]
    assert not facts["site_has_click_to_call"]          # a phone number in the text is not a tap-to-call link
    assert not facts["site_has_contact_form"]           # a search box is not a contact form
    assert not facts["site_has_online_booking"] and not facts["site_has_chat"]
    assert not facts["site_mentions_after_hours"] and not facts["site_has_reviews"]
    assert not facts["site_mobile_friendly"] and not facts["site_https"]
    assert facts["site_looks_outdated"] and facts["site_copyright_year"] == 2016


def test_emails_come_from_text_and_mailto_links():
    html = '<html><body><p>Write to hello@acme.example</p><a href="mailto:owner@acme.example?subject=Hi">Email</a></body></html>'
    emails = ss.analyze([Page("https://acme.example/", html)])["emails"]
    assert "hello@acme.example" in emails and "owner@acme.example" in emails


def test_booking_words_on_a_button_count_as_online_booking():
    html = '<html><body><a class="btn" href="/x">Book Online</a></body></html>'
    assert ss.analyze([Page("https://a.example/", html)])["facts"]["site_has_online_booking"]


# ─── collecting pages ────────────────────────────────────────────────────────

def test_collect_follows_the_contact_link_and_finds_its_form():
    home = '<html><head><meta name="viewport" content="x"></head><body><a href="/contact-us">Contact</a><a href="/about">About</a></body></html>'
    fetch = fake_fetch({"https://shop.example": home, "https://shop.example/contact-us": CONTACT_PAGE})
    out = ss.collect_site_signals("shop.example", fetch=fetch)
    assert out["facts"]["site_pages_checked"] == 2 and out["facts"]["site_has_contact_form"]


def test_collect_falls_back_to_http_and_gives_up_cleanly():
    out = ss.collect_site_signals("plain.example", fetch=fake_fetch({"http://plain.example": GOOD_HOME}))
    assert out["facts"]["site_https"] is False
    assert ss.collect_site_signals("gone.example", fetch=fake_fetch({})) is None
    assert ss.collect_site_signals("", fetch=fake_fetch({})) is None


# ─── the fetcher must not be usable to reach internal addresses ──────────────

@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "10.1.2.3", "192.168.0.5", "169.254.169.254", "::1"])
def test_internal_addresses_are_refused(host):
    assert ss._is_public_host(host) is False
    assert ss.fetch_page(f"http://{host}/admin") is None


def test_a_public_address_is_allowed(monkeypatch):
    monkeypatch.setattr(ss.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))])
    assert ss._is_public_host("example.com") is True


def test_a_redirect_to_an_internal_address_is_refused(monkeypatch):
    resolved = {"public.example": "93.184.216.34", "internal.example": "127.0.0.1"}
    monkeypatch.setattr(ss.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", (resolved[host], 0))])
    calls = []

    class Redirect:
        status_code = 302
        headers = {"Location": "http://internal.example/secret"}
        encoding = "utf-8"

        def close(self):
            pass

    def fake_get(url, **kwargs):
        calls.append(url)
        return Redirect()

    monkeypatch.setattr(ss.requests, "get", fake_get)
    assert ss.fetch_page("http://public.example/") is None
    assert calls == ["http://public.example/"]            # the internal address was never requested


# ─── the signals our workflows build on ──────────────────────────────────────

SERVICE_SITE = """
<html><head><meta name="viewport" content="x"></head><body>
<p>Proudly serving Austin and the surrounding counties. Request a free estimate today.</p>
<p>Join our Comfort Club maintenance plan. Referring physicians: send us a referral.</p>
<a href="/forms">New patient forms</a> <a href="/locations">Our locations</a>
</body></html>
"""


def test_signals_for_estimates_service_area_plans_forms_referrals_and_branches():
    facts = ss.analyze([Page("https://acme.example/", SERVICE_SITE)])["facts"]
    assert facts["site_mentions_service_area"] and facts["site_invites_estimates"]
    assert facts["site_mentions_recurring_plan"] and facts["site_mentions_referrals"]
    assert facts["site_has_patient_forms"] and facts["site_multiple_locations"]


def test_those_signals_are_off_on_a_page_that_says_none_of_it():
    facts = ss.analyze([Page("https://acme.example/", "<html><body><p>Welcome to our shop.</p></body></html>")])["facts"]
    for key in ("site_mentions_service_area", "site_invites_estimates", "site_mentions_recurring_plan",
                "site_mentions_referrals", "site_has_patient_forms", "site_multiple_locations"):
        assert facts[key] is False, key
