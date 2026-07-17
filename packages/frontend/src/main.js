// ReckonBox 前端入口

import './style.css'
import { api } from './api.js'
import { store, loadAppData } from './state.js'
import { renderApp } from './router.js'
import { updateSidebarStatus, updateSidebarUserInfo } from './components/Sidebar.js'

async function init() {
  // 1. 检查后端健康
  try {
    const data = await api.health()
    store.backendStatus = 'ok'
    store.backendVersion = data.version
  } catch (err) {
    store.backendStatus = 'error'
    console.error('后端健康检查失败:', err)
  }
  updateSidebarStatus()

  // 2. 检查是否已有登录 session（cookie 自动携带）
  try {
    const status = await api.authStatus()
    if (status.authenticated) {
      store.authenticated = true
      store.role = status.role
      store.llmRemaining = status.remaining
      store.llmLimit = status.limit
    }
  } catch {
    // 未登录，显示登录页
  }

  // 3. 如果已登录，加载领域包和模板
  if (store.authenticated) {
    await loadAppData()
  }

  renderApp()
}

// 暴露给全局，供各视图调用后刷新 LLM 剩余次数
window.__refreshLlmRemaining = function () {
  const remaining = window.__llmRemaining
  if (remaining !== undefined) {
    store.llmRemaining = remaining
    updateSidebarUserInfo()
  }
}

// 后端重连
async function retryBackendConnection() {
  for (let i = 0; i < 10; i++) {
    await new Promise((r) => setTimeout(r, 2000))
    try {
      const data = await api.health()
      store.backendStatus = 'ok'
      store.backendVersion = data.version
      updateSidebarStatus()
      if (store.authenticated) {
        await loadAppData()
      }
      renderApp()
      return
    } catch {
      // continue retrying
    }
  }
}

init()

if (store.backendStatus !== 'ok') {
  retryBackendConnection()
}
