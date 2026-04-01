// ─── Constraints (enforce uniqueness + create implicit indexes) ───

CREATE CONSTRAINT skill_name IF NOT EXISTS
  FOR (s:Skill) REQUIRE s.name IS UNIQUE;

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


// ─── Seed data: domain nodes ───

MERGE (d1:Domain {name: "nlp", description: "Natural language processing and LLMs", weight: 1.0})
MERGE (d2:Domain {name: "ml_infrastructure", description: "MLOps, serving, pipelines", weight: 1.0})
MERGE (d3:Domain {name: "computer_vision", description: "Image and video models", weight: 1.0})
MERGE (d4:Domain {name: "data_engineering", description: "Pipelines, storage, retrieval", weight: 1.0})

// ─── Seed data: foundational skill prerequisite chain (example) ───

MERGE (py:Skill {name: "Python", category: "language", level: "foundational"})
MERGE (np:Skill {name: "NumPy", category: "ml_framework", level: "foundational"})
MERGE (pt:Skill {name: "PyTorch", category: "ml_framework", level: "intermediate"})
MERGE (hf:Skill {name: "HuggingFace Transformers", category: "ml_framework", level: "advanced"})
MERGE (lc:Skill {name: "LangChain", category: "tool", level: "intermediate"})
MERGE (es:Skill {name: "Elasticsearch", category: "platform", level: "intermediate"})

MERGE (py)-[:PREREQUISITE_OF {strength: 1.0}]->(np)
MERGE (np)-[:PREREQUISITE_OF {strength: 0.9}]->(pt)
MERGE (pt)-[:PREREQUISITE_OF {strength: 0.8}]->(hf)
MERGE (py)-[:PREREQUISITE_OF {strength: 0.7}]->(lc)

MERGE (pt)-[:BELONGS_TO]->(d1)
MERGE (hf)-[:BELONGS_TO]->(d1)
MERGE (lc)-[:BELONGS_TO]->(d1)
MERGE (es)-[:BELONGS_TO]->(d4)

// MATCH (pytorch:Skill {name: "PyTorch"}),
//       (llmft:Skill {name: "LLM fine-tuning"})
// MERGE (pytorch)-[:PREREQUISITE_OF {strength: 0.8}]->(llmft);

// MATCH (es:Skill {name: "Elasticsearch"}),
//       (vdb:Skill {name: "Vector Databases"})
// MERGE (es)-[:PREREQUISITE_OF {strength: 0.7}]->(vdb);

// MATCH (python:Skill {name: "Python"}),
//       (vdb:Skill {name: "Vector Databases"})
// MERGE (python)-[:PREREQUISITE_OF {strength: 0.6}]->(vdb);

// MATCH (n) RETURN n LIMIT 50

// MATCH (u:User {id: "user_gargi_001"})-[r:HAS_SKILL]->(s:Skill)
// RETURN u.name, s.name, r.proficiency
// ORDER BY r.proficiency DESC

// MATCH (j:JobPosting {id: "job_001"})-[r:REQUIRES_SKILL]->(s:Skill)
// RETURN j.title, s.name, r.importance, r.weight
// ORDER BY r.weight DESC

// MATCH (j:JobPosting {id: "job_001"})-[req:REQUIRES_SKILL]->(s:Skill)
// OPTIONAL MATCH (u:User {id: "user_gargi_001"})-[has:HAS_SKILL]->(s)
// RETURN 
//   s.name AS skill,
//   req.importance AS importance,
//   req.weight AS required_weight,
//   COALESCE(has.proficiency, 0.0) AS your_proficiency,
//   CASE 
//     WHEN has.proficiency IS NULL THEN "missing"
//     WHEN has.proficiency >= req.weight THEN "met"
//     ELSE "partial"
//   END AS status
// ORDER BY req.importance, your_proficiency ASC

