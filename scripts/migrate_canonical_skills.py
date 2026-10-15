"""One-time migration: make every Skill node canonical (TDD §4.6).

    python scripts/migrate_canonical_skills.py           # dry run: prints the plan
    python scripts/migrate_canonical_skills.py --apply   # applies it

For each Skill node in the graph:
  * canonical name          → sets s.key / category / level from the ontology
  * alias of a canonical    → moves its HAS_SKILL / REQUIRES_SKILL edges onto the
    skill (e.g. "pytorch")    canonical node (keeping the stronger edge on collision),
                              then deletes the alias node
  * unknown                 → appends the name to each owner's unmapped_skills list,
                              then deletes the node
One transaction per skill, so the script is safe to interrupt and re-run.
Run cypher/constraints.cypher and cypher/seed.cypher afterwards.

Uses the app's normal settings (NEO4J_* from the environment / .env).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.neo4j import neo4j_client  # noqa: E402
from app.graph.ontology import get_ontology  # noqa: E402


def _all_skill_names(tx) -> list[str]:
    return [r["name"] for r in tx.run("MATCH (s:Skill) RETURN s.name AS name ORDER BY name")]


def _set_canonical(tx, name, canonical):
    tx.run("""
        MATCH (s:Skill {name: $name})
        SET s.key = $key, s.category = $category, s.level = $level
    """, name=name, key=canonical.key, category=canonical.category, level=canonical.level)


def _merge_into(tx, old_name, canonical):
    tx.run("""
        MERGE (s:Skill {name: $name})
        SET s.key = $key, s.category = $category, s.level = $level
    """, name=canonical.name, key=canonical.key, category=canonical.category,
         level=canonical.level)

    # user edges: keep the higher proficiency
    tx.run("""
        MATCH (old:Skill {name: $old})<-[r:HAS_SKILL]-(u)
        MATCH (new:Skill {name: $new})
        MERGE (u)-[r2:HAS_SKILL]->(new)
          ON CREATE SET r2 = properties(r)
          ON MATCH SET r2.proficiency =
              CASE WHEN r.proficiency > r2.proficiency THEN r.proficiency ELSE r2.proficiency END
        DELETE r
    """, old=old_name, new=canonical.name)

    # job edges: keep the existing canonical edge, else move the alias edge
    tx.run("""
        MATCH (old:Skill {name: $old})<-[r:REQUIRES_SKILL]-(j)
        MATCH (new:Skill {name: $new})
        MERGE (j)-[r2:REQUIRES_SKILL]->(new)
          ON CREATE SET r2 = properties(r)
        DELETE r
    """, old=old_name, new=canonical.name)

    # ontology edges on alias nodes are dropped: the seed owns PREREQUISITE_OF / BELONGS_TO
    tx.run("MATCH (old:Skill {name: $old}) DETACH DELETE old", old=old_name)


def _drop_unknown(tx, name):
    tx.run("""
        MATCH (s:Skill {name: $name})
        OPTIONAL MATCH (owner)-[:HAS_SKILL|REQUIRES_SKILL]->(s)
        WITH s, collect(DISTINCT owner) AS owners
        FOREACH (o IN owners |
            SET o.unmapped_skills =
                CASE WHEN $name IN coalesce(o.unmapped_skills, [])
                     THEN o.unmapped_skills
                     ELSE coalesce(o.unmapped_skills, []) + $name END)
        DETACH DELETE s
    """, name=name)


def main(apply: bool):
    ontology = get_ontology()
    with neo4j_client.get_session() as session:
        names = session.execute_read(_all_skill_names)

        plan = {"canonical": [], "merge": [], "drop": []}
        for name in names:
            canonical = ontology.resolve(name)
            if canonical is None:
                plan["drop"].append(name)
            elif canonical.name == name:
                plan["canonical"].append(name)
            else:
                plan["merge"].append((name, canonical))

        print(f"{len(plan['canonical'])} canonical, {len(plan['merge'])} to merge, "
              f"{len(plan['drop'])} unknown to move into unmapped_skills")
        for old, canonical in plan["merge"]:
            print(f"  merge  {old!r} -> {canonical.name!r}")
        for name in plan["drop"]:
            print(f"  drop   {name!r}")

        if not apply:
            print("\nDry run only. Re-run with --apply to make these changes.")
            return

        for name in plan["canonical"]:
            session.execute_write(_set_canonical, name, ontology.resolve(name))
        for old, canonical in plan["merge"]:
            session.execute_write(_merge_into, old, canonical)
        for name in plan["drop"]:
            session.execute_write(_drop_unknown, name)
        print("\nDone. Now run cypher/constraints.cypher and cypher/seed.cypher.")
    neo4j_client.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
