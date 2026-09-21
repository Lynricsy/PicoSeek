import { useEffect, useRef } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Check, ChevronDown, Loader2, Square } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from 'cn'
import { cancelJob } from '@/api/queries'
import type { Connection } from '@/api/useJobEvents'
import type { JobLive, StageKey } from '@/lib/jobLive'
import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { TraceList } from './TraceList'
import { StageRail } from './StageRail'
import { askRailNodes, steps } from './askRail'
import type { KbBackground } from './useBackgroundKb'

// 只有「在等」和「出问题了」值得占一行字；连接正常是默认预期，说出来是噪音。
const connections: Partial<Record<Connection, [string, string]>> = {
  connecting: ['正在连接…', 'bg-muted-foreground animate-pulse'],
  reconnecting: ['连接中断，正在重连…', 'bg-warning'],
}

function summary(stage: StageKey, detail: Record<string, unknown>) {
  if (stage === 'search') {
    const dropped = (detail.dropped ?? {}) as Record<string, number>
    return (
      '候选 ' +
      (detail.candidates ?? 0) +
      ' → 保留 ' +
      (detail.kept ?? 0) +
      '（年份 -' +
      (dropped.year ?? 0) +
      ' / 分区 -' +
      (dropped.quartile ?? 0) +
      ' / 未收录 -' +
      (dropped.unranked ?? 0) +
      ' / 期刊 -' +
      (dropped.journal ?? 0) +
      '）'
    )
  }
  if (stage === 'read')
    return '相关 ' + (detail.relevant ?? 0) + '/' + (detail.total ?? 0)
  const labels: Record<string, string> = {
    queries: '检索式',
    papers: '文献',
    items: '条目',
    total: '总计',
    fulltext: '全文',
    n_fulltext: '全文',
    chars: '字符',
    count: '数量',
    facts: '事实',
  }
  return Object.entries(detail)
    .filter(([, v]) => typeof v === 'number')
    .map(([k, v]) => (labels[k] ?? k) + ' ' + v)
    .join(' · ')
}

export function ProgressPipeline({
  live,
  connection,
  jobId,
  useKb,
  kb,
  engine,
  pipeline = steps,
}: {
  live: JobLive
  connection: Connection
  jobId: string | null
  useKb: boolean
  kb: KbBackground | null
  engine: 'ask' | 'codex' | 'interpret'
  /** 自定义流水线节点；默认是 ask 的五段式。 */
  pipeline?: [StageKey, string][]
}) {
  const cancel = useMutation({
    mutationFn: cancelJob,
    onSuccess: () => toast.success('已请求取消，等待任务响应'),
  })
  const logRef = useRef<HTMLUListElement>(null)
  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [live.logs.length])
  const isCodex = engine === 'codex'
  const connectionNote = connections[connection]
  const finished = pipeline.filter(
    ([stage]) => live.stages[stage]?.status === 'finished',
  )
  const progress =
    live.progress && live.stages[live.progress.stage]?.status !== 'finished'
      ? live.progress
      : null
  return (
    <section className="rounded-xl border bg-card p-5 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm font-medium">
          {engine === 'interpret'
            ? '文献解读'
            : isCodex
              ? '智能体检索'
              : '证据流水线'}
        </p>
        {connectionNote && (
          <span
            className={cn(
              'inline-flex items-center gap-1.5 text-xs',
              connection === 'reconnecting'
                ? 'text-warning'
                : 'text-muted-foreground',
            )}
          >
            <span className={cn('size-1.5 rounded-full', connectionNote[1])} />
            {connectionNote[0]}
          </span>
        )}
        {jobId && (
          <Button
            className="ml-auto"
            variant="outline"
            size="sm"
            disabled={cancel.isPending || cancel.isSuccess}
            onClick={() => cancel.mutate(jobId)}
          >
            <Square className="size-3" />
            {cancel.isPending || cancel.isSuccess ? '取消中…' : '取消'}
          </Button>
        )}
      </div>
      {isCodex ? (
        // 轨迹本身就是进展，不再用文字复述系统在干什么；
        // 还没有轨迹时只给一个转圈表示在跑。
        <div className="mt-5">
          {live.tools.length > 0 ? (
            <TraceList calls={live.tools} />
          ) : (
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          )}
        </div>
      ) : (
        <>
          <StageRail
            className="mt-6"
            nodes={askRailNodes(live, { useKb, kb, steps: pipeline })}
            progress={progress}
          />
          {finished.length > 0 && (
            <ul className="mt-5 space-y-1 text-xs text-muted-foreground">
              {finished.map(([stage, label]) => (
                <li key={stage} className="flex items-start gap-1.5">
                  <Check className="mt-1 size-3 shrink-0 text-success" />
                  <span className="min-w-0 break-words">
                    {label}：{summary(stage, live.stages[stage]?.detail ?? {})}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <Collapsible className="mt-5 border-t pt-3">
        <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground [&>svg]:transition-transform [&[data-state=open]>svg]:rotate-180">
          <ChevronDown className="size-3.5" />
          运行日志（{live.logs.length}）
        </CollapsibleTrigger>
        <CollapsibleContent>
          <ul
            ref={logRef}
            className="mt-2 max-h-56 space-y-0.5 overflow-y-auto rounded-md bg-muted/60 p-3 font-mono text-[11.5px] leading-5"
          >
            {live.logs.map((log, i) => (
              <li
                key={i}
                className={
                  log.level === 'warning'
                    ? 'text-warning'
                    : 'text-muted-foreground'
                }
              >
                {log.message}
              </li>
            ))}
          </ul>
        </CollapsibleContent>
      </Collapsible>
    </section>
  )
}
