import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { userProjects, type Project, type OutlineSection } from '@/api/client'

const ACTIVE_KEY = 'active_project_id'

export const useProjectStore = defineStore('project', () => {
  const projects = ref<Project[]>([])
  const activeProjectId = ref<string | null>(localStorage.getItem(ACTIVE_KEY))
  const loading = ref(false)

  const activeProject = computed(() =>
    projects.value.find((p) => p.project_id === activeProjectId.value) ?? null,
  )

  const activeOutline = computed<OutlineSection[] | null>(
    () => activeProject.value?.outline ?? null,
  )

  async function loadProjects() {
    loading.value = true
    try {
      const res = await userProjects.list()
      projects.value = res.projects

      // Auto-select: if saved id no longer exists, pick first
      if (activeProjectId.value && !projects.value.find((p) => p.project_id === activeProjectId.value)) {
        setActive(projects.value[0] ? projects.value[0].project_id : null)
      } else if (!activeProjectId.value && projects.value.length > 0) {
        setActive(projects.value[0]?.project_id ?? null)
      }
    } finally {
      loading.value = false
    }
  }

  async function createProject(name: string, type: string) {
    const project = await userProjects.create(name, type)
    projects.value.push(project)
    setActive(project.project_id)
    return project
  }

  async function renameProject(projectId: string, name: string) {
    const updated = await userProjects.rename(projectId, name)
    const idx = projects.value.findIndex((p) => p.project_id === projectId)
    if (idx !== -1) projects.value[idx] = updated
    return updated
  }

  function setActive(id: string | null) {
    activeProjectId.value = id
    if (id) localStorage.setItem(ACTIVE_KEY, id)
    else localStorage.removeItem(ACTIVE_KEY)
  }

  async function updateOutline(sections: OutlineSection[]) {
    if (!activeProjectId.value) return
    const updated = await userProjects.updateOutline(activeProjectId.value, sections)
    const idx = projects.value.findIndex((p) => p.project_id === updated.project_id)
    if (idx !== -1) projects.value[idx] = updated
  }

  function $reset() {
    projects.value = []
    activeProjectId.value = null
    localStorage.removeItem(ACTIVE_KEY)
    loading.value = false
  }

  return { projects, activeProjectId, activeProject, activeOutline, loading, loadProjects, createProject, renameProject, setActive, updateOutline, $reset }
})
