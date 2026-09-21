/**
 * multipart 请求体：File 原样带上，其余转成字符串；空值直接跳过（后端有默认值）。
 * 数组展开成同名重复字段，对应后端的 `list[UploadFile]`。
 */
export function toFormData(body: unknown): FormData {
  const form = new FormData()
  for (const [key, value] of Object.entries(body as Record<string, unknown>)) {
    if (value === undefined || value === null) continue
    for (const item of Array.isArray(value) ? value : [value])
      form.append(key, item instanceof File ? item : String(item))
  }
  return form
}
