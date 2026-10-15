// Ad-hoc inspection queries (not part of setup). Replace the ids as needed.

// All nodes
MATCH (n) RETURN n;

// A user's skills
MATCH (u:User {id: "user_gargi_001"})-[r:HAS_SKILL]->(s:Skill)
RETURN u.name, s.name, r.proficiency
ORDER BY r.proficiency DESC;

// A job's requirements
MATCH (j:JobPosting {id: "job_001"})-[r:REQUIRES_SKILL]->(s:Skill)
RETURN j.title, s.name, r.importance, r.weight
ORDER BY r.weight DESC;

// Raw gap for a user/job pair
MATCH (j:JobPosting {id: "job_001"})-[req:REQUIRES_SKILL]->(s:Skill)
OPTIONAL MATCH (u:User {id: "user_gargi_001"})-[has:HAS_SKILL]->(s)
RETURN
  s.name AS skill,
  req.importance AS importance,
  req.weight AS required_weight,
  COALESCE(has.proficiency, 0.0) AS your_proficiency,
  CASE
    WHEN has.proficiency IS NULL THEN "missing"
    WHEN has.proficiency >= req.weight THEN "met"
    ELSE "partial"
  END AS status
ORDER BY req.importance, your_proficiency ASC;
