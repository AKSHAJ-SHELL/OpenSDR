import { api } from "@/lib/api";
import { getSession } from "@/lib/session";
import { ConnectionCard } from "@/components/crm/ConnectionCard";
import { ConnectionCreateForm } from "@/components/crm/ConnectionCreateForm";
import { ApiDown } from "@/components/ui/ApiDown";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import type { Campaign, CRMSyncRun } from "@/lib/types";

export const dynamic = "force-dynamic";

const SUBTITLE =
  "Connect HubSpot or Salesforce: import contact lists through the same ingest gate as CSV, and push reply and meeting activity back out.";

export default async function CrmPage() {
  const session = await getSession();
  const role = session?.role ?? "viewer";
  // Mirrors the API's scopes: owner ↔ admin (credentials, mapping),
  // operator ↔ operate (lists, import, sync). The /api/proxy route-scope
  // check is the enforcement; this only decides what to render.
  const canAdmin = role === "owner";
  const canOperate = canAdmin || role === "operator";

  let connections;
  try {
    connections = await api.crmConnections();
  } catch (e) {
    return (
      <>
        <PageHeader title="CRM sync" subtitle={SUBTITLE} />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  // Campaigns for the import picker plus each connection's run history —
  // allSettled so one failing lookup degrades that section, never the page.
  const [campaignsRes, ...runsRes] = await Promise.allSettled([
    api.campaigns(),
    ...connections.map((c) => api.crmRuns(c.id)),
  ]);
  const campaigns: Campaign[] =
    campaignsRes.status === "fulfilled" ? campaignsRes.value : [];
  const runsByConnection: CRMSyncRun[][] = runsRes.map((r) =>
    r.status === "fulfilled" ? (r.value as CRMSyncRun[]) : [],
  );

  return (
    <>
      <PageHeader title="CRM sync" subtitle={SUBTITLE} />

      <div className="grid gap-5 animate-rise-delay-1">
        {canAdmin ? <ConnectionCreateForm /> : null}

        {connections.length === 0 ? (
          <EmptyState
            title="No CRM connections yet"
            body={
              canAdmin
                ? "Connect a CRM above. Credentials are validated, encrypted at rest, and never shown again — imports always dry-run before touching your leads."
                : "Connecting a CRM (and rotating its credentials) is limited to owner sessions. Ask an owner to set one up; import and sync are available to operators once it exists."
            }
          />
        ) : (
          connections.map((connection, i) => (
            <ConnectionCard
              key={connection.id}
              connection={connection}
              runs={runsByConnection[i]}
              campaigns={campaigns}
              canAdmin={canAdmin}
              canOperate={canOperate}
            />
          ))
        )}
      </div>
    </>
  );
}
