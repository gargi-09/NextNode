"""Canonical skill ontology — the single source of truth for Skill and Domain nodes.

Loaded from ontology/skills.json (version-controlled). Resolution is a pure,
deterministic lookup: exact canonical key, then alias key, else unresolved.
There is deliberately no fuzzy matching — "Java" must never become "JavaScript".
"""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "ontology" / "skills.json"

_SEPARATORS = re.compile(r"[-_/]")
_DISALLOWED = re.compile(r"[^a-z0-9 +#.]")
_SPACES     = re.compile(r"\s+")


def canonical_key(text: str) -> str:
    """NFKC → casefold → separators to spaces → keep [a-z0-9 +#.] → collapse spaces."""
    s = unicodedata.normalize("NFKC", text).casefold()
    s = _SEPARATORS.sub(" ", s)
    s = _DISALLOWED.sub("", s)
    s = _SPACES.sub(" ", s).strip()
    return s.rstrip(".").strip()


@dataclass(frozen=True)
class CanonicalSkill:
    key: str
    name: str
    category: str
    level: str
    domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class Prerequisite:
    source: str      # canonical name of the prerequisite
    target: str      # canonical name of the skill it unlocks
    strength: float


@dataclass
class Ontology:
    version: int
    skills: dict[str, CanonicalSkill]                 # key → skill
    aliases: dict[str, str]                           # alias key → canonical key
    domains: dict[str, str]                           # domain key → description
    prerequisites: list[Prerequisite] = field(default_factory=list)

    def resolve(self, name: str | None) -> CanonicalSkill | None:
        if not name:
            return None
        key = canonical_key(name)
        if key in self.skills:
            return self.skills[key]
        target = self.aliases.get(key)
        return self.skills[target] if target else None

    def resolve_domain(self, name: str | None) -> str | None:
        if not name:
            return None
        key = canonical_key(name).replace(" ", "_")
        return key if key in self.domains else None

    @property
    def skill_names(self) -> list[str]:
        return sorted(s.name for s in self.skills.values())

    def resolve_names(self, names: list[str]) -> dict:
        """Resolution report for raw names: `{resolved: [...], unmapped: [...]}`.
        Shared by GET /ontology/resolve and the MCP `resolve_skills` tool."""
        resolved, unmapped = [], []
        for name in (n.strip() for n in names):
            if not name:
                continue
            skill = self.resolve(name)
            if skill:
                resolved.append({"input": name, "canonical": skill.name, "key": skill.key,
                                 "category": skill.category,
                                 "via": "name" if canonical_key(name) == skill.key else "alias"})
            elif name not in unmapped:
                unmapped.append(name)
        return {"resolved": resolved, "unmapped": unmapped}


def load_ontology(path: Path = ONTOLOGY_PATH) -> Ontology:
    raw = json.loads(path.read_text(encoding="utf-8"))

    domains = {d["key"]: d.get("description", "") for d in raw["domains"]}
    skills: dict[str, CanonicalSkill] = {}
    names: dict[str, str] = {}  # canonical name → key

    for s in raw["skills"]:
        key = canonical_key(s["name"])
        if key in skills:
            raise ValueError(f"Duplicate skill key '{key}' for '{s['name']}'")
        unknown = set(s.get("domains", [])) - domains.keys()
        if unknown:
            raise ValueError(f"Skill '{s['name']}' references unknown domains {unknown}")
        skills[key] = CanonicalSkill(
            key=key, name=s["name"], category=s["category"], level=s["level"],
            domains=tuple(s.get("domains", [])),
        )
        names[s["name"]] = key

    aliases: dict[str, str] = {}
    for s in raw["skills"]:
        key = names[s["name"]]
        for alias in s.get("aliases", []):
            alias_key = canonical_key(alias)
            if alias_key == key:
                continue
            if alias_key in skills or aliases.get(alias_key, key) != key:
                raise ValueError(f"Alias '{alias}' of '{s['name']}' collides with another skill")
            aliases[alias_key] = key

    prerequisites: list[Prerequisite] = []
    for s in raw["skills"]:
        for p in s.get("prerequisites", []):
            if p["skill"] not in names:
                raise ValueError(f"'{s['name']}' lists unknown prerequisite '{p['skill']}'")
            prerequisites.append(Prerequisite(p["skill"], s["name"], p["strength"]))

    _check_acyclic(prerequisites)
    return Ontology(raw["version"], skills, aliases, domains, prerequisites)


def _check_acyclic(prerequisites: list[Prerequisite]):
    graph: dict[str, list[str]] = {}
    for p in prerequisites:
        graph.setdefault(p.source, []).append(p.target)

    visiting, done = set(), set()

    def visit(node, path):
        if node in done:
            return
        if node in visiting:
            raise ValueError(f"Prerequisite cycle: {' -> '.join(path + [node])}")
        visiting.add(node)
        for nxt in graph.get(node, []):
            visit(nxt, path + [node])
        visiting.discard(node)
        done.add(node)

    for node in list(graph):
        visit(node, [])


@lru_cache(maxsize=1)
def get_ontology() -> Ontology:
    """Loaded once per process (once per Lambda container) — zero graph reads."""
    return load_ontology()
