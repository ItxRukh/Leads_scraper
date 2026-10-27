import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import profile_service as svc
import queue_service as q
import scheduling as sch
from database import EmailLog, Lead, LeadTier, OutreachStepLog, PipelineStage
from profile_schema import ProfileIn

NY = ZoneInfo("America/New_York")
MON_10AM_NY = datetime(2026, 10, 12, 14, 0)        # Monday 10:00 in New York (EDT, UTC-4)


@pytest.fixture()
def db(seeded_db):
    return seeded_db


def queue_lead(db, **kw):
    base = dict(name="Lead", email="a@b.com", phone="+13055550100", city="Miami FL",
                tier=LeadTier.HOT, final_score=90)
    base.update(kw)
    lead = Lead(**base)
    db.add(lead)
    db.commit()
    entry = q.enqueue_if_eligible(db, lead)
    assert entry is not None
    return lead, entry


# ─── Working-hours maths ──────────────────────────────────────────────────────

S = sch.ScheduleSettings()     # Mon-Fri 09:00-17:00, fallback New York


@pytest.mark.parametrize("after,expected", [
    (datetime(2026, 10, 12, 14, 0), datetime(2026, 10, 12, 14, 0)),    # Mon 10:00 NY: already inside
    (datetime(2026, 10, 12, 11, 0), datetime(2026, 10, 12, 13, 0)),    # Mon 07:00 NY: wait for 09:00
    (datetime(2026, 10, 12, 21, 30), datetime(2026, 10, 13, 13, 0)),   # Mon 17:30 NY: next morning
    (datetime(2026, 10, 16, 21, 30), datetime(2026, 10, 19, 13, 0)),   # Fri 17:30 NY: Monday
    (datetime(2026, 10, 17, 16, 0), datetime(2026, 10, 19, 13, 0)),    # Saturday: Monday
])
def test_next_window_start_new_york(after, expected):
    assert sch.next_window_start(after, NY, S) == expected


def test_next_window_start_uses_the_leads_own_zone():
    la = ZoneInfo("America/Los_Angeles")
    # Mon 14:00Z = 07:00 in Los Angeles -> opens 09:00 PT = 16:00Z
    assert sch.next_window_start(MON_10AM_NY, la, S) == datetime(2026, 10, 12, 16, 0)
    assert sch.in_window(datetime(2026, 10, 12, 16, 30), la, S)
    assert not sch.in_window(MON_10AM_NY, la, S)


def test_settings_validation(schedule_store):
    for bad in ({"work_start": "18:00", "work_end": "09:00"}, {"work_start": "9am"},
                {"work_days": []}, {"work_days": [7]}, {"fallback_timezone": "Mars/Base"},
                {"email": {"min_gap_seconds": 500, "max_gap_seconds": 100}},
                {"email": {"mode": "yolo"}}):
        with pytest.raises(ValidationError):
            sch.ScheduleSettings.model_validate(bad)

    sch.save_settings(sch.ScheduleSettings(work_start="08:30"))
    assert sch.load_settings().work_start == "08:30"
    schedule_store["schedule"] = {"work_start": "nonsense"}
    assert sch.load_settings().work_start == "09:00"            # bad saved data falls back to defaults


# ─── Per-lead schedule ────────────────────────────────────────────────────────

def test_first_step_is_due_immediately_inside_working_hours(db, clock):
    _, entry = queue_lead(db)
    assert (entry.next_step_key, entry.next_channel) == ("email_1", "email")
    assert entry.next_due_at == MON_10AM_NY


def test_a_lead_queued_outside_hours_waits_for_its_own_morning(db, clock):
    clock(datetime(2026, 10, 12, 11, 0))                         # 07:00 in Miami
    _, miami = queue_lead(db, name="Miami", email="m@b.com")
    _, la = queue_lead(db, name="LA", email="l@b.com", city="Los Angeles CA")
    assert miami.next_due_at == datetime(2026, 10, 12, 13, 0)    # 09:00 ET
    assert la.next_due_at == datetime(2026, 10, 12, 16, 0)       # 09:00 PT


def test_follow_ups_count_from_when_the_previous_step_was_done(db, clock):
    lead, entry = queue_lead(db)

    clock(MON_10AM_NY + timedelta(minutes=5))
    sch.complete_step(db, lead.id)
    assert (entry.next_step_key, entry.next_due_at) == ("email_2", datetime(2026, 10, 15, 14, 5))   # +3 days

    clock(datetime(2026, 10, 15, 14, 5))
    sch.complete_step(db, lead.id)
    assert (entry.next_step_key, entry.next_channel) == ("email_3", "email")                        # WhatsApp step skipped: not used here
    assert entry.next_due_at > datetime(2026, 10, 15, 14, 5) + timedelta(days=3)


def test_a_due_time_that_lands_outside_hours_moves_to_the_next_working_morning(db, clock):
    lead, entry = queue_lead(db)
    clock(datetime(2026, 10, 16, 21, 30))                        # Friday 17:30 in Miami
    sch.complete_step(db, lead.id)                               # email_2 would be due Monday 17:30: after closing
    assert entry.next_due_at == datetime(2026, 10, 20, 13, 0)    # so it waits for Tuesday 09:00 in Miami


