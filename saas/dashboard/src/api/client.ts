/**
 * API client for the Klemma backend.
 * Handles JWT token management, refresh, and request formatting.
 */

// `?? '/api'` (not `||`) so an explicitly-empty VITE_API_BASE (portal build,
// where the bonum container serves both API and SPA at root) is preserved.
const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

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
    throw new ApiError(res.status, body.detail || res.statusText || `Ошибка сервера (${res.status})`)
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

// Meeting-analytics portal (Bonum B2B)
export interface MeetingTask {
  title: string
  who: string
  due: string
  overdue: boolean
  time: string
}
export interface MeetingItem {
  id: string
  date: string
  type: string
  site: string
  time: string
  title: string
  tasks: number
  speakers: string[]
  chips: { label: string; tone: string }[]
  summary: string
  decisions: string[]
  task_list: MeetingTask[]
}
export interface MeetingsList {
  meetings: MeetingItem[]
  stats: { meetings: number; tasks: number; escalations: number }
}
export interface SearchResultItem {
  quote: string
  score: number
  speaker: string
  meeting: string
  type: string
  site: string
  date: string
  time: string
  citekey: string
  tag: string
}
export interface MeetingsSearch {
  query: string
  results: SearchResultItem[]
  semantic_count: number
  keyword_count: number
}
export interface AskSource {
  n: number
  quote: string
  meeting: string
  date: string
  time: string
  speaker: string
  citekey: string
}
export interface AskAnswer {
  answer: string
  model: string
  sources: AskSource[]
  followups: string[]
}
export interface TasksBoard {
  stats: { n: number; label: string; tone: string }[]
  themes: {
    title: string
    count: number
    escalated: boolean
    meetings: { date: string; type: string; site: string }[]
  }[]
  overdue_persons: { name: string; n: number; pct: string }[]
  overdue_sites: { name: string; n: number; pct: string }[]
  escalations: { title: string; owner: string; site: string; age: string }[]
}
export interface SiteInfo {
  slug: string
  name: string
  type: string
  leader: string
  meetings: number
}
export interface SitesResponse {
  role: 'director' | 'leader'
  can_view_all: boolean
  sites: SiteInfo[]
}
export interface AnalyticsWeek {
  week: string
  label: string
  meetings: number
  tasks: number
  escalations: number
  overdue: number
}
export interface AnalyticsTopic {
  title: string
  status: string
  first_seen: string
  last_seen: string
  meetings: number
  timeline: { date: string; note: string }[]
  insight: string
}
export interface AnalyticsKpi {
  name: string
  trend: string
  evidence: string
}
export interface AnalyticsPattern {
  observation: string
  recommendation: string
  severity: string
}
export interface AnalyticsReport {
  site: string
  site_name: string
  days: number
  window: { from: string; to: string }
  meetings_analyzed: number
  truncated: boolean
  generated_at: string
  model: string
  cached: boolean
  detail?: string
  metrics: {
    weeks: AnalyticsWeek[]
    totals: { meetings: number; tasks: number; escalations: number; overdue: number }
    top_assignees: { name: string; tasks: number; overdue: number }[]
  }
  summary: string
  topics: AnalyticsTopic[]
  kpis: AnalyticsKpi[]
  patterns: AnalyticsPattern[]
}

export const meetings = {
  sites: () => request<SitesResponse>('/meetings/sites'),
  list: (opts?: { site?: string; days?: number }) => {
    const params = new URLSearchParams()
    if (opts?.site) params.set('site', opts.site)
    if (opts?.days) params.set('days', String(opts.days))
    const qs = params.size ? `?${params}` : ''
    return request<MeetingsList>(`/meetings${qs}`)
  },
  get: (id: string) => request<MeetingItem>(`/meetings/${encodeURIComponent(id)}`),
  search: (q: string, site?: string) => {
    const params = new URLSearchParams({ q })
    if (site) params.set('site', site)
    return request<MeetingsSearch>(`/meetings/search?${params}`)
  },
  tasks: (opts?: { site?: string; days?: number }) => {
    const params = new URLSearchParams()
    if (opts?.site) params.set('site', opts.site)
    if (opts?.days) params.set('days', String(opts.days))
    const qs = params.size ? `?${params}` : ''
    return request<TasksBoard>(`/meetings/tasks${qs}`)
  },
  ask: (query: string, site?: string) =>
    request<AskAnswer>('/meetings/ask', {
      method: 'POST',
      body: JSON.stringify(site ? { query, site } : { query }),
    }),
  analytics: (opts: { site?: string; days: number; refresh?: boolean }) => {
    const params = new URLSearchParams({ days: String(opts.days) })
    if (opts.site) params.set('site', opts.site)
    if (opts.refresh) params.set('refresh', '1')
    return request<AnalyticsReport>(`/meetings/analytics?${params}`)
  },
}

