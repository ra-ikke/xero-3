"""Lightweight dataclasses used across the bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True, slots=True)
class MapData:
    code: str
    xml: str
    map_type: str
    maker: str

    @classmethod
    def from_api_dict(cls, *, code: str, data: Mapping[str, Any]) -> "MapData | None":
        if not data:
            return None
        xml = str(data.get("xml") or "")
        if not xml:
            return None
        return cls(
            code=code,
            xml=xml,
            map_type=str(data.get("type") or ""),
            maker=str(data.get("maker") or ""),
        )


@dataclass(frozen=True, slots=True)
class Category:
    name: str
    description: str
    picture: Optional[str] = None
    emoji: Optional[str] = None
    color: Optional[str] = None
    thread: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Category | None":
        if not data:
            return None
        name = str(data.get("name") or "").strip()
        if not name:
            return None
        description = str(data.get("description") or name).strip() or name
        return cls(
            name=name,
            description=description,
            picture=(str(data.get("picture")).strip() if data.get("picture") else None),
            emoji=(str(data.get("emoji")).strip() if data.get("emoji") else None),
            color=(str(data.get("color")).strip() if data.get("color") else None),
            thread=(str(data.get("thread")).strip() if data.get("thread") else None),
        )
