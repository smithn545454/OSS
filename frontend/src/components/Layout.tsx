import { Link, Outlet, useLocation } from 'react-router-dom'
import { Activity, Settings, BarChart3, Scan, Target, Crosshair, TrendingUp } from 'lucide-react'
import clsx from 'clsx'

const navItems = [
  { path: '/dashboard', label: 'Dashboard', shortLabel: 'Dash', icon: BarChart3 },
  { path: '/opps', label: 'Opportunities', shortLabel: 'Opps', icon: Crosshair },
  { path: '/pipeline', label: 'Pipeline', shortLabel: 'Pipeline', icon: Activity },
  { path: '/paper-trading', label: 'Paper Trading', shortLabel: 'Paper', icon: TrendingUp },
  { path: '/calibration', label: 'Calibration', shortLabel: 'Cal', icon: Target },
  { path: '/config', label: 'Policy', shortLabel: 'Policy', icon: Settings },
]

export default function Layout() {
  const location = useLocation()

  return (
    <div className="min-h-screen bg-oss-bg">
      {/* Top navigation */}
      <nav className="sticky top-0 z-50 border-b border-oss-border bg-oss-surface/95 backdrop-blur">
        <div className="mx-auto max-w-7xl px-3 sm:px-4 lg:px-8">
          <div className="flex h-12 sm:h-14 lg:h-16 items-center justify-between">
            {/* Logo */}
            <Link to="/dashboard" className="flex items-center gap-2 sm:gap-3 shrink-0">
              <div className="flex h-8 w-8 sm:h-9 sm:w-9 items-center justify-center rounded-lg bg-oss-accent/10 text-oss-accent">
                <Scan className="h-4 w-4 sm:h-5 sm:w-5" />
              </div>
              <div>
                <span className="text-base sm:text-lg font-semibold text-oss-text">OSS</span>
                <span className="ml-2 text-sm text-oss-muted hidden lg:inline">Option Scanner System</span>
              </div>
            </Link>

            {/* Navigation links */}
            <div className="flex items-center gap-0.5 sm:gap-1 overflow-x-auto">
              {navItems.map((item) => {
                const isActive = location.pathname === item.path
                const Icon = item.icon
                return (
                  <Link
                    key={item.path}
                    to={item.path}
                    className={clsx(
                      'flex items-center gap-1.5 sm:gap-2 rounded-lg px-2 sm:px-3 lg:px-4 py-1.5 sm:py-2 text-xs sm:text-sm font-medium transition-colors whitespace-nowrap',
                      isActive
                        ? 'bg-oss-accent/10 text-oss-accent'
                        : 'text-oss-muted hover:bg-oss-border hover:text-oss-text'
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    <span className="hidden sm:inline lg:hidden">{item.shortLabel}</span>
                    <span className="hidden lg:inline">{item.label}</span>
                  </Link>
                )
              })}
            </div>

            {/* Status indicator */}
            <div className="hidden md:flex items-center gap-2 shrink-0">
              <div className="flex items-center gap-2 rounded-lg bg-oss-surface px-3 py-1.5 text-xs">
                <span className="h-2 w-2 rounded-full bg-oss-approve animate-pulse" />
                <span className="text-oss-muted">System Online</span>
              </div>
            </div>
          </div>
        </div>
      </nav>

      {/* Main content */}
      <main className="mx-auto max-w-7xl px-3 py-4 sm:px-4 sm:py-6 lg:px-8 lg:py-8">
        <Outlet />
      </main>
    </div>
  )
}
