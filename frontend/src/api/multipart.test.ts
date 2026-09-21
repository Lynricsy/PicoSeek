import { describe, expect, it } from 'vitest'
import { toFormData } from './multipart'

describe('toFormData', () => {
  it('数组展开成同名重复字段并保持顺序', () => {
    const a = new File(['a'], 'a.pdf', { type: 'application/pdf' })
    const b = new File(['b'], 'b.pdf', { type: 'application/pdf' })

    const form = toFormData({ files: [a, b], question: '样本量多少' })

    expect(form.getAll('files')).toEqual([a, b])
    expect(form.get('question')).toBe('样本量多少')
  })

  it('空值跳过，非字符串转成字符串', () => {
    const form = toFormData({ doi: undefined, title: null, year: 2024 })

    expect(form.has('doi')).toBe(false)
    expect(form.has('title')).toBe(false)
    expect(form.get('year')).toBe('2024')
  })
})
