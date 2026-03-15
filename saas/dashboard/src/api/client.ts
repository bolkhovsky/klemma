/**
 * API client for the Klemma backend.
 * Handles JWT token management and request formatting.
 */

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
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

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })

  if (res.status === 401) {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    window.location.href = '/login'
    throw new ApiError(401, 'Unauthorized')
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

// Process
export const process = {
  submit: (citekey: string) =>
    request<{ job_id: string; status: string }>(`/process/sources/${citekey}`, { method: 'POST' }),

  jobStatus: (jobId: string) =>
    request<{ job_id: string; status: string; result: any }>(`/process/jobs/${jobId}`),
}
