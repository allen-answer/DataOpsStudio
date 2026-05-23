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

## ✅ Phase 14 落地

- **artifact attestation** —— `release.yml` 接 `actions/attest-build-provenance@v2`,
  给 Windows offline zip 加 SLSA-style 来源证明(release tag 触发时签发,
  workflow_dispatch 不签)。下载者用 `gh attestation verify` 能验证「这个
  zip 真是这个 commit 跑出来的」
- **SBOM(CycloneDX)** —— `ci.yml` 新增 `sbom` job(push 到 main 时跑):
  - backend 走 `cyclonedx-bom`(`cyclonedx-py requirements`)从 requirements.txt
  - frontend 走 `@cyclonedx/cyclonedx-npm` 从 package-lock.json
  - 两份 JSON 上传 artifact 保留 90 天(够审计期)
- **dependency-review-action** —— `ci.yml` 新增 `dependency-review` job(PR
  触发):拦 PR diff 新引入的 high/critical CVE 依赖,避免「补 fix 顺手塞 vulnerable
  lib」。跟 `dependency-audit` job 互补 —— audit 看 requirements.txt 当前的依赖
  状态,dependency-review 看 PR diff 引入了什么
