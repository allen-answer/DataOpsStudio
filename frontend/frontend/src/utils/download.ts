/**
 * 结果文件下载 helper —— 跟「配置导出」同一个解法：
 * 不能用 <a href="/results/x.json">，浏览器导航不带 Authorization 头，端点
 * 强制鉴权必 401。改 button + fetch + Bearer + blob → 临时 <a download> 触发。
 *
 * 用法：
 *   <button @click="downloadResultFile(item.excel_filename)">Excel</button>
 *
 * 适配 /results/<relative_path>。带子目录的 parquet 形态（如
 * `<run_id>/export.xlsx`）直接传完整 relative_path 即可。
 */

import { apiJson } from '../api'

const TOKEN_KEY = 'dataops.token'

interface SignedDownloadInfo {
  token: string
  download_url: string
  expires_in: number
  relative_path: string
}


/**
 * compare run 走签名 token 下载 —— 比 downloadResultFile（直链 /results/*）
 * 更紧：POST /api/runs/{run_id}/downloads 拿到 5 分钟有效 token，再 GET。
 *
 * 只对 **compare run** 有效（端点会反查 task_id → project 权限）。
 * 血缘脚本 / workflow artifacts 等非 compare 资源继续用 downloadResultFile。
 */
export async function downloadSignedRunFile(
  runId: unknown,
  kind: 'result' | 'excel',
): Promise<void> {
  const rid = typeof runId === 'string' ? runId.trim() : ''
  if (!rid) {
    window.alert('run_id 为空')
    return
  }
  let info: SignedDownloadInfo
  try {
    info = await apiJson<SignedDownloadInfo>(`/api/runs/${rid}/downloads`, 'POST', { kind })
  } catch (err) {
    const msg = String((err as { message?: string } | null)?.message || err)
    window.alert(`签发下载链接失败：${msg}`)
    return
  }
  const token = localStorage.getItem(TOKEN_KEY) || ''
  let resp: Response
  try {
    resp = await fetch(info.download_url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
  } catch (err) {
    window.alert(`下载失败：${(err as { message?: string } | null)?.message || err}`)
    return
  }
  if (!resp.ok) {
    const msg = resp.status === 401 ? '登录已失效，请重新登录'
              : resp.status === 403 ? '无权下载该结果'
              : resp.status === 404 ? '文件不存在或已删除'
              : `下载失败（HTTP ${resp.status}）`
    window.alert(msg)
    return
  }
  const blob = await resp.blob()
  const cd = resp.headers.get('Content-Disposition') || ''
  const match = /filename\*?=["']?(?:UTF-8'')?([^"';]+)/i.exec(cd)
  const fallback = info.relative_path.split('/').pop() || `${rid}.${kind === 'excel' ? 'xlsx' : 'json'}`
  const filename = match ? decodeURIComponent(match[1]) : fallback
  const objUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objUrl)
}


export async function downloadResultFile(relativePath: unknown): Promise<void> {
  // 接 unknown：调用方常是 store 里类型为 unknown / optional 的字段，
  // 内部 narrow 一次，避免每个 caller 都得 cast。
  const rel = typeof relativePath === 'string' ? relativePath.trim() : ''
  if (!rel) {
    window.alert('文件路径为空')
    return
  }
  const url = `/results/${rel}`
  const token = localStorage.getItem(TOKEN_KEY) || ''
  let resp: Response
  try {
    resp = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
  } catch (err) {
    window.alert(`下载失败：${(err as { message?: string } | null)?.message || err}`)
    return
  }
  if (!resp.ok) {
    const msg = resp.status === 401 ? '登录已失效，请重新登录'
              : resp.status === 403 ? '无权下载该文件'
              : resp.status === 404 ? '文件不存在或已删除'
              : `下载失败（HTTP ${resp.status}）`
    window.alert(msg)
    return
  }
  const blob = await resp.blob()
  const cd = resp.headers.get('Content-Disposition') || ''
  const match = /filename\*?=["']?(?:UTF-8'')?([^"';]+)/i.exec(cd)
  const fallback = rel.split('/').pop() || 'download'
  const filename = match ? decodeURIComponent(match[1]) : fallback
  const objUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objUrl)
}
