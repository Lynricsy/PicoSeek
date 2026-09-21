import { describe, expect, it } from 'vitest'
import { EMPTY_LIVE } from '@/lib/jobLive'
import { askRailNodes, interpretSteps } from './askRail'

describe('askRailNodes', () => {
  it('把写库排在综合成稿之后，并在答案出来前保持未开始', () => {
    const nodes = askRailNodes(EMPTY_LIVE, { useKb: true, kb: null })
    expect(nodes.map((n) => n.key)).toEqual([
      'queries',
      'search',
      'fulltext',
      'read',
      'synthesize',
      'kb',
    ])
    expect(nodes[5]).toMatchObject({
      status: 'todo',
      hint: '答案交付后在后台进行',
    })
  })

  it('关掉写库时不出现该节点', () => {
    const nodes = askRailNodes(EMPTY_LIVE, { useKb: false, kb: null })
    expect(nodes.some((n) => n.key === 'kb')).toBe(false)
  })

  it('答案已出：前序阶段一律完成，写库按后台任务给出计数', () => {
    const nodes = askRailNodes(EMPTY_LIVE, {
      useKb: true,
      kb: { status: 'queued', current: 0, total: 10 },
      settled: true,
    })
    expect(nodes.slice(0, 5).every((n) => n.status === 'done')).toBe(true)
    expect(nodes[5]).toMatchObject({
      status: 'waiting',
      hint: '等待后台，优先执行新问答（0/10 篇）',
    })
  })

  it('后台运行、成功与失败分别给出进度、总数与不影响答案的说明', () => {
    const at = (kb: Parameters<typeof askRailNodes>[1]['kb']) =>
      askRailNodes(EMPTY_LIVE, { useKb: true, kb, settled: true })[5]
    expect(at({ status: 'running', current: 3, total: 10 })).toMatchObject({
      status: 'running',
      hint: '后台写入中（3/10 篇）',
    })
    expect(at({ status: 'succeeded', current: 10, total: 10 })).toMatchObject({
      status: 'done',
      hint: '已写入 10 篇',
    })
    expect(at({ status: 'failed', current: 4, total: 10 })).toMatchObject({
      status: 'failed',
      hint: '写入失败，答案不受影响',
    })
  })

  it('文献解读只有解析与生成两节点，第二节点按模式换文案', () => {
    const overview = askRailNodes(EMPTY_LIVE, {
      useKb: true,
      kb: null,
      steps: interpretSteps('overview'),
    })
    expect(overview.map((n) => [n.key, n.label])).toEqual([
      ['fulltext', '解析 PDF'],
      ['synthesize', '梳理文献关系'],
      ['kb', '写入知识库'],
    ])
    const qa = askRailNodes(EMPTY_LIVE, {
      useKb: false,
      kb: null,
      steps: interpretSteps('qa'),
    })
    expect(qa.map((n) => n.label)).toEqual(['解析 PDF', '生成回答'])
  })
})
