// Site-filter state for the meeting portal — module-scope singleton shared by
// the layout (dropdown) and every portal view (data scoping). 'all' = вся компания.
import { computed, ref } from 'vue'
import { meetings as api, type SiteInfo } from '@/api/client'

const STORAGE_KEY = 'bonum-portal-site'

const sites = ref<SiteInfo[]>([])
const role = ref<'director' | 'leader'>('director')
const canViewAll = ref(true)
const selected = ref<string>('all')
const loaded = ref(false)

let loadPromise: Promise<void> | null = null

async function doLoad(): Promise<void> {
  try {
    const data = await api.sites()
    sites.value = data.sites
    role.value = data.role
    canViewAll.value = data.can_view_all
    let saved = ''
    try {
      saved = localStorage.getItem(STORAGE_KEY) || ''
    } catch { /* storage unavailable */ }
    const savedValid =
      (saved === 'all' && data.can_view_all) || data.sites.some((s) => s.slug === saved)
    if (savedValid) {
      selected.value = saved
    } else {
      selected.value = data.can_view_all ? 'all' : (data.sites[0]?.slug ?? 'all')
    }
  } catch {
    // Backend may not expose /meetings/sites yet — keep the portal usable
    // with the full-company scope and no site list.
    sites.value = []
    selected.value = 'all'
  } finally {
    loaded.value = true
  }
}

/** Idempotent: concurrent/repeated calls share one request. */
function load(): Promise<void> {
  if (!loadPromise) loadPromise = doLoad()
  return loadPromise
}

function setSite(slug: string) {
  selected.value = slug
  try {
    localStorage.setItem(STORAGE_KEY, slug)
  } catch { /* storage unavailable */ }
}

const siteName = computed(() => {
  if (selected.value === 'all') return 'Бонум — вся компания'
  return sites.value.find((s) => s.slug === selected.value)?.name ?? selected.value
})

/** Query-param form: undefined for 'all' (omit param), else the slug. */
const siteParam = computed<string | undefined>(() =>
  selected.value === 'all' ? undefined : selected.value,
)

export function useSiteFilter() {
  return { sites, role, canViewAll, selected, loaded, load, setSite, siteName, siteParam }
}
