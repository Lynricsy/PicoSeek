import { MutationCache, QueryClient, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api, dataOf } from './client'
import { toFormData } from './multipart'
import { ApiError, problemMessage } from './errors'
import { filenameFromDisposition, saveBlob } from '@/lib/download'
import type { components, paths } from './schema'

type Schemas = components['schemas']
export type AnswersParams = NonNullable<
  paths['/v1/answers']['get']['parameters']['query']
>
export type PapersParams = NonNullable<
  paths['/v1/papers']['get']['parameters']['query']
>
export type UsersParams = NonNullable<
  paths['/v1/users']['get']['parameters']['query']
>
export type KbSearchParams = NonNullable<
  paths['/v1/kb/search']['get']['parameters']['query']
>
export type LiteratureSearchParams = NonNullable<
  paths['/v1/literature/search']['get']['parameters']['query']
>
export const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError(error) {
      toast.error(
        problemMessage(error),
        error instanceof ApiError && error.code === 'too_many_jobs'
          ? {
              action: {
                label: '查看历史',
                onClick: () => window.location.assign('/history'),
              },
            }
          : undefined,
      )
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: (n, error) => !(error instanceof ApiError) && n < 2,
    },
  },
})
export const getMe = (signal?: AbortSignal) =>
  dataOf(api.GET('/v1/auth/me', { signal }))
export function useAnswers(params: AnswersParams) {
  return useQuery({
    queryKey: ['answers', params],
    queryFn: ({ signal }) =>
      dataOf(api.GET('/v1/answers', { params: { query: params }, signal })),
    refetchInterval: (query) =>
      query.state.data?.items.some(
        (a) => a.status === 'queued' || a.status === 'running',
      )
        ? 5000
        : false,
  })
}
export function useAnswer(id?: string, sseOpen = false) {
  return useQuery({
    queryKey: ['answer', id],
    enabled: !!id,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET('/v1/answers/{answer_id}', {
          params: { path: { answer_id: id! } },
          signal,
        }),
      ),
    refetchInterval: (q) =>
      !sseOpen &&
      (q.state.data?.status === 'queued' || q.state.data?.status === 'running')
        ? 5000
        : false,
  })
}
export function useAnswerPaper(id: string, n: number, enabled = true) {
  return useQuery({
    queryKey: ['answerPaper', id, n],
    enabled,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET('/v1/answers/{answer_id}/papers/{n}', {
          params: { path: { answer_id: id, n } },
          signal,
        }),
      ),
  })
}
export function useAnswerMarkdown(id: string, enabled = true) {
  return useQuery({
    queryKey: ['answerMarkdown', id],
    enabled,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET('/v1/answers/{answer_id}/markdown', {
          params: { path: { answer_id: id } },
          parseAs: 'text',
          signal,
        }),
      ),
  })
}
/** 下载服务端渲染的 PDF：三端共用同一份字节，前端只负责落盘。 */
export async function downloadAnswerPdf(id: string): Promise<void> {
  const { data, response } = await api.GET('/v1/answers/{answer_id}/pdf', {
    params: { path: { answer_id: id } },
    parseAs: 'blob',
  })
  // 生成类型把 application/pdf 记成 string，运行时 parseAs:'blob' 给的是 Blob
  const blob = data as unknown as Blob | undefined
  if (!blob) throw new Error('Missing response data')
  saveBlob(
    blob,
    filenameFromDisposition(response.headers.get('content-disposition')) ??
      `PicoSeek-${id}.pdf`,
  )
}
export function useAnswerThread(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ['answer-thread', id],
    enabled,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET('/v1/answers/{answer_id}/thread', {
          params: { path: { answer_id: id } },
          signal,
        }),
      ),
  })
}
export function usePaperMarkdown(id: string, n: number, enabled = true) {
  return useQuery({
    queryKey: ['paperMarkdown', id, n],
    enabled,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET('/v1/answers/{answer_id}/papers/{n}/markdown', {
          params: { path: { answer_id: id, n } },
          parseAs: 'text',
          signal,
        }),
      ),
  })
}
export function useJob(id?: string | null, enabled = true) {
  return useQuery({
    queryKey: ['job', id],
    enabled: !!id && enabled,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET('/v1/jobs/{job_id}', {
          params: { path: { job_id: id! } },
          signal,
        }),
      ),
    refetchInterval: (q) =>
      q.state.data?.status === 'queued' || q.state.data?.status === 'running'
        ? 5000
        : false,
  })
}
export function useUsers(params: UsersParams) {
  return useQuery({
    queryKey: ['users', params],
    queryFn: ({ signal }) =>
      dataOf(api.GET('/v1/users', { params: { query: params }, signal })),
  })
}
export function usePapers(params: PapersParams) {
  return useQuery({
    queryKey: ['papers', params],
    queryFn: ({ signal }) =>
      dataOf(api.GET('/v1/papers', { params: { query: params }, signal })),
  })
}
export function usePaper(key: string) {
  return useQuery({
    queryKey: ['paper', key],
    enabled: !!key,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET('/v1/papers/{key}', { params: { path: { key } }, signal }),
      ),
  })
}
export function usePaperFulltext(key: string, enabled = true) {
  return useQuery({
    queryKey: ['paperFulltext', key],
    enabled: !!key && enabled,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET('/v1/papers/{key}/fulltext', {
          params: { path: { key } },
          parseAs: 'text',
          signal,
        }),
      ),
  })
}
export function usePaperFacts(key: string, enabled = true) {
  return useQuery({
    queryKey: ['paperFacts', key],
    enabled: !!key && enabled,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET('/v1/papers/{key}/facts', {
          params: { path: { key } },
          signal,
        }),
      ),
  })
}
export function useKbStats() {
  return useQuery({
    queryKey: ['kbStats'],
    queryFn: ({ signal }) => dataOf(api.GET('/v1/kb/stats', { signal })),
  })
}
export function useKbSearch(params: KbSearchParams | null) {
  return useQuery({
    queryKey: ['kbSearch', params],
    enabled: !!params,
    queryFn: ({ signal }) =>
      dataOf(api.GET('/v1/kb/search', { params: { query: params! }, signal })),
  })
}
export function useLiteratureSearch(params: LiteratureSearchParams | null) {
  return useQuery({
    queryKey: ['litSearch', params],
    enabled: !!params,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET('/v1/literature/search', {
          params: { query: params! },
          signal,
        }),
      ),
  })
}
export function useLiteratureFulltext(ident: string | null, section = '') {
  return useQuery({
    queryKey: ['litFulltext', ident, section],
    enabled: !!ident,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET('/v1/literature/fulltext', {
          params: { query: { ident: ident!, section, max_chars: 20000 } },
          signal,
        }),
      ),
  })
}
export const createAnswer = (body: Schemas['AnswerCreate']) =>
  dataOf(api.POST('/v1/answers', { body }))
