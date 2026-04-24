/**
 * Authenticated fetch for admin API routes.
 * Mirrors static/manage.html apiRequest: Bearer admin session token, 401 → login.
 */
export async function adminFetch(
  path: string,
  token: string | null,
  init?: RequestInit
): Promise<Response | null> {
  if (!token) {
    localStorage.removeItem("adminToken")
    window.location.href = "/login"
    return null
  }

  const headers = new Headers(init?.headers)
  const body = init?.body
  if (body !== undefined && typeof body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }
  headers.set("Authorization", `Bearer ${token}`)

  const res = await fetch(path, { ...init, headers })

  if (res.status === 401) {
    localStorage.removeItem("adminToken")
    window.location.href = "/login"
    return null
  }

  return res
}

export async function adminJson<T>(
  path: string,
  token: string | null,
  init?: RequestInit
): Promise<{ ok: boolean; status: number; data: T | null }> {
  const res = await adminFetch(path, token, init)
  if (!res) return { ok: false, status: 401, data: null }
  let data: T | null = null
  try {
    const text = await res.text()
    if (text) data = JSON.parse(text) as T
  } catch {
    data = null
  }
  return { ok: res.ok, status: res.status, data }
}
