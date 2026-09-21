import type { JobLive, StageKey } from '@/lib/jobLive'
import type { KbBackground } from './useBackgroundKb'

/** 节点状态：`waiting` 是「排上队但还没轮到」，与还没走到的 `todo` 不是一回事。 */
export type RailStatus =
  'todo' | 'waiting' | 'running' | 'done' | 'failed' | 'cancelled'
export type RailNode = {
  key: string
  label: string
  status: RailStatus
  /** 节点下的一行小字，用来承载「（1/10 篇）」这类计数与原因。 */
  hint?: string
}

// kb 不在这条流水线里跑：API 侧 defer_kb=True，写库排到答案交付之后，
// 所以它是「综合成稿」之后的一个节点，状态来自独立的后台任务。
export const steps: [StageKey, string][] = [
  ['queries', '生成检索式'],
  ['search', '检索文献'],
  ['fulltext', '获取全文'],
  ['read', '逐篇阅读'],
  ['synthesize', '综合成稿'],
]

/** 文献解读只有两段：文献是用户给的，没有检索与逐篇精读。 */
export const interpretSteps = (mode: string): [StageKey, string][] => [
  ['fulltext', '解析 PDF'],
  ['synthesize', mode === 'overview' ? '梳理文献关系' : '生成回答'],
]

const KB_LABEL = '写入知识库'

function kbNode(kb: KbBackground | null): RailNode {
  const node = { key: 'kb', label: KB_LABEL } as const
  if (!kb) return { ...node, status: 'todo', hint: '答案交付后在后台进行' }
  const count = kb.total > 0 ? `（${kb.current}/${kb.total} 篇）` : ''
  switch (kb.status) {
    case 'queued':
      return {
        ...node,
        status: 'waiting',
        hint: `等待后台，优先执行新问答${count}`,
      }
    case 'running':
      return { ...node, status: 'running', hint: `后台写入中${count}` }
    case 'succeeded':
      return {
        ...node,
        status: 'done',
        hint: kb.total > 0 ? `已写入 ${kb.total} 篇` : '已写入',
      }
    case 'failed':
      return { ...node, status: 'failed', hint: '写入失败，答案不受影响' }
    case 'cancelled':
      return { ...node, status: 'cancelled', hint: '已取消，答案不受影响' }
    default:
      return { ...node, status: 'todo', hint: '状态暂时无法读取' }
  }
}

/** 答案页与运行页共用的节点序列；`settled` 表示答案已出，前序阶段一律算完成。 */
export function askRailNodes(
  live: JobLive,
  {
    useKb,
    kb,
    settled,
    steps: pipeline = steps,
  }: {
    useKb: boolean
    kb: KbBackground | null
    settled?: boolean
    steps?: [StageKey, string][]
  },
): RailNode[] {
  const nodes: RailNode[] = pipeline.map(([stage, label]) => ({
    key: stage,
    label,
    status:
      live.stages[stage]?.status === 'finished' || settled
        ? 'done'
        : live.stages[stage]
          ? 'running'
          : 'todo',
  }))
  return useKb ? [...nodes, kbNode(kb)] : nodes
}
