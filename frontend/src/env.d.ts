/**
 * TypeScript 环境类型声明文件。
 * 告诉 TS 编译器认识 Vite 客户端内置环境变量（import.meta.env 等）以及 .vue 单文件组件，避免编辑器报红。
 */
/// <reference types="vite/client" />

// 声明所有 .vue 文件默认导出为 Vue 组件类型，方便在其他 .ts/.vue 文件中 import
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}
