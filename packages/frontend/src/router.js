// 简单前端路由

import { store } from './state.js'
import { Sidebar, updateSidebarActive, updateSidebarStatus } from './components/Sidebar.js'
import { LoginView } from './views/LoginView.js'
import { ScenarioView } from './views/ScenarioView.js'
import { BlackBoxView } from './views/BlackBoxView.js'
import { DesignerView, refreshDesigner } from './views/DesignerView.js'
import { SimulationView } from './views/SimulationView.js'
import { ReviewView } from './views/ReviewView.js'

const views = {
  login: LoginView,
  scenario: ScenarioView,
  blackbox: BlackBoxView,
  designer: DesignerView,
  simulation: SimulationView,
  review: ReviewView,
}

export function goTo(viewName) {
  if (!views[viewName]) return
  store.currentView = viewName
  renderApp()
}

export function renderApp() {
  const app = document.getElementById('app')
  app.innerHTML = ''

  // 未登录 → 显示登录页
  if (!store.authenticated) {
    const view = LoginView()
    app.appendChild(view)
    return
  }

  const layout = document.createElement('div')
  layout.className = 'layout'

  const sidebar = Sidebar()
  const main = document.createElement('div')
  main.id = 'main-view'
  main.style.flex = '1'

  const View = views[store.currentView]
  if (View) {
    main.appendChild(View())
  }

  layout.appendChild(sidebar)
  layout.appendChild(main)
  app.appendChild(layout)

  updateSidebarActive()
  updateSidebarStatus()

  if (store.currentView === 'designer') {
    refreshDesigner()
  }
}
