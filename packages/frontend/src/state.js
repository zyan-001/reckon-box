// ReckonBox 全局状态（轻量级 Store，直接内存对象）

import { reactive } from './utils/reactive.js'

export const store = reactive({
  // 后端健康状态
  backendStatus: 'unknown',
  backendVersion: '',

  // 认证状态
  authenticated: false,
  role: null,        // 'admin' | 'tourist' | null
  token: null,
  llmRemaining: -1,  // -1 = 无限 (admin), 0~3 = tourist
  llmLimit: -1,

  // 领域包
  domainPack: null,
  strategyTemplates: {},

  // 当前旅程阶段
  currentView: 'scenario',

  // 场景与黑盒规格
  scenarioInput: '',
  blackboxSpec: null,

  // 设计器状态
  components: {}, // { name -> component }
  selectedComponent: null,

  // 模拟运行状态
  selectedScenarioId: 'normal',
  simulationReport: null,
  convergenceResult: null,
  reviewResult: null,

  // 加载/错误状态
  loading: false,
  error: '',
})

// 辅助函数
export function getComponent(name) {
  return store.components[name]
}

export function setComponent(name, comp) {
  store.components[name] = comp
}

export function removeComponent(name) {
  delete store.components[name]
  if (store.selectedComponent === name) {
    store.selectedComponent = null
  }
  // 清理依赖
  Object.values(store.components).forEach((c) => {
    c.dependencies = c.dependencies.filter((d) => {
      const depName = typeof d === 'string' ? d : d.target
      return depName !== name
    })
    c.dependency_edges = (c.dependency_edges || []).filter(
      (e) => e.target !== name
    )
  })
}
