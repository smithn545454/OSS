import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Layout from '../components/Layout'

describe('Layout', () => {
  it('renders navigation links', () => {
    render(
      <MemoryRouter initialEntries={['/opportunities']}>
        <Layout />
      </MemoryRouter>
    )

    expect(screen.getByText('Opportunities')).toBeInTheDocument()
    expect(screen.getByText('Pipeline')).toBeInTheDocument()
    expect(screen.getByText('Paper Trading')).toBeInTheDocument()
    expect(screen.getByText('Policy')).toBeInTheDocument()
  })

  it('renders the OSS branding', () => {
    render(
      <MemoryRouter initialEntries={['/opportunities']}>
        <Layout />
      </MemoryRouter>
    )

    expect(screen.getByText('OSS')).toBeInTheDocument()
    expect(screen.getByText('Option Scanner System')).toBeInTheDocument()
  })

  it('highlights active nav item', () => {
    render(
      <MemoryRouter initialEntries={['/pipeline']}>
        <Layout />
      </MemoryRouter>
    )

    const pipelineLink = screen.getByText('Pipeline').closest('a')
    expect(pipelineLink?.className).toContain('text-oss-accent')
  })

  it('renders all navigation items', () => {
    render(
      <MemoryRouter initialEntries={['/opportunities']}>
        <Layout />
      </MemoryRouter>
    )

    const links = screen.getAllByRole('link')
    // 5 nav items + 1 logo link = 6 links
    expect(links.length).toBeGreaterThanOrEqual(5)
  })
})
