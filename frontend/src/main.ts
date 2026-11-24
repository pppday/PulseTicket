/**
 * 前端应用启动入口（整个 Vue 3 项目的“总开关”）。
 * 负责加载全局字体（Inter）、Tailwind 全局样式表，并将根组件 App 挂载到 index.html 的 #app 节点上。
 */
import { createApp } from 'vue'
import '@fontsource-variable/inter'
import './style.css'
import App from './App.vue'

// 创建 Vue 应用实例并挂载到页面根节点
createApp(App).mount('#app')
