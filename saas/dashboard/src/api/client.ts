/**
 * API client for the Klemma backend.
 * Handles JWT token management, refresh, and request formatting.
 */

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

/** Auth endpoints where 401 means bad credentials, not expired token. */
const AUTH_PATHS = ['/auth/login', '/auth/register']

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

let refreshPromise: Promise<boolean> | null = null

async function tryRefreshToken(): Promise<boolean> {
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return false

  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
    if (!res.ok) return false

    const data = await res.json()
    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    return true
  } catch {
    return false
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('access_token')
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  let res = await fetch(`${API_BASE}${path}`, { ...options, headers })

  // On 401 for non-auth endpoints: try refresh, then retry once
  if (res.status === 401 && !AUTH_PATHS.includes(path)) {
    // Deduplicate concurrent refresh attempts
    if (!refreshPromise) {
      refreshPromise = tryRefreshToken().finally(() => { refreshPromise = null })
    }
    const refreshed = await refreshPromise

    if (refreshed) {
      // Retry with new token
      const newToken = localStorage.getItem('access_token')!
      headers['Authorization'] = `Bearer ${newToken}`
      res = await fetch(`${API_BASE}${path}`, { ...options, headers })
    }

    // Still 401 after refresh attempt — session expired
    if (res.status === 401) {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      window.location.href = '/login'
      throw new ApiError(401, 'Session expired')
    }
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(res.status, body.detail || res.statusText)
  }

  if (res.status === 204) return {} as T
  return res.json()
}

// Auth
export const auth = {
  register: (email: string, password: string, name = '') =>
    request<{ access_token: string; refresh_token: string }>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password, name }),
    }),

  login: (email: string, password: string) =>
    request<{ access_token: string; refresh_token: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  me: () => request<{ user_id: string; email: string; name: string }>('/auth/me'),
}

// Library
export const library = {
  list: () => request<{ sources: any[]; total: number }>('/library/sources'),

  get: (citekey: string) => request<any>(`/library/sources/${citekey}`),

  add: (data: { citekey: string; title: string; authors?: string; year?: number; doi?: string }) =>
    request<any>('/library/sources', { method: 'POST', body: JSON.stringify(data) }),

  remove: (citekey: string) =>
    request<void>(`/library/sources/${citekey}`, { method: 'DELETE' }),

  upload: async (file: File) => {
    const token = localStorage.getItem('access_token')
    const formData = new FormData()
    formData.append('file', file)
    const res = await fetch(`${API_BASE}/library/upload`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: formData,
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(res.status, body.detail || res.statusText)
    }
    return res.json() as Promise<{
      citekey: string
      paper_id: string
      pdf_hash: string
      status: string
      deduplicated: boolean
    }>
  },
}

// Analyze
export const analyze = {
  status: () =>
    request<{
      sources: { total: number; completed: number; pending: number; failed: number }
      coverage: { section: string; source_count: number }[]
      total_fragments: number
    }>('/analyze/status'),
}

// Projects
export const projects = {
  coverage: () =>
    request<{ total_sources: number; sections: Record<string, number>; chapters: Record<string, number> }>('/projects/coverage'),

  sectionSources: (section: string) =>
    request<{ section: string; citekeys: string[]; count: number }>(`/projects/sections/${section}/sources`),

  assignSections: (citekey: string, sections: string[], chapters: number[] = []) =>
    request<{ citekey: string; sections: string[] }>('/projects/sections/assign', {
      method: 'POST',
      body: JSON.stringify({ citekey, sections, chapters }),
    }),

  sourceSections: (citekey: string) =>
    request<{ citekey: string; sections: string[] }>(`/projects/sources/${citekey}/sections`),
}

// Process
export const process = {
  submit: (citekey: string) =>
    request<{ job_id: string; status: string }>(`/process/sources/${citekey}`, { method: 'POST' }),

  jobStatus: (jobId: string) =>
    request<{ job_id: string; status: string; result: any }>(`/process/jobs/${jobId}`),
}

// Write
export const write = {
  research: (section: string) =>
    request<{ job_id: string; status: string; section: string; task_type: string }>('/write/research', {
      method: 'POST',
      body: JSON.stringify({ section }),
    }),

  draft: (section: string) =>
    request<{ job_id: string; status: string; section: string; task_type: string }>('/write/draft', {
      method: 'POST',
      body: JSON.stringify({ section }),
    }),
}
