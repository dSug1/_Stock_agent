"""Reconcile the institutions table with institution_registry.py.

seed_institutions() uses INSERT OR IGNORE and only updates CIK /
edgar_name when they are NULL. After an in-place CIK correction in
the registry, the DB still holds the old value. This script:

  * UPDATEs rows whose CIK or edgar_name no longer matches the seed.
  * DELETEs rows whose name is no longer in the seed (cascading
    filings_log and institution_holdings first, since filings_log
    has no ON DELETE CASCADE).
  * Leaves rows that match the seed untouched.

Run from 1_not_used/ with PYTHONPATH=src:

    python scripts/sync_institution_seed.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from database.db import get_connection, now_iso
from layer_minus1.institution_registry import get_all_institutions


def main(db_path: str = "stockpicker.db") -> int:
    if not Path(db_path).exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = get_connection(db_path)
    try:
        seed = {i["name"]: i for i in get_all_institutions()}
        db_rows = conn.execute(
            "SELECT id, name, cik, edgar_name FROM institutions"
        ).fetchall()

        updates: list[tuple[int, str, str, str, str, str]] = []
        deletions: list[tuple[int, str]] = []
        unchanged = 0

        for r in db_rows:
            spec = seed.get(r["name"])
            if spec is None:
                deletions.append((r["id"], r["name"]))
                continue
            if (
                r["cik"] != spec["cik"]
                or r["edgar_name"] != spec["edgar_name"]
            ):
                updates.append((
                    r["id"], r["name"],
                    r["cik"], spec["cik"],
                    r["edgar_name"] or "", spec["edgar_name"] or "",
                ))
            else:
                unchanged += 1

        # Any seed entries not yet in DB — insert via seed_institutions.
        missing = [
            n for n in seed if n not in {r["name"] for r in db_rows}
        ]

        print(f"unchanged:  {unchanged}")
        print(f"updates:    {len(updates)}")
        print(f"deletions:  {len(deletions)}")
        print(f"new insert: {len(missing)}")

        ts = now_iso()
        if updates:
            print("\n=== Applying CIK / edgar_name updates ===")
            for inst_id, name, old_cik, new_cik, old_en, new_en in updates:
                print(
                    f"  {name}: CIK {old_cik} -> {new_cik}  "
                    f"edgar_name '{old_en}' -> '{new_en}'"
                )
                conn.execute(
                    "UPDATE institutions "
                    "SET cik = ?, edgar_name = ?, updated_at = ? "
                    "WHERE id = ?",
                    (new_cik, new_en, ts, inst_id),
                )

        if deletions:
            print("\n=== Deleting rows no longer in seed ===")
            for inst_id, name in deletions:
                holdings_n = conn.execute(
                    "SELECT COUNT(*) c FROM institution_holdings "
                    "WHERE institution_id = ?",
                    (inst_id,),
                ).fetchone()["c"]
                filings_n = conn.execute(
                    "SELECT COUNT(*) c FROM filings_log "
                    "WHERE institution_id = ?",
                    (inst_id,),
                ).fetchone()["c"]
                print(
                    f"  {name}: cascading "
                    f"{holdings_n} holdings, {filings_n} filings_log rows"
                )
                # filings_log has no ON DELETE CASCADE — drop manually.
                conn.execute(
                    "DELETE FROM filings_log WHERE institution_id = ?",
                    (inst_id,),
                )
                # institution_holdings cascades, but delete explicitly
                # in case foreign_keys pragma is off on someone's conn.
                conn.execute(
                    "DELETE FROM institution_holdings "
                    "WHERE institution_id = ?",
                    (inst_id,),
                )
                conn.execute(
                    "DELETE FROM institutions WHERE id = ?",
                    (inst_id,),
                )

        if missing:
            print("\n=== Seeding missing rows ===")
            from layer_minus1.institution_registry import seed_institutions
            seed_institutions(conn)
            for n in missing:
                print(f"  inserted: {n}")

        conn.commit()
        print("\nDone.")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main(sys.argv[1] if len(sys.argv) > 1 else "stockpicker.db")
    )
