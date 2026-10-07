"""Email styles (sequences) for distributor / brand outreach, rewritten from the Commercial Lemonade templates
for Eigenreach: shorter, plainer, one question per email. Each: first email → 3 days → follow-up → 4 days →
last follow-up → 5 days → finish. A reply at any point stops the sequence and notifies you.

    .venv/bin/python -m app.styles          # adds the styles (skips ones that already exist)
"""
from . import db, sequences

THANKS = "{Thanks|Thank you|Best}"

FOLLOW_1 = ("Hi {{first_name|there}},\n\n"
            "{Just following up on my note below.|Following up on my email below.} "
            "{Is there a wholesale application I should fill out, or a better person to talk to?|"
            "If there's a wholesale application or a better contact for this, could you point me to it?}\n\n"
            f"{THANKS},")


def last_note(line):
    return ("Hi {{first_name|there}},\n\n"
            f"One last note from me. {line}\n\n"
            "If you're open to new retail accounts, I can send our resale certificate and business details right away.\n\n"
            f"{THANKS},")


def style(name, rating, best_for, about, subject, body, last_line):
    return {
        "name": name,
        "settings": {"rating": rating, "best_for": best_for, "about": about,
                     "days": [0, 1, 2, 3, 4], "window_start": "09:00", "window_end": "16:00", "jitter_minutes": 20},
        "steps": [
            {"subject": subject, "body": body, "wait_days": 3},
            {"same_thread": True, "body": FOLLOW_1, "wait_days": 4},
            {"same_thread": True, "body": last_note(last_line), "wait_days": 5},
        ],
    }


STYLES = [
    style(
        "Short & direct", 5, "Any batch - the safe default",
        "Who we are in one line, then one clear question. Short emails get the most replies from busy sales inboxes.",
        "{Wholesale account inquiry|Opening a wholesale account|New wholesale account|Wholesale pricing request}",
        "Hi {{first_name|there}},\n\n"
        "I'm Oleg, the founder of Eigenreach, an online retailer in Ann Arbor, Michigan. "
        "Our team has sold consumer products online in international markets since 2021, "
        "and we're now expanding to the U.S.\n\n"
        "I'd like to open a wholesale account with {{company|you}}. Could you send me your price list "
        "and your requirements for new retailers?\n\n"
        f"{THANKS},",
        "We're adding new suppliers this month and would be glad to include you.",
    ),
    style(
        "Your brands", 5, "Product suppliers batches only (we know which brand they carry)",
        "Names the exact brand we found them selling. Specific emails look hand-written, so they get read.",
        "{Question about {{brands|your wholesale program}}|{{brands|Wholesale}} - wholesale account|"
        "Carrying {{brands|your products}}}",
        "Hi {{first_name|there}},\n\n"
        "I'm Oleg, the founder of Eigenreach, an online retailer in Ann Arbor, Michigan. "
        "We've sold online in international markets since 2021 and are now growing in the U.S. "
        "I found you as a supplier of {{brands|products we'd like to carry}}, and I'd like to open a "
        "wholesale account with you.\n\n"
        "Could you send me your price list and what you need from a new retailer?\n\n"
        f"{THANKS},",
        "We'd still love to carry {{brands|your products}}.",
    ),
    style(
        "We bring demand", 4, "Competitors batches, brands and manufacturers",
        "Our real difference: we run ads where search demand is growing, so products keep selling and we keep reordering.",
        "{Wholesale account inquiry|Selling your products online|Wholesale partnership}",
        "Hi {{first_name|there}},\n\n"
        "I'm Oleg, the founder of Eigenreach, an online retailer in Ann Arbor, Michigan. "
        "We've sold online in international markets since 2021 and are now growing in the U.S.\n\n"
        "We don't just list products and wait. We look at where search demand for a product is growing "
        "and run ads to reach those buyers, so the items we carry keep selling and we keep reordering.\n\n"
        "We'd like to open a wholesale account with {{company|you}}. Could you share your price list "
        "and account requirements?\n\n"
        f"{THANKS},",
        "We advertise the products we carry, so new suppliers usually see steady reorders from us.",
    ),
    style(
        "Brand-safe partner", 4, "Astra brand suppliers and brand owners (they care about MAP)",
        "Leads with MAP and clean listings. Brand owners worry about price undercutting; this answers it first.",
        "{Authorized retailer inquiry|Wholesale account - MAP-compliant retailer|Becoming an authorized retailer}",
        "Hi {{first_name|there}},\n\n"
        "I'm Oleg, the founder of Eigenreach, an online retailer in Ann Arbor, Michigan. "
        "We've sold online in international markets since 2021 and are now growing in the U.S.\n\n"
        "We'd like to become an authorized retailer for {{company|your brand}}. We follow MAP and brand "
        "policies, keep listings accurate, and compete on reach, not on price.\n\n"
        "Could you send me your reseller requirements and wholesale price list?\n\n"
        f"{THANKS},",
        "We follow MAP strictly and only sell through channels you approve.",
    ),
    style(
        "Steady reorders", 3, "Distributors and catalogs (paid catalogs batches)",
        "Data-driven, repeat purchasing. Solid, but a bit more generic than the others.",
        "{Wholesale account inquiry|Recurring wholesale orders|New retail account}",
        "Hi {{first_name|there}},\n\n"
        "I'm Oleg, the founder of Eigenreach, an online retailer in Ann Arbor, Michigan. "
        "We've sold online in international markets since 2021 and are now growing in the U.S.\n\n"
        "Our team of mathematicians uses search-demand data to decide what to stock, so we order on a "
        "steady schedule instead of one-off buys.\n\n"
        "We'd like to open a wholesale account with {{company|you}}. Could you send me your price list "
        "and your requirements for new retailers?\n\n"
        f"{THANKS},",
        "We plan purchases from demand data, so we reorder on a regular schedule.",
    ),
]


def refresh_styles():
    """Rewrites existing styles' text from STYLES, keeping step ids so running batches stay on track."""
    import json
    done = []
    for st in STYLES:
        row = db.q1("SELECT * FROM pipelines WHERE name=?", (st["name"],))
        if not row:
            continue
        old = sequences.steps_of(json.loads(row["definition"]))
        steps = [dict(x) for x in st["steps"]]
        for i, x in enumerate(steps):
            if i < len(old):
                x["id"] = old[i]["id"]
        settings = {**json.loads(row["settings"] or "{}"), **{k: st["settings"][k] for k in ("rating", "best_for", "about")}}
        sequences.save(row["id"], st["name"], steps, settings)
        done.append(st["name"])
    return done


def seed_styles():
    made = []
    for s in STYLES:
        if db.q1("SELECT id FROM pipelines WHERE name=?", (s["name"],)):
            continue
        pid, errs = sequences.save(None, s["name"], [dict(x) for x in s["steps"]], s["settings"])
        made.append((s["name"], pid, errs))
    return made


if __name__ == "__main__":
    import sys
    db.init()
    if "--refresh" in sys.argv:
        print("updated:", refresh_styles())
    for m in seed_styles():
        print(m)