// Library
export const library = {
  list: (projectId?: string, q?: string) => {
    const params = new URLSearchParams()
    if (projectId) params.set('project_id', projectId)
    if (q) params.set('q', q)
    const qs = params.size ? `?${params}` : ''
    return request<{ sources: any[]; total: number }>(`/library/sources${qs}`)
  },

  get: (citekey: string) => request<any>(`/library/sources/${citekey}`),

  fragmentSearch: (q: string, limit = 10) => {
    const params = new URLSearchParams({ q, limit: String(limit) })
    return request<{
      results: {
        fragment_id: string
        citekey: string
        title: string
        authors: string
        year: number | null
        text: string
        fragment_type: string
      }[]
      total: number
      query: string
    }>(`/library/fragments/search?${params}`)
  },

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
      throw new ApiError(res.status, body.detail || res.statusText || `Ошибка сервера (${res.status})`)
    }
    return res.json() as Promise<{
      citekey: string
      paper_id: string
      pdf_hash: string
      status: string
      deduplicated: boolean
      already_owned: boolean
      job_id: string | null
    }>
  },

  gaps: () =>
    request<{
      gaps: {
        title: string
        authors: string | null
        year: number | null
        doi: string | null
        cited_by_count: number
        score: number
        avg_quality: number
        intent_weight: number
        semantic_factor: number
        intents: string[]
        top_intent: string | null
        sections_served: Array<{ section: string; count: number }>
      }[]
      total: number
      detail?: string | null
    }>('/library/gaps'),

  recommendations: (projectId: string) =>
    request<{
      recommendations: {
        title: string
        authors: string
        year: number | null
        doi: string | null
        rationale: string
        score: number
      }[]
      total: number
      model: string
      generated_at: string
      cached: boolean
      detail?: string | null
      warning?: string | null
    }>(`/library/recommendations?project_id=${encodeURIComponent(projectId)}`),
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

  delete: (projectId: string) =>
    request<void>(`/projects/${projectId}`, { method: 'DELETE' }),

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
  briefing: (projectId: string) =>
    request<{
      total_sources: number
      total_fragments: number
      suggested_count: number
      accepted_count: number
      by_section: {
        section_id: string
        section_name: string
        fragment_count: number
        accepted_count: number
        source_count: number
        readiness: 'ready' | 'partial' | 'empty'
      }[]
      empty_sections: string[]
      coach_findings: {
        category: string
        section: string | null
        message: string
        severity: string
      }[]
      readiness_pct: number
    }>(`/analyze/briefing/${projectId}`),
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

  detachSection: (citekey: string, section: string) =>
    request<void>(`/projects/sections/${encodeURIComponent(section)}/sources/${encodeURIComponent(citekey)}`, {
      method: 'DELETE',
    }),
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

// Write (draft generation)
export const write = {
  draft: (section: string, projectId?: string, instruction?: string, wordTarget?: number) =>
    request<{ job_id: string; status: string; section: string; task_type: string }>('/write/draft', {
      method: 'POST',
      body: JSON.stringify({ section, project_id: projectId, instruction: instruction || undefined, word_target: wordTarget }),
    }),
}

// Draft files (Markdown-first, single .md per project)
export interface DraftHeading {
  level: number
  section_id: string
  title: string
  full_title: string
  line: number
}

