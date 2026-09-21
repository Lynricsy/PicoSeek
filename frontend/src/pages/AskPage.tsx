import { useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { useMutation } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertCircle } from 'lucide-react'
import { FilterColumn, FilterSheet } from '@/components/ask/FilterPanel'
import { ComposerDock } from '@/components/ask/ComposerDock'
import { Hero } from '@/components/ask/Hero'
import { AnswerView } from '@/components/ask/AnswerView'
import { EmptyState } from '@/components/common/EmptyState'
import { DeleteAnswerDialog } from '@/components/common/DeleteAnswerDialog'
import { ReaderPane, ReaderSheet } from '@/components/ask/ReaderPane'
import {
  Group,
  Panel,
  Separator as ResizeSeparator,
  type Layout,
} from 'react-resizable-panels'
import { BREAKPOINTS, useMediaQuery } from '@/lib/useMediaQuery'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  loadFilters,
  saveFilters,
  describeFilters,
  fromAnswerOptions,
  toAnswerCreate,
  validYears,
  type FilterState,
} from '@/lib/filters'
import {
  createAnswer,
  downloadAnswerPdf,
  followupAnswer,
  queryClient,
  useAnswer,
} from '@/api/queries'
import type { components } from '@/api/schema'
import { useJobEvents } from '@/api/useJobEvents'
import { ApiError, problemMessage } from '@/api/errors'

