# CI 安全基线

安全加固方案 CI 项。仓库已有 `secret-scan`（gitleaks）job —— 本切片补依赖
供应链侧。

## 已落地（仓库内文件）

### Dependabot 版本更新 —— `.github/dependabot.yml`

三个生态每周扫一次，各自 `groups` 合成一个 PR 防刷屏：

- `pip`（`/` 的 `requirements.txt`）—— Python 后端依赖
- `npm`（`/frontend/frontend`）—— 前端依赖
- `github-actions`（`/`）—— workflow 里 pin 的 action 版本

### 依赖 CVE 审计 —— `ci.yml` 的 `dependency-audit` job

- `pip-audit -r requirements.txt` —— Python 依赖查 PyPI Advisory DB
- `npm audit --audit-level=high` —— 前端依赖查 high / critical

**当前是 advisory**（job 设 `continue-on-error: true`）：命中漏洞会在 PR 检查
里显示红叉，但**不阻塞合并**。依赖整治稳定后，去掉 `continue-on-error` 那行，
critical CVE 就真正卡合并 —— 「先 fail on critical 再收紧」的推进节奏。

## 需在 GitHub 仓库设置里开启（不是文件能配的）

以下要仓库 admin 在 GitHub 网页 Settings → Code security 里开：

- **Dependabot alerts** —— 公开仓库默认开；私有仓库需手动开。开了才会对
  `dependabot.yml` 之外的已知漏洞推告警。
- **Dependabot security updates** —— 自动为有漏洞的依赖开修复 PR。
- **CodeQL code scanning（default setup）** —— 一键开启，扫 Python /
  JavaScript-TypeScript / Actions。没用 advanced-setup workflow 文件，是因为
  私有仓库无 GitHub Advanced Security 时那种 workflow 会让 CI 变红；default
  setup 走设置开关更稳。

## 未覆盖（后续）

- artifact attestation（给 release 产物加来源证明）—— 接 `release.yml`。
- SBOM（CycloneDX / SPDX）生成。
- `dependency-review-action`（PR 上拦新引入的漏洞依赖）。
