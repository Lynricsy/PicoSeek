import { useState } from 'react'
import { Link, NavLink } from 'react-router'
import { BookOpenText, Menu, Search, Settings, Users } from 'lucide-react'
import { cn } from 'cn'
import { useAuth } from '@/auth/store'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { MOBILE_TABS } from './nav'

export function MobileTabBar() {
  const { isAdmin } = useAuth()
  const [open, setOpen] = useState(false)
  const more = [
    { to: '/interpret', label: '文献解读', icon: BookOpenText },
    { to: '/search', label: '查文献', icon: Search },
    ...(isAdmin
      ? [{ to: '/admin/users', label: '用户管理', icon: Users }]
      : []),
    { to: '/account', label: '账号设置', icon: Settings },
  ]
  return (
    <nav
      aria-label="底部导航"
      className="grid h-14 shrink-0 grid-cols-5 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
    >
      {MOBILE_TABS.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            cn(
              'flex flex-col items-center justify-center gap-1 text-[10px] text-muted-foreground transition-colors',
              isActive && 'text-primary',
            )
          }
        >
          <Icon className="size-5" />
          {label}
        </NavLink>
      ))}
      <Sheet open={open} onOpenChange={setOpen}>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex flex-col items-center justify-center gap-1 text-[10px] text-muted-foreground"
        >
          <Menu className="size-5" />
          更多
        </button>
        <SheetContent
          side="bottom"
          className="gap-0 rounded-t-2xl px-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)]"
        >
          <SheetHeader className="px-1">
            <SheetTitle>更多</SheetTitle>
            <SheetDescription className="sr-only">
              文献解读、查文献、用户管理与账号设置入口
            </SheetDescription>
          </SheetHeader>
          <div className="grid">
            {more.map(({ to, label, icon: Icon }) => (
              <Link
                key={to}
                to={to}
                onClick={() => setOpen(false)}
                className="flex h-11 items-center gap-3 rounded-md px-3 text-sm transition-colors hover:bg-accent"
              >
                <Icon className="size-4 text-muted-foreground" />
                {label}
              </Link>
            ))}
          </div>
        </SheetContent>
      </Sheet>
    </nav>
  )
}
