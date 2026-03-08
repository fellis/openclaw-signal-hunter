interface PageHeaderProps {
  title: string
  subtitle: string
  action?: React.ReactNode
}

export default function PageHeader({ title, subtitle, action }: PageHeaderProps) {
  return (
    <div
      className="flex items-center justify-between px-4 py-3 border-b shrink-0"
      style={{ borderColor: 'var(--border)' }}
    >
      <div>
        <h1 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
          {title}
        </h1>
        <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
          {subtitle}
        </p>
      </div>
      {action != null && <div>{action}</div>}
    </div>
  )
}
