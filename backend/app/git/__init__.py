"""Git sync package for Nubi — M20-A/B.

Exports
-------
GitSync
    Wraps a local git repository; used to version queries and dashboards.
serialize_resource
    Converts a query or board resource to a list of ``{path, content}`` pairs
    ready for committing.
RemoteAuth
    Backward-compatible shim (M20-A).  New code should import from
    ``app.git.remote`` directly.
GitHubAppAuth
    GitHub App JWT-based push authentication (M20-B).
GitLabTokenAuth
    GitLab personal/project token push authentication (M20-B).
NullRemote
    No-op remote — default when no provider is configured (M20-B).
make_remote_auth
    Factory that selects the right provider from a ``Settings`` config (M20-B).
"""

from app.git.sync import GitSync, RemoteAuth, serialize_resource
from app.git.remote import (
    GitHubAppAuth,
    GitLabTokenAuth,
    NullRemote,
    make_remote_auth,
)

__all__ = [
    "GitSync",
    "RemoteAuth",
    "serialize_resource",
    "GitHubAppAuth",
    "GitLabTokenAuth",
    "NullRemote",
    "make_remote_auth",
]
