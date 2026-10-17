"""Approved rule registry: the only source of normative references in the output."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent / "registry.json"


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

        return f"{self.act_number}; п. {self.paragraph}"

    def full_citation(self) -> str:
        return f"{self.act_number}, пункт {self.paragraph}: «{self.quoted_text}»"


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
            known = {f.name for f in RuleReference.__dataclass_fields__.values()}  # type: ignore[attr-defined]
            rules.append(RuleReference(**{k: v for k, v in raw.items() if k in known}))
        return cls(rules, data.get("registry_version", ""), data.get("verified_on", ""))

    def get(self, rule_id: str) -> RuleReference | None:
        return self._by_id.get(rule_id)

    def for_event(self, event_type: str) -> RuleReference | None:
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
