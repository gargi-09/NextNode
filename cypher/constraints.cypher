// ─── Constraints (enforce uniqueness + create implicit indexes) ───
// Safe to re-run: every statement uses IF NOT EXISTS.

CREATE CONSTRAINT skill_name IF NOT EXISTS
  FOR (s:Skill) REQUIRE s.name IS UNIQUE;

CREATE CONSTRAINT skill_key IF NOT EXISTS
  FOR (s:Skill) REQUIRE s.key IS UNIQUE;

CREATE CONSTRAINT user_id IF NOT EXISTS
  FOR (u:User) REQUIRE u.id IS UNIQUE;

CREATE CONSTRAINT job_id IF NOT EXISTS
  FOR (j:JobPosting) REQUIRE j.id IS UNIQUE;

CREATE CONSTRAINT domain_name IF NOT EXISTS
  FOR (d:Domain) REQUIRE d.name IS UNIQUE;

CREATE CONSTRAINT cluster_label IF NOT EXISTS
  FOR (c:SkillCluster) REQUIRE c.label IS UNIQUE;

// ─── Extra indexes for common query patterns ───

CREATE INDEX skill_category IF NOT EXISTS
  FOR (s:Skill) ON (s.category);

CREATE INDEX job_seniority IF NOT EXISTS
  FOR (j:JobPosting) ON (j.seniority);

// MATCH (n) RETURN n LIMIT 50

// MATCH (u:User {id: "u_test"}) RETURN u

// MATCH (n)
// WHERE (n:User AND n.id = "u_test")
//    OR (n:JobPosting AND n.id IN ["j_test", "j_dupe", "j_text"])
// DETACH DELETE n

// MATCH (u:User {name: "Temp User"}) DETACH DELETE u