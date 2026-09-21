import {
  AlertCircle,
  Ban,
  ChevronRight,
  FileDown,
  Filter,
  MoreHorizontal,
  RotateCcw,
  Trash2,
} from 'lucide-react'
import type { components } from '@/api/schema'
import type { Connection } from '@/api/useJobEvents'
import { jobErrorMessage } from '@/api/errors'
import { toolCallOf, type JobLive } from '@/lib/jobLive'
import { useAnswerThread } from '@/api/queries'
import { MARKER_RE } from '@/lib/citations'
import { dateTime, relativeTime } from '@/lib/format'
import { EmptyState } from '@/components/common/EmptyState'
import { ListRows } from '@/components/common/ListRows'
import { StatusBadge } from '@/components/common/StatusBadge'
import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { AnswerBody } from './AnswerBody'
import { KbSupplement } from './KbSupplement'
import { ProgressPipeline } from './ProgressPipeline'
import { SourceCard } from './SourceCard'
import { SourceList } from './SourceList'
import { StageRail } from './StageRail'
import { askRailNodes, interpretSteps } from './askRail'
import { EngineBadge } from './EnginePicker'
import { ThreadNav } from './ThreadNav'
import { TraceList } from './TraceList'
import { useBackgroundKb } from './useBackgroundKb'

