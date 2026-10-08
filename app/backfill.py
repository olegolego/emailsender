"""One-off: pull attachments / price-list links out of emails that arrived before the Price lists section existed.

    .venv/bin/python -m app.backfill
"""
from . import db, engine, mailer


def run():
    db.init()
    done = 0
    for acc in db.q("SELECT * FROM accounts WHERE active=1"):
        msgs = db.q("SELECT id, message_id, contact_id FROM messages WHERE direction='in' AND account_id=? AND is_bounce=0 "
                    "AND message_id<>'' AND id NOT IN (SELECT message_row_id FROM price_lists WHERE message_row_id IS NOT NULL)",
                    (acc["id"],))
        if not msgs:
            continue
        folders = ["INBOX"] + ([mailer.spam_folder(acc)] if mailer.spam_folder(acc) else [])
        m = mailer._imap(acc)
        try:
            for folder in folders:
                m.select(mailer._quote_folder(folder), readonly=True)
                for row in msgs:
                    typ, data = m.uid("search", None, "HEADER", "Message-ID", row["message_id"].strip("<>"))
                    uids = (data[0] or b"").split() if typ == "OK" else []
                    if not uids:
                        continue
                    typ, md = m.uid("fetch", uids[0], "(BODY.PEEK[])")
                    if typ == "OK" and md and isinstance(md[0], tuple):
                        p = mailer.parse(md[0][1])
                        c = db.q1("SELECT * FROM contacts WHERE id=?", (row["contact_id"],)) if row["contact_id"] else None
                        done += engine.save_price_lists(row["id"], p, c)
        finally:
            m.logout()
    return done


if __name__ == "__main__":
    print("price lists found in earlier emails:", run())
