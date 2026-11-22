"""
Agreement archive.

Keeps a copy of every agreement that is generated so it can be found again.

The finished PDF is stored rather than regenerated on demand from the saved
values. The template has changed before and will change again; a regenerated
document would reflect today's template rather than the one the hirer actually
signed, which defeats the point of keeping it.

The archive is optional. With no DATABASE_URL set every function here turns
into a no-op and the rest of the app behaves exactly as it did before, so this
can be deployed before Postgres is provisioned without taking agreement
generation down.
"""

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

try:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
except ImportError:                                    # not installed locally
    psycopg2 = None

DATABASE_URL = os.environ.get('DATABASE_URL', '')

# How long an agreement is kept before it is purged. Six years is the UK
# limitation period for contract claims, so it spans the window in which a
# dispute over a hire could still be brought. Set AGREEMENT_RETENTION_YEARS to
# 0 to keep agreements indefinitely.
RETENTION_YEARS = int(os.environ.get('AGREEMENT_RETENTION_YEARS', '6'))

# Never written to the archive. Both signatures are already drawn into the
# stored PDF, and a detached copy of someone's signature serves no purpose that
# the signed document does not already serve.
EXCLUDED_FIELDS = ('hirer_signature', 'lessor_signature')

# Set by init_schema. Reports whether the archive is configured AND reachable,
# which is not the same as DATABASE_URL being present.
_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS agreements (
    id          text PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text        NOT NULL DEFAULT 'admin',
    hirer_name  text        NOT NULL DEFAULT '',
    vehicle_reg text        NOT NULL DEFAULT '',
    make_model  text        NOT NULL DEFAULT '',
    hire_start  text        NOT NULL DEFAULT '',
    filename    text        NOT NULL,
    pdf         bytea       NOT NULL,
    form_data   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    ended_at    timestamptz,
    end_pdf     bytea,
    end_data    jsonb
);

-- The table already exists in production, where CREATE TABLE IF NOT EXISTS is
-- a no-op, so new columns have to be added explicitly.
ALTER TABLE agreements ADD COLUMN IF NOT EXISTS ended_at timestamptz;
ALTER TABLE agreements ADD COLUMN IF NOT EXISTS end_pdf  bytea;
ALTER TABLE agreements ADD COLUMN IF NOT EXISTS end_data jsonb;
CREATE INDEX IF NOT EXISTS agreements_created_at_idx
    ON agreements (created_at DESC);
CREATE INDEX IF NOT EXISTS agreements_hirer_name_idx
    ON agreements (lower(hirer_name));
CREATE INDEX IF NOT EXISTS agreements_vehicle_reg_idx
    ON agreements (lower(vehicle_reg));
