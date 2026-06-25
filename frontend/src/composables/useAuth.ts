import { ref } from 'vue'
import { useRouter } from 'vue-router'
import type { User } from '@/types'
import { login as apiLogin, register as apiRegister } from '@/api/auth'

const user = ref<User | null>(loadUser())
const token = ref<string | null>(localStorage.getItem('cookagent_token'))
const isAuthenticated = ref(!!token.value)

function loadUser(): User | null {
  try {
    const raw = localStorage.getItem('cookagent_user')
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function saveAuth(accessToken: string, u: User) {
  token.value = accessToken
  user.value = u
  isAuthenticated.value = true
  localStorage.setItem('cookagent_token', accessToken)
  localStorage.setItem('cookagent_user', JSON.stringify(u))
}

export function useAuth() {
  const router = useRouter()
  const error = ref('')
  const loading = ref(false)

  async function login(username: string, password: string) {
    error.value = ''
    loading.value = true
    try {
      const res = await apiLogin(username, password)
      saveAuth(res.access_token, res.user)
      router.push('/agent')
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '登录失败'
    } finally {
      loading.value = false
    }
  }

  async function register(username: string, password: string) {
    error.value = ''
    loading.value = true
    try {
      const res = await apiRegister(username, password)
      saveAuth(res.access_token, res.user)
      router.push('/agent')
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '注册失败'
    } finally {
      loading.value = false
    }
  }

  function logout() {
    token.value = null
    user.value = null
    isAuthenticated.value = false
    localStorage.removeItem('cookagent_token')
    localStorage.removeItem('cookagent_user')
    router.push('/login')
  }

  return { user, token, isAuthenticated, error, loading, login, register, logout }
}
