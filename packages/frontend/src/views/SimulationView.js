// 性能模拟视图

import { api } from '../api.js'
import { store } from '../state.js'
import { goTo } from '../router.js'

export function SimulationView() {
  const container = document.createElement('div')
  container.className = 'main'

  const scenarios = store.domainPack?.scenarios || []

  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">性能模拟</div>
      <div class="page-desc">选择压力场景并运行模拟，观察失败如何涌现。</div>
    </div>

    <div class="card" style="display: flex; gap: 1rem; align-items: flex-end;">
      <div class="form-group" style="flex: 1; margin-bottom: 0;">
        <label class="form-label">压力场景</label>
        <select id="sim-scenario" class="form-select"></select>
      </div>
      <button id="btn-run" class="btn btn-primary">▶ 运行模拟</button>
    </div>

    <div id="sim-result" style="margin-top: 1rem;"></div>
  `

  const select = container.querySelector('#sim-scenario')
  scenarios.forEach((s) => {
    const opt = document.createElement('option')
    opt.value = s.id
    opt.textContent = `${s.name} - ${s.description}`
    select.appendChild(opt)
  })
  if (store.selectedScenarioId) select.value = store.selectedScenarioId

  select.onchange = () => {
    store.selectedScenarioId = select.value
  }

  container.querySelector('#btn-run').onclick = async () => {
    store.loading = true
    const resultEl = container.querySelector('#sim-result')
    resultEl.innerHTML = '<div class="empty-state">运行中...</div>'

    try {
      const components = buildComponentsForApi()
      const res = await api.runSimulation(components, store.selectedScenarioId)
      store.loading = false
      if (res.ok && res.report) {
        store.simulationReport = res.report
        await runConvergence(res.report)
        await runReview(res.report)
        renderResult(resultEl)
      } else {
        resultEl.innerHTML = `<div style="color: var(--danger);">模拟失败</div>`
      }
    } catch (err) {
      store.loading = false
      resultEl.innerHTML = `<div style="color: var(--danger);">${err.message}</div>`
    }
  }

  if (store.simulationReport) {
    renderResult(container.querySelector('#sim-result'))
  }

  return container
}

function buildComponentsForApi() {
  const result = {}
  Object.values(store.components).forEach((comp) => {
    const clone = { ...comp }
    delete clone.x
    delete clone.y
    if (clone.micro_skill) {
      clone.micro_skill = {
        strategy_template_id: clone.micro_skill.strategy_template_id,
        params: clone.micro_skill.params || {},
        assertions: clone.micro_skill.assertions || [],
        step_tree: clone.micro_skill.step_tree,
      }
    }
    // 构建 dependency_edges 默认 sync
    if (!clone.dependency_edges || clone.dependency_edges.length === 0) {
      clone.dependency_edges = clone.dependencies.map((dep) => {
        const depName = typeof dep === 'string' ? dep : dep.target
        return { source: clone.name, target: depName, communication_mode: 'sync', timeout_ms: 2000 }
      })
    }
    result[clone.name] = clone
  })
  return result
}

async function runConvergence(report) {
  try {
    const res = await api.checkConvergence(report, store.blackboxSpec)
    store.convergenceResult = res
  } catch (err) {
    store.convergenceResult = null
  }
}

async function runReview(report) {
  try {
    const microSkills = Object.fromEntries(
      Object.entries(store.components).map(([name, c]) => [name, c.micro_skill])
    )
    const res = await api.reviewSimulation(report, microSkills, store.blackboxSpec)
    store.reviewResult = res
  } catch (err) {
    store.reviewResult = null
  }
}

function renderResult(el) {
  const report = store.simulationReport
  if (!report) return

  const convergence = store.convergenceResult
  const review = store.reviewResult

  let convergenceHtml = ''
  if (convergence) {
    const badgeClass = convergence.converged ? 'badge-success' : 'badge-warning'
    convergenceHtml = `
      <div class="card">
        <div class="card-title">收敛检查</div>
        <div style="margin-bottom: 0.75rem;">
          <span class="badge ${badgeClass}">${convergence.converged ? '已收敛' : '未收敛'}</span>
          <span style="color: var(--text-secondary); margin-left: 0.5rem;">${convergence.summary}</span>
        </div>
        <div style="font-size: 0.875rem;">
          ${convergence.gaps.map((g) => `
            <div class="list-item" style="display: flex; justify-content: space-between;">
              <span>${g.metric}</span>
              <span>${g.actual} / ${g.target}</span>
              <span class="badge ${g.passed ? 'badge-success' : 'badge-danger'}">${g.passed ? '通过' : '未通过'}</span>
            </div>
          `).join('')}
        </div>
      </div>
    `
  }

  let reviewHtml = ''
  if (review) {
    reviewHtml = `
      <div class="card">
        <div class="card-title">LLM 审查</div>
        <div style="margin-bottom: 0.75rem; color: var(--text-secondary); font-size: 0.875rem;">${review.summary}</div>
        ${review.issues.map((i) => `
          <div class="list-item">
            <div style="display: flex; justify-content: space-between; margin-bottom: 0.25rem;">
              <strong>${i.component}</strong>
              <span class="badge ${i.is_guidance ? 'badge-info' : 'badge-warning'}">${i.is_guidance ? '引导' : '知识'}</span>
            </div>
            <div>${i.description}</div>
            <div style="color: var(--accent); font-size: 0.8125rem; margin-top: 0.25rem;">建议：${i.suggestion}</div>
          </div>
        `).join('')}
      </div>
    `
  }

  el.innerHTML = `
    <div class="metric-grid">
      <div class="metric-card">
        <div class="metric-label">总请求</div>
        <div class="metric-value">${report.total_requests}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">成功率</div>
        <div class="metric-value" style="color: ${report.success_rate >= 99 ? 'var(--success)' : 'var(--warning)'}">${report.success_rate}%</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">成功 / 失败</div>
        <div class="metric-value" style="font-size: 1rem; padding-top: 0.375rem;">${report.successful} / ${report.failed}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">崩溃点</div>
        <div class="metric-value" style="color: ${report.assertion_events.length === 0 ? 'var(--success)' : 'var(--danger)'}">${report.assertion_events.length}</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">关键事件时间线</div>
      <div class="event-list">
        ${report.key_events.length === 0
          ? '<div class="empty-state">无关键事件</div>'
          : report.key_events.map((e) => `
            <div class="event-item">
              <span class="event-tick">Tick ${e.tick}</span>
              <span class="event-type">${e.type}</span>
              <span style="flex: 1;">${e.component}: ${e.detail}</span>
            </div>
          `).join('')}
      </div>
    </div>

    ${convergenceHtml}
    ${reviewHtml}

    <div style="margin-top: 1rem;">
      <button id="btn-to-review" class="btn btn-secondary">查看完整审查</button>
    </div>
  `

  const btn = el.querySelector('#btn-to-review')
  if (btn) btn.onclick = () => goTo('review')
}
