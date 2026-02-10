import { Link, Outlet, useLocation } from 'react-router-dom'
import { Activity, Settings, BarChart3, Scan, Target, Crosshair, TrendingUp } from 'lucide-react'
import clsx from 'clsx'

const navItems = [
  { path: '/dashboard', label: 'Dashboard', icon: BarChart3 },
  { path: '/opportunities', label: 'Opportunities', icon: Crosshair },
  { path: '/pipeline', label: 'Pipeline', icon: Activity },
  { path: '/paper-trading', label: 'Paper Trading', icon: TrendingUp },
  { path: '/calibration', label: 'Calibration', icon: Target },
  { path: '/config', label: 'Policy', icon: Settings },
]

export default function Layout() {
  const location = useLocation()

  return (
    <div className="min-h-screen bg-oss-bg">
      {/* Top navigation */}
      <nav className="sticky top-0 z-50 border-b border-oss-border bg-oss-surface/95 backdrop-blur">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex h-16 items-center justify-between">
            {/* Logo */}
            <Link to="/dashboard" className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-oss-accent/10 text-oss-accent">
                <Scan className="h-5 w-5" />
              </div>
              <div>
                <span className="text-lg font-semibold text-oss-text">OSS</span>
                <span className="ml-2 text-sm text-oss-muted">Option Scanner System</span>
              </div>
            </Link>

            {/* Navigation links */}
            <div className="flex items-center gap-1">
              {navItems.map((item) => {
                const isActive = location.pathname === item.path
                const Icon = item.icon
                return (
                  <Link
                    key={item.path}
                    to={item.path}
                    className={clsx(
                      'flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors',
                      isActive
                        ? 'bg-oss-accent/10 text-oss-accent'
                        : 'text-oss-muted hover:bg-oss-border hover:text-oss-text'
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    {item.label}
                  </Link>
                )
              })}
            </div>

            {/* Status indicator */}
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-2 rounded-lg bg-oss-surface px-3 py-1.5 text-xs">
                <span className="h-2 w-2 rounded-full bg-oss-approve animate-pulse" />
                <span className="text-oss-muted">System Online</span>
              </div>
            </div>
          </div>
        </div>
      </nav>

      {/* Main content */}
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  )
}
