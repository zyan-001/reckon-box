// 轻量级响应式系统

const listeners = new WeakMap()

export function reactive(obj) {
  const deps = new Map()

  const notify = (key) => {
    const callbacks = deps.get(key)
    if (callbacks) {
      callbacks.forEach((cb) => cb())
    }
  }

  const track = (key) => {
    if (!currentEffect) return
    if (!deps.has(key)) deps.set(key, new Set())
    deps.get(key).add(currentEffect)
    listeners.set(currentEffect, deps)
  }

  return new Proxy(obj, {
    get(target, key) {
      if (typeof target[key] === 'object' && target[key] !== null) {
        return reactive(target[key])
      }
      track(key)
      return target[key]
    },
    set(target, key, value) {
      target[key] = value
      notify(key)
      return true
    },
  })
}

let currentEffect = null

export function watch(getter, callback) {
  currentEffect = () => callback(getter())
  getter() // 触发依赖收集
  currentEffect = null
}

export function onChange(store, key, callback) {
  let effect = null
  currentEffect = () => callback(store[key])
  store[key] // 触发收集
  effect = currentEffect
  currentEffect = null
  return () => {
    // 简单清理
    if (effect) {
      // 实际项目中需要清理 deps 中的 effect，这里简化为不清理
      effect = null
    }
  }
}
