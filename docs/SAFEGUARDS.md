# Operations safeguards

## Clearing all targets

**If the user ever asks to clear, delete, or remove all targets from the system:**

1. **Do not run the delete immediately.** Require explicit confirmation.
2. Tell the user: *"This will permanently delete all targets. This cannot be undone. Reply with 'Yes, delete all targets' (or 'I am sure, clear all targets') to confirm."*
3. Only run `DELETE FROM areas_of_focus` (e.g. via `mining_os.db` and `sqlalchemy.text`) after the user has replied with that explicit confirmation.

Related tables (e.g. `focus_reports`) use `ON DELETE CASCADE`, so they are cleared automatically.
