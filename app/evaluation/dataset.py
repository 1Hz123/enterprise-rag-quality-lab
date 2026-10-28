"""Fixed evaluation dataset loading and validation."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Behavior = Literal["answer", "refuse", "resist_injection"]


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^q\d{3}$")
    question: str = Field(min_length=1, max_length=2000)
    category: str = Field(min_length=1, max_length=50)
    behavior: Behavior = "answer"
    as_of: date | None = Field(
        default=None,
        description="Policy date for this case; keeps regressions reproducible.",
    )
    expected_sources: list[str] = Field(default_factory=list)
    expected_keywords: list[str] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)
    reference_answer: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_case(self) -> EvalCase:
        if self.behavior == "answer" and not self.expected_sources:
            raise ValueError("answer cases require at least one expected source")
        if self.behavior == "answer" and not self.expected_keywords:
            raise ValueError("answer cases require at least one expected keyword")
        if self.behavior in {"refuse", "resist_injection"} and self.expected_sources:
            raise ValueError("refusal cases must not declare expected sources")
        return self


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    name: str
    path: Path
    checksum: str
    cases: list[EvalCase]

    @property
    def categories(self) -> dict[str, int]:
        return dict(sorted(Counter(case.category for case in self.cases).items()))

    def preview(self, limit: int = 100) -> list[dict[str, Any]]:
        return [case.model_dump() for case in self.cases[:limit]]


def load_dataset(path: str | Path) -> EvaluationDataset:
    dataset_path = Path(path)
    if not dataset_path.is_file():
        raise FileNotFoundError(f"evaluation dataset not found: {dataset_path}")

    raw = dataset_path.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    cases: list[EvalCase] = []
    seen_ids: set[str] = set()

    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on dataset line {line_number}: {exc}") from exc
        try:
            case = EvalCase.model_validate(payload)
        except Exception as exc:
            raise ValueError(f"invalid case on dataset line {line_number}: {exc}") from exc
        if case.id in seen_ids:
            raise ValueError(f"duplicate case id: {case.id}")
        seen_ids.add(case.id)
        cases.append(case)

    if not cases:
        raise ValueError("evaluation dataset is empty")

    return EvaluationDataset(
        name=dataset_path.stem,
        path=dataset_path,
        checksum=checksum,
        cases=cases,
    )
