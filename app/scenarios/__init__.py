"""Scenario sandbox package (Phase 12 候选).

把 demo / 回归 / 性能基线用的 fixture 数据从硬编码 SQL 抽到声明式 scenario：
一份 YAML 描述 schema + 缺陷 + 工作负载，generator 把它落到 demo 数据库，
feature 模块（compare / lineage / slow-sql / workflow）从同一份数据起。

当前阶段：DSL 骨架（models + loader）。generator / materializer / ai_filler
留给下一切片。
"""
