"""
Build the ``candidates`` table from open BLM claims enriched with
PLSS TRS labels and nearby MRDS occurrences, then score each row.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from mining_os.config import settings
from mining_os.db import get_engine, vacuum_analyze
from mining_os.scoring import total_score

log = logging.getLogger("mining_os.build_candidates")


def build() -> None:
    eng = get_engine()
    radius_m = int(settings.MRDS_RADIUS_KM * 1000)

    with eng.begin() as conn:
        conn.execute(text("TRUNCATE candidates;"))

        # 1) Join claims → PLSS (polygon intersect) for TRS label
        # 2) Lateral join MRDS within radius of claim centroid
        # 3) Aggregate commodities + reference-text flag
        sql = text(f"""
        INSERT INTO candidates (
          claim_table, claim_id, state_abbr, serial_num, claim_name, claim_type,
          case_status, case_disposition, trs,
          mrds_hit_count, commodities, has_reference_text,
          score, geom_centroid, geom
        )
        SELECT
          'open'          AS claim_table,
          c.id            AS claim_id,
          c.state_abbr,
          c.serial_num,
          c.claim_name,
          c.claim_type,
          c.case_status,
          c.case_disposition,
          p.trs,
          COALESCE(m.mrds_hit_count, 0),
          COALESCE(m.commodities, ARRAY[]::text[]),
          COALESCE(m.has_reference_text, false),
          0,                           -- placeholder; scored in Python below
          c.geom_centroid,
          c.geom
        FROM blm_claims_open c
        LEFT JOIN LATERAL (
          SELECT p2.trs
          FROM plss_sections p2
          WHERE ST_Intersects(c.geom, p2.geom)
          LIMIT 1
        ) p ON true
        LEFT JOIN LATERAL (
          SELECT
            COUNT(*)::int AS mrds_hit_count,
            ARRAY(
              SELECT DISTINCT unnest(o.commodities)
            ) AS commodities,
            BOOL_OR(
              o.reference_text IS NOT NULL AND length(o.reference_text) > 0
            ) AS has_reference_text
          FROM mrds_occurrences o
          WHERE ST_DWithin(o.geom, c.geom_centroid, {radius_m})
        ) m ON true
        WHERE c.state_abbr IS NULL
           OR c.state_abbr = ANY(:states);
        """)

        conn.execute(sql, {"states": settings.TARGET_STATES})

        # ---- Python-side scoring -----------------------------------------
        rows = conn.execute(
            text("SELECT id, commodities, mrds_hit_count, has_reference_text FROM candidates;")
        ).fetchall()
        log.info("Scoring %s candidates...", len(rows))

        key_commodities = set(settings.COMMODITIES)

        for r in rows:
            cid, raw_comm, mrds_hits, has_ref = r
            comm = [str(x).lower().strip() for x in (raw_comm or []) if x]
            score = total_score(comm, int(mrds_hits or 0), bool(has_ref))

            # Bonus if any key commodity appears
            if any(k in c for c in comm for k in key_commodities):
                score = min(score + 10, 100)

            conn.execute(
                text("UPDATE candidates SET score = :score WHERE id = :id;"),
                {"score": score, "id": cid},
            )

    vacuum_analyze("candidates")
    log.info("Candidates built + scored.")
