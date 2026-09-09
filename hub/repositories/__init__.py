"""Data-access layer. Routes and the WS ingest handler call these, never the ORM directly."""
from hub.repositories import alerts, groups, licenses, nodes, tenants

__all__ = ["alerts", "groups", "licenses", "nodes", "tenants"]
