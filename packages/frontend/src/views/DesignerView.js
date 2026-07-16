// 沙盒设计器视图

import { api } from '../api.js'
import { store, getComponent, setComponent, removeComponent } from '../state.js'
import { goTo } from '../router.js'

const componentIcons = {
  gateway: 'G',
  service: 'S',
  database: 'DB',
  cache: 'C',
  message_queue: 'MQ',
  scheduled_task: 'T',
}

const defaultComponentData = (type) => ({
  name: `${type}_${Date.now()}`,
  type,
  replicas: 2,
  cpu_per_replica: 100,
  memory_limit: 1024,
  memory_per_request: 5,
  queue_capacity: 500,
  timeout_ms: 2000,
  retry_count: 0,
  has_circuit_breaker: false,
  has_rate_limiter: false,
  rate_limit: 0,
  dependencies: [],
  dependency_edges: [],
  micro_skill: null,
  description: '',
  x: 50 + Math.random() * 300,
  y: 50 + Math.random() * 200,
})

export function DesignerView() {
  const container = document.createElement('div')
  container.className = 'main'
  container.style.display = 'flex'
  container.style.flexDirection = 'column'
  container.style.minHeight = '0'

  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">沙盒设计</div>
      <div class="page-desc">点击左侧组件添加，拖拽移动，点击编辑属性。</div>
    </div>
    <div class="designer" style="flex: 1;">
      <div class="palette">
        <div class="properties-title">组件面板</div>
        <div id="palette-list"></div>
      </div>
      <div class="canvas" id="designer-canvas">
        <div class="canvas-placeholder">从左侧选择组件点击添加</div>
      </div>
      <div class="properties" id="properties-panel">
        <div class="properties-title">属性面板</div>
        <div class="empty-state">选择一个组件</div>
      </div>
    </div>
    <div style="margin-top: 1rem; flex-shrink: 0;">
      <button id="btn-run-simulation" class="btn btn-primary">运行模拟</button>
    </div>
  `

  renderPalette(container.querySelector('#palette-list'))
  refreshCanvas()

  container.querySelector('#btn-run-simulation').onclick = () => {
    goTo('simulation')
  }

  return container
}

function renderPalette(el) {
  if (!el) return
  if (!store.domainPack) {
    if (store.backendStatus === 'error') {
      el.innerHTML = '<div class="empty-state" style="padding: 1rem;">后端未连接，无法加载组件</div>'
    } else {
      el.innerHTML = '<div class="empty-state" style="padding: 1rem;">加载领域包...</div>'
    }
    return
  }

  el.innerHTML = ''
  Object.entries(store.domainPack.component_types).forEach(([typeId, spec]) => {
    const item = document.createElement('div')
    item.className = 'palette-item'
    item.innerHTML = `<span>${componentIcons[typeId] || '?'}</span> ${spec.display_name}`
    item.onclick = () => addComponent(typeId)
    el.appendChild(item)
  })
}

function addComponent(type) {
  const comp = defaultComponentData(type)
  let idx = 1
  while (getComponent(comp.name)) {
    comp.name = `${type}_${idx}_${Date.now() % 1000}`
    idx++
  }
  setComponent(comp.name, comp)
  store.selectedComponent = comp.name
  refreshCanvas()
  refreshProperties()
}

function refreshCanvas() {
  const canvas = document.getElementById('designer-canvas')
  if (!canvas) return
  const names = Object.keys(store.components)
  if (names.length === 0) {
    canvas.innerHTML = '<div class="canvas-placeholder">从左侧选择组件点击添加</div>'
    return
  }
  canvas.innerHTML = ''
  names.forEach((name) => {
    const node = createNode(name)
    canvas.appendChild(node)
  })
}

function createNode(name) {
  const comp = getComponent(name)
  const node = document.createElement('div')
  node.className = `node ${store.selectedComponent === name ? 'selected' : ''}`
  node.style.left = `${comp.x}px`
  node.style.top = `${comp.y}px`

  const strategyName = comp.micro_skill?.strategy_template_id || 'sync_direct'
  node.innerHTML = `
    <div class="node-title">${comp.name}</div>
    <div class="node-type">${comp.type}</div>
    <div class="node-strategy">${strategyName}</div>
  `
  node.onclick = (e) => {
    e.stopPropagation()
    store.selectedComponent = name
    refreshCanvas()
    refreshProperties()
  }

  let dragging = false
  let startX, startY, origX, origY
  node.onmousedown = (e) => {
    dragging = true
    startX = e.clientX
    startY = e.clientY
    origX = comp.x
    origY = comp.y
    e.preventDefault()
  }
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return
    comp.x = origX + e.clientX - startX
    comp.y = origY + e.clientY - startY
    node.style.left = `${comp.x}px`
    node.style.top = `${comp.y}px`
  })
  window.addEventListener('mouseup', () => {
    dragging = false
  })

  return node
}

function refreshProperties() {
  const panel = document.getElementById('properties-panel')
  if (!panel) return
  if (!store.selectedComponent) {
    panel.innerHTML = `
      <div class="properties-title">属性面板</div>
      <div class="empty-state">选择一个组件</div>
    `
    return
  }
  const comp = getComponent(store.selectedComponent)
  if (!comp) return

  const typeSpec = store.domainPack?.component_types?.[comp.type]
  const params = typeSpec?.params || {}

  panel.innerHTML = `
    <div class="properties-title">${comp.name}</div>
    <div style="margin-bottom: 1rem;">
      <button id="btn-delete-comp" class="btn btn-danger" style="width: 100%;">删除组件</button>
    </div>
    <div class="form-group">
      <label class="form-label">名称</label>
      <input id="prop-name" class="form-input" value="${comp.name}">
    </div>
    <div class="form-group">
      <label class="form-label">描述（自然语言）</label>
      <textarea id="prop-description" class="form-textarea" rows="3">${comp.description || ''}</textarea>
    </div>
    <div id="prop-params"></div>
    <div class="card" style="margin-top: 1rem;">
      <div class="card-title">MicroSkill</div>
      <div class="form-group">
        <label class="form-label">策略模板</label>
        <select id="prop-strategy" class="form-select"></select>
      </div>
      <div id="prop-strategy-params"></div>
      <button id="btn-parse-skill" class="btn btn-secondary" style="width: 100%; margin-top: 0.5rem;">用 LLM 解析描述</button>
    </div>
  `

  const paramsEl = panel.querySelector('#prop-params')
  Object.entries(params).forEach(([paramName, paramSpec]) => {
    const value = comp[paramName] !== undefined ? comp[paramName] : paramSpec.default
    const div = document.createElement('div')
    div.className = 'form-group'
    if (paramSpec.type === 'bool') {
      div.innerHTML = `
        <label class="form-label">
          <input type="checkbox" id="prop-${paramName}" ${value ? 'checked' : ''}>
          ${paramName}
        </label>
      `
    } else {
      const inputType = paramSpec.type === 'int' ? 'number' : 'text'
      div.innerHTML = `
        <label class="form-label">${paramName}${paramSpec.unit ? ` (${paramSpec.unit})` : ''}</label>
        <input id="prop-${paramName}" class="form-input" type="${inputType}" value="${value}">
      `
    }
    paramsEl.appendChild(div)
  })

  // 绑定参数变化
  Object.entries(params).forEach(([paramName, paramSpec]) => {
    const el = panel.querySelector(`#prop-${paramName}`)
    if (!el) return
    el.onchange = () => {
      if (paramSpec.type === 'bool') {
        comp[paramName] = el.checked
      } else if (paramSpec.type === 'int') {
        comp[paramName] = Number(el.value)
      } else {
        comp[paramName] = el.value
      }
    }
  })

  // 策略模板选择
  const strategySelect = panel.querySelector('#prop-strategy')
  Object.entries(store.strategyTemplates).forEach(([tid, tpl]) => {
    const opt = document.createElement('option')
    opt.value = tid
    opt.textContent = tpl.description || tid
    if (comp.micro_skill?.strategy_template_id === tid) opt.selected = true
    strategySelect.appendChild(opt)
  })
  strategySelect.onchange = () => {
    comp.micro_skill = {
      strategy_template_id: strategySelect.value,
      params: {},
      assertions: [],
      step_tree: null,
    }
    refreshStrategyParams(panel, comp)
    refreshCanvas()
  }

  refreshStrategyParams(panel, comp)

  panel.querySelector('#btn-delete-comp').onclick = () => {
    removeComponent(comp.name)
    refreshCanvas()
    refreshProperties()
  }

  panel.querySelector('#btn-parse-skill').onclick = async () => {
    const desc = panel.querySelector('#prop-description').value.trim()
    if (!desc) return
    try {
      const res = await api.parseSkill(desc, null)
      if (res.ok && res.skill) {
        comp.micro_skill = res.skill
        comp.description = desc
        refreshCanvas()
        refreshProperties()
      }
    } catch (err) {
      alert(err.message)
    }
  }
}

