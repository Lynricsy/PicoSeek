import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { ArrowUp, Loader2, X } from 'lucide-react'
import { cn } from 'cn'
import { createInterpretation, queryClient } from '@/api/queries'
import { PageHeader } from '@/components/common/PageHeader'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

// 与后端 PICOSEEK_INTERPRET_MAX_FILES / PICOSEEK_UPLOAD_MAX_MB 的默认值一致。
// 这里只为提前给出反馈，服务端仍是权威校验。
const MAX_FILES = 10
const MAX_MB = 50

export default function InterpretPage() {
  const navigate = useNavigate()
  const [files, setFiles] = useState<File[]>([])
  const [question, setQuestion] = useState('')
  const create = useMutation({
    mutationFn: createInterpretation,
    onSuccess(answer) {
      queryClient.setQueryData(['answer', answer.id], answer)
      void queryClient.invalidateQueries({ queryKey: ['answers'] })
      navigate('/a/' + answer.id)
    },
  })
  const oversized = files.filter((f) => f.size > MAX_MB * 1024 * 1024)
  const tooMany = files.length > MAX_FILES
  const single = files.length === 1
  const blocked =
    files.length === 0 ||
    tooMany ||
    oversized.length > 0 ||
    (single && !question.trim()) ||
    create.isPending
  return (
    <div className="h-full overflow-y-auto">
      <div className="page max-w-3xl">
        <PageHeader
          title="文献解读"
          description="上传一篇或一组 PDF，基于原文提问；多篇会先自动梳理组织关系与差异"
        />
        <form
          className="space-y-6 rounded-xl border bg-card p-6"
          onSubmit={(event) => {
            event.preventDefault()
            if (!blocked)
              create.mutate({ files, question: question.trim() || undefined })
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="interpret-files">
              PDF 文件（1–{MAX_FILES} 篇，单篇不超过 {MAX_MB} MB）
            </Label>
            <Input
              id="interpret-files"
              type="file"
              accept="application/pdf"
              multiple
              className="h-10 py-2"
              disabled={create.isPending}
              onChange={(event) => {
                // 同一个 input 多次选择是覆盖而非追加，与浏览器原生语义一致
                setFiles(Array.from(event.target.files ?? []))
              }}
            />
            {files.length > 0 && (
              <ul className="mt-3 space-y-1.5">
                {files.map((file, index) => {
                  const bad =
                    file.size > MAX_MB * 1024 * 1024 || index >= MAX_FILES
                  return (
                    <li
                      key={file.name + index}
                      className={cn(
                        'flex items-center gap-2 rounded-md border px-3 py-2 text-sm',
                        bad && 'border-destructive/50 text-destructive',
                      )}
                    >
                      <span className="w-5 shrink-0 font-mono text-xs text-muted-foreground tabular-nums">
                        {index + 1}
                      </span>
                      <span className="min-w-0 flex-1 truncate">
                        {file.name}
                      </span>
                      <span className="shrink-0 font-mono text-xs tabular-nums">
                        {(file.size / 1024 / 1024).toFixed(1)} MB
                      </span>
                      <button
                        type="button"
                        aria-label={`移除 ${file.name}`}
                        className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
                        disabled={create.isPending}
                        onClick={() =>
                          setFiles(files.filter((_, i) => i !== index))
                        }
                      >
                        <X className="size-4" />
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
            {tooMany && (
              <p className="text-sm text-destructive">
                最多 {MAX_FILES} 篇，请移除多余文件
              </p>
            )}
            {oversized.length > 0 && (
              <p className="text-sm text-destructive">
                有 {oversized.length} 个文件超过 {MAX_MB} MB
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="interpret-question">
              问题{single ? '' : '（选填）'}
            </Label>
            <Textarea
              id="interpret-question"
              rows={3}
              maxLength={2000}
              className="resize-none"
              placeholder={
                single
                  ? '例如：这篇研究的主要结局是什么？'
                  : '可留空：先生成关系综述，之后在答案页继续提问'
              }
              value={question}
              disabled={create.isPending}
              onChange={(event) => setQuestion(event.target.value)}
            />
            {single && !question.trim() && (
              <p className="text-sm text-muted-foreground">
                只有一篇时没有可对照的对象，需要给出具体问题
              </p>
            )}
          </div>
          <div className="flex items-center justify-end gap-3">
            {question.length > 0 && (
              <span className="font-mono text-[11px] text-muted-foreground tabular-nums">
                {question.length}/2000
              </span>
            )}
            <Button type="submit" disabled={blocked}>
              开始解读
              {create.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <ArrowUp />
              )}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
