import { useId, useState } from 'react'
import type { BuildResult, CompatibilityResult, Part, ScoredBuild } from '../api'
import { formatPeso, partName, partSpecs } from '../format'
import { WarningIcon } from './icons'

const ROWS: { key: keyof ScoredBuild['components']; label: string }[] = [
  { key: 'cpu', label: 'CPU' },
  { key: 'gpu', label: 'GPU' },
  { key: 'motherboard', label: 'Board' },
  { key: 'ram', label: 'Memory' },
  { key: 'storage', label: 'Storage' },
  { key: 'psu', label: 'Power' },
  { key: 'cooler', label: 'Cooler' },
  { key: 'case', label: 'Case' },
]

function priceNote(parts: Part[]): string {
  const types = new Set(parts.map((p) => p.price_type))
  if (types.size === 1 && types.has('observed')) return 'Median of current Shopee and Lazada listings.'
  if (types.has('observed')) return 'Mix of live retailer medians and curated estimates.'
  return 'Prices are curated estimates, not live quotes.'
}

function Compatibility({ results }: { results: CompatibilityResult[] }) {
  const issues = results.filter((r) => r.severity !== 'info')
  const passed = results.length - issues.length
  return (
    <div className="build-compat">
      <p className="build-compat-summary">
        {issues.length === 0
          ? `All ${results.length} compatibility checks passed.`
          : `${passed} of ${results.length} checks passed.`}
      </p>
      {issues.length > 0 && (
        <ul className="build-issues">
          {issues.map((issue) => (
            <li key={issue.rule} className={`build-issue build-issue--${issue.severity}`}>
              <WarningIcon width={16} height={16} />
              <span>{issue.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function PartsTable({ build }: { build: ScoredBuild }) {
  const rows = ROWS.map(({ key, label }) => {
    const value = build.components[key]
    return { key, label, parts: Array.isArray(value) ? value : value ? [value] : [] }
  })
  const parts = rows.flatMap((r) => r.parts)

  return (
    <table className="build-parts">
      <caption className="visually-hidden">Parts in the {build.label.toLowerCase()} build</caption>
      <tbody>
        {rows.map(({ key, label, parts: list }) => {
          if (list.length === 0) {
            return key === 'gpu' ? (
              <tr key={key}>
                <th scope="row">{label}</th>
                <td className="build-part-none">Integrated graphics</td>
                <td className="build-part-price" />
              </tr>
            ) : null
          }
          return list.map((part, i) => (
            <tr key={`${key}-${part.id}`}>
              <th scope="row">{i === 0 ? label : ''}</th>
              <td>
                <span className="build-part-name">{partName(part)}</span>
                <span className="build-part-specs">{partSpecs(part)}</span>
              </td>
              <td className="build-part-price">{formatPeso(part.price_php)}</td>
            </tr>
          ))
        })}
      </tbody>
      <tfoot>
        <tr>
          <th scope="row">Total</th>
          <td className="build-price-note">{priceNote(parts)}</td>
          <td className="build-part-price build-total">{formatPeso(build.score.total_price_php)}</td>
        </tr>
      </tfoot>
    </table>
  )
}

export function BuildCard({ result }: { result: BuildResult }) {
  const builds = [result.primary, ...result.alternatives]
  const [selected, setSelected] = useState(0)
  const tabsId = useId()
  const build = builds[selected]

  return (
    <section className="build" aria-label="Recommended build">
      {builds.length > 1 && (
        <div className="build-tabs" role="tablist" aria-label="Build options">
          {builds.map((b, i) => (
            <button
              key={b.label}
              type="button"
              role="tab"
              id={`${tabsId}-tab-${i}`}
              aria-selected={i === selected}
              aria-controls={`${tabsId}-panel`}
              tabIndex={i === selected ? 0 : -1}
              className="build-tab"
              onClick={() => setSelected(i)}
              onKeyDown={(e) => {
                if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
                const next = (i + (e.key === 'ArrowRight' ? 1 : builds.length - 1)) % builds.length
                setSelected(next)
                document.getElementById(`${tabsId}-tab-${next}`)?.focus()
              }}
            >
              <span>{b.label}</span>
              <span className="build-tab-price">{formatPeso(b.score.total_price_php)}</span>
            </button>
          ))}
        </div>
      )}
      <div
        id={`${tabsId}-panel`}
        role={builds.length > 1 ? 'tabpanel' : undefined}
        aria-labelledby={builds.length > 1 ? `${tabsId}-tab-${selected}` : undefined}
      >
        <PartsTable build={build} />
        <Compatibility results={build.compatibility} />
      </div>
    </section>
  )
}
