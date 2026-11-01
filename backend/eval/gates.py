"""Small, shared threshold checks for evaluation scripts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateFailure:
    name: str
    actual: float | int | None
    limit: float | int
    kind: str

    def describe(self) -> str:
        if self.actual is None:
            return f"{self.name}: 无可用数据（要求 {self.kind} {self.limit}）"
        if self.kind == "min":
            return f"{self.name}: {self.actual:.4f} < 下限 {self.limit}"
        return f"{self.name}: {self.actual:.4f} > 上限 {self.limit}"


def check_min(
    name: str, actual: float | int | None, minimum: float | int | None
) -> GateFailure | None:
    if minimum is None:
        return None
    if actual is None or actual < minimum:
        return GateFailure(name, actual, minimum, "min")
    return None


def check_max(
    name: str, actual: float | int | None, maximum: float | int | None
) -> GateFailure | None:
    if maximum is None:
        return None
    if actual is None or actual > maximum:
        return GateFailure(name, actual, maximum, "max")
    return None


def enforce(failures: list[GateFailure | None]) -> None:
    failures = [failure for failure in failures if failure is not None]
    if not failures:
        return
    print("评测阈值未通过：")
    for failure in failures:
        print(f"  - {failure.describe()}")
    raise SystemExit(1)
