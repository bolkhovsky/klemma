// Shared presentation helpers for the meeting portal — mirror the design tokens.

const MONTHS_RU = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

export function dayOf(date: string): string {
  const parts = (date || '').split('-')
  return parts.length === 3 ? String(parseInt(parts[2] ?? '', 10) || '') : ''
}

export function monOf(date: string): string {
  const parts = (date || '').split('-')
  if (parts.length < 2) return ''
  const m = parseInt(parts[1] ?? '', 10)
  return MONTHS_RU[m - 1] ?? ''
}

// Meeting type → [background, ink]
export const TYPE_COLORS: Record<string, [string, string]> = {
  'ОМС': ['var(--accent-pale)', 'var(--accent-deep)'],
  'Scrum': ['var(--violet-pale)', 'var(--violet-deep)'],
  'Продажи': ['var(--amber-pale)', 'var(--amber-deep)'],
}
export function typeBg(t: string): string {
  const c = TYPE_COLORS[t]
  return c ? c[0] : 'var(--rule-light)'
}
export function typeInk(t: string): string {
  const c = TYPE_COLORS[t]
  return c ? c[1] : 'var(--ink-muted)'
}

// chip tone → [ink, background]
export const TONE: Record<string, [string, string]> = {
  err: ['var(--err)', 'var(--err-bg)'],
  warn: ['var(--warn)', 'var(--warn-bg)'],
  ok: ['var(--ok)', 'var(--ok-bg)'],
  mute: ['var(--ink-muted)', 'var(--rule-light)'],
}
export function toneInk(t: string): string {
  const c = TONE[t] ?? TONE.mute!
  return c[0]
}
export function toneBg(t: string): string {
  const c = TONE[t] ?? TONE.mute!
  return c[1]
}

// stat card colour by semantic name
export function statColor(tone: string): string {
  if (tone === 'err') return 'var(--err)'
  if (tone === 'cta') return 'var(--cta)'
  if (tone === 'warn') return 'var(--warn)'
  return 'var(--ink)'
}

const AVATARS: [string, string][] = [
  ['var(--accent-pale)', 'var(--accent-deep)'],
  ['var(--violet-pale)', 'var(--violet-deep)'],
  ['var(--amber-pale)', 'var(--amber-deep)'],
  ['var(--picked-tint)', 'var(--picked)'],
]
export function avatarBg(i: number): string {
  return (AVATARS[i % AVATARS.length] ?? AVATARS[0]!)[0]
}
export function avatarFg(i: number): string {
  return (AVATARS[i % AVATARS.length] ?? AVATARS[0]!)[1]
}

// task due → colour (overdue=red, otherwise muted)
export function dueColor(overdue: boolean): string {
  return overdue ? 'var(--err)' : 'var(--ink-muted)'
}

export function scoreStr(score: number): string {
  return `${Math.round((score || 0) * 100)}%`
}

// intent/tag → label + colours for search results
export function tagLabel(tag: string): string {
  const m: Record<string, string> = {
    summary: 'обсуждение',
    decision: 'решение',
    escalation: 'эскалация',
    new: 'задача',
    closed: 'закрыта',
    rescheduled: 'перенос',
    transferred: 'передана',
    mentioned: 'упоминание',
  }
  return m[tag] || tag || 'фрагмент'
}
export function tagTone(tag: string): string {
  if (tag === 'escalation') return 'err'
  if (tag === 'decision') return 'ok'
  if (tag === 'summary') return 'mute'
  return 'warn'
}
