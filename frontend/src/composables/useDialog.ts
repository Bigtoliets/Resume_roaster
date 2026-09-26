/**
 * 主题化对话框：替代 window.confirm / window.prompt。
 *
 * 用法（在任意组件里）：
 *   if (!(await confirmDialog({ title: '删除会话？', danger: true }))) return
 *   const name = await promptDialog({ title: '重命名会话', value: session.title })
 *
 * 界面由全局挂载的 <DialogHost /> 渲染，因此视觉与整个「卷宗」主题一致。
 */

import { reactive } from 'vue'

export interface DialogOptions {
  title: string
  description?: string
  confirmText?: string
  cancelText?: string
  /** 危险操作：确认按钮用朱红实底 */
  danger?: boolean
  /** prompt 模式用 */
  mode?: 'confirm' | 'prompt'
  label?: string
  value?: string
  placeholder?: string
  /** 空值不允许提交 */
  required?: boolean
  /** 提交时的校验提示 */
  hint?: string
}

export const dialogState = reactive({
  open: false,
  mode: 'confirm' as 'confirm' | 'prompt',
  title: '',
  description: '',
  confirmText: '确认',
  cancelText: '取消',
  danger: false,
  label: '',
  value: '',
  placeholder: '',
  required: false,
  hint: '',
  error: '',
})

type Resolver = (value: boolean | string | null) => void
let resolver: Resolver | null = null

function open(options: DialogOptions): Promise<boolean | string | null> {
  // 上一个对话框还没关就再开一个：旧的按取消处理，避免 Promise 悬挂
  if (resolver) {
    resolver(dialogState.mode === 'prompt' ? null : false)
    resolver = null
  }
  Object.assign(dialogState, {
    mode: 'confirm',
    description: '',
    confirmText: '确认',
    cancelText: '取消',
    danger: false,
    label: '',
    value: '',
    placeholder: '',
    required: false,
    hint: '',
    error: '',
    ...options,
    open: true,
  })
  return new Promise((resolve) => {
    resolver = resolve
  })
}

export function confirmDialog(options: DialogOptions): Promise<boolean> {
  return open({ ...options, mode: 'confirm' }).then((value) => value === true)
}

export function promptDialog(options: DialogOptions): Promise<string | null> {
  return open({ ...options, mode: 'prompt' }).then((value) =>
    typeof value === 'string' ? value : null,
  )
}

export function resolveDialog(result: boolean | string | null): void {
  dialogState.open = false
  const current = resolver
  resolver = null
  current?.(result)
}
