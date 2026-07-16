// ReckonBox 前端 API 调用层

const BASE_URL = '' // Vite 代理会自动转发 /api

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (res.status === 401) {
    throw new Error('AUTH_REQUIRED')
  }
  if (res.status === 429) {
    const data = await res.json()
    const err = new Error('RATE_LIMITED')
    err.remaining = data.remaining || 0
    throw err
  }
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  // 从响应头读取剩余次数
  const remaining = res.headers.get('X-LLM-Remaining')
  if (remaining !== null) {
    window.__llmRemaining = Number(remaining)
  }
  return res.json()
}

export const api = {
  health() {
    return request('/api/health')
  },
  templates() {
    return request('/api/templates')
  },
  domainPacks() {
    return request('/api/domain-packs')
  },
  parseScenario(nlpInput) {
    return request('/api/scenario/parse', {
      method: 'POST',
      body: JSON.stringify({ nlp_input: nlpInput }),
    })
  },
  parseSkill(nlDescription, stepTree = null) {
    return request('/api/skill/parse', {
      method: 'POST',
      body: JSON.stringify({ nl_description: nlDescription, step_tree: stepTree }),
    })
  },
  runSimulation(components, scenarioId = null, customScenario = null) {
    return request('/api/simulation/run', {
      method: 'POST',
      body: JSON.stringify({
        components,
        scenario_id: scenarioId,
        custom_scenario: customScenario,
      }),
    })
  },
  checkConvergence(report, blackboxSpec = null) {
    return request('/api/simulation/convergence', {
      method: 'POST',
      body: JSON.stringify({ report, blackbox_spec: blackboxSpec }),
    })
  },
  reviewSimulation(report, microSkills = null, blackboxSpec = null) {
    return request('/api/simulation/review', {
      method: 'POST',
      body: JSON.stringify({ report, micro_skills: microSkills, blackbox_spec: blackboxSpec }),
    })
  },
  // 认证
  login(username, password) {
    return request('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
  },
  logout() {
    return request('/api/auth/logout', {
      method: 'POST',
    })
  },
  authStatus() {
    return request('/api/auth/status')
  },
}
