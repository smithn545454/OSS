import { useEffect } from 'react'

export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = title ? `OSS - ${title}` : 'OSS - Option Scanner System'
  }, [title])
}
