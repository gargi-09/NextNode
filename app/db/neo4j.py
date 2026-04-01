from neo4j import GraphDatabase
from app.core.config import settings

class Neo4jClient:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password)
        )

    def close(self):
        self.driver.close()

    def get_session(self):
        return self.driver.session()

neo4j_client = Neo4jClient()