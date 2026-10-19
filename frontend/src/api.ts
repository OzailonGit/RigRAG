// Types mirror the FastAPI response models in backend/app/api/routers/chat.py
// and backend/app/recommendation/engine.py.

export type Severity = 'blocking' | 'warning' | 'info'

export interface CompatibilityResult {
  rule: string
  compatible: boolean
  severity: Severity
  message: string
}

export interface ScoreBreakdown {
  total: number
  performance_fit: number
  price_to_performance: number
  preference_match: number
  upgrade_headroom: number
  power_penalty: number
  budget_overrun_penalty: number
  total_price_php: number
}

/** A flat Product + spec row, as returned by parts_service.to_full_dict. */
export type Part = Record<string, unknown> & {
  id: string
  category: string
  manufacturer: string
  model: string
  /** Serialized from a Python Decimal, so it arrives as a string like "11500.00". */
  price_php: number | string | null
  price_type?: string
}

export interface BuildComponents {
  cpu: Part
  motherboard: Part
  ram: Part
  storage: Part[]
  psu: Part
  cooler: Part
  case: Part
  gpu: Part | null
}

export interface ScoredBuild {
  label: string
  components: BuildComponents
  score: ScoreBreakdown
  compatibility: CompatibilityResult[]
}

export interface BuildResult {
  primary: ScoredBuild
  alternatives: ScoredBuild[]
}

export interface BuildRequest {
  budget_php: number
  currency: string
  use_case: string[]
  resolution: string | null
  target_fps: number | null
  games: string[]
  cpu_brand: string | null
  gpu_brand: string | null
  wifi_required: boolean
  small_form_factor: boolean
  quiet: boolean
  low_power: boolean
  prioritize_upgradeability: boolean
}

export interface ChatResponse {
  reply: string
  state: BuildRequest
  build: BuildResult | null
}

export class ChatError extends Error {}

export async function sendChatMessage(sessionId: string, message: string, signal?: AbortSignal): Promise<ChatResponse> {
  let response: Response
  try {
    response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message }),
      signal,
    })
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error
    throw new ChatError("Can't reach the RigRAG server. Check that the backend is running.")
  }

  if (!response.ok) {
    let detail = `The server returned an error (${response.status}).`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // Non-JSON error body; keep the status message.
    }
    throw new ChatError(detail)
  }
  return response.json()
}
