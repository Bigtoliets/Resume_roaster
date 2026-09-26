import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'overview', component: () => import('@/views/OverviewView.vue') },
  { path: '/models', name: 'models', component: () => import('@/views/ModelsView.vue') },
  { path: '/prompts', name: 'prompts', component: () => import('@/views/PromptsView.vue') },
  { path: '/tools', name: 'tools', component: () => import('@/views/ToolsView.vue') },
  {
    path: '/knowledge-bases',
    name: 'knowledge-bases',
    component: () => import('@/views/KnowledgeBasesView.vue'),
  },
  { path: '/skills', name: 'skills', component: () => import('@/views/SkillsView.vue') },
  { path: '/agents', name: 'agents', component: () => import('@/views/AgentsView.vue') },
  { path: '/sessions', name: 'sessions', component: () => import('@/views/SessionDetailView.vue') },
  {
    path: '/sessions/:id',
    name: 'session-detail',
    component: () => import('@/views/SessionDetailView.vue'),
    props: true,
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})
