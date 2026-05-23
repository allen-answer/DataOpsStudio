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

const TOKEN_KEY = 'dataops.token'

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
