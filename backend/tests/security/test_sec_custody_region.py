"""Region-residency regression tests for the dedicated custody provider.

``tests/test_custody_dedicated.py`` already covers the GCS region-mismatch
path (``_gcs_ensure_bucket``) and the fail-closed custody gate (403 when the
tier is off) is exhaustively covered by ``tests/security/test_custody_gate.py``.

Gap closed here: the S3 / MinIO path (``_s3_ensure_bucket``) had NO
region-mismatch coverage — this file adds it, mirroring the GCS test exactly,
so the residency guarantee ("when NUBI_LAKE_REGION is set, storage whose
region mismatches is refused") holds for BOTH supported providers, not just
GCS.
"""

from __future__ import annotations

import pytest

from app.lakehouse.dedicated import ManagedLakehouseError, _s3_ensure_bucket


class _FakeClientError(Exception):
    """Minimal botocore.exceptions.ClientError stand-in."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class TestS3RegionMismatch:
    def test_existing_bucket_wrong_region_raises_region_mismatch(self):
        """An existing S3 bucket in a DIFFERENT region than NUBI_LAKE_REGION
        must raise ManagedLakehouseError(region_mismatch, 409) — the residency
        pin is a hard refusal, not a warning."""

        class _FakeS3:
            def get_bucket_location(self, Bucket):
                return {"LocationConstraint": "us-west-2"}

        with pytest.raises(ManagedLakehouseError) as exc_info:
            _s3_ensure_bucket(_FakeS3(), "test-bucket", region="af-south-1", kms_key_id=None)

        err = exc_info.value
        assert err.code == "region_mismatch"
        assert err.status == 409
        assert "af-south-1" in err.message
        assert "us-west-2" in err.message

    def test_existing_bucket_matching_region_does_not_raise(self):
        """Sanity: a matching region is a no-op (must not over-block)."""

        class _FakeS3:
            def get_bucket_location(self, Bucket):
                return {"LocationConstraint": "af-south-1"}

        # Must not raise.
        _s3_ensure_bucket(_FakeS3(), "test-bucket", region="af-south-1", kms_key_id=None)

    def test_no_region_pin_skips_the_check_entirely(self):
        """When NUBI_LAKE_REGION is unset (region=None), any existing bucket's
        region is accepted — no residency guarantee is claimed."""

        class _FakeS3:
            def get_bucket_location(self, Bucket):
                return {"LocationConstraint": "us-west-2"}

        # Must not raise: region=None means no pin was requested.
        _s3_ensure_bucket(_FakeS3(), "test-bucket", region=None, kms_key_id=None)

    def test_us_east_1_default_location_constraint_handled(self):
        """S3's quirk: buckets in us-east-1 report LocationConstraint=None —
        _s3_ensure_bucket must normalise that to 'us-east-1', not crash or
        silently treat it as a match for every other pinned region."""

        class _FakeS3:
            def get_bucket_location(self, Bucket):
                return {"LocationConstraint": None}

        with pytest.raises(ManagedLakehouseError) as exc_info:
            _s3_ensure_bucket(_FakeS3(), "test-bucket", region="af-south-1", kms_key_id=None)
        assert exc_info.value.code == "region_mismatch"

        # And it DOES match when the pin is explicitly us-east-1.
        _s3_ensure_bucket(_FakeS3(), "test-bucket", region="us-east-1", kms_key_id=None)

    def test_bucket_lookup_failure_other_than_missing_raises_lookup_error(self):
        """A non-NoSuchBucket ClientError (e.g. access denied) must surface as
        a distinct, honest error — never silently treated as 'bucket absent,
        proceed to create' (which could mask a permissions misconfiguration)."""
        import app.lakehouse.dedicated as dedicated_mod

        class _FakeS3:
            def get_bucket_location(self, Bucket):
                raise _FakeClientError("AccessDenied")

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "botocore.exceptions.ClientError", _FakeClientError
        ):
            with pytest.raises(ManagedLakehouseError) as exc_info:
                _s3_ensure_bucket(_FakeS3(), "test-bucket", region="af-south-1", kms_key_id=None)
        assert exc_info.value.code == "bucket_lookup_failed"
