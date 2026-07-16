// 黑盒规格确认视图

import { store } from '../state.js'
import { goTo } from '../router.js'

export function BlackBoxView() {
  const container = document.createElement('div')
  container.className = 'main'

  const spec = store.blackboxSpec || {
    functional_points: [],
    performance_bounds: {},
    exception_scenarios: [],
  }

  const bounds = spec.performance_bounds || {}

  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">黑盒规格</div>
      <div class="page-desc">确认系统功能点、性能边界和异常场景。</div>
    </div>

    <div class="card">
      <div class="card-title">功能点</div>
      <div id="functional-points"></div>
    </div>

    <div class="card">
      <div class="card-title">性能边界</div>
      <div class="form-group">
        <label class="form-label">最大 QPS</label>
        <input id="bb-max-qps" class="form-input" type="number" value="${bounds.max_qps || 1000}">
      </div>
      <div class="form-group">
        <label class="form-label">目标延迟 (ms)</label>
        <input id="bb-latency" class="form-input" type="number" value="${bounds.target_latency_ms || 200}">
      </div>
      <div class="form-group">
        <label class="form-label">可用性目标</label>
        <input id="bb-availability" class="form-input" value="${bounds.availability_target || '99.9%'}">
      </div>
    </div>

    <div class="card">
      <div class="card-title">异常场景</div>
      <div id="exception-scenarios"></div>
    </div>

    <button id="btn-confirm-blackbox" class="btn btn-primary" style="margin-top: 0.5rem;">确认并进入设计</button>
  `

  const fpEl = container.querySelector('#functional-points')
  if (spec.functional_points.length === 0) {
    fpEl.innerHTML = '<div class="empty-state">暂无功能点</div>'
  } else {
    spec.functional_points.forEach((fp) => {
      const div = document.createElement('div')
      div.className = 'list-item'
      div.textContent = fp
      fpEl.appendChild(div)
    })
  }

  const excEl = container.querySelector('#exception-scenarios')
  if (spec.exception_scenarios.length === 0) {
    excEl.innerHTML = '<div class="empty-state">暂无异常场景</div>'
  } else {
    spec.exception_scenarios.forEach((ex) => {
      const div = document.createElement('div')
      div.className = 'list-item'
      div.innerHTML = `<strong>${ex.name}</strong><br><span style="color: var(--text-secondary); font-size: 0.8125rem;">${ex.description}</span>`
      excEl.appendChild(div)
    })
  }

  container.querySelector('#btn-confirm-blackbox').onclick = () => {
    store.blackboxSpec = {
      ...spec,
      performance_bounds: {
        max_qps: Number(container.querySelector('#bb-max-qps').value),
        target_latency_ms: Number(container.querySelector('#bb-latency').value),
        availability_target: container.querySelector('#bb-availability').value,
      },
    }
    goTo('designer')
  }

  return container
}
