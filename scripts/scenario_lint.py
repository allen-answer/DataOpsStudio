"""Scenario lint —— CI 友好的纯 Python scenario 静态校验（Phase 12 切片 18）。

不连数据库、不调 LLM —— 只做「能不能跑」的离线体检：

  1. YAML / DSL 校验   —— loader 跑得通（Pydantic `extra='forbid'` 已拦笔误）
  2. generator 冒烟    —— `generate_scenario()` 在内存里真跑一遍，catch 未知
                          dist kind / 坏 range / anomaly 配错列等运行期才炸的 bug
  3. 交叉引用          —— anomaly.table / derives_from / column_overrides.from
                          / compare_task source·target 指向的表都存在
  4. 模板变量          —— workload.sql 里 `{{var}}` 都能在 scenario.variables 找到

任一 scenario 有 error → 退出码 1，让 CI 红。warning 默认不致命，`--strict`
时也算 error。

用法：
    python scripts/scenario_lint.py                     # 扫 config/scenarios/
    python scripts/scenario_lint.py --dir path/to/dir   # 指定目录
    python scripts/scenario_lint.py --strict            # warning 也算失败
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 允许 `python scripts/scenario_lint.py` 直接跑（把仓库根加进 sys.path）
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.scenarios.generator import generate_scenario  # noqa: E402
from app.scenarios.loader import load_scenario  # noqa: E402
from app.scenarios.models import Scenario  # noqa: E402
from app.scenarios.templating import render_template  # noqa: E402


@dataclass
class ScenarioLintResult:
    file: str
    scenario_id: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class LintReport:
    results: list[ScenarioLintResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def warned(self) -> int:
        return sum(1 for r in self.results if r.warnings)


def lint_scenarios(scenarios_dir: Path | str) -> LintReport:
    """扫目录下所有 `*.yml`（含 example），逐个静态体检。纯函数：不 print / 不 exit。"""
    sdir = Path(scenarios_dir)
    report = LintReport()
    if not sdir.is_dir():
        return report
    for yml in sorted(sdir.glob("*.yml")):
        report.results.append(_lint_one(yml))
    return report


def _lint_one(path: Path) -> ScenarioLintResult:
    result = ScenarioLintResult(file=path.name)

    # ── 1. load + DSL 校验 ──────────────────────────────────────────────────
    try:
        scenario = load_scenario(path)
    except Exception as exc:  # FileNotFound / YAMLError / ValidationError / ValueError
        result.errors.append(f"load failed: {exc}")
        return result
    result.scenario_id = scenario.id

    # ── 2. generator 冒烟 ───────────────────────────────────────────────────
    try:
        data = generate_scenario(scenario)
    except Exception as exc:
        result.errors.append(f"generate_scenario raised: {type(exc).__name__}: {exc}")
        data = {}

    # ── 3. 交叉引用 ─────────────────────────────────────────────────────────
    _check_cross_refs(scenario, data, result)

    # ── 4. 模板变量 ─────────────────────────────────────────────────────────
    _check_templates(scenario, result)

    return result


def _check_cross_refs(
    scenario: Scenario, data: dict, result: ScenarioLintResult
) -> None:
    table_names = {t.name for t in scenario.tables}
    by_name = {t.name: t for t in scenario.tables}

    # derives_from / column_overrides.from
    for t in scenario.tables:
        if t.derives_from and t.derives_from not in table_names:
            result.warnings.append(
                f"table '{t.name}' derives_from '{t.derives_from}' —— 该表未定义，"
                f"会生成 0 行")
        if t.column_overrides:
            parent = by_name.get(t.derives_from or "")
            parent_cols = {c.name for c in parent.columns} if parent else set()
            for ov in t.column_overrides:
                if parent and ov.from_ not in parent_cols:
                    result.warnings.append(
                        f"table '{t.name}' column_override from '{ov.from_}' —— "
                        f"父表 '{t.derives_from}' 无此列")

    # anomaly.table / anomaly.column
    for a in scenario.anomalies:
        if a.table not in table_names:
            result.errors.append(
                f"anomaly kind={a.kind} 指向未定义的表 '{a.table}'")
            continue
        if a.column:
            # 用生成出来的行做实际列校验（覆盖 derived 表 rename 后的列名）
            rows = data.get(a.table) or []
            sample_cols = set(rows[0].keys()) if rows else set()
            if sample_cols and a.column not in sample_cols:
                result.warnings.append(
                    f"anomaly kind={a.kind} table='{a.table}' column='{a.column}' "
                    f"—— 该表实际列里没有此列，anomaly 不会生效")

    # compare_task workload source / target
    for wl in scenario.workloads:
        if getattr(wl, "kind", None) != "compare_task":
            continue
        for ref_field in ("source", "target"):
            ref = getattr(wl, ref_field, None)
            if ref and ref not in table_names:
                result.warnings.append(
                    f"compare_task '{getattr(wl, 'name', '')}' {ref_field}='{ref}' "
                    f"—— 该表未在 scenario.tables 定义")


def _check_templates(scenario: Scenario, result: ScenarioLintResult) -> None:
    for wl in scenario.workloads:
        sql = getattr(wl, "sql", None)
        if not isinstance(sql, str) or not sql:
            continue
        rendered = render_template(sql, scenario.variables)
        if rendered.missing:
            result.warnings.append(
                f"workload '{getattr(wl, 'name', '')}' SQL 引用了未定义的模板变量："
                f"{', '.join(rendered.missing)}（scenario.variables 缺这些 key）")


# ─── CLI ────────────────────────────────────────────────────────────────────


def _format_report(report: LintReport, *, strict: bool) -> str:
    lines: list[str] = []
    for r in report.results:
        if r.errors:
            badge = "FAIL"
        elif r.warnings:
            badge = "WARN"
        else:
            badge = "OK  "
        lines.append(f"[{badge}] {r.file}  ({r.scenario_id or '—'})")
        for e in r.errors:
            lines.append(f"        error:   {e}")
        for w in r.warnings:
            lines.append(f"        warning: {w}")
    lines.append("")
    summary = (f"{len(report.results)} scenarios · {report.passed} ok · "
               f"{report.failed} failed · {report.warned} with warnings")
    if strict and report.warned:
        summary += "  (--strict: warnings count as failure)"
    lines.append(summary)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint DataOps scenario YAML files.")
    parser.add_argument(
        "--dir", default=str(_REPO_ROOT / "config" / "scenarios"),
        help="scenario 目录（默认 config/scenarios/）")
    parser.add_argument(
        "--strict", action="store_true",
        help="把 warning 也当失败（退出码 1）")
    args = parser.parse_args(argv)

    sdir = Path(args.dir)
    if not sdir.is_dir():
        print(f"scenario dir not found: {sdir}", file=sys.stderr)
        return 1

    report = lint_scenarios(sdir)
    if not report.results:
        print(f"no *.yml found in {sdir} —— nothing to lint")
        return 0

    print(_format_report(report, strict=args.strict))
    if not report.ok:
        return 1
    if args.strict and report.warned:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
