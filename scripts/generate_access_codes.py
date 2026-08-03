"""Generate participant access codes and matching Supabase insert SQL.

Example:
  set ACCESS_CODE_PEPPER=<random-secret>
  python scripts/generate_access_codes.py --count 40 --max-sessions 6

The CSV contains plaintext codes and must never be committed. The SQL contains
only hashes and is safe to paste into the Supabase SQL editor.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import os
import secrets
from pathlib import Path


def hash_code(pepper: str, code: str) -> str:
    return hashlib.sha256(f"{pepper}:{code}".encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--prefix", default="P")
    parser.add_argument("--max-sessions", type=int, default=6)
    parser.add_argument("--output-dir", default="secrets")
    args = parser.parse_args()

    pepper = os.getenv("ACCESS_CODE_PEPPER", "")
    if not pepper:
        raise SystemExit("ACCESS_CODE_PEPPER 환경변수를 먼저 설정하세요.")
    if args.count < 1 or args.count > 500:
        raise SystemExit("--count는 1~500이어야 합니다.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"access_codes_{stamp}.csv"
    sql_path = output_dir / f"access_codes_{stamp}.sql"

    rows: list[tuple[str, str, str]] = []
    for number in range(args.start, args.start + args.count):
        participant_id = f"{args.prefix}{number:02d}"
        code = secrets.token_urlsafe(9)
        rows.append((participant_id, code, hash_code(pepper, code)))

    with csv_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["participant_id", "access_code"])
        writer.writerows((participant_id, code) for participant_id, code, _ in rows)

    values = ",\n".join(
        f"  ('{participant_id}', '{code_hash}', 'participant', {args.max_sessions})"
        for participant_id, _, code_hash in rows
    )
    sql = f"""insert into public.access_codes
  (participant_id, code_hash, role, max_sessions)
values
{values}
on conflict (participant_id) do update set
  code_hash = excluded.code_hash,
  role = excluded.role,
  max_sessions = excluded.max_sessions,
  sessions_started = 0,
  expires_at = null,
  active = true;
"""
    sql_path.write_text(sql, encoding="utf-8")

    print(f"Plaintext CSV: {csv_path.resolve()}")
    print(f"Supabase SQL: {sql_path.resolve()}")
    print("CSV는 참가자 배포용이며 Git에 커밋하지 마세요.")


if __name__ == "__main__":
    main()
