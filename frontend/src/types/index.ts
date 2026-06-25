// ===== SSE Events =====
export interface SSEEvent {
  type: string
  content?: string
  sources?: Source[]
  message_id?: string
  session_id?: string
  tool_name?: string
  arguments?: Record<string, unknown>
  result?: string
  step_number?: number
}

// ===== Chat =====
export interface Source {
  dish_name: string
  category: string
  source: string
  relevance_score: number
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
  sources?: Source[]
  thoughts?: AgentStep[]
}

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

// ===== Agent =====
export interface AgentStep {
  type: 'thought' | 'tool_call' | 'observation'
  content: string
  tool_name?: string
  step_number: number
}

export interface AgentSession {
  id: string
  title: string
  status: 'active' | 'completed' | 'error'
  created_at: string
  updated_at: string
}

// ===== Auth =====
export interface User {
  id: string
  username: string
  nickname: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user: User
}
