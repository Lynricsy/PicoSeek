import { useEffect, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router'
import { useMutation } from '@tanstack/react-query'
import {
  FileDown,
  History,
  MoreHorizontal,
  Plus,
  RotateCcw,
  Search,
  Square,
  Trash2,
} from 'lucide-react'
import { toast } from 'sonner'
import type { components } from '@/api/schema'
import { cancelJob, downloadAnswerPdf, useAnswers } from '@/api/queries'
import { jobErrorMessage } from '@/api/errors'
import { useAuth } from '@/auth/store'
import { DeleteAnswerDialog } from '@/components/common/DeleteAnswerDialog'
import { EmptyState } from '@/components/common/EmptyState'
import { ListRow, ListRows } from '@/components/common/ListRows'
import { Loading } from '@/components/common/Loading'
import { PageHeader } from '@/components/common/PageHeader'
import { Pagination } from '@/components/common/Pagination'
import { Pill } from '@/components/common/Pill'
import { QueryError } from '@/components/common/QueryError'
import { StatusBadge } from '@/components/common/StatusBadge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { dateTime, relativeTime } from '@/lib/format'
import { EngineBadge } from '@/components/ask/EnginePicker'

type AnswerStatus = components['schemas']['AnswerSummary']['status']
const statuses: { value: AnswerStatus; label: string }[] = [
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '进行中' },
  { value: 'ready', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]
const limit = 20

export default function HistoryPage() {
  const { isAdmin } = useAuth()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const status = statuses.find(
    (item) => item.value === searchParams.get('status'),
  )?.value
  const q = searchParams.get('q') ?? ''
  const rawOffset = Number(searchParams.get('offset') ?? 0)
  const offset =
    Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0
  const [draft, setDraft] = useState({ key: location.key, value: q })
  const [composing, setComposing] = useState(false)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [cancelRequested, setCancelRequested] = useState<Set<string>>(
    () => new Set(),
  )

  // 导航时同步草稿并取消旧防抖，不由同步副作用反向覆盖历史记录。
  if (draft.key !== location.key) setDraft({ key: location.key, value: q })
  useEffect(() => {
    if (draft.key !== location.key || draft.value === q || composing) return
    const timer = window.setTimeout(() => {
      setSearchParams((current) => {
        const next = new URLSearchParams(current)
        if (draft.value) next.set('q', draft.value)
        else next.delete('q')
        next.delete('offset')
        return next
      })
    }, 300)
    return () => window.clearTimeout(timer)
  }, [draft, location.key, q, composing, setSearchParams])

  const answers = useAnswers({ status, q: q || undefined, limit, offset })
  const cancel = useMutation({
    mutationFn: cancelJob,
    onSuccess: (_job, id) => {
      setCancelRequested((current) => new Set(current).add(id))
      toast.success('已请求取消，等待任务响应')
    },
  })
  const exportPdf = useMutation({
    mutationFn: downloadAnswerPdf,
    onSuccess: () => toast.success('PDF 已导出'),
  })
  function changeOffset(value: number) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (value > 0) next.set('offset', String(value))
      else next.delete('offset')
      return next
    })
  }
  function clearFilters() {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('q')
      next.delete('status')
      next.delete('offset')
      return next
    })
  }
  const filtered = !!q || !!status
  const items = answers.data?.items ?? []

  return (
    <div className="h-full overflow-y-auto">
      <div className="page">
        <PageHeader
          title="问答历史"
          description="按状态与关键词回顾提问，进行中的任务可在此取消"
          actions={
            <Button asChild>
              <Link to="/">
                <Plus />
                新建问答
              </Link>
            </Button>
          }
        />
        <div className="mb-6 flex flex-col gap-3 md:flex-row md:items-center">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute top-3 left-3 size-4 text-muted-foreground" />
            <Input
              aria-label="搜索历史问题"
              placeholder="搜索历史问题"
              className="h-10 rounded-lg pl-9"
              value={draft.key === location.key ? draft.value : q}
              onChange={(event) =>
                setDraft({ key: location.key, value: event.target.value })
              }
              onCompositionStart={() => setComposing(true)}
              onCompositionEnd={() => setComposing(false)}
            />
          </div>
          <ToggleGroup
            type="single"
            value={status ?? 'all'}
            onValueChange={(value) => {
              if (!value) return
              setSearchParams((current) => {
                const next = new URLSearchParams(current)
                if (value === 'all') next.delete('status')
                else next.set('status', value)
                next.delete('offset')
                return next
              })
            }}
            spacing={1}
            aria-label="问答状态"
            className="flex gap-1.5 overflow-x-auto"
          >
            <ToggleGroupItem className="chip shrink-0" value="all">
              全部
            </ToggleGroupItem>
            {statuses.map((item) => (
              <ToggleGroupItem
                key={item.value}
                className="chip shrink-0"
                value={item.value}
              >
                {item.label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          {filtered && (
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              <RotateCcw />
              清除筛选
            </Button>
          )}
        </div>
        {answers.isError && (
          <div className="mb-5">
            <QueryError error={answers.error} retry={answers.refetch} />
          </div>
        )}
        {answers.isPending ? (
          <Loading>正在加载问答历史…</Loading>
        ) : (
          answers.data && (
            <>
              {items.length === 0 ? (
                answers.data.total > 0 ? (
                  <EmptyState
                    icon={History}
                    title="本页暂无问答"
                    action={
                      <Button variant="outline" onClick={() => changeOffset(0)}>
                        返回第一页
                      </Button>
                    }
                  />
                ) : filtered ? (
                  <EmptyState
                    icon={Search}
                    title="没有符合条件的问答"
                    action={
                      <Button variant="outline" onClick={clearFilters}>
                        <RotateCcw />
                        清除筛选
                      </Button>
                    }
                  />
                ) : (
                  <EmptyState
                    icon={History}
                    title="还没有问答，去提问"
                    action={
                      <Button asChild>
                        <Link to="/">
                          <Plus />
                          新建问答
                        </Link>
                      </Button>
                    }
                  />
                )
              ) : (
                <ListRows aria-label="问答列表">
                  {items.map((answer, i) => {
                    const active =
                      answer.status === 'queued' || answer.status === 'running'
                    const cancelling =
                      !!answer.job_id &&
                      (cancelRequested.has(answer.job_id) ||
                        (cancel.isPending &&
                          cancel.variables === answer.job_id))
                    return (
                      <ListRow
                        key={answer.id}
                        index={i}
                        className="flex items-start gap-3"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <StatusBadge status={answer.status} />
                            <EngineBadge engine={answer.engine ?? 'ask'} />
                            {(answer.n_turns ?? 1) > 1 && (
                              <Pill>{answer.n_turns} 轮</Pill>
                            )}
                            {isAdmin &&
                              answer.status === 'ready' &&
                              answer.filters_label === null &&
                              answer.n_papers === null && <Pill>历史导入</Pill>}
                            {active && cancelling && (
                              <span className="text-xs text-muted-foreground">
                                取消中…
                              </span>
                            )}
                          </div>
                          <Link
                            to={`/a/${answer.id}`}
                            className="mt-1.5 block text-[15px] font-medium leading-6 break-words transition-colors group-hover:text-primary"
                          >
                            {answer.question}
                          </Link>
                          {answer.root_question && (
                            <p className="mt-1 truncate text-xs text-muted-foreground">
                              始于：{answer.root_question}
                            </p>
                          )}
                          {answer.filters_label && (
                            <p className="mt-1.5 text-xs leading-5 break-words text-muted-foreground">
                              {answer.filters_label}
                            </p>
                          )}
                          <div className="metadata mt-2">
                            {answer.n_papers != null && (
                              <span>{answer.n_papers} 篇文献</span>
                            )}
                            {answer.n_fulltext != null && (
                              <span>{answer.n_fulltext} 篇全文</span>
                            )}
                            <time
                              dateTime={answer.created_at}
                              title={dateTime(answer.created_at)}
                            >
                              {relativeTime(answer.created_at)}
                            </time>
                          </div>
                          {answer.error && (
                            <p className="mt-2 text-xs leading-5 break-words text-danger">
                              {jobErrorMessage(answer.error.code)}
                            </p>
                          )}
                        </div>
                        {(!active || answer.job_id) && (
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                className="shrink-0"
                                aria-label={`问答操作：${answer.question}`}
                                title="问答操作"
                              >
                                <MoreHorizontal />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              {active ? (
                                <DropdownMenuItem
                                  disabled={cancelling || cancel.isPending}
                                  onSelect={() => {
                                    if (answer.job_id)
                                      cancel.mutate(answer.job_id)
                                  }}
                                >
                                  <Square />
                                  {cancelling ? '取消中…' : '取消任务'}
                                </DropdownMenuItem>
                              ) : (
                                <>
                                  <DropdownMenuItem
                                    disabled={
                                      answer.status !== 'ready' ||
                                      exportPdf.isPending
                                    }
                                    onSelect={() => exportPdf.mutate(answer.id)}
                                  >
                                    <FileDown />
                                    导出 PDF
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    variant="destructive"
                                    onSelect={() => setDeleteId(answer.id)}
                                  >
                                    <Trash2 />
                                    删除
                                  </DropdownMenuItem>
                                </>
                              )}
                            </DropdownMenuContent>
                          </DropdownMenu>
                        )}
                      </ListRow>
                    )
                  })}
                </ListRows>
              )}
              {items.length > 0 && (
                <Pagination
                  total={answers.data.total}
                  limit={limit}
                  offset={offset}
                  onChange={changeOffset}
                />
              )}
            </>
          )
        )}
      </div>
      <DeleteAnswerDialog
        id={deleteId}
        onClose={() => setDeleteId(null)}
        onDeleted={() => {
          if (items.length <= 1 && offset > 0)
            changeOffset(Math.max(0, offset - limit))
        }}
      />
    </div>
  )
}