export function AnswerView({
  answer,
  live,
  connection,
  onOpenPaper,
  onReask,
  onExportPdf,
  exporting,
  onDelete,
}: {
  answer: components['schemas']['Answer']
  live: JobLive
  connection: Connection
  onOpenPaper: (n: number, pid: number | null) => void
  onReask: () => void
  onExportPdf: () => void
  exporting: boolean
  onDelete: () => void
}) {
  const active = answer.status === 'queued' || answer.status === 'running'
  const isCodex = answer.engine === 'codex'
  const isInterpret = answer.engine === 'interpret'
  // 解读只有两段轨道；综述与问答的第二节点文案不同
  const pipeline = isInterpret
    ? interpretSteps(String(answer.options?.mode ?? ''))
    : undefined
  const thread = useAnswerThread(answer.id, isCodex || isInterpret)
  const turns = thread.data ?? []
  const trace = (answer.trace ?? []).map(toolCallOf).filter((c) => c !== null)
  const useKb = !isCodex && answer.options?.use_kb !== false
  // 写库排在答案之后，只有答案已出时才存在后台任务可查。
  const kb = useBackgroundKb(answer.job_id, useKb && answer.status === 'ready')
  const citedCounts: Record<number, number> = {}
  for (const marker of (answer.body_md ?? '').matchAll(MARKER_RE)) {
    const n = Number(marker[1])
    citedCounts[n] = (citedCounts[n] ?? 0) + 1
  }
  return (
    <article className="@container/answer mx-auto w-full max-w-[760px] px-5 py-8 md:px-8 md:py-10">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <StatusBadge status={answer.status} />
        <EngineBadge engine={answer.engine ?? 'ask'} />
        <time dateTime={answer.created_at} title={dateTime(answer.created_at)}>
          {relativeTime(answer.created_at)}
        </time>
        {answer.n_papers != null && (
          <span>
            {answer.n_papers} 篇文献 · {answer.n_fulltext ?? 0} 篇全文
          </span>
        )}
        {answer.status === 'ready' && (
          // 导出是答案页的主动作之一，藏进「…」菜单里没人找得到，所以独立成按钮
          <Button
            className="ml-auto"
            variant="outline"
            size="sm"
            disabled={exporting}
            onClick={onExportPdf}
          >
            <FileDown />
            {exporting ? '导出中…' : '导出 PDF'}
          </Button>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              className={answer.status === 'ready' ? undefined : 'ml-auto'}
              variant="ghost"
              size="icon-sm"
              aria-label="答案操作"
            >
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {/* 解读会话没有筛选条件，也没有可复用的检索问题：重新提问对它无意义 */}
            {!isInterpret && (
              <>
                <DropdownMenuItem onSelect={onReask}>
                  <RotateCcw />
                  沿用此次筛选重新提问
                </DropdownMenuItem>
                <DropdownMenuSeparator />
              </>
            )}
            <DropdownMenuItem
              variant="destructive"
              disabled={active}
              onSelect={onDelete}
            >
              <Trash2 />
              删除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {turns.length > 1 && <ThreadNav turns={turns} currentId={answer.id} />}
      <h1 className="mt-3 font-serif text-2xl font-semibold leading-snug tracking-tight break-words md:text-[28px]">
        {answer.question}
      </h1>
      {answer.filters_label && (
        <p className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
          <Filter className="size-3 shrink-0" />
          {answer.filters_label}
        </p>
      )}
      {!!answer.queries?.length && (
        <Collapsible>
          <CollapsibleTrigger className="mt-4 flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground [&>svg]:transition-transform [&[data-state=open]>svg]:rotate-90">
            <ChevronRight className="size-3" />
            检索式（{answer.queries.length}）
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul className="mt-2 space-y-1.5">
              {answer.queries.map((q) => (
                <li
                  key={q}
                  className="rounded-md border bg-muted/50 px-3 py-2 font-mono text-[12px] leading-5 break-words"
                >
                  {q}
                </li>
              ))}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      )}
      <hr className="my-6" />
      {active ? (
        <>
          <ProgressPipeline
            key={answer.job_id}
            live={live}
            connection={connection}
            jobId={answer.job_id ?? null}
            useKb={useKb}
            kb={kb}
            engine={answer.engine ?? 'ask'}
            pipeline={pipeline}
          />
          {!!live.search?.papers.length && (
            <section className="mt-8">
              <h2 className="font-serif text-lg font-semibold">
                候选文献{' '}
                <span className="text-sm font-normal text-muted-foreground">
                  {live.search.papers.length}
                </span>
              </h2>
              <ListRows>
                {live.search.papers.map((paper, index) => (
                  <SourceCard
                    key={paper.n}
                    index={index}
                    paper={paper}
                    compact
                    onOpen={onOpenPaper}
                  />
                ))}
              </ListRows>
            </section>
          )}
        </>
      ) : answer.status === 'ready' ? (
        <div className="space-y-10">
          {kb && (
            <StageRail
              nodes={askRailNodes(live, {
                useKb,
                kb,
                settled: true,
                steps: pipeline,
              })}
              progress={
                kb.status === 'running' && kb.total > 0
                  ? {
                      title: '写入知识库',
                      current: kb.current,
                      total: kb.total,
                    }
                  : null
              }
            />
          )}
          <AnswerBody answer={answer} onOpen={onOpenPaper} />
          {isCodex ? (
            // 智能体没有逐篇原文快照；能交代的只有它实际走过的检索路径
            trace.length > 0 && (
              <Collapsible>
                <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground [&>svg]:transition-transform [&[data-state=open]>svg]:rotate-90">
                  <ChevronRight className="size-3" />
                  检索轨迹（{trace.length}）
                </CollapsibleTrigger>
                <CollapsibleContent className="pt-3">
                  <TraceList calls={trace} />
                </CollapsibleContent>
              </Collapsible>
            )
          ) : (
            <>
              <SourceList
                papers={answer.papers ?? []}
                nFulltext={answer.n_fulltext ?? 0}
                citedCounts={citedCounts}
                onOpen={onOpenPaper}
              />
              {!!answer.kb_hits?.length && (
                <KbSupplement hits={answer.kb_hits} />
              )}
              {!isInterpret && (
                <div className="border-t pt-6">
                  <Button variant="outline" size="sm" onClick={onReask}>
                    <RotateCcw />
                    沿用此次筛选重新提问
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      ) : answer.status === 'failed' ? (
        <div role="alert" className="error-panel flex gap-3">
          <AlertCircle className="mt-1 size-4 shrink-0 text-danger" />
          <div className="min-w-0">
            <p className="font-medium">
              {jobErrorMessage(answer.error?.code ?? 'internal_error')}
            </p>
            {answer.error?.message && (
              <p className="mt-1 text-xs break-words text-muted-foreground">
                {answer.error.message}
              </p>
            )}
            <Button
              className="mt-3"
              variant="outline"
              size="sm"
              onClick={onReask}
            >
              {isCodex ? '重新提问' : '放宽筛选后重新提问'}
            </Button>
          </div>
        </div>
      ) : (
        <EmptyState
          icon={Ban}
          title="任务已取消"
          action={
            <Button variant="outline" size="sm" onClick={onReask}>
              重新提问
            </Button>
          }
        />
      )}
    </article>
  )
}
