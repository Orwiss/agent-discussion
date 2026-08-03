"""Create/update Cloud Run secrets without printing secret values."""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
from pathlib import Path

from dotenv import dotenv_values


def run(gcloud: str, project: str, *args: str, input_bytes: bytes | None = None):
    return subprocess.run(
        [gcloud, *args, "--project", project],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def ensure_secret(gcloud: str, project: str, name: str) -> bool:
    described = run(gcloud, project, "secrets", "describe", name)
    if described.returncode == 0:
        return False
    created = run(
        gcloud,
        project,
        "secrets",
        "create",
        name,
        "--replication-policy=automatic",
    )
    if created.returncode != 0:
        raise RuntimeError(created.stderr.decode("utf-8", errors="replace"))
    return True


def add_version(gcloud: str, project: str, name: str, value: str) -> None:
    result = run(
        gcloud,
        project,
        "secrets",
        "versions",
        "add",
        name,
        "--data-file=-",
        input_bytes=value.encode("utf-8"),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gcloud", required=True)
    parser.add_argument("--project", default="mas-503315")
    parser.add_argument("--discussion-env", default=".env")
    parser.add_argument(
        "--survey-env",
        default="../agent-web-survey/.env.local",
    )
    args = parser.parse_args()

    discussion = dotenv_values(Path(args.discussion_env))
    survey = dotenv_values(Path(args.survey_env))
    openrouter_key = discussion.get("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    service_role_key = (
        discussion.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )
    supabase_url = survey.get("VITE_SUPABASE_URL") or os.getenv("SUPABASE_URL")

    if not openrouter_key:
        raise SystemExit("OPENROUTER_API_KEY를 찾지 못했습니다.")
    if not supabase_url:
        raise SystemExit("VITE_SUPABASE_URL을 찾지 못했습니다.")

    ensure_secret(args.gcloud, args.project, "openrouter-api-key")
    add_version(args.gcloud, args.project, "openrouter-api-key", openrouter_key)
    print("Configured: openrouter-api-key")

    pepper_created = ensure_secret(
        args.gcloud,
        args.project,
        "access-code-pepper",
    )
    if pepper_created:
        add_version(
            args.gcloud,
            args.project,
            "access-code-pepper",
            secrets.token_urlsafe(48),
        )
    print("Configured: access-code-pepper")

    if service_role_key:
        ensure_secret(args.gcloud, args.project, "supabase-service-role-key")
        add_version(
            args.gcloud,
            args.project,
            "supabase-service-role-key",
            service_role_key,
        )
        print("Configured: supabase-service-role-key")
    else:
        print("Missing: SUPABASE_SERVICE_ROLE_KEY")

    print(f"Supabase URL detected: {'yes' if supabase_url else 'no'}")


if __name__ == "__main__":
    main()
