from __future__ import annotations

from typing import Any

import pytest
from gspread.utils import a1_to_rowcol

from majors_lair_bot.models import ActionType, EngagementAction, ReconcileScope
from majors_lair_bot.sheets import GoogleSheetRepository, LinkConflictError


class FakeWorksheet:
    def __init__(self, title: str, values: list[list[Any]] | None = None) -> None:
        self.title = title
        self.values = [list(row) for row in (values or [])]

    def row_values(self, row: int) -> list[Any]:
        return list(self.values[row - 1]) if len(self.values) >= row else []

    def get_all_values(self) -> list[list[Any]]:
        return [list(row) for row in self.values]

    def update(self, *, range_name: str, values: list[list[Any]], value_input_option: str) -> None:
        assert value_input_option == "RAW"
        if range_name == "A1" and len(values) == 1 and self.values:
            self.values[0] = list(values[0])
        else:
            self.values = [list(row) for row in values]

    def clear(self) -> None:
        self.values = []

    def append_row(self, row: list[Any], *, value_input_option: str) -> None:
        self.values.append(list(row))

    def append_rows(self, rows: list[list[Any]], *, value_input_option: str) -> None:
        self.values.extend(list(row) for row in rows)

    def batch_update(
        self,
        updates: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> None:
        for update in updates:
            start = str(update["range"]).split(":", maxsplit=1)[0]
            start_row, start_column = a1_to_rowcol(start)
            for row_offset, values in enumerate(update["values"]):
                target_row = start_row + row_offset
                while len(self.values) < target_row:
                    self.values.append([])
                while len(self.values[target_row - 1]) < start_column:
                    self.values[target_row - 1].append("")
                self.values[target_row - 1][start_column - 1] = values[0]


class FakeSpreadsheet:
    def __init__(self) -> None:
        self.tabs: dict[str, FakeWorksheet] = {
            "Users": FakeWorksheet(
                "Users",
                [["discord_user_id", "custom_column"], ["1", "preserve-me"]],
            )
        }

    def worksheet(self, title: str) -> FakeWorksheet:
        if title not in self.tabs:
            from gspread.exceptions import WorksheetNotFound

            raise WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, *, title: str, rows: int, cols: int) -> FakeWorksheet:
        worksheet = FakeWorksheet(title)
        self.tabs[title] = worksheet
        return worksheet


@pytest.fixture
def repository() -> GoogleSheetRepository:
    repo = GoogleSheetRepository(
        sheet_id="fake", credentials_file=None, credentials_info={"fake": True}
    )
    repo._spreadsheet = FakeSpreadsheet()  # type: ignore[assignment]
    repo._ensure_schema_sync()
    return repo


def test_schema_and_linking_preserve_existing_user_columns(
    repository: GoogleSheetRepository,
) -> None:
    repository._link_user_sync("1", "member", "Alice", "x-1")
    headers, rows = repository._read_table("Users")
    assert "custom_column" in headers
    assert rows[0]["custom_column"] == "preserve-me"
    assert rows[0]["twitter_handle"] == "alice"


def test_duplicate_active_x_account_is_rejected(repository: GoogleSheetRepository) -> None:
    repository._link_user_sync("1", "member", "alice", "x-1")
    with pytest.raises(LinkConflictError):
        repository._link_user_sync("2", "other", "newhandle", "x-1")


def test_complete_scope_deactivates_missing_action(repository: GoogleSheetRepository) -> None:
    action = EngagementAction(
        action_key="cycle:reply:1",
        cycle_id="cycle",
        discord_user_id="1",
        twitter_user_id="x-1",
        twitter_handle="alice",
        action_type=ActionType.REPLY,
        target_handle="m_m3l",
        source_post_id="source",
        action_tweet_id="reply",
        action_url="https://x.com/alice/status/reply",
        text="A useful reply",
        normalized_text="a useful reply",
        content_hash="hash",
        has_media=False,
        occurred_at="2026-08-01T10:00:00Z",
    )
    repository._reconcile_actions_sync("cycle", [action], [])
    repository._reconcile_actions_sync(
        "cycle",
        [],
        [
            ReconcileScope(
                action_type=ActionType.REPLY,
                target_handle="m_m3l",
                source_post_id="source",
                since_iso="2026-08-01T00:00:00Z",
                until_iso="2026-08-02T00:00:00Z",
                complete=True,
            )
        ],
    )
    stored = repository._list_actions_sync("cycle", True)
    assert len(stored) == 1
    assert stored[0].active is False


def test_incomplete_scope_does_not_remove_points(repository: GoogleSheetRepository) -> None:
    action = EngagementAction(
        action_key="cycle:retweet:1",
        cycle_id="cycle",
        discord_user_id="1",
        twitter_user_id="x-1",
        twitter_handle="alice",
        action_type=ActionType.RETWEET,
        target_handle="m_m3l",
        source_post_id="source",
        action_tweet_id="",
        action_url="https://x.com/m_m3l/status/source",
        text="",
        normalized_text="",
        content_hash="",
        has_media=False,
        occurred_at="2026-08-01T10:00:00Z",
    )
    repository._reconcile_actions_sync("cycle", [action], [])
    changed = repository._reconcile_actions_sync(
        "cycle",
        [],
        [
            ReconcileScope(
                action_type=ActionType.RETWEET,
                target_handle="m_m3l",
                source_post_id="source",
                complete=False,
            )
        ],
    )
    assert changed == 0
    assert repository._list_actions_sync("cycle", True)[0].active is True
