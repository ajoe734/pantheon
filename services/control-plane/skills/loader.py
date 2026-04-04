"""
Skills Loader
Reads skills.yaml and selects the best matching skill for a given intent string.

Business logic is a TODO stub — real intent matching requires LLM or embedding
similarity, deferred until P2-001 Signal JSON Schema is LOCKED.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

import yaml


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str]
    worker: str | None


_SKILLS_YAML = pathlib.Path(__file__).parent / "skills.yaml"


def load_skills() -> list[Skill]:
    with open(_SKILLS_YAML) as f:
        data = yaml.safe_load(f)
    return [
        Skill(
            name=s["name"],
            description=s["description"],
            triggers=s.get("triggers", []),
            worker=s.get("worker"),
        )
        for s in data["skills"]
    ]


def select_skill(intent: str) -> Skill | None:
    """
    TODO: replace naive keyword match with embedding similarity once
    P2-001 Signal JSON Schema is LOCKED and LLM backend is confirmed.
    """
    skills = load_skills()
    intent_lower = intent.lower()
    for skill in skills:
        for trigger in skill.triggers:
            if trigger in intent_lower:
                return skill
    return None
