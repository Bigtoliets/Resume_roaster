/**
 * 深拷贝一份 JSON 数据。
 *
 * 不要用 `structuredClone`：Vue 的响应式对象是 Proxy，`structuredClone(proxy)` 会抛
 * `DataCloneError`。而这类调用通常紧跟在 `selectedId = row.id` 之后，一旦抛错，
 * 表现就是"左边选中了、右边表单还是空的"——错误被吞掉，很难查。
 */
export function cloneDeep<T>(value: T): T {
  if (value === undefined || value === null) return value
  return JSON.parse(JSON.stringify(value)) as T
}
