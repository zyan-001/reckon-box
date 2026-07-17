// 侧边栏导航组件

import { store } from '../state.js'
import { api } from '../api.js'
import { goTo } from '../router.js'

const views = [
  { id: 'scenario', label: '场景输入' },
  { id: 'blackbox', label: '黑盒规格' },
  { id: 'designer', label: '沙盒设计' },
  { id: 'simulation', label: '性能模拟' },
  { id: 'review', label: '审查迭代' },
]

export function Sidebar() {
  const nav = document.createElement('nav')
  nav.className = 'sidebar'

  const brand = document.createElement('div')
  brand.className = 'nav-brand'
  brand.textContent = 'ReckonBox'

  const menu = document.createElement('div')
  menu.style.flex = '1'
  menu.className = 'nav-flow'

  views.forEach((view, idx) => {
    const btn = document.createElement('button')
    btn.className = 'nav-item'
    btn.innerHTML = `
      <span class="nav-step-dot"></span>
      <span class="nav-item-content">
        <span class="nav-item-label">${view.label}</span>
        <span class="nav-mini-badge">待开始</span>
      </span>
    `
    btn.dataset.view = view.id
    btn.onclick = () => {
      goTo(view.id)
    }
    menu.appendChild(btn)

    if (idx < views.length - 1) {
      const line = document.createElement('div')
      line.className = 'nav-flow-line'
      line.dataset.index = String(idx)
      menu.appendChild(line)
    }
  })

  // 用户信息区
  const userInfo = document.createElement('div')
  userInfo.style.marginTop = 'auto'
  userInfo.style.paddingTop = '1rem'
  userInfo.style.borderTop = '1px solid var(--border)'
  userInfo.id = 'sidebar-user-info'

  const status = document.createElement('div')
  status.className = 'status-bar'
  status.id = 'sidebar-status'

  nav.appendChild(brand)
  nav.appendChild(menu)
  nav.appendChild(userInfo)
  nav.appendChild(status)

  renderUserInfo(userInfo)

  return nav
}

function renderUserInfo(el) {
  if (!el) return
  const roleLabel = store.role === 'admin' ? '管理员' : '游客'
  const remainingText = store.llmRemaining === -1
    ? 'LLM: 无限'
    : `LLM: ${store.llmRemaining}/${store.llmLimit} 次`

  el.innerHTML = `
    <div style="font-size: 0.875rem; margin-bottom: 0.5rem;">
      <span class="badge ${store.role === 'admin' ? 'badge-info' : 'badge-warning'}">${roleLabel}</span>
    </div>
    <div style="font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 0.75rem;">${remainingText}</div>
    <button id="btn-logout" class="btn btn-secondary" style="width: 100%; font-size: 0.8125rem;">登出</button>
  `

  el.querySelector('#btn-logout').onclick = async () => {
    try {
      await api.logout()
    } catch {
      // 忽略
    }
    store.authenticated = false
    store.role = null
    store.token = null
    store.llmRemaining = -1
    store.llmLimit = -1
    goTo('login')
  }
}

export function updateSidebarActive() {
  document.querySelectorAll('.nav-item').forEach((btn, idx) => {
    const view = btn.dataset.view
    const state = getViewState(view)
    const isActive = view === store.currentView
    btn.classList.toggle('active', isActive)
    btn.classList.toggle('done', state === 'done')
    btn.classList.toggle('pending', state === 'pending')

    const badge = btn.querySelector('.nav-mini-badge')
    if (!badge) return
    badge.className = 'nav-mini-badge'
    if (isActive) {
      badge.textContent = '进行中'
      badge.classList.add('active')
    } else if (state === 'done') {
      badge.textContent = '完成'
      badge.classList.add('done')
    } else {
      badge.textContent = '待开始'
    }

    const line = document.querySelector(`.nav-flow-line[data-index="${idx}"]`)
    if (line) {
      line.classList.toggle('done', state === 'done')
    }
  })
}

export function updateSidebarStatus() {
  const el = document.getElementById('sidebar-status')
  if (!el) return
  if (store.backendStatus === 'ok') {
    el.textContent = `后端已连接 v${store.backendVersion}`
    el.className = 'status-bar ok'
  } else if (store.backendStatus === 'error') {
    el.textContent = '后端未连接'
    el.className = 'status-bar error'
  } else {
    el.textContent = '连接中...'
    el.className = 'status-bar'
  }
}

export function updateSidebarUserInfo() {
  renderUserInfo(document.getElementById('sidebar-user-info'))
}

function getViewState(viewId) {
  if (viewId === 'scenario') {
    return store.blackboxSpec ? 'done' : 'pending'
  }
  if (viewId === 'blackbox') {
    return store.blackboxSpec ? 'done' : 'pending'
  }
  if (viewId === 'designer') {
    return Object.keys(store.components).length > 0 ? 'done' : 'pending'
  }
  if (viewId === 'simulation') {
    return store.simulationReport ? 'done' : 'pending'
  }
  if (viewId === 'review') {
    return store.reviewResult || store.convergenceResult ? 'done' : 'pending'
  }
  return 'pending'
}
