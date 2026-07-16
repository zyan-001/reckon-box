// 审查迭代视图

import { store } from '../state.js'
import { goTo } from '../router.js'

export function ReviewView() {
  const container = document.createElement('div')
  container.className = 'main'

  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">审查迭代</div>
      <div class="page-desc">根据审查建议调整设计，重新运行模拟。</div>
    </div>
    <div id="review-content"></div>
    <div style="margin-top: 1rem;">
      <button id="btn-back-design" class="btn btn-secondary">返回设计器调整</button>
    </div>
  `

  container.querySelector('#btn-back-design').onclick = () => goTo('designer')
  renderReview(container.querySelector('#review-content'))

  return container
}

function renderReview(el) {
  const report = store.simulationReport
  const convergence = store.convergenceResult
  const review = store.reviewResult

  if (!report) {
    el.innerHTML = '<div class="empty-state">请先运行模拟</div>'
    return
  }

  let html = ''

  // 收敛检查
  if (convergence) {
    html += `
      <div class="card">
        <div class="card-title">收敛检查</div>
        <div style="margin-bottom: 1rem;">
          <span class="badge ${convergence.converged ? 'badge-success' : 'badge-warning'}">
            ${convergence.converged ? '已收敛' : '未收敛'}
          </span>
          <span style="color: var(--text-secondary); margin-left: 0.5rem;">${convergence.summary}</span>
        </div>
        <table style="width: 100%; font-size: 0.875rem; border-collapse: collapse;">
          <thead>
            <tr style="border-bottom: 1px solid var(--border);">
              <th style="text-align: left; padding: 0.5rem;">指标</th>
              <th style="text-align: left; padding: 0.5rem;">目标</th>
              <th style="text-align: left; padding: 0.5rem;">实际</th>
              <th style="text-align: left; padding: 0.5rem;">状态</th>
            </tr>
          </thead>
          <tbody>
            ${convergence.gaps.map((g) => `
              <tr style="border-bottom: 1px solid var(--border);">
                <td style="padding: 0.5rem;">${g.metric}</td>
                <td style="padding: 0.5rem;">${g.target}</td>
                <td style="padding: 0.5rem;">${g.actual}</td>
                <td style="padding: 0.5rem;">
                  <span class="badge ${g.passed ? 'badge-success' : 'badge-danger'}">${g.passed ? '通过' : '未通过'}</span>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `
  }

  // 审查建议
  if (review && review.issues.length > 0) {
    html += `
      <div class="card">
        <div class="card-title">审查建议</div>
        ${review.issues.map((i) => `
          <div class="list-item">
            <div style="display: flex; justify-content: space-between; margin-bottom: 0.375rem;">
              <div>
                <strong>${i.component}</strong>
                <span class="badge ${i.severity === 'high' ? 'badge-danger' : i.severity === 'medium' ? 'badge-warning' : 'badge-info'}" style="margin-left: 0.5rem;">${i.severity}</span>
              </div>
              <span class="badge ${i.is_guidance ? 'badge-info' : 'badge-warning'}">${i.is_guidance ? '设计决策引导' : '知识卡片'}</span>
            </div>
            <div style="margin-bottom: 0.25rem;">${i.description}</div>
            <div style="color: var(--accent); font-size: 0.8125rem;">建议：${i.suggestion}</div>
          </div>
        `).join('')}
      </div>
    `
  } else if (review) {
    html += `
      <div class="card">
        <div class="card-title">审查建议</div>
        <div class="empty-state">${review.summary}</div>
      </div>
    `
  }

  // 知识卡片
  if (review && review.knowledge_cards.length > 0) {
    html += `
      <div class="card">
        <div class="card-title">知识点</div>
        ${review.knowledge_cards.map((card) => `
          <div class="list-item">
            <strong>${card.title}</strong>
            <div style="color: var(--text-secondary); font-size: 0.8125rem; margin-top: 0.25rem;">${card.concept}</div>
            <div style="margin-top: 0.375rem; font-size: 0.875rem;">${card.explanation}</div>
          </div>
        `).join('')}
      </div>
    `
  }

  el.innerHTML = html
}
