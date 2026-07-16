// 场景输入视图

import { api } from '../api.js'
import { store } from '../state.js'
import { goTo } from '../router.js'
import { updateSidebarUserInfo } from '../components/Sidebar.js'

export function ScenarioView() {
  const container = document.createElement('div')
  container.className = 'main'

  // tourist 限额提示
  const limitHint = store.llmRemaining !== -1
    ? `<div style="margin-bottom: 1rem; color: var(--warning); font-size: 0.875rem;">剩余 LLM 调用次数：${store.llmRemaining} / ${store.llmLimit}</div>`
    : ''

  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">场景输入</div>
      <div class="page-desc">用自然语言描述你要设计的系统场景。</div>
    </div>
    ${limitHint}
    <div class="card">
      <div class="form-group">
        <label class="form-label">场景描述</label>
        <textarea id="scenario-input" class="form-textarea" placeholder="例如：设计一个在线商城的订单服务，需要支持秒杀场景..."></textarea>
      </div>
      <button id="btn-parse-scenario" class="btn btn-primary" ${store.llmRemaining === 0 ? 'disabled' : ''}>
        ${store.llmRemaining === 0 ? 'LLM次数已用完' : '解析场景'}
      </button>
    </div>
    <div id="scenario-error" style="color: var(--danger); margin-top: 1rem;"></div>
  `

  const input = container.querySelector('#scenario-input')
  const btn = container.querySelector('#btn-parse-scenario')
  const errorEl = container.querySelector('#scenario-error')

  input.value = store.scenarioInput

  btn.onclick = async () => {
    const text = input.value.trim()
    if (!text) return
    store.scenarioInput = text
    errorEl.textContent = ''
    store.loading = true

    try {
      const res = await api.parseScenario(text)
      store.loading = false
      // 刷新剩余次数
      window.__refreshLlmRemaining?.()
      if (res.ok && res.spec) {
        store.blackboxSpec = res.spec
        goTo('blackbox')
      } else {
        errorEl.textContent = res.error || '解析失败'
      }
    } catch (err) {
      store.loading = false
      if (err.message === 'AUTH_REQUIRED') {
        goTo('login')
        return
      }
      if (err.message === 'RATE_LIMITED') {
        errorEl.textContent = `LLM调用次数已用完（剩余 ${err.remaining} 次）`
        store.llmRemaining = 0
        btn.disabled = true
        btn.textContent = 'LLM次数已用完'
        updateSidebarUserInfo()
        return
      }
      errorEl.textContent = err.message
    }
  }

  return container
}
