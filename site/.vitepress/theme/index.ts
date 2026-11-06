import DefaultTheme from 'vitepress/theme'
import './style.css'

import HomeHero from './components/HomeHero.vue'
import Mermaid from './components/Mermaid.vue'
import StackDiagram from './components/StackDiagram.vue'
import ArchitectureDiagram from './components/ArchitectureDiagram.vue'
import BaselineMatrix from './components/BaselineMatrix.vue'
import SimReplay from './components/SimReplay.vue'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('HomeHero', HomeHero)
    app.component('Mermaid', Mermaid)
    app.component('StackDiagram', StackDiagram)
    app.component('ArchitectureDiagram', ArchitectureDiagram)
    app.component('BaselineMatrix', BaselineMatrix)
    app.component('SimReplay', SimReplay)
  },
}
