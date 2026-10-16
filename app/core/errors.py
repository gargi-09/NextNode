class NotFoundError(Exception):
    """A requested User or JobPosting does not exist in the graph."""


class ConflictError(Exception):
    """A User or JobPosting with the requested id already exists."""


class UnprocessableError(Exception):
    """The request is well-formed but can't be answered (e.g. a role with no known skills)."""
