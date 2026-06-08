"""Flow task handlers package.

Each sub-module exposes a single ``handle(config, ctx, claims) -> dict``
function that implements a specific task kind.  Handlers are *registered*
by ``CoreWiringAgent`` in ``app.flows.registry``; this package does not
perform registration itself.

Sub-modules
-----------
``extract``
    Unpack archive files (zip, tar, tar.gz/tgz, gz) from a storage URI
    and upload extracted members to a destination storage prefix.
"""

from __future__ import annotations
