"""Approved rule registry: the only source of normative references in the output."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent / "registry.json"

TAXONOMY = {
    "LEAVE_DURING_SERVICE": ("Уход с рабочего места во время обслуживания клиента", "KZ-NB231-P22"),
    "LEAVE_VALUABLES": ("Уход с рабочего места с оставлением ценностей на столе", "KZ-NB231-P20-2"),
    "LEAVE_SCREEN_UNLOCKED": ("Уход с рабочего места без блокировки компьютера", "KZ-ARDFM53-P71-9"),
    "SAFE_OPEN": ("Сейф оставлен открытым без присмотра", "KZ-NB231-P20-2"),
    "PERSONAL_ITEMS": ("Личные вещи, еда или напитки на рабочем месте кассира", "KZ-NB231-P12-1"),
    "PHONE_USE": ("Использование мобильного телефона на рабочем месте", "KZ-NB231-P12-2"),
    "OUTSIDER": ("Посторонний в кассовом помещении", "KZ-NB14-P16-1"),
    "DOOR_OPEN": ("Дверь кассы оставлена открытой", "KZ-NB14-P16-1"),
    "MANUAL_COUNT": ("Пересчёт наличных вручную без счётчика банкнот", "KZ-NB231-P41"),
}


@dataclass
class RuleReference:
    rule_id: str
    event_type: str
    event_label: str
    act_name: str
    act_number: str
    source_url: str
    paragraph: str
    quoted_text: str
    observable_predicate: str
    required_context: str
    cannot_infer: list[str] = field(default_factory=list)
    status: str = "draft"
    revision_note: str = ""
    reviewer: str = ""
    approval_note: str = ""

    @property
    def approved(self) -> bool:
        return self.status == "approved"

    def short_citation(self) -> str:
        """Human readable citation used in the JSON ``description`` field."""

        return f"{self.act_number}; {self.paragraph}"

    def full_citation(self) -> str:
        return f"{self.act_number}, {self.paragraph}: «{self.quoted_text}» ({self.source_url})"


class RuleRegistry:
    """Registry of normative references with an explicit approval gate."""

    def __init__(self, rules: list[RuleReference], version: str = "", verified_on: str = ""):
        self.rules = rules
        self.version = version
        self.verified_on = verified_on
        self._by_id = {r.rule_id: r for r in rules}
        self._by_event = {r.event_type: r for r in rules}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "RuleRegistry":
        data = json.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
        rules = []
        for raw in data.get("rules", []):
            if "acts" in data:
                act = data["acts"][raw["act"]]
                raw = dict(raw, **act, event_type="", event_label=raw["applies_to"][0],
                           quoted_text=raw["quote"], observable_predicate=raw["observable"],
                           required_context="Видимые факты; требуется проверка человеком",
                           cannot_infer=[raw["cannot_infer"]], status=data.get("status", "draft"),
                           reviewer=data.get("provenance", ""))
            known = {f.name for f in RuleReference.__dataclass_fields__.values()}  # type: ignore[attr-defined]
            rules.append(RuleReference(**{k: v for k, v in raw.items() if k in known}))
        return cls(rules, data.get("registry_version", ""), data.get("verified_on", ""))

    def get(self, rule_id: str) -> RuleReference | None:
        return self._by_id.get(rule_id)

    def for_event(self, event_type: str) -> RuleReference | None:
        if event_type in TAXONOMY:
            label, rule_id = TAXONOMY[event_type]
            rule = self.get(rule_id)
            return replace(rule, event_type=event_type, event_label=label) if rule and rule.approved else None
        rule = self._by_event.get(event_type)
        return rule if rule and rule.approved else None

    def approved_rules(self) -> list[RuleReference]:
        return [r for r in self.rules if r.approved]

    def as_public_list(self) -> list[dict[str, Any]]:
        """Documentation payload: every rule with its verification status."""

        return [
            {
                "rule_id": r.rule_id,
                "event_type": r.event_type,
                "event_codes": [code for code, (_, rule_id) in TAXONOMY.items() if rule_id == r.rule_id],
                "event_label": r.event_label,
                "act_number": r.act_number,
                "act_name": r.act_name,
                "source_url": r.source_url,
                "paragraph": r.paragraph,
                "quoted_text": r.quoted_text,
                "revision_note": r.revision_note,
                "observable_predicate": r.observable_predicate,
                "cannot_infer": r.cannot_infer,
                "status": r.status,
                "reviewer": r.reviewer,
            }
            for r in self.rules
        ]
