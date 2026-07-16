// 登录视图

import { api } from '../api.js'
import { store } from '../state.js'
import { goTo } from '../router.js'

export function LoginView() {
  const container = document.createElement('div')
  container.className = 'main'

  container.innerHTML = `
    <div style="max-width: 400px; margin: 4rem auto;">
      <div class="card" style="padding: 2rem;">
        <div class="page-title" style="text-align: center; margin-bottom: 0.5rem;">ReckonBox</div>
        <div class="page-desc" style="text-align: center; margin-bottom: 2rem;">建构主义教育沙盒</div>

        <div class="form-group">
          <label class="form-label">用户名</label>
          <input id="login-username" class="form-input" type="text" placeholder="admin / tourist" autocomplete="username">
        </div>
        <div class="form-group">
          <label class="form-label">密码</label>
          <input id="login-password" class="form-input" type="password" placeholder="从 .env 获取" autocomplete="current-password">
        </div>

        <button id="btn-login" class="btn btn-primary" style="width: 100%; justify-content: center;">登录</button>

        <div id="login-error" style="color: var(--danger); margin-top: 1rem; font-size: 0.875rem; text-align: center;"></div>

        <div style="margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid var(--border);">
          <div style="font-size: 0.8125rem; color: var(--text-secondary);">
            <div style="margin-bottom: 0.5rem;"><strong>角色说明：</strong></div>
            <div style="margin-bottom: 0.375rem;">admin - 完整访问，无调用限制</div>
            <div>tourist - 仅可调用 LLM 3 次/会话</div>
          </div>
        </div>
      </div>
    </div>
  `

  const usernameInput = container.querySelector('#login-username')
  const passwordInput = container.querySelector('#login-password')
  const loginBtn = container.querySelector('#btn-login')
  const errorEl = container.querySelector('#login-error')

  async function doLogin() {
    const username = usernameInput.value.trim()
    const password = passwordInput.value.trim()
    if (!username || !password) {
      errorEl.textContent = '请输入用户名和密码'
      return
    }
    errorEl.textContent = ''
    loginBtn.disabled = true
    loginBtn.textContent = '登录中...'

    try {
      const res = await api.login(username, password)
      store.authenticated = true
      store.role = res.role
      store.token = res.token
      store.llmRemaining = res.remaining
      store.llmLimit = res.role === 'tourist' ? 3 : -1
      goTo('scenario')
    } catch (err) {
      loginBtn.disabled = false
      loginBtn.textContent = '登录'
      if (err.message.includes('401')) {
        errorEl.textContent = '用户名或密码错误'
      } else {
        errorEl.textContent = err.message
      }
    }
  }

  loginBtn.onclick = doLogin
  passwordInput.onkeydown = (e) => {
    if (e.key === 'Enter') doLogin()
  }

  return container
}
