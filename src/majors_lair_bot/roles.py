"""Which Discord roles make a member protected, worked out from the server's live role list.

Protection used to be configured by typing role names, which broke twice in practice: a
name without the emoji ("Nucleus" for "Nucleus ✅") and a misspelling ("Eternals" for
"Ethernals"). Roles can now be picked, which stores the role's ID and survives renames;
typed names still work for older settings, and near misses are named so a typo is visible.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any


def _normalise(name: str) -> str:
    """Lowercase letters and digits only, so emoji, spaces and punctuation never decide."""
    return re.sub(r"[^0-9a-z]", "", name.lower())


def split_setting(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


@dataclass
class ProtectedRoleMatch:
    # role ID -> role name, for every server role that grants protection
    roles: dict[str, str] = field(default_factory=dict)
    # typed names that match no role exactly, with the server roles they probably meant
    unmatched_names: list[dict[str, Any]] = field(default_factory=list)
    # picked role IDs that no longer exist in the server (the role was deleted)
    missing_ids: list[str] = field(default_factory=list)


def match_protected_roles(
    server_roles: list[dict[str, Any]], wanted_ids: list[str], wanted_names: list[str]
) -> ProtectedRoleMatch:
    match = ProtectedRoleMatch()
    by_id = {str(role.get("id", "")): str(role.get("name", "")) for role in server_roles}
    for role_id in wanted_ids:
        if role_id in by_id:
            match.roles[role_id] = by_id[role_id]
        else:
            match.missing_ids.append(role_id)

    exact = {name.lower() for name in wanted_names}
    for role_id, name in by_id.items():
        if name.lower() in exact:
            match.roles[role_id] = name

    matched_lower = {name.lower() for name in match.roles.values()}
    for wanted in wanted_names:
        if wanted.lower() in matched_lower:
            continue
        target = _normalise(wanted)
        scored: list[tuple[float, str]] = []
        for name in by_id.values():
            candidate = _normalise(name)
            if not target or not candidate:
                continue
            if target == candidate or target in candidate or candidate in target:
                score = 1.0
            else:
                score = difflib.SequenceMatcher(None, target, candidate).ratio()
            if score >= 0.75:
                scored.append((score, name))
        scored.sort(key=lambda item: (-item[0], item[1].lower()))
        match.unmatched_names.append(
            {"configured": wanted, "did_you_mean": [name for _, name in scored[:5]]}
        )
    return match
