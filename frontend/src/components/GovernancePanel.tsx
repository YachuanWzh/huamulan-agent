import { useCallback, useEffect, useState } from 'react'
import { api, type BudgetSnapshot, type GovernancePolicyDocument, type GovernancePolicyVersion } from '../lib/api'

interface Props { threadId: string | null }

export function GovernancePanel({ threadId }: Props) {
  const [budget, setBudget] = useState<BudgetSnapshot | null>(null)
  const [policies, setPolicies] = useState<GovernancePolicyVersion[]>([])
  const [draft, setDraft] = useState<GovernancePolicyDocument>({})
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try { const [nextBudget, nextPolicies] = await Promise.all([api.getBudget(threadId ?? undefined), api.listGovernancePolicies()]); setBudget(nextBudget); setPolicies(nextPolicies); setDraft(nextBudget.policy.document); setError(null) } catch { setError('暂时无法读取治理状态。') }
  }, [threadId])
  useEffect(() => { void load() }, [load])
  const numeric = (field: 'max_global_tokens' | 'max_thread_tokens', value: string) => setDraft((current) => ({ ...current, [field]: value === '' ? null : Number(value) }))
  const save = async () => { try { const saved = await api.createGovernancePolicy(draft); setPolicies((current) => [saved, ...current]); setBudget((current) => current ? { ...current, policy: saved } : current); setError(null) } catch { setError('策略保存失败，请检查输入。') } }
  const totals = budget?.totals
  return <section className="workspace-section governance-panel" aria-labelledby="governance-heading">
    <header className="workspace-header command-header"><div><span className="command-kicker">执行治理</span><h2 id="governance-heading">作战约束</h2><p>预算、策略与自动处置边界。</p></div><button className="command-button quiet" onClick={() => void load()}>刷新</button></header>
    {error && <p role="alert" className="workspace-empty">{error}</p>}
    {totals && <div className="governance-metrics" data-testid="governance-status-strip"><div><span>当前消耗</span><strong>{totals.total_tokens.toLocaleString()} <em>tokens</em></strong></div><div><span>预计成本</span><strong>${String(totals.estimated_cost_usd)}</strong></div><div><span>生效策略</span><strong>策略 v{budget?.policy.version}</strong></div></div>}
    <div className="governance-form"><label>全局 Token 上限<input aria-label="Global token limit" type="number" min="0" value={draft.max_global_tokens ?? ''} onChange={(event) => numeric('max_global_tokens', event.target.value)} /></label><label>单会话 Token 上限<input aria-label="Thread token limit" type="number" min="0" value={draft.max_thread_tokens ?? ''} onChange={(event) => numeric('max_thread_tokens', event.target.value)} /></label><button className="command-button" onClick={() => void save()}>保存为新策略</button></div>
    <div className="governance-history"><span>策略历史</span>{policies.map((policy) => <span className={`policy-chip ${policy.is_active ? 'active' : ''}`} key={policy.version}>v{policy.version}{policy.is_active ? ' · 当前' : ''}</span>)}</div>
  </section>
}
