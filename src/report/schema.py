"""Pydantic report models and JSON serializer."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Issue(BaseModel):
    issue: str
    severity: Severity
    evidence_from_plan: str
    suggested_rewrite: str | None = None
    expected_impact: str
    verified: bool = False


class ValidatedRewrite(BaseModel):
    candidate_sql: str
    rationale: str
    targets_finding_ids: list[str]
    scan_bytes_delta: int | None = None
    join_strategy_changes: list[str] = Field(default_factory=list)
    verdict: str = "UNKNOWN"
    verified: bool = False


class ReviewReport(BaseModel):
    query_hash: str
    issues: list[Issue] = Field(default_factory=list)
    validated_rewrites: list[ValidatedRewrite] = Field(default_factory=list)
    pii_flags: list[str] = Field(default_factory=list)
    metadata_coverage: float = Field(
        ge=0.0, le=1.0, description="Fraction of referenced columns found in metadata"
    )

    @model_validator(mode="before")
    @classmethod
    def compute_hash(cls, values: dict[str, Any]) -> dict[str, Any]:
        if "query_hash" not in values or not values["query_hash"]:
            sql = values.get("raw_sql", "")
            values["query_hash"] = hashlib.sha256(sql.encode()).hexdigest()[:16]
        return values

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.model_dump(), indent=indent, default=str)


# ---------------------------------------------------------------------------
# Intermediate structures used during rewrite proposal
# ---------------------------------------------------------------------------


class CandidateRewrite(BaseModel):
    candidate_sql: str
    rationale: str
    targets_finding_ids: list[str]


class CandidateRewriteList(BaseModel):
    rewrites: list[CandidateRewrite]
