/**
 * Humanize LiteLLM `provider/model-id` strings for UI display.
 *
 * Examples:
 *   "anthropic/claude-sonnet-4-20250514"     → "Sonnet 4"
 *   "anthropic/claude-sonnet-4-5-20251201"   → "Sonnet 4.5"
 *   "anthropic/claude-opus-4-7"              → "Opus 4.7"
 *   "anthropic/claude-haiku-4-5-20251001"    → "Haiku 4.5"
 *   "openai/gpt-4.1"                          → "GPT-4.1"
 *   "openai/gpt-4o"                           → "GPT-4o"
 *   "openai/o3-mini"                          → "o3-mini"
 *   "ollama/bge-m3"                           → "bge-m3"
 *   ""                                         → ""
 *   "unknown/whatever"                         → "whatever"
 *
 * Unknown patterns fall back to the part after the slash, or the raw
 * string if there's no slash. Never throws.
 */
export function humanizeModel(raw: string | null | undefined): string {
  if (!raw) return ''
  const id = raw.trim()
  if (!id) return ''

  const parts = id.split('/')
  const providerKey = parts.length > 1 ? (parts[0] ?? '') : ''
  const model = parts.length > 1 ? parts.slice(1).join('/') : id

  // Anthropic: claude-<family>-<major>[-<minor>][-<YYYYMMDD date suffix>]
  if (providerKey === 'anthropic' || model.startsWith('claude-')) {
    const m = model.match(/^claude-(sonnet|opus|haiku)-(\d+)(?:-(\d+))?/i)
    if (m && m[1] && m[2]) {
      const family = m[1].charAt(0).toUpperCase() + m[1].slice(1).toLowerCase()
      const major = m[2]
      // Second numeric group is the minor version ONLY if it has ≤2 digits.
      // 8-digit groups are release date suffixes (e.g. "20250514") and are dropped.
      const minor = m[3] && m[3].length <= 2 ? m[3] : null
      return minor ? `${family} ${major}.${minor}` : `${family} ${major}`
    }
  }

  // OpenAI: gpt-*, o-series
  if (providerKey === 'openai' || model.startsWith('gpt-') || /^o\d/.test(model)) {
    if (model.startsWith('gpt-')) {
      return 'GPT-' + model.slice(4)
    }
    return model
  }

  // Fallback: strip provider/, return model slug
  return model
}
