import { api } from "@/lib/api";
import { BanditChart } from "@/components/analytics/BanditChart";
import { ApiDown } from "@/components/ui/ApiDown";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import type { ArmPosterior } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AnalyticsPage() {
  let campaigns;
  try {
    campaigns = await api.campaigns();
  } catch (e) {
    return (
      <>
        <PageHeader
          title="Analytics"
          subtitle="Thompson sampling over copy variants. Traffic shifts to winners as posteriors tighten."
        />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  const bandits: { name: string; arms: ArmPosterior[] }[] = [];
  for (const c of campaigns) {
    try {
      const arms = await api.bandit(c.id);
      if (arms.length) bandits.push({ name: c.name, arms });
    } catch {
      // campaign with no variants yet
    }
  }

  return (
    <>
      <PageHeader
        title="Analytics"
        subtitle="Thompson sampling over copy variants. Traffic shifts to winners as posteriors tighten."
      />

      {bandits.length === 0 ? (
        <EmptyState
          title="No bandit data yet"
          body="Add variants to a campaign and send — posteriors appear here as replies come in."
        />
      ) : (
        <div className="grid gap-6 animate-rise-delay-1">
          {bandits.map((b) => {
            const byStep = new Map<number, ArmPosterior[]>();
            for (const arm of b.arms) {
              const list = byStep.get(arm.step_order) ?? [];
              list.push(arm);
              byStep.set(arm.step_order, list);
            }
            return (
              <div key={b.name} className="space-y-4">
                {[...byStep.entries()]
                  .sort((a, c) => a[0] - c[0])
                  .map(([step, arms]) => (
                    <BanditChart
                      key={`${b.name}-${step}`}
                      title={`${b.name} — step ${step}`}
                      arms={arms}
                    />
                  ))}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
