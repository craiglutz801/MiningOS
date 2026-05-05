"""Discovery agent: load and save editable prompts (per mineral or default)."""

from __future__ import annotations

from typing import List

from sqlalchemy import text

from mining_os.db import get_engine
from mining_os.services.auth import current_account_id


def _effective_account_id(account_id: int | None = None) -> int:
    return int(account_id or current_account_id())


def get_all_prompts(account_id: int | None = None) -> List[dict]:
    """Return all rows: mineral_name, system_instruction, user_prompt_template, updated_at."""
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT mineral_name, system_instruction, user_prompt_template, updated_at "
                "FROM discovery_prompts WHERE account_id = :account_id ORDER BY mineral_name"
            ),
            {"account_id": account_id},
        ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("mineral_name") is None:
            d["mineral_name"] = ""
        out.append(d)
    return out


def get_prompt_for_mineral(mineral_name: str, account_id: int | None = None) -> dict | None:
    """Get prompt row for this mineral, or default (mineral_name = '') if no row."""
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text(
                "SELECT mineral_name, system_instruction, user_prompt_template, updated_at "
                "FROM discovery_prompts WHERE account_id = :account_id AND mineral_name = :name"
            ),
            {"account_id": account_id, "name": mineral_name.strip()},
        ).mappings().first()
        if row:
            return dict(row)
        row = conn.execute(
            text(
                "SELECT mineral_name, system_instruction, user_prompt_template, updated_at "
                "FROM discovery_prompts WHERE account_id = :account_id AND mineral_name = ''"
            ),
            {"account_id": account_id},
        ).mappings().first()
    return dict(row) if row else None


def upsert_prompt(mineral_name: str, system_instruction: str, user_prompt_template: str, account_id: int | None = None) -> None:
    """Insert or update prompt for this mineral (use '' for default)."""
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO discovery_prompts (account_id, mineral_name, system_instruction, user_prompt_template, updated_at)
            VALUES (:account_id, :mineral_name, :system_instruction, :user_prompt_template, now())
            ON CONFLICT (account_id, mineral_name) DO UPDATE SET
              system_instruction = EXCLUDED.system_instruction,
              user_prompt_template = EXCLUDED.user_prompt_template,
              updated_at = now()
            """),
            {
                "account_id": account_id,
                "mineral_name": mineral_name.strip(),
                "system_instruction": system_instruction,
                "user_prompt_template": user_prompt_template,
            },
        )
