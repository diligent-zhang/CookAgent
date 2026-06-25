import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/pages/LoginPage.vue'),
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('@/pages/RegisterPage.vue'),
    },
    {
      path: '/chat',
      name: 'chat-new',
      component: () => import('@/pages/ChatPage.vue'),
      meta: { mode: 'chat' },
    },
    {
      path: '/chat/:id',
      name: 'chat',
      component: () => import('@/pages/ChatPage.vue'),
      meta: { mode: 'chat' },
    },
    {
      path: '/agent',
      name: 'agent-new',
      component: () => import('@/pages/ChatPage.vue'),
      meta: { mode: 'agent' },
    },
    {
      path: '/agent/:id',
      name: 'agent',
      component: () => import('@/pages/ChatPage.vue'),
      meta: { mode: 'agent' },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/agent',
    },
  ],
})

export default router