function refreshStrategyParams(panel, comp) {
  const container = panel.querySelector('#prop-strategy-params')
  if (!container) return
  const skill = comp.micro_skill
  if (!skill) {
    container.innerHTML = '<div class="empty-state" style="padding: 1rem;">请选择策略模板</div>'
    return
  }
  const tpl = store.strategyTemplates[skill.strategy_template_id]
  if (!tpl) return

  container.innerHTML = ''
  Object.entries(tpl.params_schema).forEach(([paramName, typeName]) => {
    const value = skill.params?.[paramName] !== undefined ? skill.params[paramName] : ''
    const div = document.createElement('div')
    div.className = 'form-group'
    div.innerHTML = `
      <label class="form-label">${paramName}</label>
      <input id="skill-param-${paramName}" class="form-input" type="${typeName === 'int' ? 'number' : 'text'}" value="${value}">
    `
    container.appendChild(div)
  })

  Object.keys(tpl.params_schema).forEach((paramName) => {
    const el = container.querySelector(`#skill-param-${paramName}`)
    el.onchange = () => {
      if (!skill.params) skill.params = {}
      skill.params[paramName] = tpl.params_schema[paramName] === 'int' ? Number(el.value) : el.value
    }
  })
}

export function refreshDesigner() {
  renderPalette(document.querySelector('#palette-list'))
  refreshCanvas()
  refreshProperties()
}
