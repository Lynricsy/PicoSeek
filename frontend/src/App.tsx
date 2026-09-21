import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import { RequireAuth } from '@/auth/RequireAuth'
import { BrandSplash } from '@/components/common/BrandSplash'
import { AppShell } from '@/components/layout/AppShell'
const AskPage = lazy(() => import('@/pages/AskPage'))
const LoginPage = lazy(() => import('@/pages/LoginPage'))
const HistoryPage = lazy(() => import('@/pages/HistoryPage'))
const InterpretPage = lazy(() => import('@/pages/InterpretPage'))
const AccountPage = lazy(() => import('@/pages/AccountPage'))
const UsersPage = lazy(() => import('@/pages/admin/UsersPage'))
const InstitutionPage = lazy(() => import('@/pages/admin/InstitutionPage'))
const LibraryPage = lazy(() => import('@/pages/explore/LibraryPage'))
const PaperPage = lazy(() => import('@/pages/explore/PaperPage'))
const KbSearchPage = lazy(() => import('@/pages/explore/KbSearchPage'))
const LiteratureSearchPage = lazy(
  () => import('@/pages/explore/LiteratureSearchPage'),
)

export default function App() {
  return (
    <Suspense fallback={<BrandSplash label="正在加载…" />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<AppShell />}>
            <Route index element={<AskPage />} />
            <Route path="/a/:answerId" element={<AskPage />} />
            <Route path="/interpret" element={<InterpretPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/account" element={<AccountPage />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/library/:key" element={<PaperPage />} />
            <Route path="/kb" element={<KbSearchPage />} />
            <Route path="/search" element={<LiteratureSearchPage />} />
            <Route element={<RequireAuth admin />}>
              <Route path="/admin/users" element={<UsersPage />} />
              <Route path="/admin/institution" element={<InstitutionPage />} />
            </Route>
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  )
}
