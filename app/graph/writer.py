from app.db.neo4j import neo4j_client
from app.models.schemas import ExtractedJD, ExtractedResume
import uuid

def write_jd(extracted: ExtractedJD, job_id: str):
    with neo4j_client.get_session() as session:
        # Create job posting node
        session.run("""
            MERGE (j:JobPosting {id: $job_id})
            SET j.title = $title,
                j.company = $company,
                j.seniority = $seniority
        """, job_id=job_id, title=extracted.title,
             company=extracted.company, seniority=extracted.seniority)

        # Write skills and relationships
        importance_map = {
            "core": extracted.core_skills,
            "preferred": extracted.preferred_skills,
            "nice_to_have": extracted.nice_to_have_skills
        }
        for importance, skills in importance_map.items():
            for skill in skills:
                session.run("""
                    MERGE (s:Skill {name: $name})
                    SET s.category = $category, s.level = $level
                    WITH s
                    MATCH (j:JobPosting {id: $job_id})
                    MERGE (j)-[:REQUIRES_SKILL {importance: $importance, weight: $weight}]->(s)
                """, name=skill.name, category=skill.category,
                     level=skill.level, job_id=job_id,
                     importance=importance, weight=skill.proficiency)

        # Write domains
        for domain_name in extracted.domains:
            session.run("""
                MERGE (d:Domain {name: $name})
                WITH d
                MATCH (j:JobPosting {id: $job_id})
                MERGE (j)-[:IN_DOMAIN]->(d)
            """, name=domain_name, job_id=job_id)

def write_resume(extracted: ExtractedResume, user_id: str):
    with neo4j_client.get_session() as session:
        # Create user node
        session.run("""
            MERGE (u:User {id: $user_id})
            SET u.name = $name,
                u.target_domain = $target_domain
        """, user_id=user_id, name=extracted.name,
             target_domain=extracted.target_domain)

        # Write skills and relationships
        for skill in extracted.skills:
            session.run("""
                MERGE (s:Skill {name: $name})
                SET s.category = $category, s.level = $level
                WITH s
                MATCH (u:User {id: $user_id})
                MERGE (u)-[:HAS_SKILL {proficiency: $proficiency, source: "resume"}]->(s)
            """, name=skill.name, category=skill.category,
                 level=skill.level, user_id=user_id,
                 proficiency=skill.proficiency)

        # Write target domain
        if extracted.target_domain:
            session.run("""
                MERGE (d:Domain {name: $name})
                WITH d
                MATCH (u:User {id: $user_id})
                MERGE (u)-[:TARGETS]->(d)
            """, name=extracted.target_domain, user_id=user_id)