import {
  BookOpenText,
  Database,
  History,
  KeyRound,
  Library,
  MessageSquareText,
  Search,
  Users,
  type LucideIcon,
} from 'lucide-react'

export type NavItem = {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
  admin?: boolean
}

export const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: '工作台',
    items: [
      { to: '/', label: '提问', icon: MessageSquareText, end: true },
      { to: '/interpret', label: '解读', icon: BookOpenText },
      { to: '/history', label: '历史', icon: History },
    ],
  },
  {
    label: '探索',
    items: [
      { to: '/library', label: '文献库', icon: Library },
      { to: '/kb', label: '知识库', icon: Database },
      { to: '/search', label: '查文献', icon: Search },
    ],
  },
  {
    label: '管理',
    items: [
      { to: '/admin/users', label: '用户', icon: Users, admin: true },
      {
        to: '/admin/institution',
        label: '机构访问',
        icon: KeyRound,
        admin: true,
      },
    ],
  },
]

/** 移动端底部标签（第 5 格固定为「更多」）。 */
export const MOBILE_TABS: NavItem[] = [
  { to: '/', label: '提问', icon: MessageSquareText, end: true },
  { to: '/history', label: '历史', icon: History },
  { to: '/library', label: '文献库', icon: Library },
  { to: '/kb', label: '知识库', icon: Database },
]

/**
 * 页面过渡的动画键：同一功能区内换参数（如 `/a/<id>`、`/library/<key>`）
 * 不应重播进入动画，只有跨功能区才换 key。
 */
export function routeKey(pathname: string): string {
  if (pathname === '/' || pathname.startsWith('/a/')) return 'ask'
  if (pathname.startsWith('/library/')) return 'paper'
  return pathname.split('/')[1] ?? 'ask'
}
