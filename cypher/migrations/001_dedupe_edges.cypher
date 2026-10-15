// One-time cleanup for graphs written before the writer matched relationships on identity.
// Older writes MERGEd on relationship properties, which could leave several
// REQUIRES_SKILL / HAS_SKILL / IN_DOMAIN / TARGETS edges between the same pair of nodes.
// Keeps one edge per pair; for REQUIRES_SKILL the highest-importance tier wins,
// for HAS_SKILL the highest proficiency wins. Safe to re-run.

MATCH (j:JobPosting)-[r:REQUIRES_SKILL]->(s:Skill)
WITH j, s, r
ORDER BY CASE r.importance WHEN "core" THEN 0 WHEN "preferred" THEN 1 ELSE 2 END, r.weight DESC
WITH j, s, collect(r) AS rels
WHERE size(rels) > 1
UNWIND rels[1..] AS dup
DELETE dup;

MATCH (u:User)-[r:HAS_SKILL]->(s:Skill)
WITH u, s, r
ORDER BY r.proficiency DESC
WITH u, s, collect(r) AS rels
WHERE size(rels) > 1
UNWIND rels[1..] AS dup
DELETE dup;

MATCH (a)-[r:IN_DOMAIN|TARGETS]->(d:Domain)
WITH a, d, type(r) AS t, collect(r) AS rels
WHERE size(rels) > 1
UNWIND rels[1..] AS dup
DELETE dup;