"""


def describe_error(error):
    """
    An exception as one line that is safe to log.

    Postgres adds a DETAIL to some errors that spells out the whole row it was
    given ("Failing row contains ..."), and every row here is a hirer's
    personal details: name, address, date of birth, NI and licence numbers.
    Only the primary message is kept, which names the problem and the column
    but not the values. Anything that is not a database error is logged as it
    is.
    """
    primary = getattr(getattr(error, 'diag', None), 'message_primary', None)
    if primary:
        code = getattr(error, 'pgcode', None)
        return f"{type(error).__name__}{f' ({code})' if code else ''}: {primary}"
    return f"{type(error).__name__}: {error}"


def is_enabled():
    """Whether there is an archive to talk to."""
    return bool(DATABASE_URL) and psycopg2 is not None


def status():
    """How the archive is doing, for the health check."""
    if not DATABASE_URL:
        return 'not configured'
    if psycopg2 is None:
        return 'driver missing'
    return 'connected' if _ready else 'unreachable'


@contextmanager
def connection():
    """
    A connection per request rather than one held open per worker.

    Railway recycles idle connections, and a stale module-level handle fails on
    the first query after an idle period - which would be the operator opening
    History first thing in the morning.
    """
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema():
    """Create the table if it is missing. Safe to call on every boot."""
    global _ready
    if not is_enabled():
        print("No DATABASE_URL set - agreement history is disabled.")
        return False
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(SCHEMA)
        _ready = True
        print(f"Agreement history ready (retention: "
              f"{RETENTION_YEARS or 'indefinite'} years).")
        return True
    except Exception as e:
        _ready = False
        print(f"Could not prepare agreement history: {describe_error(e)}")
        return False


def save_agreement(form_data, pdf_bytes, filename, created_by='admin'):
    """
    Archive one generated agreement. Returns its id, or None.

    Never raises: an archive that is down must not stop an operator handing a
    vehicle over, so a failure here is logged and the PDF is still returned.
    """
    if not is_enabled():
        return None

    stored = {k: v for k, v in form_data.items() if k not in EXCLUDED_FIELDS}
    agreement_id = str(uuid.uuid4())

    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agreements (id, created_by, hirer_name, vehicle_reg,
                                        make_model, hire_start, filename, pdf,
                                        form_data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (agreement_id, created_by,
                 form_data.get('full_name', ''), form_data.get('vehicle_reg', ''),
                 form_data.get('make_model', ''), form_data.get('hire_start', ''),
                 filename, psycopg2.Binary(pdf_bytes), Json(stored)),
            )
        purge_expired()
        return agreement_id
    except Exception as e:
        print(f"Could not archive agreement: {describe_error(e)}")
        return None


def list_agreements(search='', limit=50, before=None):
    """
    Most recent first, one page at a time, matching on hirer name or
    registration. Returns (rows, has_more).

    A page is asked for by position: before is the (created_at, id) of the
    oldest agreement already on screen, and the page is what comes after it.
    Skipping a count of rows instead would shift whenever an agreement was
    added or deleted between pages, showing one twice or missing one. The id
    settles the order between agreements created in the same instant.

    One row more than the page is read, only to learn whether another page
    follows. It is not returned.
    """
    if not is_enabled():
        return [], False

    conditions, params = [], []
    if search.strip():
        like = f"%{search.strip().lower()}%"
        conditions.append("(lower(hirer_name) LIKE %s "
                          "OR lower(vehicle_reg) LIKE %s "
                          "OR lower(replace(vehicle_reg, ' ', '')) LIKE %s)")
        params += [like, like, like.replace(' ', '')]
    if before:
        conditions.append("(created_at, id) < (%s, %s)")
        params += list(before)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ''

    with connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id, created_at, hirer_name, vehicle_reg, make_model,
                   hire_start, filename, ended_at
            FROM agreements {where}
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            params + [limit + 1],
        )
        rows = cur.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    for row in rows:
        row['created_at'] = row['created_at'].isoformat()
        row['ended_at'] = row['ended_at'].isoformat() if row['ended_at'] else None
    return rows, has_more


def get_agreement(agreement_id):
    """Everything about one agreement except the stored PDFs."""
    if not is_enabled():
        return None
    with connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, created_at, hirer_name, vehicle_reg, make_model,
                   hire_start, filename, ended_at, form_data, end_data
            FROM agreements WHERE id = %s
            """,
            (agreement_id,),
        )
        row = cur.fetchone()
    if row:
        row['created_at'] = row['created_at'].isoformat()
        row['ended_at'] = row['ended_at'].isoformat() if row['ended_at'] else None
    return row


def get_pdf(agreement_id):
    """
    (filename, agreement bytes, end page bytes or None).

    The two are kept apart rather than stored pre-merged. Each is exactly what
    was produced at the time it was signed, and the single file the operator
    sees is assembled from them on the way out.
    """
    if not is_enabled():
        return None, None, None
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT filename, pdf, end_pdf FROM agreements WHERE id = %s",
                    (agreement_id,))
        row = cur.fetchone()
    if not row:
        return None, None, None
    return row[0], bytes(row[1]), (bytes(row[2]) if row[2] else None)


def get_form_data(agreement_id):
    """The submitted values, for starting a repeat hire. Signatures excluded."""
    if not is_enabled():
        return None
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT form_data FROM agreements WHERE id = %s",
                    (agreement_id,))
        row = cur.fetchone()
    return row[0] if row else None


def save_end(agreement_id, ended_at, end_data, end_pdf):
    """Record that a hire finished, with the page produced for it."""
    if not is_enabled():
        return None
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE agreements
            SET ended_at = %s, end_data = %s, end_pdf = %s
            WHERE id = %s RETURNING ended_at
            """,
            (ended_at, Json(end_data), psycopg2.Binary(end_pdf), agreement_id),
        )
        row = cur.fetchone()
    return row[0].isoformat() if row and row[0] else None


def clear_end(agreement_id):
    """
    Put a hire back to running.

    Needed because the button will eventually be pressed on the wrong
    agreement, and without a way back the only remedy would be deleting the
    record. The end page goes with it, so a reopened hire reads as it did
    before it was ended.
    """
    if not is_enabled():
        return False
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE agreements SET ended_at = NULL, end_data = NULL, "
            "end_pdf = NULL WHERE id = %s",
            (agreement_id,),
        )
        return cur.rowcount > 0


def delete_agreement(agreement_id):
    """Erase one agreement. Used for data protection requests."""
    if not is_enabled():
        return False
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM agreements WHERE id = %s", (agreement_id,))
        return cur.rowcount > 0


def purge_expired():
    """Drop anything past the retention period."""
    if not is_enabled() or RETENTION_YEARS <= 0:
        return 0
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM agreements "
                "WHERE created_at < now() - make_interval(years => %s)",
                (RETENTION_YEARS,),
            )
            if cur.rowcount:
                print(f"Purged {cur.rowcount} agreement(s) past retention.")
            return cur.rowcount
    except Exception as e:
        print(f"Could not purge expired agreements: {describe_error(e)}")
        return 0
