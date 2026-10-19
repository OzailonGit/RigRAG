import type { BuildRequest, Part } from './api'

const peso = new Intl.NumberFormat('en-PH', { style: 'currency', currency: 'PHP', maximumFractionDigits: 0 })

export function formatPeso(value: number | string | null | undefined): string {
  const amount = value == null || value === '' ? NaN : Number(value)
  return Number.isFinite(amount) ? peso.format(amount) : 'No price yet'
}

export function partName(part: Part): string {
  return `${part.manufacturer} ${part.model}`
}

function n(value: unknown): number | null {
  return typeof value === 'number' ? value : null
}

function s(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null
}

function join(...bits: (string | null | false | undefined)[]): string {
  return bits.filter(Boolean).join(' · ')
}

function capacity(gb: number | null): string | null {
  if (gb == null) return null
  return gb >= 1000 && gb % 1000 === 0 ? `${gb / 1000}TB` : `${gb}GB`
}

const STORAGE_LABEL: Record<string, string> = { SSD_NVME: 'NVMe SSD', SSD_SATA: 'SATA SSD', HDD: 'HDD' }

/** One-line spec summary for a part, showing what matters for that category. */
export function partSpecs(part: Part): string {
  const p = part
  switch (p.category) {
    case 'cpu':
      return join(
        n(p.cores) != null && `${p.cores}C/${p.threads}T`,
        s(p.socket),
        n(p.boost_clock_ghz) != null && `${p.boost_clock_ghz} GHz`,
        n(p.tdp_w) != null && `${p.tdp_w}W`,
      )
    case 'gpu': {
      const chip = s(p.chip)
      const showChip = chip && !p.model.toLowerCase().includes(chip.toLowerCase())
      return join(showChip && chip, n(p.vram_gb) != null && `${p.vram_gb}GB ${s(p.vram_type) ?? ''}`.trim(), n(p.tdp_w) != null && `${p.tdp_w}W`)
    }
    case 'motherboard':
      return join(s(p.chipset), s(p.form_factor), s(p.ram_type), p.wifi === true && 'Wi-Fi')
    case 'ram': {
      const cap = n(p.capacity_gb)
      const sticks = n(p.sticks)
      const layout = cap != null && sticks ? `${sticks}×${cap / sticks}GB` : null
      return join(capacity(cap), layout, s(p.ddr_gen) && n(p.speed_mhz) != null && `${p.ddr_gen}-${p.speed_mhz}`)
    }
    case 'storage':
      return join(capacity(n(p.capacity_gb)), STORAGE_LABEL[s(p.type) ?? ''] ?? s(p.type))
    case 'psu':
      return join(n(p.wattage) != null && `${p.wattage}W`, s(p.efficiency_rating))
    case 'cooler':
      return p.type === 'aio'
        ? join('Liquid AIO', n(p.radiator_mm) != null && `${p.radiator_mm}mm radiator`)
        : join('Air cooler', n(p.height_mm) != null && `${p.height_mm}mm tall`)
    case 'case': {
      const factors = Array.isArray(p.form_factor_support) ? (p.form_factor_support as string[]).join(', ') : null
      return join(factors, n(p.max_gpu_length_mm) != null && `GPUs up to ${p.max_gpu_length_mm}mm`)
    }
    default:
      return ''
  }
}

const USE_CASE_LABEL: Record<string, string> = {
  gaming: 'Gaming',
  productivity: 'Productivity',
  ai_ml: 'AI / ML',
  video_editing: 'Video editing',
  rendering_3d: '3D rendering',
  programming: 'Programming',
  general: 'General use',
  mixed: 'Mixed use',
}

// Mirrors MIN_VALID_BUDGET in backend/app/llm/chat_orchestrator.py: below this,
// the budget is a placeholder the server stores until the user states one.
const MIN_VALID_BUDGET = 500

/** Short labels for the constraints the server has parsed so far. */
export function requestChips(state: BuildRequest | undefined): string[] {
  if (!state || state.budget_php < MIN_VALID_BUDGET) return []
  return [
    formatPeso(state.budget_php),
    ...state.use_case.map((u) => USE_CASE_LABEL[u] ?? u),
    state.resolution,
    state.cpu_brand && `${state.cpu_brand} CPU`,
    state.gpu_brand && `${state.gpu_brand} GPU`,
    state.wifi_required && 'Wi-Fi',
    state.small_form_factor && 'Small form factor',
    state.quiet && 'Quiet',
    state.low_power && 'Low power',
    state.prioritize_upgradeability && 'Upgradeable',
  ].filter((x): x is string => Boolean(x))
}
