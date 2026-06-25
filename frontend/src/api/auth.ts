import { apiPost } from './client'
import type { AuthResponse, User } from '@/types'

export async function login(username: string, password: string): Promise<AuthResponse> {
  return apiPost<AuthResponse>('/auth/login', { username, password })
}

export async function register(username: string, password: string): Promise<AuthResponse> {
  return apiPost<AuthResponse>('/auth/register', { username, password })
}

export async function getMe(): Promise<User> {
  return apiPost<User>('/auth/me')
}