export default function AskPage() {
  const { answerId } = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const paperParam = searchParams.get('paper') ?? ''
  const paperN = /^[1-9]\d{0,2}$/.test(paperParam) ? Number(paperParam) : null
  const pidParam = searchParams.get('pid') ?? ''
  const paperPid = /^\d{1,4}$/.test(pidParam) ? Number(pidParam) : null
  const openPaper = (n: number, pid: number | null) =>
    setSearchParams({
      paper: String(n),
      ...(pid !== null ? { pid: String(pid) } : {}),
    })
  const [filters, setFilters] = useState(loadFilters)
  const [question, setQuestion] = useState('')
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [sseOpen, setSseOpen] = useState(false)
  const [filterOpen, setFilterOpen] = useState(false)
  // 分栏宽度只在挂载时读一次；写入交给 onLayoutChanged。localStorage 可被手改，
  // 只有两个面板 id 都是 0–100 的有限数才采用，否则退回库的默认布局。
  const [readerLayout] = useState<Layout | undefined>(() => {
    try {
      const saved = localStorage.getItem('picoseek.reader-layout')
      if (!saved) return undefined
      const parsed: unknown = JSON.parse(saved)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
        return undefined
      const entries = Object.entries(parsed as Record<string, unknown>)
      const valid =
        entries.length === 2 &&
        ['answer', 'reader'].every((id) => id in (parsed as object)) &&
        entries.every(
          ([, size]) =>
            typeof size === 'number' &&
            Number.isFinite(size) &&
            size >= 0 &&
            size <= 100,
        )
      return valid ? (parsed as Layout) : undefined
    } catch {
      return undefined
    }
  })
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const isXl = useMediaQuery(BREAKPOINTS.xl)
  const is2xl = useMediaQuery(BREAKPOINTS['2xl'])
  const answerQuery = useAnswer(answerId, sseOpen)
  const answer = answerQuery.data
  const active = answer?.status === 'queued' || answer?.status === 'running'
  const { live, connection } = useJobEvents(answer?.job_id, active)
  if (sseOpen !== (connection === 'open')) setSseOpen(connection === 'open')
  // 阅读器占用右侧分栏时，<2xl 收起筛选列，改由输入框上方的芯片打开抽屉。
  const readerOpen = !!answer && paperN !== null
  const filterColumn = isXl && (!readerOpen || is2xl)
  const update = (f: FilterState) => {
    setFilters(f)
    saveFilters(f)
  }
  const onCreated = (created: components['schemas']['Answer']) => {
    queryClient.setQueryData(['answer', created.id], created)
    void queryClient.invalidateQueries({ queryKey: ['answers'] })
    void queryClient.invalidateQueries({ queryKey: ['answer-thread'] })
    setQuestion('')
    navigate('/a/' + created.id)
  }
  const create = useMutation({ mutationFn: createAnswer, onSuccess: onCreated })
  const followup = useMutation({
    mutationFn: followupAnswer,
    onSuccess: onCreated,
  })
  const exportPdf = useMutation({
    mutationFn: downloadAnswerPdf,
    onSuccess: () => toast.success('PDF 已导出'),
  })
  const fill = (text: string) => {
    setQuestion(text)
    inputRef.current?.focus()
  }
  // 智能体与文献解读的已完成答案下方是「继续提问」，不是新建：引擎与筛选都由会话决定
  const followingUp =
    (answer?.engine === 'codex' || answer?.engine === 'interpret') &&
    answer.status === 'ready'
  const composer = {
    value: question,
    onChange: setQuestion,
    onSubmit: () =>
      followingUp && answer
        ? followup.mutate({ id: answer.id, question: question.trim() })
        : create.mutate(toAnswerCreate(question, filters)),
    pending: create.isPending || followup.isPending,
    disabled: !validYears(filters),
    inputRef,
    filterSummary: describeFilters(filters),
    onOpenFilters: filterColumn ? null : () => setFilterOpen(true),
    engine: filters.engine,
    onEngineChange: (engine: FilterState['engine']) =>
      update({ ...filters, engine }),
    mode: (followingUp ? 'followup' : 'ask') as 'ask' | 'followup',
    sessionEngine: (answer?.engine === 'interpret' ? 'interpret' : 'codex') as
      'codex' | 'interpret',
  }
  const closeReader = () =>
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous)
      next.delete('paper')
      next.delete('pid')
      return next
    })
  const readerProps = {
    answerId: answer?.id ?? '',
    n: paperN ?? 0,
    pid: paperPid,
    papers: answer?.papers ?? [],
    citations: answer?.citations ?? [],
    answerActive: active,
    onClose: closeReader,
  }
  return (
    <div className="flex min-h-0 flex-1">
      {filterColumn && <FilterColumn value={filters} onChange={update} />}
      <Group
        orientation="horizontal"
        className="min-w-0 flex-1"
        defaultLayout={readerLayout}
        // 该回调在初始挂载与约束重算时也会触发（meta.isUserInteraction=false）。
        // 阅读器关闭时组内只有 answer 一个面板，若无条件写入就会把用户拖出的
        // 两栏宽度覆盖成单键布局，下次加载再被校验丢弃。只存真实拖拽/键盘调整。
        onLayoutChanged={(layout, meta) => {
          if (!meta.isUserInteraction) return
          if (!('answer' in layout) || !('reader' in layout)) return
          localStorage.setItem('picoseek.reader-layout', JSON.stringify(layout))
        }}
      >
        <Panel id="answer" minSize="40%" className="flex h-full flex-col">
          <div
            className="min-h-0 flex-1 overflow-y-auto"
            data-testid="answer-scroll"
          >
            {!answerId ? (
              <Hero {...composer} onExample={fill} />
            ) : answerQuery.isPending ? (
              <div className="mx-auto min-h-[60dvh] w-full max-w-[760px] space-y-5 px-5 py-8 md:px-8 md:py-10">
                <Skeleton className="h-8 w-3/4" />
                <Skeleton className="h-4 w-1/3" />
                <Skeleton className="h-64 w-full" />
              </div>
            ) : answerQuery.error ? (
              <EmptyState
                icon={AlertCircle}
                title={
                  answerQuery.error instanceof ApiError &&
                  answerQuery.error.status === 404
                    ? '答案不存在或无权访问'
                    : problemMessage(answerQuery.error)
                }
                action={
                  <Button variant="outline" onClick={() => navigate('/')}>
                    返回提问
                  </Button>
                }
              />
            ) : (
              answer && (
                <AnswerView
                  answer={answer}
                  live={live}
                  connection={connection}
                  onOpenPaper={openPaper}
                  onReask={() => {
                    update(fromAnswerOptions(answer.options ?? {}))
                    fill(answer.question)
                  }}
                  onDelete={() => setDeleteId(answer.id)}
                  onExportPdf={() => exportPdf.mutate(answer.id)}
                  exporting={exportPdf.isPending}
                />
              )
            )}
          </div>
          {answerId && <ComposerDock {...composer} />}
        </Panel>
        {readerOpen && isXl && (
          <>
            <ResizeSeparator className="reader-separator" />
            <Panel
              id="reader"
              defaultSize="45%"
              minSize={360}
              maxSize="60%"
              className="h-full"
            >
              <ReaderPane {...readerProps} />
            </Panel>
          </>
        )}
      </Group>
      {readerOpen && !isXl && <ReaderSheet {...readerProps} />}
      <FilterSheet
        open={filterOpen}
        onOpenChange={setFilterOpen}
        value={filters}
        onChange={update}
      />
      <DeleteAnswerDialog
        id={deleteId}
        onClose={() => setDeleteId(null)}
        onDeleted={() => navigate('/history')}
      />
    </div>
  )
}
