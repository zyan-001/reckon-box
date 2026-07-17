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
        <label class="form-label" style="display: flex; justify-content: space-between; align-items: center;">
          <span>场景描述</span>
          <div class="preset-scenarios" style="display: flex; gap: 0.5rem;">
            <button class="btn btn-secondary preset-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" data-preset="电商秒杀">电商秒杀</button>
            <button class="btn btn-secondary preset-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" data-preset="12306抢票">12306抢票</button>
            <button class="btn btn-secondary preset-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" data-preset="微信红包">微信红包</button>
          </div>
        </label>
        <textarea id="scenario-input" class="form-textarea" placeholder="例如：设计一个在线商城的订单服务，需要支持秒杀场景..."></textarea>
      </div>
      <button id="btn-parse-scenario" class="btn btn-primary" ${store.llmRemaining === 0 ? 'disabled' : ''}>
        ${store.llmRemaining === 0 ? 'LLM次数已用完' : '解析场景'}
      </button>
      <div id="scenario-io-status" class="io-status" style="display: none;"></div>
    </div>
    <div id="scenario-error" style="color: var(--danger); margin-top: 1rem;"></div>
  `

  const input = container.querySelector('#scenario-input')
  const btn = container.querySelector('#btn-parse-scenario')
  const errorEl = container.querySelector('#scenario-error')
  const ioStatus = container.querySelector('#scenario-io-status')

  const presets = {
    '电商秒杀': '设计一个电商秒杀系统。平时 QPS 在 1000 左右，但在秒杀活动开始的瞬间，QPS 会飙升到 10万 以上。要求：\n1. 绝对不能超卖\n2. 核心链路（扣库存、下订单）高可用\n3. 允许部分请求被限流或排队，但不能让整个系统崩溃',
    '12306抢票': '设计一个类似12306的火车票抢票系统。春节期间并发极高，读多写少（查余票请求远大于下单请求）。要求：\n1. 保证余票查询的性能和一致性\n2. 订单创建时的座位锁定逻辑\n3. 处理大量无效的重复刷新请求',
    '微信红包': '设计一个微信群抢红包系统。发红包和抢红包的并发量都很大。要求：\n1. 保证每个红包的金额拆分正确，不能超发\n2. 抢红包的响应速度要快（目标延迟 < 100ms）\n3. 保证账务数据的一致性，不能出现资金丢失'
  }

  container.querySelectorAll('.preset-btn').forEach(presetBtn => {
    presetBtn.onclick = () => {
      const presetKey = presetBtn.dataset.preset
      if (presets[presetKey]) {
        input.value = presets[presetKey]
        store.scenarioInput = presets[presetKey]
      }
    }
  })

  input.value = store.scenarioInput

  btn.onclick = async () => {
    const text = input.value.trim()
    if (!text) return
    store.scenarioInput = text
    errorEl.textContent = ''
    store.loading = true
    btn.disabled = true
    btn.textContent = '解析中...'
    ioStatus.style.display = 'block'
    const stopHint = startHintRotation(ioStatus, [
      '正在理解你的场景目标...',
      '正在提取关键功能点与边界...',
      '正在生成可编辑的黑盒规格...',
    ])

    try {
      const res = await api.parseScenario(text)
      store.loading = false
      stopHint()
      ioStatus.style.display = 'none'
      // 刷新剩余次数
      window.__refreshLlmRemaining?.()
      if (res.ok && res.spec) {
        store.blackboxSpec = res.spec
        goTo('blackbox')
      } else {
        btn.disabled = false
        btn.textContent = '解析场景'
        errorEl.textContent = res.error || '解析失败'
      }
    } catch (err) {
      store.loading = false
      stopHint()
      ioStatus.style.display = 'none'
      btn.disabled = false
      btn.textContent = '解析场景'
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

function startHintRotation(el, hints) {
  let index = 0
  el.textContent = hints[index]
  const timer = setInterval(() => {
    index = (index + 1) % hints.length
    el.textContent = hints[index]
  }, 1400)
  return () => clearInterval(timer)
}
