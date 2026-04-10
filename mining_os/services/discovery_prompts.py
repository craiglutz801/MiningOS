"""Discovery agent: load and save editable prompts (per mineral or default)."""

from __future__ import annotations

from typing import List

from sqlalchemy import text

from mining_os.db import get_engine


def get_all_prompts() -> List[dict]:
    """Return all rows: mineral_name, system_instruction, user_prompt_template, updated_at."""
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text("SELECT mineral_name, system_instruction, user_prompt_template, updated_at FROM discovery_prompts ORDER BY mineral_name")
        ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("mineral_name") is None:
            d["mineral_name"] = ""
        out.append(d)
    return out


def get_prompt_for_mineral(mineral_name: str) -> dict | None:
    """Get prompt row for this mineral, or default (mineral_name = '') if no row."""
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT mineral_name, system_instruction, user_prompt_template, updated_at FROM discovery_prompts WHERE mineral_name = :name"),
            {"name": mineral_name.strip()},
        ).mappings().first()
        if row:
            return dict(row)
        row = conn.execute(
            text("SELECT mineral_name, system_instruction, user_prompt_template, updated_at FROM discovery_prompts WHERE mineral_name = ''"),
            {},
        ).mappings().first()
    return dict(row) if row else None


def upsert_prompt(mineral_name: str, system_instruction: str, user_prompt_template: str) -> None:
    """Insert or update prompt for this mineral (use '' for default)."""
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO discovery_prompts (mineral_name, system_instruction, user_prompt_template, updated_at)
            VALUES (:mineral_name, :system_instruction, :user_prompt_template, now())
            ON CONFLICT (mineral_name) DO UPDATE SET
              system_instruction = EXCLUDED.system_instruction,
              user_prompt_template = EXCLUDED.user_prompt_template,
              updated_at = now()
            """),
            {
                "mineral_name": mineral_name.strip(),
                "system_instruction": system_instruction,
                "user_prompt_template": user_prompt_template,
            },
        )
