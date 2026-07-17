// 沙盒设计器视图

import { api } from '../api.js'
import { store, getComponent, setComponent, removeComponent, renameComponent } from '../state.js'
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
  
  canvas.innerHTML = `
    <svg class="canvas-svg" id="canvas-svg">
      <defs>
        <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="var(--text-secondary)" />
        </marker>
      </defs>
    </svg>
    <div id="canvas-nodes"></div>
  `
  const nodesContainer = canvas.querySelector('#canvas-nodes')
  
  names.forEach((name) => {
    const node = createNode(name)
    nodesContainer.appendChild(node)
  })

  // 延迟一帧绘制连线，确保节点已经渲染并有尺寸
  requestAnimationFrame(() => {
    updateEdges()
  })
}

function updateEdges() {
  const svg = document.getElementById('canvas-svg')
  if (!svg) return
  
  // 清除旧的连线，保留 defs
  const lines = svg.querySelectorAll('line')
  lines.forEach(l => l.remove())

  Object.values(store.components).forEach(comp => {
    if (!comp.dependency_edges) return
    comp.dependency_edges.forEach(edge => {
      const targetComp = store.components[edge.target]
      if (!targetComp) return
      
      // 节点宽度 140px，高度约 80px，取中心点
      const x1 = comp.x + 70
      const y1 = comp.y + 40
      const x2 = targetComp.x + 70
      const y2 = targetComp.y + 40

      // 计算缩短后的终点，避免箭头被节点遮挡
      const dx = x2 - x1
      const dy = y2 - y1
      const len = Math.sqrt(dx * dx + dy * dy)
      if (len === 0) return
      
      const shorten = 60 // 缩短距离，大致为节点半径
      const endX = x2 - (dx / len) * shorten
      const endY = y2 - (dy / len) * shorten

      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line')
      line.setAttribute('x1', x1)
      line.setAttribute('y1', y1)
      line.setAttribute('x2', endX)
      line.setAttribute('y2', endY)
      line.setAttribute('stroke', 'var(--text-secondary)')
      line.setAttribute('stroke-width', '2')
      line.setAttribute('marker-end', 'url(#arrowhead)')
      
      if (edge.communication_mode === 'async') {
        line.setAttribute('stroke-dasharray', '5,5')
      }
      
      svg.appendChild(line)
    })
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
    updateEdges()
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
    
    <div id="prop-dependencies" class="card" style="margin-top: 1rem; padding: 1rem; background: var(--bg);">
      <div class="card-title" style="font-size: 0.875rem; margin-bottom: 0.75rem;">下游依赖关系</div>
      <div id="edges-list"></div>
      <div id="add-edge-container"></div>
    </div>

    <div class="card" style="margin-top: 1rem;">
      <div class="card-title">MicroSkill</div>
      <div class="form-group">
        <label class="form-label">策略模板</label>
        <select id="prop-strategy" class="form-select"></select>
      </div>
      <div id="prop-strategy-params"></div>
      <button id="btn-parse-skill" class="btn btn-secondary" style="width: 100%; margin-top: 0.5rem;">用 LLM 解析描述</button>
      <div id="skill-io-status" class="io-status" style="display: none;"></div>
    </div>
  `

  const paramsEl = panel.querySelector('#prop-params')
  
  // 绑定名称和描述变化
  const nameInput = panel.querySelector('#prop-name')
  if (nameInput) {
    nameInput.onchange = () => {
      const newName = nameInput.value.trim()
      if (newName && newName !== comp.name) {
        const success = renameComponent(comp.name, newName)
        if (!success) {
          alert('名称无效或已存在')
          nameInput.value = comp.name
        } else {
          refreshCanvas()
          refreshProperties()
        }
      } else {
        nameInput.value = comp.name
      }
    }
  }

  const descInput = panel.querySelector('#prop-description')
  if (descInput) {
    descInput.onchange = () => {
      comp.description = descInput.value.trim()
    }
  }

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

  // 渲染依赖关系
  const edgesListEl = panel.querySelector('#edges-list')
  const edges = comp.dependency_edges || []
  
  if (edges.length === 0) {
    edgesListEl.innerHTML = '<div style="color: var(--text-secondary); font-size: 0.75rem; margin-bottom: 0.5rem;">无下游依赖</div>'
  } else {
    edgesListEl.innerHTML = edges.map((edge, idx) => `
      <div style="padding: 0.5rem; border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 0.5rem; background: var(--bg-secondary);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
          <strong style="font-size: 0.8125rem; color: var(--accent);">-> ${edge.target}</strong>
          <button class="btn btn-danger btn-remove-edge" data-idx="${idx}" style="padding: 0.125rem 0.375rem; font-size: 0.75rem;">删除</button>
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem;">
          <div>
            <label class="form-label" style="font-size: 0.75rem;">模式</label>
            <select class="form-select edge-mode" data-idx="${idx}" style="padding: 0.25rem; font-size: 0.75rem;">
              <option value="sync" ${edge.communication_mode === 'sync' ? 'selected' : ''}>同步</option>
              <option value="async" ${edge.communication_mode === 'async' ? 'selected' : ''}>异步</option>
            </select>
          </div>
          <div>
            <label class="form-label" style="font-size: 0.75rem;">超时 (ms)</label>
            <input type="number" class="form-input edge-timeout" data-idx="${idx}" value="${edge.timeout_ms || 2000}" style="padding: 0.25rem; font-size: 0.75rem;">
          </div>
        </div>
      </div>
    `).join('')
  }

  // 绑定依赖关系的事件
  panel.querySelectorAll('.btn-remove-edge').forEach(btn => {
    btn.onclick = () => {
      comp.dependency_edges.splice(btn.dataset.idx, 1)
      refreshCanvas()
      refreshProperties()
    }
  })
  panel.querySelectorAll('.edge-mode').forEach(sel => {
    sel.onchange = () => {
      comp.dependency_edges[sel.dataset.idx].communication_mode = sel.value
      updateEdges()
    }
  })
  panel.querySelectorAll('.edge-timeout').forEach(inp => {
    inp.onchange = () => {
      comp.dependency_edges[inp.dataset.idx].timeout_ms = Number(inp.value)
    }
  })

  // 渲染添加依赖
  const addEdgeContainer = panel.querySelector('#add-edge-container')
  const otherComps = Object.keys(store.components).filter(n => n !== comp.name && !edges.find(e => e.target === n))
  
  if (otherComps.length > 0) {
    addEdgeContainer.innerHTML = `
      <div style="display: flex; gap: 0.5rem; margin-top: 0.5rem;">
        <select id="new-edge-target" class="form-select" style="flex: 1; padding: 0.375rem; font-size: 0.8125rem;">
          ${otherComps.map(n => `<option value="${n}">${n}</option>`).join('')}
        </select>
        <button id="btn-add-edge" class="btn btn-secondary" style="padding: 0.375rem 0.75rem; font-size: 0.8125rem;">添加依赖</button>
      </div>
    `
    panel.querySelector('#btn-add-edge').onclick = () => {
      const target = panel.querySelector('#new-edge-target').value
      if (!comp.dependency_edges) comp.dependency_edges = []
      comp.dependency_edges.push({
        source: comp.name,
        target: target,
        communication_mode: 'sync',
        timeout_ms: 2000
      })
      refreshCanvas()
      refreshProperties()
    }
  }

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
    const parseBtn = panel.querySelector('#btn-parse-skill')
    const ioStatus = panel.querySelector('#skill-io-status')
    parseBtn.disabled = true
    parseBtn.textContent = '解析中...'
    ioStatus.style.display = 'block'
    const stopHint = startHintRotation(ioStatus, [
      '正在解析行为语义与调用步骤...',
      '正在匹配最合适的策略模板...',
      '正在生成可编辑的参数与断言...',
    ])
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
    } finally {
      stopHint()
      ioStatus.style.display = 'none'
      parseBtn.disabled = false
      parseBtn.textContent = '用 LLM 解析描述'
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

function startHintRotation(el, hints) {
  let index = 0
  el.textContent = hints[index]
  const timer = setInterval(() => {
    index = (index + 1) % hints.length
    el.textContent = hints[index]
  }, 1400)
  return () => clearInterval(timer)
}
