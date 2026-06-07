"""MariaDB connector — thin alias over :class:`MySQLConnector`.

MariaDB began as a fork of MySQL and speaks the same client/server wire
protocol, so the same drivers (connectorx, PyMySQL) and the same placeholder
translation apply unchanged.  ``MariaDBConnector`` therefore subclasses
``MySQLConnector`` with no behavioural changes; it exists so that:

* the connector registry can map the ``"mariadb"`` source type to a dedicated
  class (clearer in logs / ``registry.all()`` than reusing ``MySQLConnector``);
* a future MariaDB-only divergence (e.g. ``RETURNING``, sequences, vector
  columns) has an obvious home without touching the MySQL path.

Both types are registered in ``app/connectors/registry.py``.  The DSN scheme is
the same MySQL URI (``mysql://user:pass@host:port/db``); connectorx accepts it
for MariaDB servers as well.
"""

from __future__ import annotations

from app.connectors.mysql import MySQLConnector

SOURCE_TYPE = "mariadb"


class MariaDBConnector(MySQLConnector):
    """MariaDB connector — identical behaviour to :class:`MySQLConnector`.

    See :mod:`app.connectors.mysql` for the full execution contract,
    placeholder translation, and capability flags.
    """

    # No overrides: MariaDB is wire-compatible with MySQL for the SELECT-only
    # query path Nubi uses.  Capabilities, execution, and streaming are
    # inherited verbatim from MySQLConnector.