def test_each_leads_clock_is_independent(db, clock):
    a, ea = queue_lead(db, name="A", email="a@b.com")
    clock(datetime(2026, 10, 13, 15, 30))                        # next day
    b, eb = queue_lead(db, name="B", email="b@b.com")
    sch.complete_step(db, a.id, now=MON_10AM_NY)
    sch.complete_step(db, b.id)
    assert ea.next_due_at == datetime(2026, 10, 15, 14, 0)       # A: Monday + 3 days
    assert eb.next_due_at == datetime(2026, 10, 16, 15, 30)      # B: Tuesday + 3 days


def test_steps_the_lead_cannot_receive_are_skipped_and_logged(db, clock):
    lead, entry = queue_lead(db, email=None)                     # phone only: there is nothing to email
    assert entry.sequence_status == sch.COMPLETED and entry.next_step_key is None
    skipped = db.query(OutreachStepLog).filter_by(lead_id=lead.id, status="skipped").order_by(OutreachStepLog.id).all()
    assert [s.step_key for s in skipped] == ["email_1", "email_2", "whatsapp_1", "email_3", "call_1", "email_4"]
    assert skipped[0].note == "no email address"
    assert "GoHighLevel" in skipped[2].note                      # SMS, WhatsApp and calls are not made here


def test_cold_leads_only_get_email_steps(db, clock):
    lead = Lead(name="Cold", email="c@b.com", phone="+13055550101", city="Miami FL",
                tier=LeadTier.COLD, final_score=5)
    db.add(lead)
    db.commit()
    entry = q.add_manually(db, lead.id)
    channels = []
    for _ in range(10):
        if not entry.next_channel:
            break
        channels.append(entry.next_channel)
        sch.complete_step(db, lead.id)
    assert channels == ["email", "email", "email", "email"]      # WhatsApp and call skipped
    assert entry.sequence_status == sch.COMPLETED


def test_sequence_completes_after_the_last_step(db, clock):
    lead, entry = queue_lead(db)
    keys = []
    while entry.sequence_status == sch.ACTIVE:
        keys.append(entry.next_step_key)
        sch.complete_step(db, lead.id)
    assert keys == ["email_1", "email_2", "email_3", "email_4"]       # the WhatsApp and call steps are skipped
    assert entry.next_due_at is None


def test_skipping_a_step_moves_on_without_counting_it_as_done(db, clock):
    lead, entry = queue_lead(db)
    sch.skip_step(db, lead.id)
    assert entry.next_step_key == "email_2"
    assert entry.last_done_at is None
    assert db.query(OutreachStepLog).filter_by(lead_id=lead.id, status="done").count() == 0


def test_complete_step_errors(db, clock):
    with pytest.raises(LookupError):
        sch.complete_step(db, 9999)
    lead, entry = queue_lead(db)
    entry.sequence_status = sch.STOPPED
    db.commit()
    with pytest.raises(ValueError):
        sch.complete_step(db, lead.id)


def test_profile_sequence_is_used(db, clock):
    p = svc.create_profile(db, ProfileIn(name="Two steps", config={"sequence": {"steps": [
        {"key": "call_first", "channel": "call", "delay_days": 0, "min_tier": "cold"},
        {"key": "mail_after", "channel": "email", "delay_days": 1.5, "min_tier": "cold"}]}}))
    lead, entry = queue_lead(db, profile_id=p.id)
    assert entry.next_step_key == "mail_after"                   # the call step is skipped
    assert entry.next_due_at == MON_10AM_NY                      # the first real step is due now, its delay is ignored
    sch.complete_step(db, lead.id)
    assert entry.sequence_status == sch.COMPLETED


# ─── Stopping ─────────────────────────────────────────────────────────────────

def test_a_reply_stops_the_sequence(db, clock):
    lead, entry = queue_lead(db)
    db.add(EmailLog(lead_id=lead.id, replied=True))
    db.commit()
    assert sch.refresh_stops(db) == 1
    assert entry.sequence_status == sch.STOPPED and entry.stop_reason == "replied"
    assert entry.next_due_at is None
    assert sch.due_entries(db) == []


def test_do_not_contact_and_replied_stage_stop_too(db, clock):
    a, ea = queue_lead(db, name="A", email="a@b.com")
    b, eb = queue_lead(db, name="B", email="b@b.com")
    a.do_not_contact = True
    b.pipeline_stage = PipelineStage.REPLIED
    db.commit()
    sch.refresh_stops(db)
    assert (ea.stop_reason, eb.stop_reason) == ("do not contact", "replied")


def test_stopped_leads_show_in_the_list_with_a_reason(db, clock):
    lead, _ = queue_lead(db)
    db.add(EmailLog(lead_id=lead.id, replied=True))
    db.commit()
    item = q.list_queue(db)["items"][0]
    assert item["sequence_status"] == "stopped" and item["stop_reason"] == "replied"


# ─── What is due, caps and gaps ───────────────────────────────────────────────