/** Compute word count for every section from full draft content. */
export function computeSectionWordCounts(content: string, headings: DraftHeading[]): Record<string, number> {
  const lines = content.split('\n')
  const counts: Record<string, number> = {}
  headings.forEach((h, i) => {
    const nextLine = headings[i + 1]?.line ?? lines.length
    const body = lines.slice(h.line + 1, nextLine).join('\n').trim()
    counts[h.section_id] = body ? body.split(/\s+/).filter(Boolean).length : 0
  })
  return counts
}

export interface DraftFile {
  name: string
  headings: DraftHeading[]
  word_count: number
}

export interface DraftContent extends DraftFile {
  content: string
}

export const drafts = {
  list: (projectId: string) =>
    request<{ files: DraftFile[] }>(`/projects/${projectId}/drafts`),

  get: (projectId: string, filename: string) =>
    request<DraftContent>(`/projects/${projectId}/drafts/${encodeURIComponent(filename)}`),

  save: (projectId: string, filename: string, content: string) =>
    request<DraftContent>(`/projects/${projectId}/drafts/${encodeURIComponent(filename)}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),

  init: (projectId: string, filename?: string) =>
    request<DraftContent>(`/projects/${projectId}/drafts/init`, {
      method: 'POST',
      body: JSON.stringify({ filename: filename ?? null }),
    }),

  scaffold: (projectId: string) =>
    request<{ files: DraftFile[] }>(`/projects/${projectId}/drafts/scaffold`, {
      method: 'POST',
    }),

  upsertSection: (
    projectId: string,
    filename: string,
    sectionId: string,
    body: string,
    headingTitle?: string,
  ) =>
    request<{ section_id: string; filename: string; commit: string }>(
      `/projects/${projectId}/drafts/${encodeURIComponent(filename)}/sections/${encodeURIComponent(sectionId)}`,
      {
        method: 'PUT',
        body: JSON.stringify({ body, heading_title: headingTitle ?? null }),
      },
    ),
}

// Curation (fragment accept/reject + curated bank)
export const curation = {
  pending: (projectId: string, citekey: string) =>
    request<{
      fragments: { fragment_id: string; text: string; citation_intent: string; fragment_type: string; page: number | null; citekey: string; verbatim: boolean; suggested_text: string | null; sentence_model: string | null }[]
      total: number
      curated_count: number
    }>(`/projects/${projectId}/fragments/pending?citekey=${encodeURIComponent(citekey)}`),

  curate: (projectId: string, decisions: { fragment_id: string; citekey: string; verdict: string; assigned_section?: string; note?: string; suggested_text?: string; sentence_model?: string }[]) =>
    request<{ curated: number; accepted: number; rejected: number }>(`/projects/${projectId}/fragments/curate`, {
      method: 'POST',
      body: JSON.stringify({ decisions }),
    }),

  curated: (projectId: string, params?: { verdict?: string; section?: string; citekey?: string }) => {
    const qs = params ? `?${new URLSearchParams(params as Record<string, string>)}` : ''
    return request<{
      fragments: { fragment_id: string; citekey: string; text: string; citation_intent: string; assigned_section: string | null; note: string | null; verdict: string; curated_at: string; suggested_text: string | null; sentence_model: string | null }[]
      total: number
      by_section: Record<string, number>
    }>(`/projects/${projectId}/fragments/curated${qs}`)
  },

  update: (projectId: string, fragmentId: string, patch: { verdict?: string; assigned_section?: string; note?: string; suggested_text?: string; sentence_model?: string }) =>
    request<{ ok: boolean }>(`/projects/${projectId}/fragments/curate/${fragmentId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  suggest: (projectId: string, section: string) =>
    request<{
      gap_alert: { missing_intents: string[]; message: string } | null
      suggestions: { fragment_id: string; text: string; citation_intent: string; source: string; citekey: string; match_reason: string; score: number }[]
    }>(`/projects/${projectId}/fragments/suggest?section=${encodeURIComponent(section)}`),

  generateSentences: (projectId: string, citekey: string, mode: 'missing' | 'force' = 'missing') =>
    request<{ job_id: string; status: string; citekey: string }>(
      `/projects/${projectId}/fragments/generate-sentences`,
      {
        method: 'POST',
        body: JSON.stringify({ citekey, mode }),
      },
    ),
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