export const followupAnswer = ({
  id,
  question,
}: {
  id: string
  question: string
}) =>
  dataOf(
    api.POST('/v1/answers/{answer_id}/followup', {
      params: { path: { answer_id: id } },
      body: { question },
    }),
  )
export async function deleteAnswer(id: string) {
  await api.DELETE('/v1/answers/{answer_id}', {
    params: { path: { answer_id: id } },
  })
  queryClient.removeQueries({ queryKey: ['answer', id] })
  await queryClient.invalidateQueries({ queryKey: ['answers'] })
}
export async function cancelJob(id: string) {
  const job = await dataOf(
    api.POST('/v1/jobs/{job_id}/cancel', { params: { path: { job_id: id } } }),
  )
  await queryClient.invalidateQueries({ queryKey: ['answers'] })
  return job
}
export async function changePassword(body: Schemas['PasswordChangeRequest']) {
  await api.POST('/v1/auth/password', { body })
}
export async function createUser(body: Schemas['CreateUserRequest']) {
  const user = await dataOf(api.POST('/v1/users', { body }))
  await queryClient.invalidateQueries({ queryKey: ['users'] })
  return user
}
export async function updateUser({
  id,
  is_active,
}: {
  id: string
  is_active: boolean
}) {
  const user = await dataOf(
    api.PATCH('/v1/users/{user_id}', {
      params: { path: { user_id: id } },
      body: { is_active },
    }),
  )
  await queryClient.invalidateQueries({ queryKey: ['users'] })
  return user
}
export async function resetUserPassword({
  id,
  new_password,
}: {
  id: string
  new_password: string
}) {
  await api.POST('/v1/users/{user_id}/password', {
    params: { path: { user_id: id } },
    body: { new_password },
  })
}
export const reindexKb = () => dataOf(api.POST('/v1/kb/reindex'))
export function useRankTables() {
  return useQuery({
    queryKey: ['rankTables'],
    queryFn: ({ signal }) => dataOf(api.GET('/v1/journals/tables', { signal })),
    staleTime: 5 * 60_000,
  })
}
export function usePaywallStatus() {
  return useQuery({
    queryKey: ['paywallStatus'],
    queryFn: ({ signal }) => dataOf(api.GET('/v1/paywall/status', { signal })),
    staleTime: 60_000,
  })
}
/**
 * openapi-typescript 把 multipart 的二进制字段生成成 `string`，`File` 无法直接赋值。
 * 强转集中在这一处，运行期真正的编码交给 `toFormData`；响应类型不受影响。
 */
function multipart<T extends object>(body: T) {
  return { body: body as never, bodySerializer: toFormData }
}
export async function uploadPaywallState(body: {
  storage_state: File
  session_storage?: File
  context_meta?: File
}) {
  const status = await dataOf(api.PUT('/v1/paywall/state', multipart(body)))
  await queryClient.invalidateQueries({ queryKey: ['paywallStatus'] })
  return status
}
export async function clearPaywallState() {
  await api.DELETE('/v1/paywall/state')
  await queryClient.invalidateQueries({ queryKey: ['paywallStatus'] })
}
export const uploadPaper = (body: {
  file: File
  title: string
  doi?: string
  journal?: string
  year?: string
  authors?: string
}) => dataOf(api.POST('/v1/papers/upload', multipart(body)))
export const createInterpretation = (body: {
  files: File[]
  question?: string
}) => dataOf(api.POST('/v1/answers/interpret', multipart(body)))
export const ingestDoi = ({ doi }: { doi: string }) =>
  dataOf(api.POST('/v1/papers/ingest', { body: { doi } }))
export type RelatedKind = 'citations' | 'references' | 'recommendations'
export function useLiteratureRelated(
  kind: RelatedKind,
  ident: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: ['literature', kind, ident],
    enabled: !!ident && enabled,
    queryFn: ({ signal }) =>
      dataOf(
        api.GET(`/v1/literature/${kind}`, {
          params: { query: { ident: ident!, limit: 20 } },
          signal,
        }),
      ),
  })
}
