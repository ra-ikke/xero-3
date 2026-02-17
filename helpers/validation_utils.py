"""Validation helpers (ported from the legacy JavaScript helpers)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import discord


@dataclass(frozen=True)
class MapCodeValidation:
    is_valid: bool
    formatted_code: str


def validate_map_code(map_code: str, *, min_digits: int = 4) -> MapCodeValidation:
    """
    Validates and formats a map code.

    - Ensures it starts with '@'
    - Ensures it contains only digits after '@'
    - Ensures it has at least `min_digits` digits
    """
    code = (map_code or "").strip()
    if not code.startswith("@"):
        code = f"@{code}"
    pattern = re.compile(rf"^@\d{{{min_digits},}}$")
    return MapCodeValidation(is_valid=bool(pattern.match(code)), formatted_code=code)


def has_public_role(member: discord.Member) -> bool:
    """Returns True if the member has a role named 'Public'."""
    return any(role.name == "Public" for role in member.roles)


def has_mapcrew_role(member: discord.Member) -> bool:
    """Returns True if the member has a Mapcrew-like role name."""
    for role in member.roles:
        name = role.name or ""
        normalized = name.replace(" ", "").lower()
        if normalized == "mapcrew":
            return True
    return False


def has_votecrew_role(member: discord.Member) -> bool:
    """Returns True if the member has a Votecrew role."""
    return any((role.name or "") == "Votecrew" for role in member.roles)


def has_trial_mapcrew_role(member: discord.Member) -> bool:
    """Returns True if the member has a Trial Mapcrew role."""
    for role in member.roles:
        name = role.name or ""
        normalized = name.replace(" ", "").lower()
        if normalized == "trialmapcrew":
            return True
    return False


def get_display_name(member: discord.Member) -> str:
    """Returns a display name depending on whether the member is public or not."""
    if has_public_role(member):
        return member.nick or getattr(member, "global_name", None) or member.name
    return "Private Member"

