from scripts import email_verify as ev


def test_bad_syntax_is_invalid():
    assert ev.verify_email_local("bad@@x")["status"] == "invalid"
    assert ev.verify_email_local("")["status"] == "invalid"


def test_disposable_domain_is_invalid():
    assert ev.verify_email_local("a@mailinator.com")["sub_status"] == "disposable"


def test_domain_without_mail_server_is_invalid(monkeypatch):
    monkeypatch.setattr(ev, "_domain_accepts_mail", lambda d: False)
    assert ev.verify_email_local("owner@nowhere.test")["status"] == "invalid"


def test_domain_with_mail_server_is_valid(monkeypatch):
    monkeypatch.setattr(ev, "_domain_accepts_mail", lambda d: True)
    assert ev.verify_email_local("owner@example.com")["status"] == "valid"


def test_dns_failure_is_unknown_not_invalid(monkeypatch):
    monkeypatch.setattr(ev, "_domain_accepts_mail", lambda d: None)
    assert ev.verify_email_local("owner@example.com")["status"] == "unknown"