def test_due_entries_respect_working_hours_right_now(db, clock):
    queue_lead(db, name="Miami", email="m@b.com")
    queue_lead(db, name="LA", email="l@b.com", city="Los Angeles CA")   # 07:00 there: shifted to 16:00Z
    assert [l.name for _, l in sch.due_entries(db)] == ["Miami"]
    clock(datetime(2026, 10, 12, 16, 0))
    assert sorted(l.name for _, l in sch.due_entries(db)) == ["LA", "Miami"]
    clock(datetime(2026, 10, 12, 22, 0))                                # 18:00 ET, 15:00 PT
    assert [l.name for _, l in sch.due_entries(db)] == ["LA"]


def test_follow_ups_come_before_first_contacts(db, clock):
    new, _ = queue_lead(db, name="New", email="n@b.com", final_score=99)
    old, _ = queue_lead(db, name="Old", email="o@b.com", final_score=50)
    sch.complete_step(db, old.id, now=MON_10AM_NY - timedelta(days=4))   # its next step is long overdue
    assert [l.name for _, l in sch.due_entries(db)] == ["Old", "New"]


def test_send_plan_applies_daily_cap_and_random_gaps(db, clock):
    sch.save_settings(sch.ScheduleSettings(email=sch.ChannelRules(
        mode="assisted", min_gap_seconds=60, max_gap_seconds=120, daily_cap=3)))
    for i in range(5):
        queue_lead(db, name=f"L{i}", email=f"l{i}@b.com", final_score=90 - i)

    plan = sch.build_send_plan(db, rng=random.Random(1))["email"]
    assert plan["daily_cap"] == 3 and plan["cap_left"] == 3
    assert [i["name"] for i in plan["items"]] == ["L0", "L1", "L2"]       # cap of 3, best scores first
    assert all(i["approval_required"] for i in plan["items"])

    times = [datetime.fromisoformat(i["release_at"].rstrip("Z")) for i in plan["items"]]
    assert times[0] == MON_10AM_NY
    for earlier, later in zip(times, times[1:]):
        assert 60 <= (later - earlier).total_seconds() <= 120


def test_done_steps_use_up_the_daily_cap_and_start_the_gap(db, clock):
    sch.save_settings(sch.ScheduleSettings(email=sch.ChannelRules(
        mode="auto", min_gap_seconds=300, max_gap_seconds=300, daily_cap=2)))
    a, _ = queue_lead(db, name="A", email="a@b.com")
    queue_lead(db, name="B", email="b@b.com")
    queue_lead(db, name="C", email="c@b.com")
    sch.complete_step(db, a.id)                                  # one email already done today

    plan = sch.build_send_plan(db, rng=random.Random(2))["email"]
    assert plan["cap_left"] == 1 and len(plan["items"]) == 1
    assert plan["items"][0]["approval_required"] is False        # auto mode
    release = datetime.fromisoformat(plan["items"][0]["release_at"].rstrip("Z"))
    assert release == MON_10AM_NY + timedelta(seconds=300)       # a gap after the last send


def test_resuming_a_paused_lead_plans_from_now(db, clock):
    lead, entry = queue_lead(db)
    q.set_status(db, lead.id, q.PAUSED)
    clock(datetime(2026, 10, 14, 14, 0))
    q.set_status(db, lead.id, q.QUEUED)
    assert entry.next_due_at == datetime(2026, 10, 14, 14, 0)


def test_list_shows_the_schedule(db, clock):
    queue_lead(db)
    out = q.list_queue(db)
    item = out["items"][0]
    assert out["due_now"] == 1
    assert item["next_step_label"] == "First email" and item["next_channel"] == "email"
    assert item["step_number"] == 1 and item["step_total"] == 6
    assert item["due_now"] is True and item["in_working_hours"] is True
    assert item["lead_local_time"] == "Mon 10:00" and item["mode"] == "assisted"
    assert q.list_queue(db, due=True)["total"] == 1


# ─── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(db):
    import queue_api
    from database import get_db
    app = FastAPI()
    app.include_router(queue_api.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_schedule_api(client, db, clock):
    lead, _ = queue_lead(db)

    assert client.get("/outreach-queue/schedule-settings").json()["work_start"] == "09:00"
    body = client.get("/outreach-queue/schedule-settings").json()
    body["work_start"] = "08:00"
    assert client.put("/outreach-queue/schedule-settings", json=body).json()["work_start"] == "08:00"
    body["work_end"] = "07:00"
    assert client.put("/outreach-queue/schedule-settings", json=body).status_code == 422

    plan = client.get("/outreach-queue/plan").json()
    assert plan["email"]["items"][0]["lead_id"] == lead.id

    done = client.post(f"/outreach-queue/{lead.id}/done").json()
    assert done == {"lead_id": lead.id, "next_step": "email_2", "sequence_status": "active"}
    assert client.post(f"/outreach-queue/{lead.id}/skip").json()["next_step"] == "email_3"
    assert client.post("/outreach-queue/9999/done").status_code == 404

    assert client.get("/outreach-queue?due=true").json()["total"] == 0     # next step is days away
