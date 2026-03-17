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
  list: (projectId?: string) => {
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
    return request<{ sources: any[]; total: number }>(`/library/sources${qs}`)
  },

  get: (citekey: string) => request<any>(`/library/sources/${citekey}`),

  add: (data: { citekey: string; title: string; authors?: string; year?: number; doi?: string }) =>
    request<any>('/library/sources', { method: 'POST', body: JSON.stringify(data) }),

  remove: (citekey: string) =>
    request<void>(`/library/sources/${citekey}`, { method: 'DELETE' }),

  upload: async (file: File, projectId?: string) => {
    const token = localStorage.getItem('access_token')
    const formData = new FormData()
    formData.append('file', file)
    if (projectId) formData.append('project_id', projectId)
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
      job_id: string | null
    }>
  },

  gaps: () =>
    request<{ gaps: { title: string; authors: string | null; year: number | null; cited_by_count: number; intents: string | null }[]; total: number; detail?: string }>('/library/gaps'),
}

// User projects (CRUD)
export interface OutlineSection {
  id: string
  name: string
}

export interface Project {
  project_id: string
  name: string
  type: string
  created_at: string
  outline: OutlineSection[] | null
}

export const userProjects = {
  list: () => request<{ projects: Project[] }>('/projects'),

  create: (name: string, type = 'dissertation') =>
    request<Project>('/projects', {
      method: 'POST',
      body: JSON.stringify({ name, type }),
    }),

  rename: (projectId: string, name: string) =>
    request<Project>(`/projects/${projectId}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }),

  updateOutline: (projectId: string, sections: OutlineSection[]) =>
    request<Project>(`/projects/${projectId}/outline`, {
      method: 'PATCH',
      body: JSON.stringify({ sections }),
    }),

  generateOutline: (projectId: string, contextText: string) =>
    request<{ job_id: string; status: string }>(`/projects/${projectId}/outline/generate`, {
      method: 'POST',
      body: JSON.stringify({ context_text: contextText }),
    }),
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
  submit: (citekey: string, opts?: { projectId?: string; force?: boolean }) => {
    const params = new URLSearchParams()
    if (opts?.projectId) params.set('project_id', opts.projectId)
    if (opts?.force) params.set('force', 'true')
    const qs = params.toString() ? `?${params.toString()}` : ''
    return request<{ job_id: string; status: string }>(`/process/sources/${citekey}${qs}`, { method: 'POST' })
  },

  jobStatus: (jobId: string) =>
    request<{ job_id: string; status: string; result: any }>(`/process/jobs/${jobId}`),
}

// Usage
export const usage = {
  me: () =>
    request<{
      total_granted: number
      total_used: number
      remaining: number
      operations: { operation: string; count: number; tokens: number }[]
    }>('/usage/me'),
}

// Research (literature review generation + stored reports)
export const research = {
  generate: (section: string, projectId?: string) =>
    request<{ job_id: string; status: string; section: string; task_type: string }>('/write/research', {
      method: 'POST',
      body: JSON.stringify({ section, project_id: projectId }),
    }),

  getReport: (projectId: string, section: string) =>
    request<{ section: string; report_text: string; report_data: any; model: string; created_at: string }>(
      `/projects/${projectId}/research/${encodeURIComponent(section)}`
    ),

  listReports: (projectId: string) =>
    request<{ project_id: string; reports: { section: string; created_at: string }[] }>(
      `/projects/${projectId}/research`
    ),
}
