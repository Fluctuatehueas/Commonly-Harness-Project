// 演进接口：当前前端只读取经验库列表
import { API_BASE } from './client'

export type AdmissionState =
  | 'proposed'
  | 'verified'
  | 'quarantined'
  | 'active'
  | 'archived'

export interface ExperienceRecord {
  experience_id: string
  scope: string
  kind: 'success_pattern' | 'failure_trigger' | 'contrast'
  trigger: string
  rule: string
  evidence_refs: string[]
  utility: number
  utility_observations: number
  confidence: number
  provenance: string
  admission_state: AdmissionState
  generation: number
  tags: string[]
  ttl_days: number
  created_at: string
  updated_at: string
  hit_count: number
}

export interface ExperienceListResponse {
  user_id: string
  count: number
  experiences: ExperienceRecord[]
}

export async function fetchExperiences(
  userId: string,
  statuses: string[] = ['active', 'proposed'],
): Promise<ExperienceListResponse> {
  const params = new URLSearchParams({
    user_id: userId,
    limit: '200',
  })
  if (statuses.length) params.set('status', statuses.join(','))
  const res = await fetch(`${API_BASE}/evolution/experiences?${params.toString()}`, {
    signal: AbortSignal.timeout(10000),
  })
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail || `经验库读取失败: HTTP ${res.status}`)
  }
  return (await res.json()) as ExperienceListResponse
}
