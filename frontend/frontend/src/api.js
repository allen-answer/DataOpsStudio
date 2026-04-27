const parseError = async (response) => {
  const type = response.headers.get('content-type') || ''
  if (type.includes('application/json')) {
    const payload = await response.json()
    return payload.detail || JSON.stringify(payload)
  }
  return response.text()
}

export const apiGet = async (url) => {
  const response = await fetch(url)
  if (!response.ok) throw new Error(await parseError(response))
  return response.json()
}

export const apiJson = async (url, method, payload) => {
  const response = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(await parseError(response))
  return response.json()
}

export const apiForm = async (url, formData) => {
  const response = await fetch(url, { method: 'POST', body: formData })
  if (!response.ok) throw new Error(await parseError(response))
  return response.json()
}

export const readFileText = async (file) => file ? file.text() : ''
